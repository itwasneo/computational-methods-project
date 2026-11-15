"""Bounded table adapters over the shared analysis, not another scientific engine."""

from copy import deepcopy
import hashlib
from itertools import islice
import json
import secrets

from tsp_hpc.interface.cursors import decode_cursor, encode_cursor
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.storage import encode
from tsp_hpc.interface.wire import wire_value
from tsp_hpc.interface.workspace import MAX_MANIFEST_BYTES

JSON_COLUMNS = {"parameters_json": "parameters", "budget_summary_json": "budget", "work_json": "work",
                "history_policy_json": "history_policy", "cumulative_work_json": "cumulative_work"}
PRIVATE_COLUMNS = {"shard", "error_type", "reference_source"}
SEED_COLUMNS = {"solver_seed", "trial_seed", "logical_seed"}
DIAGNOSTIC_COLUMNS = ["code", "source_handle", "line", "observed_trials", "attempt_ids", "requested_execution_ids", "execution_id"]


def _columns(columns):
    return [JSON_COLUMNS.get(key, key) for key in columns if key not in PRIVATE_COLUMNS]


def _project(row):
    result = {}
    for key, value in row.items():
        if key in PRIVATE_COLUMNS:
            continue
        if key in SEED_COLUMNS:
            value = None if value is None else str(value)
        if key in JSON_COLUMNS:
            value = json.loads(value) if type(value) is str else value
            key = JSON_COLUMNS[key]
        result[key] = wire_value(value)
    return result


