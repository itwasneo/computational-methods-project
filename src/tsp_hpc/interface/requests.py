"""Strict request validators, separate from wire types and routing.

Local-run models are not wired to endpoints. Response/design DTOs remain in
contracts; these models neither authorize operations nor validate scientific
identities, reference freshness or advertised runtime limits.
"""

import math
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from tsp_hpc.interface.contracts import (
    Field as QueryField, NUMERIC_FIELDS, NULLABLE_FIELDS, PAGE_MAX, SAFE_INTEGER,
    SHARDS_MAX, VALUES_PER_FILTER_MAX, RUN_LIST_MAX, Implementation, DistanceMode,
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


# Local-run inputs. Only the service may resolve handles or start/control workers.
Count = Annotated[StrictInt, Field(ge=0, le=SAFE_INTEGER)]
Seed = Annotated[StrictStr, Field(pattern=r"\A(0|[1-9][0-9]{0,19})\z")]


def _seed_value(value):
    if int(value) > 2**64 - 1:
        raise ValueError("seed exceeds uint64")
    return value


def _distinct(values):
    if len(set(values)) != len(values):
        raise ValueError("entries must be distinct; order is preserved")
    return values


class RunRequest(WorkspaceRequest):
    session_id: OpaqueId


class GenerationParameters(StrictModel):
    n_cities: Annotated[StrictInt, Field(ge=2, le=SAFE_INTEGER)]
    seed: Seed
    side_length: Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False)]

    _seed = field_validator("seed")(_seed_value)


class UniformGenerationRequest(GenerationParameters):
    generator: Literal["uniform-v1"]


class ClusteredGenerationRequest(GenerationParameters):
    generator: Literal["clustered-v1"]
    n_clusters: Annotated[StrictInt, Field(ge=1, le=SAFE_INTEGER)]
    standard_deviation: Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def cluster_count(self):
        if self.n_clusters > self.n_cities:
            raise ValueError("n_clusters exceeds n_cities")
        return self


class InstanceLoadRequest(RunRequest):
    artifact_handle: OpaqueId
    artifact_revision: OpaqueId


class InstanceGenerateRequest(RunRequest):
    generation: Annotated[UniformGenerationRequest | ClusteredGenerationRequest, Field(discriminator="generator")]


class CampaignConfigurationRequest(StrictModel):
    implementations: Annotated[list[Implementation], Field(min_length=1, max_length=2)]
    distance_modes: Annotated[list[DistanceMode], Field(min_length=1, max_length=2)]
    start_cities: Annotated[list[Count], Field(max_length=RUN_LIST_MAX)]  # Empty work remains legal.
    repetitions: Annotated[list[Count], Field(min_length=1, max_length=RUN_LIST_MAX)]
    solver_seed: Seed
    max_matrix_bytes: Count

    _seed = field_validator("solver_seed")(_seed_value)
    _distinct = field_validator("implementations", "distance_modes", "start_cities", "repetitions")(_distinct)


class PreflightRequest(RunRequest):
    instance_snapshot_id: OpaqueId
    capabilities_revision: OpaqueId
    configuration: CampaignConfigurationRequest


class JobSubmitRequest(RunRequest):
    preflight_id: OpaqueId
    submission_key: OpaqueId


class JobStatusRequest(RunRequest):
    job_id: OpaqueId


class JobControlRequest(JobStatusRequest):
    expected_revision: OpaqueId
    action: Literal["cancel_queued", "stop_after_trial", "stop_now"]


class QueueContinueRequest(RunRequest):
    expected_queue_revision: OpaqueId


class JobResultRequest(JobStatusRequest):
    expected_revision: OpaqueId
    attempt_id: OpaqueId
