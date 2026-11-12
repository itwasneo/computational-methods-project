"""Compact result ingestion and conservative fixed-work tables/comparisons.

Record validation lives in validation.py. Tours/plans are discarded after
validation; source locators support verified lazy detail access.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import re

import pandas as pd

from tsp_hpc.analysis.sources import PathSource, ShardSource
from tsp_hpc.analysis.validation import (
    METRICS, PHASES, TRIAL_FIELDS, decode_record, require_fields, require_number,
    require_text, require_uuid, trial_row, validate_summary,
)
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.reproducibility.identity import canonical_json

MAX_RECORD_BYTES = 64 * 1024 * 1024
TRIAL_COLUMNS = ("attempt_id campaign_id requested_execution_id execution_id search_id workload_id group_id "
                 "runtime_context_id instance_id coordinate_checksum distance_kind n_cities algorithm variant "
                 "repetition parameters_json solver_seed budget_name budget_count trial_seed implementation "
                 "distance_mode backend profile workers threads_per_worker max_matrix_bytes setup_policy "
                 "source_revision source_dirty outcome status best_cost tour_digest work_json error_phase error_type").split() + list(METRICS) + ["shard", "campaign_complete", "duplicate_execution", "usable"]


class AnalysisLimitError(ValueError):
    """An explicit ingestion bound was exceeded; never publish a partial index."""


class ShardValidationError(ValueError):
    """Validation failure with safe source/line metadata for interface adapters."""

    def __init__(self, source_id, line, error):
        super().__init__(f"{source_id}:{line}: {error}")
        self.source_id, self.line = source_id, line


@dataclass
class Analysis:
    """Compact mutable table for filtering; source locators retain no tours/plans."""

    trials: pd.DataFrame
    diagnostics: list[dict]
    sources: list[dict]
    _locations: dict[str, tuple[ShardSource, int, int, str, tuple[int, ...]]] = field(repr=False)

    def trial(self, attempt_id: str) -> dict:
        """Load details on demand; reject a changed source rather than stale offsets."""
        source, offset, length, digest, stamp = self._locations[attempt_id]
        if source.stamp() != stamp:
            raise ValueError("source shard changed; reload analysis")
        with source.open() as stream:
            stream.seek(offset)
            raw = stream.read(length)
        if hashlib.sha256(raw).hexdigest() != digest or source.stamp() != stamp:
            raise ValueError("source record changed; reload analysis")
        return decode_record(raw)


def read_shards(paths, *, expected_manifest: Manifest | None = None,
                max_record_bytes: int = MAX_RECORD_BYTES) -> Analysis:
    """Validate shards without retaining tours; report incomplete final prefixes.

    Only an unterminated last line is treated as a torn write. Malformed complete
    records, schema/identity mismatches and contradictory summaries are errors.
    Without an expected manifest, entirely absent executions cannot be inferred.
    """
    if isinstance(paths, (str, Path)):
        raise TypeError("paths must be a collection of shard paths")
    return read_sources([PathSource(Path(path).resolve()) for path in paths],
                        expected_manifest=expected_manifest, max_record_bytes=max_record_bytes)


def read_sources(inputs: Iterable[ShardSource], *, expected_manifest: Manifest | None = None,
                 max_record_bytes: int = MAX_RECORD_BYTES, max_trials: int | None = None,
                 max_summary_bytes: int | None = None, checkpoint: Callable[[], None] | None = None) -> Analysis:
    """Use the same validator over caller-authorized binary sources.

    Optional bounds/cancellation serve interactive ingestion without changing CLI
    defaults. Summary bytes cap serialized scalar rows, not total process RSS.
    """
    if type(max_record_bytes) is not int or max_record_bytes < 1:
        raise ValueError("max_record_bytes must be a positive integer")
    for bound in (max_trials, max_summary_bytes):
        if bound is not None and (type(bound) is not int or bound < 1):
            raise ValueError("ingestion bounds must be positive integers")
    if expected_manifest is not None and not isinstance(expected_manifest, Manifest):
        raise TypeError("expected_manifest must be a Manifest")
    inputs = list(inputs)
    if not inputs or len({source.identity for source in inputs}) != len(inputs):
        raise ValueError("provide distinct input shards")
    check = checkpoint or (lambda: None)
    rows, diagnostics, locations, campaigns, sources = [], [], {}, set(), []
    trial_count = summary_bytes = 0
    for source in inputs:
        check()
        path = source.identity
        stamp = source.stamp()
        digest = hashlib.sha256()
        local, campaign, footer = [], None, False
        with source.open() as stream:
            line = 0
            while True:
                check()
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
                        raise AnalysisLimitError("record exceeds configured byte limit")
                    if footer:
                        raise ValueError("content after campaign summary")
                    if not raw.endswith(b"\n"):
                        diagnostics.append({"code": "unterminated_tail", "path": str(path), "line": line})
                        break
                    record = decode_record(raw)
                    if type(record) is not dict or type(record.get("schema_version")) is not int or record["schema_version"] != 1:
                        raise ValueError("unsupported runner envelope schema")
                    current = require_uuid(record["campaign_id"])
                    if campaign is None:
                        campaign = current
                        if campaign in campaigns:
                            raise ValueError("duplicate campaign across shards")
                        campaigns.add(campaign)
                    if campaign != current:
                        raise ValueError("multiple campaigns in one shard")
                    if record["record_type"] == "trial":
                        trial_count += 1
                        if max_trials is not None and trial_count > max_trials:
                            raise AnalysisLimitError("trial count exceeds configured limit")
                        row = trial_row(record)
                        if max_summary_bytes is not None:
                            summary_bytes += len(canonical_json(row).encode("ascii"))
                            if summary_bytes > max_summary_bytes:
                                raise AnalysisLimitError("scalar summaries exceed configured byte limit")
                        attempt = row["attempt_id"]
                        if attempt in locations:
                            raise ValueError("duplicate attempt ID")
                        locations[attempt] = (source, offset, len(raw), hashlib.sha256(raw).hexdigest(), stamp)
                        row["shard"] = str(path)
                        local.append(row)
                    elif record["record_type"] == "campaign_summary":
                        validate_summary(record, local)
                        footer = True
                    else:
                        raise ValueError("unknown record type")
                except AnalysisLimitError as error:
                    raise AnalysisLimitError(f"{path}:{line}: {error}") from error
                except (ValueError, TypeError, KeyError, OverflowError) as error:
                    raise ShardValidationError(path, line, error) from error
        if source.stamp() != stamp:
            raise ValueError(f"source changed during analysis: {path}")
        sources.append({"path": str(path), "sha256": digest.hexdigest(), "size_bytes": stamp[2]})
        if not footer:
            diagnostics.append({"code": "incomplete_campaign", "path": str(path), "observed_trials": len(local)})
        for row in local:
            row["campaign_complete"] = footer
        rows.extend(local)
    check()
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
    check()
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
    require_text(baseline_implementation)
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
    require_fields(document, {"schema_version", "references"})
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or type(document["references"]) is not list:
        raise ValueError("unsupported reference document")
    references = {}
    for ref in document["references"]:
        require_fields(ref, {"instance_id", "coordinate_checksum", "distance_kind", "kind", "cost", "source"})
        require_text(ref["instance_id"])
        require_text(ref["source"])
        if ref["kind"] not in ("exact", "best_known") or ref["distance_kind"] not in ("euclidean_2d", "tsplib_euc_2d"):
            raise ValueError("invalid reference kind or objective")
        if type(ref["coordinate_checksum"]) is not str or re.fullmatch(r"[0-9a-f]{64}", ref["coordinate_checksum"]) is None:
            raise ValueError("invalid reference checksum")
        ref = {**ref, "cost": require_number(ref["cost"])}
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
    return decode_record(Path(path).read_bytes())
