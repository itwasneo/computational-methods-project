"""Runner-v1/v2 record validation and scalar projection, independent of table storage.

Rebuild plans through the shared manifest rules; check recorded structure and
identities, not objective costs without original instance artifacts.
"""

from datetime import datetime
import hashlib
import json
import math
import re

import numpy as np

from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.problem.data import validate_tour
from tsp_hpc.reproducibility.identity import canonical_json, stable_id
from tsp_hpc.reproducibility.build import validate_observation, validate_source
from tsp_hpc.solvers.results import WorkCounters

TRIAL_FIELDS = set("schema_version record_type campaign_id attempt_id started_at requested_plan effective_execution execution_id runtime input setup_policy max_matrix_bytes adapter outcome result error timings".split())
PHASES = set("input_time_s distance_preparation_wall_time_s distance_warmup_time_s preprocessing_time_s solver_warmup_time_s solve_wall_time_s search_time_s solver_validation_time_s runner_validation_time_s pre_persistence_time_s failed_phase_time_s".split())
METRICS = ("search_time_s", "preprocessing_time_s", "distance_warmup_time_s", "solver_warmup_time_s",
           "solver_validation_time_s", "runner_validation_time_s", "pre_persistence_time_s",
           "persistence_time_s", "end_to_end_time_s")


def require_fields(value, required, optional=()):
    if type(value) is not dict:
        raise ValueError("expected a JSON object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f"missing fields {sorted(missing)}, unknown fields {sorted(unknown)}")


def require_number(value):
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


def require_text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("expected non-empty string")
    return value


def require_uuid(value):
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


def decode_record(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                      parse_float=_float, parse_constant=_constant)


def _plan(record):
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


def _runtime(runtime, schema_version):
    fields = {"versions", "source", "platform", "machine", "processor", "cpu_count",
              "memory", "runner_workers", "solver_threads", "cpu_affinity", "environment"}
    require_fields(runtime, fields | ({"build"} if schema_version == 2 else set()))
    require_fields(runtime["versions"], {"python", "tsp_hpc", "numpy", "numba", "llvmlite"})
    for value in runtime["versions"].values():
        require_text(value)
    validate_source(runtime["source"])
    if schema_version == 2:
        validate_observation(runtime["build"], runtime["source"], runtime["versions"]["tsp_hpc"])
    for key in ("platform", "machine", "processor"):
        if type(runtime[key]) is not str:
            raise ValueError("invalid platform observation")
    for key in ("runner_workers", "solver_threads"):
        if _count(runtime[key]) != 1:
            raise ValueError("runner analysis supports serial observations only")
    if runtime["cpu_count"] is not None and _count(runtime["cpu_count"]) == 0:
        raise ValueError("CPU count must be positive")
    affinity = runtime["cpu_affinity"]
    if affinity is not None:
        if type(affinity) is not list or any(type(cpu) is not int or cpu < 0 for cpu in affinity):
            raise ValueError("invalid CPU affinity")
        if affinity != sorted(set(affinity)):
            raise ValueError("invalid CPU affinity order/duplicates")
    require_fields(runtime["memory"], {"host_physical_bytes", "address_space_soft_limit_bytes"})
    for value in runtime["memory"].values():
        if value is not None:
            _count(value)
    if type(runtime["environment"]) is not dict or any(type(v) is not str for v in runtime["environment"].values()):
        raise ValueError("invalid environment observations")
    # Job identity changes per allocation; numerical/resource observations do not.
    context = {**runtime, "environment": {k: v for k, v in runtime["environment"].items() if k != "SLURM_JOB_ID"}}
    return stable_id("analysis-runtime", context)


def _timings(values, allowed, required=()):
    require_fields(values, set(required), set(allowed) - set(required))
    for value in values.values():
        require_number(value)


def _less_equal(left, right):
    if not math.isfinite(left) or not math.isfinite(right) or (
        left > right and not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-9)
    ):
        raise ValueError("inconsistent nested timing durations")


