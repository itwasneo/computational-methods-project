"""Strict runtime validators for implemented HTTP requests, separate from routing.

Response/design DTOs remain in contracts; none of these models perform scientific
validation or grant filesystem authority.
"""

import math
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from tsp_hpc.interface.contracts import (
    Field as QueryField, NUMERIC_FIELDS, NULLABLE_FIELDS, PAGE_MAX, SAFE_INTEGER,
    SHARDS_MAX, VALUES_PER_FILTER_MAX,
)

OpaqueId = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StrictBody(StrictModel):
    api_version: StrictInt
    request_id: StrictStr = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("api_version")
    @classmethod
    def version_one(cls, value):
        if value != 1:
            raise ValueError("unsupported API version")
        return value


class SessionRequest(StrictBody):
    secret: StrictStr = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class WorkspaceRequest(StrictBody):
    workspace_id: OpaqueId


class ArtifactRequest(WorkspaceRequest):
    limit: StrictInt = Field(ge=1, le=PAGE_MAX)
    cursor: StrictStr | None = Field(max_length=512)


class DatasetRequest(WorkspaceRequest):
    artifact_catalog_revision: OpaqueId
    result_handles: list[OpaqueId] = Field(min_length=1, max_length=SHARDS_MAX)
    expected_manifest_handle: OpaqueId | None

    @field_validator("result_handles")
    @classmethod
    def distinct_handles(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("select distinct result shards")
        return value


class DatasetStatusRequest(WorkspaceRequest):
    dataset_id: OpaqueId


class ColumnFilterRequest(StrictModel):
    field: QueryField
    values: list[StrictStr | StrictInt | StrictFloat | None] = Field(max_length=VALUES_PER_FILTER_MAX)

    @model_validator(mode="after")
    def typed_values(self):
        for value in self.values:
            if value is None:
                valid = self.field in NULLABLE_FIELDS
            elif self.field == "n_cities":
                valid = type(value) is int and 2 <= value <= SAFE_INTEGER
            elif self.field in NUMERIC_FIELDS:
                valid = ((type(value) is int and abs(value) <= SAFE_INTEGER)
                         or (type(value) is float and math.isfinite(value)))
            else:
                valid = type(value) is str
            if not valid:
                raise ValueError("invalid scalar for filter column")
        return self


class SortRequest(StrictModel):
    field: QueryField
    direction: Literal["ascending", "descending"]


class TrialQueryRequest(DatasetStatusRequest):
    revision: OpaqueId
    filters: list[ColumnFilterRequest] = Field(max_length=len(get_args(QueryField)))
    sort: SortRequest | None
    limit: StrictInt = Field(ge=1, le=PAGE_MAX)
    cursor: StrictStr | None = Field(max_length=512)

    @field_validator("filters")
    @classmethod
    def distinct_columns(cls, value):
        if len({column.field for column in value}) != len(value):
            raise ValueError("select each filter column once")
        return value
