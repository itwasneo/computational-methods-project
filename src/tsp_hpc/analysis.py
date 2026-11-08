"""Validated, compact result tables and conservative fixed-work comparisons.

Read immutable runner-v1 shards one bounded line at a time. Tours and complete
plans are validated then discarded; byte locators support lazy detail access.
This verifies recorded structure/identities, not objective costs without inputs.
"""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd

from tsp_hpc.data import validate_tour
from tsp_hpc.identity import canonical_json, stable_id
from tsp_hpc.manifests import Manifest
from tsp_hpc.results import WorkCounters

MAX_RECORD_BYTES = 64 * 1024 * 1024
TRIAL_FIELDS = set("schema_version record_type campaign_id attempt_id started_at requested_plan effective_execution execution_id runtime input setup_policy max_matrix_bytes adapter outcome result error timings".split())
PHASES = set("input_time_s distance_preparation_wall_time_s distance_warmup_time_s preprocessing_time_s solver_warmup_time_s solve_wall_time_s search_time_s solver_validation_time_s runner_validation_time_s pre_persistence_time_s failed_phase_time_s".split())
METRICS = ("search_time_s", "preprocessing_time_s", "distance_warmup_time_s", "solver_warmup_time_s",
           "solver_validation_time_s", "runner_validation_time_s", "pre_persistence_time_s",
           "persistence_time_s", "end_to_end_time_s")
TRIAL_COLUMNS = ("attempt_id campaign_id requested_execution_id execution_id search_id workload_id group_id "
                 "runtime_context_id instance_id coordinate_checksum distance_kind n_cities algorithm variant "
                 "repetition parameters_json solver_seed budget_name budget_count trial_seed implementation "
                 "distance_mode backend profile workers threads_per_worker max_matrix_bytes setup_policy "
                 "source_revision source_dirty outcome status best_cost tour_digest work_json error_phase error_type").split() + list(METRICS) + ["shard", "campaign_complete", "duplicate_execution", "usable"]


