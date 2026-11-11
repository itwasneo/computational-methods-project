"""Bounded scalar queries over shared analysis; no scientific recomputation/cache."""

import base64
import hashlib
import hmac
import json
import math
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator

from tsp_hpc.interface.contracts import Field as QueryField, TrialRow, VALUES_PER_FILTER_MAX
from tsp_hpc.interface.errors import InterfaceError

SAFE_INTEGER = 2**53 - 1
NULLABLE = {"implementation", "best_cost", "search_time_s"}
NUMERIC = {"n_cities", "best_cost", "search_time_s"}


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


class ColumnFilterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: QueryField
    values: list[StrictStr | StrictInt | StrictFloat | None] = Field(max_length=VALUES_PER_FILTER_MAX)

    @model_validator(mode="after")
    def typed_values(self):
        for value in self.values:
            if value is None:
                valid = self.field in NULLABLE
            elif self.field == "n_cities":
                valid = type(value) is int and 2 <= value <= SAFE_INTEGER
            elif self.field in NUMERIC:
                valid = ((type(value) is int and abs(value) <= SAFE_INTEGER)
                         or (type(value) is float and math.isfinite(value)))
            else:
                valid = type(value) is str
            if not valid:
                raise ValueError("invalid scalar for filter column")
        return self


class SortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: QueryField
    direction: Literal["ascending", "descending"]


def _normalized(filters, sort):
    # Ignore empty selections; normalize ordering, duplicates and numeric 1/1.0.
    normalized = []
    for column in filters:
        values = set(column["values"])
        if column["field"] in NUMERIC and column["field"] != "n_cities":
            values = {None if value is None else (float(value) if value else 0.0) for value in values}
        if values:
            normalized.append({"field": column["field"], "values": sorted(values, key=lambda value: (value is None, value))})
    return {"filters": sorted(normalized, key=lambda column: column["field"]),
            "sort": sort or {"field": "attempt_id", "direction": "ascending"}}


class TrialQueries:
    def __init__(self, datasets):
        self.datasets = datasets
        self._key = secrets.token_bytes(32)

    def _cursor(self, binding, offset):
        payload = _encoded([binding, offset])
        return base64.urlsafe_b64encode(payload + hmac.digest(self._key, payload, "sha256")).decode("ascii")

    def _offset(self, cursor, binding):
        if cursor is None:
            return 0
        try:
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self._key, payload, "sha256")):
                raise ValueError
            context, offset = json.loads(payload)
            if context != binding or type(offset) is not int or offset < 0:
                raise ValueError
            return offset
        except (ValueError, TypeError, UnicodeError):
            raise InterfaceError("cursor_invalid", "Cursor is invalid for this dataset, query or page size.", 409) from None

    def page(self, request):
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
                for field in NULLABLE:
                    if pd.isna(row[field]):
                        row[field] = None
                if type(row["n_cities"]) is not int or not 2 <= row["n_cities"] <= SAFE_INTEGER:
                    raise InterfaceError("invalid_artifact", "Trial count metadata is outside the supported wire range.", 422)
            result = {**context, "query_key": query_key, "items": items,
                      "next_cursor": self._cursor(binding, end) if end < len(frame) else None}
        return result
