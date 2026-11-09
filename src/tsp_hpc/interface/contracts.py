"""Read-only API v1 DTO types, not runtime validators.

Workspace/session/catalog endpoints implement a subset; Results DTOs remain design-only.

See docs/contracts.md. Scientific validation remains in the shared Python APIs.
All fields are required, including nullable fields. Wire seeds are decimal strings.
"""

from typing import Literal, TypedDict

API_VERSION = 1
PAGE_DEFAULT = 100
PAGE_MAX = 500
VALUES_PER_FILTER_MAX = 500
SHARDS_MAX = 64
GEOMETRY_BYTES_MAX = 16 * 1024 * 1024

Field = Literal[
    "attempt_id", "instance_id", "implementation", "n_cities", "algorithm",
    "outcome", "best_cost", "search_time_s",
]
Scalar = str | int | float | None


class Workspace(TypedDict):
    workspace_id: str
    display_name: str
    access: Literal["read_only"]


class Artifact(TypedDict):
    artifact_handle: str
    display_name: str
    kind: Literal["result_shard", "json", "instance", "manifest", "reference"]
    revision: str
    validation: Literal["unvalidated"]


class SessionRequest(TypedDict):
    api_version: Literal[1]
    request_id: str
    secret: str


class SessionResponse(TypedDict):
    api_version: Literal[1]
    request_id: str
    token_type: Literal["Bearer"]
    session_token: str


class CatalogRefreshRequest(TypedDict):
    api_version: Literal[1]
    request_id: str
    workspace_id: str


class CatalogRefreshResponse(CatalogRefreshRequest):
    revision: str


class WorkspaceResponse(TypedDict):
    api_version: Literal[1]
    request_id: str
    workspace: Workspace


class ArtifactQuery(TypedDict):
    api_version: Literal[1]
    request_id: str
    workspace_id: str
    limit: int
    cursor: str | None


class ArtifactPage(TypedDict):
    api_version: Literal[1]
    request_id: str
    workspace_id: str
    revision: str
    items: list[Artifact]
    next_cursor: str | None


class DatasetRequest(TypedDict):
    api_version: Literal[1]
    request_id: str
    workspace_id: str
    artifact_catalog_revision: str
    result_handles: list[str]
    expected_manifest_handle: str | None


class Diagnostic(TypedDict):
    code: str
    artifact_handle: str | None
    attempt_id: str | None
    message: str


class Dataset(TypedDict):
    api_version: Literal[1]
    request_id: str
    workspace_id: str
    dataset_id: str
    revision: str | None
    state: Literal["loading", "ready", "failed", "stale"]
    diagnostics: list[Diagnostic]


class Context(TypedDict):
    api_version: Literal[1]
    request_id: str
    workspace_id: str
    dataset_id: str
    revision: str


class ColumnFilter(TypedDict):
    field: Field
    values: list[Scalar]


class Sort(TypedDict):
    field: Field
    direction: Literal["ascending", "descending"]


class TrialQuery(Context):
    filters: list[ColumnFilter]
    sort: Sort | None
    limit: int
    cursor: str | None


class FacetQuery(Context):
    field: Field
    limit: int
    cursor: str | None


class TrialRow(TypedDict):
    attempt_id: str
    instance_id: str
    implementation: str | None
    n_cities: int
    algorithm: str
    outcome: Literal["completed", "failed"]
    best_cost: float | None
    search_time_s: float | None
    campaign_complete: bool
    duplicate_execution: bool
    usable: bool


class TrialPage(Context):
    query_key: str
    items: list[TrialRow]
    next_cursor: str | None


class FacetPage(Context):
    field: Field
    values: list[Scalar]
    next_cursor: str | None


class InstanceDescriptor(TypedDict):
    instance_id: str
    coordinate_checksum: str
    n_cities: int
    distance_kind: Literal["euclidean_2d", "tsplib_euc_2d"]
    instance_seed: str | None


class SourceProvenance(TypedDict):
    revision: str | None
    dirty: bool | None


class TrialDetails(Context):
    row: TrialRow
    campaign_id: str
    requested_execution_id: str
    execution_id: str | None
    search_id: str
    instance: InstanceDescriptor
    solver_seed: str
    trial_seed: str
    source: SourceProvenance
    artifact_state: Literal["available", "missing", "ambiguous", "mismatched", "stale"]
    artifact_handle: str | None
    snapshot_id: str | None


class DetailsRequest(Context):
    attempt_id: str
    artifact_handle: str | None


class GeometryRequest(Context):
    attempt_id: str
    artifact_handle: str
    snapshot_id: str


class Coordinates(TypedDict):
    encoding: Literal["f64-le-base64"]
    data: str


class Tour(TypedDict):
    encoding: Literal["u32-le-base64"]
    data: str


class Geometry(Context):
    attempt_id: str
    artifact_handle: str
    snapshot_id: str
    instance: InstanceDescriptor
    coordinates: Coordinates
    tour: Tour | None


class ErrorResponse(TypedDict):
    api_version: Literal[1]
    request_id: str
    code: Literal[
        "invalid_request", "unauthorized", "workspace_unavailable", "artifact_not_found",
        "invalid_artifact", "dataset_not_ready", "stale_revision", "cursor_invalid",
        "snapshot_mismatch", "geometry_too_large", "internal_error", "catalog_busy",
        "catalog_limit_exceeded", "artifact_too_large", "request_too_large",
    ]
    message: str
    retryable: bool