def _fields(value, required, optional=()):
    if type(value) is not dict:
        raise ValueError("expected a JSON object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f"missing fields {sorted(missing)}, unknown fields {sorted(unknown)}")


def _number(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("expected finite non-negative number")
    return float(value)


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected non-negative integer")
    return value


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("expected non-empty string")
    return value


def _uuid(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("invalid campaign/attempt ID")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _constant(text):
    raise ValueError(f"non-finite JSON constant: {text}")


def _decode(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                      parse_float=_float, parse_constant=_constant)


def _plan(record):
    """Rebuild the declared plan through the authoritative manifest/seed rules."""
    search = record["search"]
    experiment = {key: value for key, value in search.items()
                  if key not in ("instance", "repetition", "seed_scheme", "bit_generator")}
    experiment.update(instance_id=search["instance"]["instance_id"],
                      repetitions=[search["repetition"]], executions=[record["execution"]])
    manifest = Manifest({"schema_version": 1, "instances": [search["instance"]], "experiments": [experiment]})
    expected = manifest.expand()[0]
    if canonical_json(record) != canonical_json(expected.to_dict()):
        raise ValueError("requested plan, identities or random streams do not match")
    return expected


def _runtime(runtime):
    _fields(runtime, {"versions", "source", "platform", "machine", "processor", "cpu_count",
                      "memory", "runner_workers", "solver_threads", "cpu_affinity", "environment"})
    _fields(runtime["versions"], {"python", "tsp_hpc", "numpy", "numba", "llvmlite"})
    for value in runtime["versions"].values():
        _text(value)
    _fields(runtime["source"], {"revision", "dirty"})
    revision, dirty = runtime["source"]["revision"], runtime["source"]["dirty"]
    if revision is None:
        if dirty is not None:
            raise ValueError("unknown revision requires unknown dirty state")
    elif (type(revision) is not str or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is None
          or type(dirty) is not bool):
        raise ValueError("invalid source provenance")
    for key in ("platform", "machine", "processor"):
        if type(runtime[key]) is not str:
            raise ValueError("invalid platform observation")
    for key in ("runner_workers", "solver_threads"):
        if _count(runtime[key]) != 1:
            raise ValueError("runner-v1 analysis supports serial observations only")
    if runtime["cpu_count"] is not None and _count(runtime["cpu_count"]) == 0:
        raise ValueError("CPU count must be positive")
    affinity = runtime["cpu_affinity"]
    if affinity is not None:
        if type(affinity) is not list or any(type(cpu) is not int or cpu < 0 for cpu in affinity):
            raise ValueError("invalid CPU affinity")
        if affinity != sorted(set(affinity)):
            raise ValueError("invalid CPU affinity order/duplicates")
    _fields(runtime["memory"], {"host_physical_bytes", "address_space_soft_limit_bytes"})
    for value in runtime["memory"].values():
        if value is not None:
            _count(value)
    if type(runtime["environment"]) is not dict or any(type(v) is not str for v in runtime["environment"].values()):
        raise ValueError("invalid environment observations")
    # Job identity changes per allocation; numerical/resource observations do not.
    context = {**runtime, "environment": {k: v for k, v in runtime["environment"].items() if k != "SLURM_JOB_ID"}}
    return stable_id("analysis-runtime", context)


def _timings(values, allowed, required=()):
    _fields(values, set(required), set(allowed) - set(required))
    for value in values.values():
        _number(value)


def _less_equal(left, right):
    if not math.isfinite(left) or not math.isfinite(right) or (
        left > right and not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-9)
    ):
        raise ValueError("inconsistent nested timing durations")


def _trial(record):
    _fields(record, TRIAL_FIELDS)
    _uuid(record["campaign_id"])
    _uuid(record["attempt_id"])
    if datetime.fromisoformat(record["started_at"]).utcoffset() is None:
        raise ValueError("start timestamp requires a timezone")
    plan = _plan(record["requested_plan"])
    search, requested = plan.search, plan.execution
    effective = record["effective_execution"]
    if effective is not None:
        # An explicit choice cannot be substituted. An omitted label may resolve
        # to any named implementation, without relying on today's default.
        if type(effective) is not dict or "implementation" not in effective:
            raise ValueError("effective execution must name its implementation")
        _text(effective["implementation"])
        normalized = {**requested, "implementation": effective["implementation"]}
        if canonical_json(effective) != canonical_json(normalized) or (
            "implementation" in requested and canonical_json(effective) != canonical_json(requested)
        ):
            raise ValueError("effective execution changes the requested configuration")
        if record["execution_id"] != stable_id("execution", {"search_id": plan.search_id, "execution": effective}):
            raise ValueError("effective execution identity mismatch")
        if search["algorithm"] != "nn" or effective["backend"] != "serial" or search["instance"]["distance_kind"] != "euclidean_2d":
            raise ValueError("unsupported effective runner-v1 capability")
    elif record["execution_id"] is not None:
        raise ValueError("unresolved execution must have null identity")
    if record["setup_policy"] != "fresh-per-trial":
        raise ValueError("unsupported setup policy")
    _count(record["max_matrix_bytes"])
    context = _runtime(record["runtime"])
    _fields(record["input"], {"path"}, {"descriptor", "provenance"})
    if record["input"]["path"] is not None:
        _text(record["input"]["path"])
    times = record["timings"]
    _timings(times, PHASES, {"pre_persistence_time_s"})
    result, error = record["result"], record["error"]
    work, cost, tour_digest, status = None, None, None, None
    if record["outcome"] == "completed":
        if effective is None or error is not None or canonical_json(record["input"].get("descriptor")) != canonical_json(search["instance"]):
            raise ValueError("completed trial lacks verified input/execution")
        descriptor = search["instance"]
        content_id = stable_id("instance", {key: descriptor[key] for key in
                                           ("coordinate_checksum", "n_cities", "distance_kind")})
        if descriptor["instance_id"] != content_id:
            raise ValueError("completed artifact does not have a canonical content identity")
        _text(record["input"]["path"])
        provenance = record["input"].get("provenance")
        _fields(provenance, {"generator", "parameters", "seed", "bit_generator", "runtime"})
        _text(provenance["generator"])
        if type(provenance["parameters"]) is not dict or canonical_json(provenance["seed"]) != canonical_json(search["instance"]["instance_seed"]):
            raise ValueError("input provenance/seed mismatch")
        if provenance["bit_generator"] is not None:
            _text(provenance["bit_generator"])
        _fields(provenance["runtime"], {"python", "numpy", "tsp_hpc"})
        for value in provenance["runtime"].values():
            _text(value)
        adapter = record["adapter"]
        _fields(adapter, {"instance_id", "distance_kind", "distance_mode", "estimated_matrix_bytes", "matrix_bytes",
                          "max_matrix_bytes", "coordinate_bytes", "preprocessing_time_s", "jit_warmup_time_s",
                          "algorithm", "variant", "backend", "implementation", "solver_warmup_time_s"})
        n = search["instance"]["n_cities"]
        _count(adapter["max_matrix_bytes"])
        if effective["distance_mode"] == "dense" and 8*n*n > record["max_matrix_bytes"]:
            raise ValueError("dense storage exceeds recorded ceiling")
        for key, expected_bytes in {"estimated_matrix_bytes": 8*n*n, "coordinate_bytes": 16*n,
                                    "matrix_bytes": 8*n*n if effective["distance_mode"] == "dense" else 0}.items():
            if _count(adapter[key]) != expected_bytes:
                raise ValueError("adapter storage metadata mismatch")
        for key, value in {"instance_id": search["instance"]["instance_id"],
                           "distance_kind": search["instance"]["distance_kind"], "algorithm": search["algorithm"],
                           "variant": search["variant"], "backend": effective["backend"],
                           "implementation": effective["implementation"], "distance_mode": effective["distance_mode"],
                           "max_matrix_bytes": record["max_matrix_bytes"]}.items():
            if adapter[key] != value:
                raise ValueError("adapter metadata mismatch")
        _timings(times, PHASES - {"failed_phase_time_s"}, PHASES - {"failed_phase_time_s"})
        for key, phase in (("jit_warmup_time_s", "distance_warmup_time_s"),
                           ("preprocessing_time_s", "preprocessing_time_s"),
                           ("solver_warmup_time_s", "solver_warmup_time_s")):
            if _number(adapter[key]) != times[phase]:
                raise ValueError("adapter timing mismatch")
        _less_equal(times["search_time_s"] + times["solver_validation_time_s"], times["solve_wall_time_s"])
        _less_equal(times["distance_warmup_time_s"] + times["preprocessing_time_s"], times["distance_preparation_wall_time_s"])
        observed = math.fsum(times[key] for key in ("input_time_s", "distance_preparation_wall_time_s",
                                                    "solver_warmup_time_s", "solve_wall_time_s", "runner_validation_time_s"))
        _less_equal(observed, times["pre_persistence_time_s"])
        _fields(result, {"status", "best_tour", "best_cost", "work", "termination_reason", "history"})
        _fields(result["work"], set(WorkCounters().to_dict()))
        work = WorkCounters(**result["work"]).to_dict()
        count = len(search["budget"]["start_cities"])
        if work != WorkCounters(nn_starts_completed=count, full_tour_evaluations=count).to_dict():
            raise ValueError("NN work counters disagree with fixed workload")
        if result["termination_reason"] != "work_limit" or result["history"] is not None:
            raise ValueError("unsupported runner-v1 stopping/history")
        status = result["status"]
        if count == 0:
            if status != "no_solution" or result["best_tour"] is not None or result["best_cost"] is not None:
                raise ValueError("invalid empty-work result")
        else:
            tour = result["best_tour"]
            n = search["instance"]["n_cities"]
            if status != "ok" or type(tour) is not list or len(tour) != n or any(type(city) is not int or not 0 <= city < n for city in tour):
                raise ValueError("invalid recorded tour")
            array = np.array(tour, dtype=np.int64)
            validate_tour(array, n)
            tour_digest = hashlib.sha256(array.astype("<i8", copy=False).tobytes()).hexdigest()
            cost = _number(result["best_cost"])
    elif record["outcome"] == "failed":
        if result is not None:
            raise ValueError("failed trial cannot carry an accepted result")
        _fields(error, {"phase", "type", "message", "reported_work"})
        if error["phase"] not in ("capability", "input", "distance_preparation", "solver_warmup", "solve", "validation"):
            raise ValueError("unknown failure phase")
        if (effective is None) != (error["phase"] == "capability"):
            raise ValueError("failure phase disagrees with execution resolution")
        _text(error["type"])
        if type(error["message"]) is not str:
            raise ValueError("invalid error message")
        if error["reported_work"] is not None:
            _fields(error["reported_work"], set(WorkCounters().to_dict()))
            WorkCounters(**error["reported_work"])
        _number(times["failed_phase_time_s"])
    else:
        raise ValueError("unknown outcome")
    config = effective or requested
    workload = {key: value for key, value in search.items() if key != "repetition"}
    source = record["runtime"]["source"]
    group = {"workload": workload, "execution": config, "runtime": context,
             "setup_policy": record["setup_policy"], "max_matrix_bytes": record["max_matrix_bytes"],
             "unverified_source_campaign": None if source["revision"] and source["dirty"] is False else record["campaign_id"]}
    return {
        "attempt_id": record["attempt_id"], "campaign_id": record["campaign_id"],
        "requested_execution_id": plan.execution_id, "execution_id": record["execution_id"],
        "search_id": plan.search_id, "workload_id": stable_id("analysis-workload", workload),
        "group_id": stable_id("analysis-group", group), "runtime_context_id": context,
        "instance_id": search["instance"]["instance_id"], "coordinate_checksum": search["instance"]["coordinate_checksum"],
        "distance_kind": search["instance"]["distance_kind"], "n_cities": search["instance"]["n_cities"],
        "algorithm": search["algorithm"], "variant": search["variant"], "repetition": search["repetition"],
        "parameters_json": canonical_json(search["parameters"]), "solver_seed": str(search["solver_seed"]),
        "budget_name": next(iter(search["budget"])),
        "budget_count": len(search["budget"]["start_cities"]) if search["algorithm"] == "nn" else next(iter(search["budget"].values())),
        "trial_seed": str(plan.trial_seed), "implementation": config.get("implementation"),
        "distance_mode": config["distance_mode"], "backend": config["backend"],
        "profile": config["resources"]["profile"], "workers": config["resources"]["workers"],
        "threads_per_worker": config["resources"]["threads_per_worker"], "max_matrix_bytes": record["max_matrix_bytes"],
        "setup_policy": record["setup_policy"], "source_revision": record["runtime"]["source"]["revision"],
        "source_dirty": record["runtime"]["source"]["dirty"], "outcome": record["outcome"], "status": status,
        "best_cost": cost, "tour_digest": tour_digest,
        "work_json": None if work is None else canonical_json(work),
        "error_phase": None if error is None else error["phase"],
        "error_type": None if error is None else error["type"],
        **{key: float(times[key]) if key in times else None for key in METRICS},
    }


def _summary(record, rows):
    _fields(record, {"schema_version", "record_type", "campaign_id", "counts", "planned_trials", "trial_timings", "timings"})
    _uuid(record["campaign_id"])
    if _count(record["planned_trials"]) != len(rows) or not rows:
        raise ValueError("summary planned count does not match trial records")
    expected = {key: sum(row["outcome"] == key for row in rows) for key in ("completed", "failed")}
    _fields(record["counts"], expected)
    if any(_count(record["counts"][key]) != value for key, value in expected.items()):
        raise ValueError("summary outcome counts disagree")
    receipts = record["trial_timings"]
    if type(receipts) is not list or len(receipts) != len(rows):
        raise ValueError("missing/extra timing receipts")
    persistence = wall = 0.0
    for row, receipt in zip(rows, receipts):
        _fields(receipt, {"attempt_id", "persistence_time_s", "end_to_end_time_s"})
        if receipt["attempt_id"] != row["attempt_id"]:
            raise ValueError("timing receipt order/identity mismatch")
        duration, total = _number(receipt["persistence_time_s"]), _number(receipt["end_to_end_time_s"])
        _less_equal(row["pre_persistence_time_s"] + duration, total)
        row.update(persistence_time_s=duration, end_to_end_time_s=total)
        persistence += duration
        wall += total
    times = record["timings"]
    keys = {"setup_time_s", "trial_persistence_time_s", "end_to_end_time_s"}
    _timings(times, keys, keys)
    if not math.isclose(persistence, times["trial_persistence_time_s"], rel_tol=1e-10, abs_tol=1e-9):
        raise ValueError("summary persistence total disagrees")
    _less_equal(wall + times["setup_time_s"], times["end_to_end_time_s"])


def _stamp(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


@dataclass
class Analysis:
    """Compact mutable table for filtering; source locators retain no tours/plans."""

    trials: pd.DataFrame
    diagnostics: list[dict]
    sources: list[dict]
    _locations: dict = field(repr=False)

    def trial(self, attempt_id: str) -> dict:
        """Load details on demand; reject a changed source rather than stale offsets."""
        path, offset, length, digest, stamp = self._locations[attempt_id]
        if _stamp(path) != stamp:
            raise ValueError("source shard changed; reload analysis")
        with path.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read(length)
        if hashlib.sha256(raw).hexdigest() != digest or _stamp(path) != stamp:
            raise ValueError("source record changed; reload analysis")
        return _decode(raw)


def read_shards(paths, *, expected_manifest: Manifest | None = None,
                max_record_bytes: int = MAX_RECORD_BYTES) -> Analysis:
    """Validate shards without retaining tours; report incomplete final prefixes.

    Only an unterminated last line is treated as a torn write. Malformed complete
    records, schema/identity mismatches and contradictory summaries are errors.
    Without an expected manifest, entirely absent executions cannot be inferred.
    """
    if type(max_record_bytes) is not int or max_record_bytes < 1:
        raise ValueError("max_record_bytes must be a positive integer")
    if isinstance(paths, (str, Path)):
        raise TypeError("paths must be a collection of shard paths")
    if expected_manifest is not None and not isinstance(expected_manifest, Manifest):
        raise TypeError("expected_manifest must be a Manifest")
    paths = [Path(path).resolve() for path in paths]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("provide distinct input shards")
    rows, diagnostics, locations, campaigns, sources = [], [], {}, set(), []
    for path in paths:
        stamp = _stamp(path)
        digest = hashlib.sha256()
        local, campaign, footer = [], None, False
        with path.open("rb") as stream:
            line = 0
            while True:
                offset = stream.tell()
                remaining = stamp[2] - offset
                if remaining <= 0:
                    break
                raw = stream.readline(min(max_record_bytes + 1, remaining))
                if not raw:
                    break
                line += 1
                digest.update(raw)
                try:
                    if len(raw) > max_record_bytes:
                        raise ValueError("record exceeds configured byte limit")
                    if footer:
                        raise ValueError("content after campaign summary")
                    if not raw.endswith(b"\n"):
                        diagnostics.append({"code": "unterminated_tail", "path": str(path), "line": line})
                        break
                    record = _decode(raw)
                    if type(record) is not dict or type(record.get("schema_version")) is not int or record["schema_version"] != 1:
                        raise ValueError("unsupported runner envelope schema")
                    current = _uuid(record["campaign_id"])
                    if campaign is None:
                        campaign = current
                        if campaign in campaigns:
                            raise ValueError("duplicate campaign across shards")
                        campaigns.add(campaign)
                    if campaign != current:
                        raise ValueError("multiple campaigns in one shard")
                    if record["record_type"] == "trial":
                        row = _trial(record)
                        attempt = row["attempt_id"]
                        if attempt in locations:
                            raise ValueError("duplicate attempt ID")
                        locations[attempt] = (path, offset, len(raw), hashlib.sha256(raw).hexdigest(), stamp)
                        row["shard"] = str(path)
                        local.append(row)
                    elif record["record_type"] == "campaign_summary":
                        _summary(record, local)
                        footer = True
                    else:
                        raise ValueError("unknown record type")
                except (ValueError, TypeError, KeyError, OverflowError) as error:
                    raise ValueError(f"{path}:{line}: {error}") from error
        if _stamp(path) != stamp:
            raise ValueError(f"source changed during analysis: {path}")
        sources.append({"path": str(path), "sha256": digest.hexdigest(), "size_bytes": stamp[2]})
        if not footer:
            diagnostics.append({"code": "incomplete_campaign", "path": str(path), "observed_trials": len(local)})
        for row in local:
            row["campaign_complete"] = footer
        rows.extend(local)
    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=TRIAL_COLUMNS)
    if not frame.empty:
        for metric in ("best_cost", *METRICS):
            frame[metric] = pd.to_numeric(frame[metric])
        config_ids = frame["execution_id"].fillna(frame["requested_execution_id"])
        frame["duplicate_execution"] = config_ids.duplicated(keep=False)
        for identity, repeated in frame[frame["duplicate_execution"]].groupby(config_ids, sort=False):
            diagnostics.append({"code": "duplicate_execution", "execution_id": identity,
                                "attempt_ids": repeated["attempt_id"].tolist()})
        frame["usable"] = frame["campaign_complete"] & ~frame["duplicate_execution"] & frame["status"].eq("ok")
        failed = frame[frame["outcome"].eq("failed")]
        if not failed.empty:
            diagnostics.append({"code": "failed_trials", "attempt_ids": failed["attempt_id"].tolist()})
    else:
        for column in ("campaign_complete", "duplicate_execution", "usable"):
            frame[column] = pd.Series(dtype=bool)
    if expected_manifest is not None:
        expected = {plan.execution_id for plan in expected_manifest.expand()}
        observed = {row["requested_execution_id"] for row in rows}
        for code, identities in (("missing_executions", expected - observed), ("unexpected_executions", observed - expected)):
            if identities:
                diagnostics.append({"code": code, "requested_execution_ids": sorted(identities)})
    return Analysis(frame, diagnostics, sources, locations)


def summary_table(analysis: Analysis) -> pd.DataFrame:
    """Descriptive stats within exact workload/execution/observed-runtime groups."""
    frame = analysis.trials
    labels = ["instance_id", "n_cities", "algorithm", "variant", "implementation", "distance_mode",
              "backend", "profile", "workers", "threads_per_worker", "setup_policy", "max_matrix_bytes",
              "parameters_json", "solver_seed", "budget_name", "budget_count", "source_revision", "source_dirty",
              "runtime_context_id", "workload_id"]
    metrics = ["best_cost", *METRICS]
    if frame.empty:
        columns = ["group_id", *labels, "attempt_count", "failed_count", "no_solution_count",
                   "incomplete_count", "duplicate_count", "usable_count"]
        columns += [f"{metric}_{stat}" for metric in metrics for stat in ("count", "min", "max")]
        columns += [f"{metric}_{stat}" for metric in metrics for stat in ("median", "mean", "std")]
        return pd.DataFrame(columns=columns)
    grouped = frame.groupby("group_id", sort=True)
    summary = grouped[labels].first()
    summary["attempt_count"] = grouped.size()
    for name, mask in {
        "failed_count": frame["outcome"].eq("failed"), "no_solution_count": frame["status"].eq("no_solution"),
        "incomplete_count": ~frame["campaign_complete"], "duplicate_count": frame["duplicate_execution"],
        "usable_count": frame["usable"],
    }.items():
        summary[name] = mask.groupby(frame["group_id"]).sum()
    valid = frame.loc[frame["usable"], ["group_id", *metrics]]
    numeric = valid.groupby("group_id")[metrics]
    stats = numeric.agg(["count", "min", "max"])
    # Costs can be finite near float64's ceiling. Avoid overflowing sums,
    # squared deviations or the average of the two central order statistics.
    maxima = numeric.max()
    scaled = valid[metrics] / numeric.transform("max").replace(0, 1)
    normalized = scaled.groupby(valid["group_id"])
    means, deviations = normalized.mean() * maxima, normalized.std() * maxima
    lower = numeric.quantile(0.5, interpolation="lower")
    upper = numeric.quantile(0.5, interpolation="higher")
    medians = lower + (upper - lower) / 2
    for metric in metrics:
        for stat, values in (("median", medians), ("mean", means), ("std", deviations)):
            stats[(metric, stat)] = values[metric]
    stats.columns = [f"{metric}_{stat}" for metric, stat in stats.columns]
    result = summary.join(stats).reset_index()
    for metric in metrics:
        column = f"{metric}_count"
        result[column] = result[column].fillna(0).astype("int64")
    return result


def paired_comparisons(analysis: Analysis, *, baseline_implementation: str,
                       baseline_distance_mode: str | None = None, metric: str = "search_time_s") -> pd.DataFrame:
    """Pair by scientific trial, not row order or ratios of aggregate medians.

    Require unique successful attempts, matching returned work/tours/costs,
    clean known source and matching observed context/resources/setup. A match
    does not certify identical hardware or a statistically significant speedup.
    """
    if metric not in ("search_time_s", "end_to_end_time_s"):
        raise ValueError("comparison metric must be search_time_s or end_to_end_time_s")
    _text(baseline_implementation)
    if baseline_distance_mode not in (None, "dense", "on_demand"):
        raise ValueError("invalid baseline distance mode")
    frame = analysis.trials
    columns = ["attempt_id", "baseline_attempt_id", "group_id", "metric", "speedup", "reason"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    records = frame[["attempt_id", "group_id", "implementation", "distance_mode", "search_id", "usable",
                     "source_revision", "source_dirty", "runtime_context_id", "profile", "backend", "workers",
                     "threads_per_worker", "setup_policy", "max_matrix_bytes", "work_json", "tour_digest",
                     "best_cost", metric]].to_dict("records")
    bases = {}
    for row in records:
        if row["implementation"] == baseline_implementation:
            bases.setdefault((row["search_id"], row["distance_mode"]), []).append(row)
    pairs = []
    for row in records:
        mode = baseline_distance_mode or row["distance_mode"]
        if row["implementation"] == baseline_implementation and row["distance_mode"] == mode:
            continue
        candidates = bases.get((row["search_id"], mode), [])
        base = candidates[0] if len(candidates) == 1 else None
        reason, speedup = None, None
        if base is None:
            reason = "missing_or_ambiguous_baseline"
        elif not row["usable"] or not base["usable"]:
            reason = "unsuccessful_incomplete_or_duplicate_attempt"
        elif not row["source_revision"] or row["source_dirty"] is not False or base["source_dirty"] is not False:
            reason = "unknown_or_dirty_source"
        elif any(row[key] != base[key] for key in ("runtime_context_id", "profile", "backend", "workers",
                                                     "threads_per_worker", "setup_policy", "max_matrix_bytes")):
            reason = "incompatible_execution_context"
        elif row["work_json"] != base["work_json"] or row["tour_digest"] != base["tour_digest"] or abs(row["best_cost"] - base["best_cost"]) > 1e-12 + 1e-10 * abs(base["best_cost"]):
            reason = "different_returned_work_or_solution"
        elif not math.isfinite(row[metric]) or not math.isfinite(base[metric]) or min(row[metric], base[metric]) <= 0:
            reason = "missing_or_zero_duration"
        else:
            speedup = base[metric] / row[metric]
            if not math.isfinite(speedup) or speedup <= 0:
                reason, speedup = "unrepresentable_ratio", None
        pairs.append({"attempt_id": row["attempt_id"], "baseline_attempt_id": None if base is None else base["attempt_id"],
                      "group_id": row["group_id"], "metric": metric, "speedup": speedup, "reason": reason})
    return pd.DataFrame(pairs, columns=columns)


def reference_gaps(analysis: Analysis, document: dict) -> pd.DataFrame:
    """Attach explicitly sourced, objective-compatible references without pooling."""
    _fields(document, {"schema_version", "references"})
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or type(document["references"]) is not list:
        raise ValueError("unsupported reference document")
    references = {}
    for ref in document["references"]:
        _fields(ref, {"instance_id", "coordinate_checksum", "distance_kind", "kind", "cost", "source"})
        _text(ref["instance_id"])
        _text(ref["source"])
        if ref["kind"] not in ("exact", "best_known") or ref["distance_kind"] not in ("euclidean_2d", "tsplib_euc_2d"):
            raise ValueError("invalid reference kind or objective")
        if type(ref["coordinate_checksum"]) is not str or re.fullmatch(r"[0-9a-f]{64}", ref["coordinate_checksum"]) is None:
            raise ValueError("invalid reference checksum")
        ref = {**ref, "cost": _number(ref["cost"])}
        if ref["instance_id"] in references:
            raise ValueError("duplicate reference for instance")
        references[ref["instance_id"]] = ref
    rows = []
    if analysis.trials.empty:
        records = []
    else:
        records = analysis.trials[["instance_id", "coordinate_checksum", "distance_kind", "usable",
                                   "best_cost", "attempt_id", "group_id"]].to_dict("records")
    for row in records:
        ref = references.get(row["instance_id"])
        if ref is None:
            continue
        if any(row[key] != ref[key] for key in ("coordinate_checksum", "distance_kind")):
            raise ValueError("reference checksum/distance convention mismatch")
        if not row["usable"]:
            continue
        gap = row["best_cost"] - ref["cost"]
        if ref["kind"] == "exact" and gap < -(1e-12 + 1e-10 * abs(ref["cost"])):
            raise ValueError("reported solution is below the declared exact optimum")
        percent = None if ref["cost"] == 0 else 100.0 * (gap / ref["cost"])
        if percent is not None and not math.isfinite(percent):
            percent = None
        rows.append({"attempt_id": row["attempt_id"], "group_id": row["group_id"],
                     "reference_kind": ref["kind"], "reference_cost": ref["cost"], "reference_source": ref["source"],
                     "absolute_gap": gap, "percent_gap": percent})
    return pd.DataFrame(rows, columns=["attempt_id", "group_id", "reference_kind", "reference_cost", "reference_source", "absolute_gap", "percent_gap"])


def load_references(path) -> dict:
    """Strict reference JSON parsing (including duplicate/non-finite rejection)."""
    return _decode(Path(path).read_bytes())
