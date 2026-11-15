"""Bounded, lazy access to genuine observations and observed target crossings.

No interpolation or final-cost-to-history conversion. Concatenated search clocks
exclude setup, validation and persistence gaps; they are not trial wall time.
"""

import math

import pandas as pd

from tsp_hpc.analysis.core import AnalysisLimitError
from tsp_hpc.analysis.validation import require_number, require_text
from tsp_hpc.reproducibility.identity import canonical_json
from tsp_hpc.solvers.results import WorkCounters

PROGRESS_COLUMNS = ["attempt_id", "group_id", "instance_id", "algorithm", "implementation", "usable",
                    "logical_index", "logical_kind", "logical_id", "logical_seed", "sample_index",
                    "elapsed_s", "search_elapsed_s", "best_cost", "trial_best_cost", "work_json", "cumulative_work_json"]
TARGET_COLUMNS = ["attempt_id", "instance_id", "group_id", "algorithm", "implementation", "target_cost",
                  "eligible", "reached", "censored", "observed_time_s", "logical_index", "logical_elapsed_s",
                  "observation_end_s", "reason"]


def _selection(analysis, attempt_ids, max_points):
    if type(max_points) is not int or max_points < 1:
        raise ValueError("max_points must be a positive integer")
    frame = analysis.trials
    if attempt_ids is not None:
        if isinstance(attempt_ids, (str, bytes)):
            raise TypeError("attempt_ids must be a collection")
        ids = list(attempt_ids)
        if any(type(identifier) is not str for identifier in ids) or len(set(ids)) != len(ids):
            raise ValueError("provide distinct attempt IDs")
        if set(ids) - set(frame["attempt_id"]):
            raise ValueError("unknown attempt ID")
        frame = frame.loc[frame["attempt_id"].isin(ids)]
    return frame


def _points(analysis, frame, max_points):
    if sum(int(value) for value in frame["history_points"]) > max_points:
        raise AnalysisLimitError("history points exceed the explicit analysis limit")
    count = 0
    columns = ["attempt_id", "group_id", "instance_id", "algorithm", "implementation", "usable", "history_recorded"]
    for values in frame[columns].itertuples(index=False, name=None):
        row = dict(zip(columns, values))
        if not row["history_recorded"]:
            continue
        record = analysis.trial(row["attempt_id"])  # Checks original source stamp/digest.
        offset, incumbent = 0., None
        work = WorkCounters().to_dict()
        for logical_index, entry in enumerate(record.get("logical_results", [])):
            for sample_index, point in enumerate(entry["result"]["history"]):
                count += 1
                if count > max_points:
                    raise AnalysisLimitError("history points exceed the explicit analysis limit")
                incumbent = point["best_cost"] if incumbent is None else min(incumbent, point["best_cost"])
                yield {**{key: row[key] for key in ("attempt_id", "group_id", "instance_id", "algorithm", "implementation", "usable")},
                       "logical_index": logical_index, "logical_kind": entry["kind"], "logical_id": entry["logical_id"],
                       "logical_seed": str(entry["seed"]), "sample_index": sample_index,
                       "elapsed_s": point["elapsed_s"], "search_elapsed_s": offset + point["elapsed_s"],
                       "best_cost": point["best_cost"], "trial_best_cost": incumbent,
                       "work_json": canonical_json(point["work"]),
                       "cumulative_work_json": canonical_json({key: work[key] + value for key, value in point["work"].items()})}
            offset += entry["timings"]["search_time_s"]
            for key, value in entry["result"]["work"].items():
                work[key] += value


def iter_progress(analysis, *, attempt_ids=None, max_points=100_000):
    """Stream the same genuine points under a hard TOTAL selection bound.

    Callers may page the iterator without materializing a full history table.
    Bounds and source checks are applied when consumed, never silently truncate.
    """
    return _points(analysis, _selection(analysis, attempt_ids, max_points), max_points)


def progress_table(analysis, *, attempt_ids=None, max_points=100_000):
    """Preserve original logical observations, plus explicitly concatenated clocks.

    Unusable trial prefixes remain inspectable with usable=False. max_points is a
    hard total bound, not a request to truncate/downsample or change search data.
    """
    return pd.DataFrame(iter_progress(analysis, attempt_ids=attempt_ids, max_points=max_points), columns=PROGRESS_COLUMNS)


def time_to_target(analysis, *, instance_id, target_cost, attempt_ids=None, max_points=100_000):
    """First OBSERVED crossing of a predefined absolute target for one instance.

    Complete, nonduplicate no_solution runs remain eligible censored observations.
    Failed/incomplete/duplicate attempts and missing histories remain explicit rows,
    not successful-only conditioning. Censoring is descriptive, not survival inference.
    """
    require_text(instance_id)
    target = require_number(target_cost)
    selected = _selection(analysis, attempt_ids, max_points)
    if instance_id not in set(analysis.trials["instance_id"]):
        raise ValueError("unknown target instance")
    if attempt_ids is not None and not selected["instance_id"].eq(instance_id).all():
        raise ValueError("a target cannot be applied to a different instance/objective")
    frame = selected.loc[selected["instance_id"].eq(instance_id)]
    hits = {}
    for point in _points(analysis, frame, max_points):
        if point["trial_best_cost"] <= target:
            hits.setdefault(point["attempt_id"], point)
    rows = []
    for row in frame.to_dict("records"):
        eligible = (row["campaign_complete"] and not row["duplicate_execution"]
                    and row["outcome"] == "completed" and row["history_recorded"])
        reason = None
        if not row["campaign_complete"]:
            reason = "incomplete_campaign"
        elif row["duplicate_execution"]:
            reason = "duplicate_execution"
        elif row["outcome"] != "completed":
            reason = "failed_trial"
        elif not row["history_recorded"]:
            reason = "history_not_recorded"
        hit = hits.get(row["attempt_id"])
        if reason is None and hit is None:
            reason = "target_not_observed"
        end = row["search_time_s"]
        end = None if end is None or not math.isfinite(end) else end
        rows.append({**{key: row[key] for key in ("attempt_id", "instance_id", "group_id", "algorithm", "implementation")},
                     "target_cost": target, "eligible": bool(eligible), "reached": hit is not None,
                     "censored": bool(eligible and hit is None),
                     "observed_time_s": None if hit is None else hit["search_elapsed_s"],
                     "logical_index": None if hit is None else hit["logical_index"],
                     "logical_elapsed_s": None if hit is None else hit["elapsed_s"],
                     "observation_end_s": end, "reason": reason})
    result = pd.DataFrame(rows, columns=TARGET_COLUMNS)
    # Logical indices remain exact integers even alongside missing crossings.
    result["logical_index"] = pd.Series([row["logical_index"] for row in rows], dtype=object)
    return result
