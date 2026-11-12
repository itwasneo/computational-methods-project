"""Bounded scalar queries over shared analysis; no scientific recomputation/cache."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import TYPE_CHECKING

from tsp_hpc.interface.contracts import NUMERIC_FIELDS, NULLABLE_FIELDS, SAFE_INTEGER, TrialQuery, TrialRow
from tsp_hpc.interface.cursors import decode_cursor, encode_cursor
from tsp_hpc.interface.errors import InterfaceError
if TYPE_CHECKING:
    from tsp_hpc.interface.ingestion import DatasetStore


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _normalized(filters, sort):
    # Ignore empty selections; normalize ordering, duplicates and numeric 1/1.0.
    normalized = []
    for column in filters:
        values = set(column["values"])
        if column["field"] in NUMERIC_FIELDS and column["field"] != "n_cities":
            values = {None if value is None else (float(value) if value else 0.0) for value in values}
        if values:
            normalized.append({"field": column["field"], "values": sorted(values, key=lambda value: (value is None, value))})
    return {"filters": sorted(normalized, key=lambda column: column["field"]),
            "sort": sort or {"field": "attempt_id", "direction": "ascending"}}


class TrialQueries:
    def __init__(self, datasets: DatasetStore):
        self.datasets = datasets
        self._key = secrets.token_bytes(32)

    def _cursor(self, binding, offset):
        return encode_cursor(self._key, [binding, offset])

    def _offset(self, cursor, binding):
        if cursor is None:
            return 0
        try:
            context, offset = decode_cursor(self._key, cursor)
            if context != binding or type(offset) is not int or offset < 0:
                raise ValueError
            return offset
        except (ValueError, TypeError, UnicodeError):
            raise InterfaceError("cursor_invalid", "Cursor is invalid for this dataset, query or page size.", 409) from None

    def page(self, request: TrialQuery):
        import pandas as pd

        normalized = _normalized(request["filters"], request["sort"])
        query_key = "trials-v1-" + hashlib.sha256(_encoded(normalized)).hexdigest()
        context = {key: request[key] for key in ("api_version", "request_id", "workspace_id", "dataset_id", "revision")}
        cursor_context = {key: value for key, value in context.items() if key != "request_id"}
        binding = hashlib.sha256(_encoded({**cursor_context, "query_key": query_key, "limit": request["limit"]})).hexdigest()
        with self.datasets.analysis_snapshot(request["dataset_id"], request["revision"]) as analysis:
            offset = self._offset(request["cursor"], binding)
            frame = analysis.trials
            for column in normalized["filters"]:
                values = column["values"]
                series = frame[column["field"]]
                selected = series.isin([value for value in values if value is not None])
                if None in values:
                    selected |= series.isna()
                frame = frame.loc[selected]
            # A stable second sort keeps attempt IDs ascending even for descending ties.
            frame = frame.sort_values("attempt_id", kind="stable")
            sort = normalized["sort"]
            frame = frame.sort_values(sort["field"], ascending=sort["direction"] == "ascending",
                                      na_position="last", kind="stable")
            end = offset + request["limit"]
            items = frame.iloc[offset:end][list(TrialRow.__annotations__)].to_dict(orient="records")
            for row in items:
                for field in NULLABLE_FIELDS:
                    if pd.isna(row[field]):
                        row[field] = None
                if type(row["n_cities"]) is not int or not 2 <= row["n_cities"] <= SAFE_INTEGER:
                    raise InterfaceError("invalid_artifact", "Trial count metadata is outside the supported wire range.", 422)
            result = {**context, "query_key": query_key, "items": items,
                      "next_cursor": self._cursor(binding, end) if end < len(frame) else None}
        return result