class AnalysisQueries:
    def __init__(self, catalog, datasets, results, limits):
        self.catalog, self.datasets, self.results, self.limits = catalog, datasets, results, limits
        self._key = secrets.token_bytes(32)

    def _reference(self, query):
        from tsp_hpc.analysis import references_from_json
        if query["table"] != "references":
            return None, None
        source = self.catalog.select_instance_source(query["reference_handle"], query["artifact_catalog_revision"],
                                                     max_bytes=MAX_MANIFEST_BYTES)
        with source.open() as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise InterfaceError("resource_limit", "Reference document exceeds its byte bound.", 413)
        document = references_from_json(raw)
        return document, {"artifact_handle": source.identity, "artifact_catalog_revision": query["artifact_catalog_revision"],
                          "sha256": hashlib.sha256(raw).hexdigest()}

    def _offset(self, cursor, binding):
        if cursor is None:
            return 0
        try:
            context, offset = decode_cursor(self._key, cursor)
            if context != binding or type(offset) is not int or offset < 0:
                raise ValueError
            return offset
        except (ValueError, TypeError, UnicodeError):
            raise InterfaceError("cursor_invalid", "Analysis cursor does not match this revision, selection or page size.", 409) from None

    def _page(self, analysis, context, query):
        import pandas as pd
        from tsp_hpc.analysis import Analysis, iter_progress, paired_comparisons, reference_gaps, summary_table, time_to_target
        from tsp_hpc.analysis.core import AnalysisLimitError
        from tsp_hpc.analysis.progress import PROGRESS_COLUMNS
        try:
            query = deepcopy(query)
            cursor = query.pop("cursor")
            if query["attempt_ids"] is not None:
                query["attempt_ids"] = sorted(query["attempt_ids"])
            frame = analysis.trials
            if query["attempt_ids"] is not None:
                if set(query["attempt_ids"]) - set(frame["attempt_id"]):
                    raise InterfaceError("invalid_request", "Selection contains an unknown attempt.")
                frame = frame.loc[frame["attempt_id"].isin(query["attempt_ids"])]
            # Preserve flags from the FULL indexed dataset: selecting one copy of
            # a duplicate must never turn it into a usable/latest-success winner.
            selected = Analysis(frame, analysis.diagnostics, analysis.sources, analysis._locations)
            estimated = int(frame.memory_usage(index=True, deep=True).sum()) * 2 + len(frame) * 2048
            if len(frame) > self.limits.max_analysis_rows or estimated > self.limits.max_analysis_bytes:
                raise InterfaceError("resource_limit", "Analysis selection exceeds row/memory bounds; select fewer attempts.", 413)
            scope = frame
            if query["table"] == "targets":
                if query["instance_id"] not in set(analysis.trials["instance_id"]):
                    raise InterfaceError("invalid_request", "Target instance is not in this indexed dataset.")
                if query["attempt_ids"] is not None and not frame["instance_id"].eq(query["instance_id"]).all():
                    raise InterfaceError("invalid_request", "Explicit target attempts belong to a different instance.")
                scope = frame.loc[frame["instance_id"].eq(query["instance_id"])]
            coverage = {"selected_attempts": len(scope), "recorded_attempts": int(scope["history_recorded"].sum()),
                        "missing_attempts": int((~scope["history_recorded"].astype(bool)).sum()),
                        "points": sum(int(value) for value in scope["history_points"])}
            if "max_points" in query:
                if query["max_points"] > self.limits.max_history_points or coverage["points"] > query["max_points"]:
                    raise InterfaceError("resource_limit", "Full selected history exceeds its hard point bound; narrow the selection.", 413)
            reference, reference_info = self._reference(query)
            query_key = hashlib.sha256(encode({"query": query, "reference": reference_info})).hexdigest()
            binding = hashlib.sha256(encode({"context": context, "query_key": query_key})).hexdigest()
            offset = self._offset(cursor, binding)
            end = offset + query["limit"]
            kind, clock = query["table"], None
            if kind == "progress":
                clock = "logical_and_concatenated_search"
                total, columns = coverage["points"], _columns(PROGRESS_COLUMNS)
                # The shared iterator checks TOTAL bounds, not a per-page truncation.
                # Retain only the requested page, never a full progress DataFrame.
                points = iter_progress(selected, max_points=query["max_points"])
                items = [_project(row) for row in islice(points, offset, end)]
            elif kind == "diagnostics":
                total, columns = len(analysis.diagnostics), DIAGNOSTIC_COLUMNS
                items = [wire_value({"code": row["code"], "source_handle": row.get("path"),
                                     **{key: row.get(key) for key in DIAGNOSTIC_COLUMNS[2:]}})
                         for row in analysis.diagnostics[offset:end]]
            else:
                if kind == "trials":
                    table = frame
                elif kind == "summary":
                    table = summary_table(selected)
                elif kind == "comparisons":
                    table = paired_comparisons(selected, baseline_implementation=query["baseline_implementation"],
                                               baseline_distance_mode=query["baseline_distance_mode"], metric=query["metric"])
                elif kind == "targets":
                    clock = "first_observed_concatenated_search_crossing"
                    # Keep the shared explicit cross-instance selection rejection.
                    table = time_to_target(analysis, instance_id=query["instance_id"], target_cost=query["target_cost"],
                                           attempt_ids=query["attempt_ids"], max_points=query["max_points"])
                elif kind == "references":
                    table = reference_gaps(selected, reference)
                elif kind == "facets":
                    values = frame[query["field"]].value_counts(dropna=False, sort=False)
                    table = pd.DataFrame({"value": values.index, "count": values.to_numpy()}).sort_values("value", kind="stable", na_position="last")
                else:
                    raise InterfaceError("invalid_request", "Unsupported analysis table.")
                if int(table.memory_usage(index=True, deep=True).sum()) > self.limits.max_analysis_bytes:
                    raise InterfaceError("resource_limit", "Materialized table exceeds its memory estimate bound.", 413)
                total, columns = len(table), _columns(table.columns)
                items = [_project(row) for row in table.iloc[offset:end].to_dict("records")]
            if offset > total:
                raise InterfaceError("cursor_invalid", "Cursor offset is outside the current result.", 409)
            value = {"table": kind, "query_key": query_key, "columns": columns, "items": items, "total_rows": total,
                     "next_cursor": encode_cursor(self._key, [binding, end]) if end < total else None,
                     "history_coverage": coverage, "reference": reference_info, "clock": clock,
                     "validation": "shared-records"}
            if len(encode(value)) + 1024 > self.limits.max_response_bytes:
                raise InterfaceError("resource_limit", "Analysis response exceeds its byte bound; request a smaller page.", 413)
            if reference_info is not None:
                self.catalog.assert_revision(reference_info["artifact_catalog_revision"])
            return value
        except AnalysisLimitError:
            raise InterfaceError("resource_limit", "Analysis exceeds shared recording/materialization bounds.", 413) from None
        except (OSError, ValueError, TypeError, KeyError, RecursionError, ArithmeticError):
            raise InterfaceError("invalid_artifact", "Analysis inputs failed shared scientific validation or source checks.", 422) from None

    def dataset(self, request):
        context = {key: request[key] for key in ("api_version", "workspace_id", "dataset_id", "revision")}
        with self.datasets.analysis_snapshot(request["dataset_id"], request["revision"]) as analysis:
            return self._page(analysis, context, request["query"])

    def job(self, request):
        context = {key: request[key] for key in ("api_version", "workspace_id", "session_id", "job_id", "expected_revision", "result_revision")}
        with self.results.job_analysis(request["job_id"], request["expected_revision"], request["result_revision"]) as (analysis, job):
            return {"instance_snapshot_id": job["instance_snapshot_id"], **self._page(analysis, context, request["query"])}