def _execution(record, search, requested, search_id):
    effective = record["effective_execution"]
    if effective is not None:
        # An omitted implementation may resolve to any named implementation;
        # explicit choices cannot be substituted or tied to today's default.
        if type(effective) is not dict or "implementation" not in effective:
            raise ValueError("effective execution must name its implementation")
        require_text(effective["implementation"])
        normalized = {**requested, "implementation": effective["implementation"]}
        if canonical_json(effective) != canonical_json(normalized) or (
            "implementation" in requested and canonical_json(effective) != canonical_json(requested)
        ):
            raise ValueError("effective execution changes the requested configuration")
        if record["execution_id"] != stable_id("execution", {"search_id": search_id, "execution": effective}):
            raise ValueError("effective execution identity mismatch")
        if search["algorithm"] != "nn" or effective["backend"] != "serial" or search["instance"]["distance_kind"] != "euclidean_2d":
            raise ValueError("unsupported effective runner capability")
    elif record["execution_id"] is not None:
        raise ValueError("unresolved execution must have null identity")
    return effective


def _completed(record, search, effective):
    """Verify artifact/adapter observations and complete timing boundaries."""
    if effective is None or record["error"] is not None or canonical_json(record["input"].get("descriptor")) != canonical_json(search["instance"]):
        raise ValueError("completed trial lacks verified input/execution")
    descriptor = search["instance"]
    content_id = stable_id("instance", {key: descriptor[key] for key in
                                       ("coordinate_checksum", "n_cities", "distance_kind")})
    if descriptor["instance_id"] != content_id:
        raise ValueError("completed artifact does not have a canonical content identity")
    require_text(record["input"]["path"])
    provenance = record["input"].get("provenance")
    require_fields(provenance, {"generator", "parameters", "seed", "bit_generator", "runtime"})
    require_text(provenance["generator"])
    if type(provenance["parameters"]) is not dict or canonical_json(provenance["seed"]) != canonical_json(search["instance"]["instance_seed"]):
        raise ValueError("input provenance/seed mismatch")
    if provenance["bit_generator"] is not None:
        require_text(provenance["bit_generator"])
    require_fields(provenance["runtime"], {"python", "numpy", "tsp_hpc"})
    for value in provenance["runtime"].values():
        require_text(value)
    adapter = record["adapter"]
    require_fields(adapter, {"instance_id", "distance_kind", "distance_mode", "estimated_matrix_bytes", "matrix_bytes",
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
    times = record["timings"]
    _timings(times, PHASES - {"failed_phase_time_s"}, PHASES - {"failed_phase_time_s"})
    for key, phase in (("jit_warmup_time_s", "distance_warmup_time_s"),
                       ("preprocessing_time_s", "preprocessing_time_s"),
                       ("solver_warmup_time_s", "solver_warmup_time_s")):
        if require_number(adapter[key]) != times[phase]:
            raise ValueError("adapter timing mismatch")
    _less_equal(times["search_time_s"] + times["solver_validation_time_s"], times["solve_wall_time_s"])
    _less_equal(times["distance_warmup_time_s"] + times["preprocessing_time_s"], times["distance_preparation_wall_time_s"])
    observed = math.fsum(times[key] for key in ("input_time_s", "distance_preparation_wall_time_s",
                                                "solver_warmup_time_s", "solve_wall_time_s", "runner_validation_time_s"))
    _less_equal(observed, times["pre_persistence_time_s"])


def _result(result, search):
    """Return compact work/cost/tour identity without retaining the tour."""
    require_fields(result, {"status", "best_tour", "best_cost", "work", "termination_reason", "history"})
    require_fields(result["work"], set(WorkCounters().to_dict()))
    work = WorkCounters(**result["work"]).to_dict()
    count = len(search["budget"]["start_cities"])
    if work != WorkCounters(nn_starts_completed=count, full_tour_evaluations=count).to_dict():
        raise ValueError("NN work counters disagree with fixed workload")
    if result["termination_reason"] != "work_limit" or result["history"] is not None:
        raise ValueError("unsupported runner-v1 stopping/history")
    status = result["status"]
    cost = tour_digest = None
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
        cost = require_number(result["best_cost"])
    return work, cost, tour_digest, status


def _failed(record, effective):
    if record["result"] is not None:
        raise ValueError("failed trial cannot carry an accepted result")
    error = record["error"]
    require_fields(error, {"phase", "type", "message", "reported_work"})
    if error["phase"] not in ("capability", "input", "distance_preparation", "solver_warmup", "solve", "validation"):
        raise ValueError("unknown failure phase")
    if (effective is None) != (error["phase"] == "capability"):
        raise ValueError("failure phase disagrees with execution resolution")
    require_text(error["type"])
    if type(error["message"]) is not str:
        raise ValueError("invalid error message")
    if error["reported_work"] is not None:
        require_fields(error["reported_work"], set(WorkCounters().to_dict()))
        WorkCounters(**error["reported_work"])
    require_number(record["timings"]["failed_phase_time_s"])


def trial_row(record):
    """Validate one trial and project only the analysis table's scalar metadata."""
    require_fields(record, TRIAL_FIELDS)
    require_uuid(record["campaign_id"])
    require_uuid(record["attempt_id"])
    if datetime.fromisoformat(record["started_at"]).utcoffset() is None:
        raise ValueError("start timestamp requires a timezone")
    plan = _plan(record["requested_plan"])
    search, requested = plan.search, plan.execution
    effective = _execution(record, search, requested, plan.search_id)
    if record["setup_policy"] != "fresh-per-trial":
        raise ValueError("unsupported setup policy")
    _count(record["max_matrix_bytes"])
    context = _runtime(record["runtime"], record["schema_version"])
    require_fields(record["input"], {"path"}, {"descriptor", "provenance"})
    if record["input"]["path"] is not None:
        require_text(record["input"]["path"])
    times = record["timings"]
    _timings(times, PHASES, {"pre_persistence_time_s"})
    error = record["error"]
    work, cost, tour_digest, status = None, None, None, None
    if record["outcome"] == "completed":
        _completed(record, search, effective)
        work, cost, tour_digest, status = _result(record["result"], search)
    elif record["outcome"] == "failed":
        _failed(record, effective)
    else:
        raise ValueError("unknown outcome")
    config = effective or requested
    workload = {key: value for key, value in search.items() if key != "repetition"}
    source = record["runtime"]["source"]
    build = record["runtime"].get("build")
    method = build["method"] if build else "legacy-source"
    build_status = build["status"] if build else "reported"
    build_identity = (build["identity"] or {}) if build else {}
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
        "source_dirty": record["runtime"]["source"]["dirty"], "source_method": method,
        "build_status": build_status, "build_id": build_identity.get("build_id"),
        "outcome": record["outcome"], "status": status,
        "best_cost": cost, "tour_digest": tour_digest,
        "work_json": None if work is None else canonical_json(work),
        "error_phase": None if error is None else error["phase"],
        "error_type": None if error is None else error["type"],
        **{key: float(times[key]) if key in times else None for key in METRICS},
    }


def validate_summary(record, rows):
    require_fields(record, {"schema_version", "record_type", "campaign_id", "counts", "planned_trials", "trial_timings", "timings"})
    require_uuid(record["campaign_id"])
    if _count(record["planned_trials"]) != len(rows) or not rows:
        raise ValueError("summary planned count does not match trial records")
    expected = {key: sum(row["outcome"] == key for row in rows) for key in ("completed", "failed")}
    require_fields(record["counts"], expected)
    if any(_count(record["counts"][key]) != value for key, value in expected.items()):
        raise ValueError("summary outcome counts disagree")
    receipts = record["trial_timings"]
    if type(receipts) is not list or len(receipts) != len(rows):
        raise ValueError("missing/extra timing receipts")
    persistence = wall = 0.0
    for row, receipt in zip(rows, receipts):
        require_fields(receipt, {"attempt_id", "persistence_time_s", "end_to_end_time_s"})
        if receipt["attempt_id"] != row["attempt_id"]:
            raise ValueError("timing receipt order/identity mismatch")
        duration, total = require_number(receipt["persistence_time_s"]), require_number(receipt["end_to_end_time_s"])
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
