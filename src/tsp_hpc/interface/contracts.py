"""API v1 wire types, not runtime validators or authorization.

Workspace/session/catalog, dataset lifecycle and trial queries implement a subset;
facet/detail/geometry and local-run DTOs remain design-only.

See docs/interface.md. Scientific validation remains in the shared Python APIs.
All fields are required, including nullable fields. Wire seeds are decimal strings.
"""

from typing import Literal, TypedDict

API_VERSION = 1
PAGE_DEFAULT = 100
PAGE_MAX = 500
VALUES_PER_FILTER_MAX = 500
SHARDS_MAX = 64
RUN_LIST_MAX = 1000  # Wire ceiling, not an execution/resource allowance.
GEOMETRY_BYTES_MAX = 16 * 1024 * 1024
SAFE_INTEGER = 2**53 - 1
NULLABLE_FIELDS = frozenset({"implementation", "best_cost", "search_time_s"})
NUMERIC_FIELDS = frozenset({"n_cities", "best_cost", "search_time_s"})

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
    diagnostics_total: int
    diagnostics_truncated: bool


class DatasetStatusRequest(CatalogRefreshRequest):
    dataset_id: str


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


# Local-run contracts. Requests use handles, never paths or process IDs.
Implementation = Literal["visited-scan-v1", "remaining-array-v1"]
DistanceMode = Literal["dense", "on_demand"]


class RunContext(CatalogRefreshRequest):
    # Owner-authorized re-pairing revokes the old session, not durable jobs.
    session_id: str


class InstanceIdentity(TypedDict):
    instance_id: str
    coordinate_checksum: str
    n_cities: int
    distance_kind: Literal["euclidean_2d"]
    instance_seed: str | None


class UniformGeneration(TypedDict):
    generator: Literal["uniform-v1"]
    n_cities: int
    seed: str
    side_length: float


class ClusteredGeneration(TypedDict):
    generator: Literal["clustered-v1"]
    n_cities: int
    seed: str
    side_length: float
    n_clusters: int
    standard_deviation: float


class InstanceLoadRequest(RunContext):
    artifact_handle: str
    artifact_revision: str


class InstanceGenerateRequest(RunContext):
    generation: UniformGeneration | ClusteredGeneration


class InstanceSnapshot(RunContext):
    # Binds immutable artifact content and provenance, not just coordinates.
    instance_snapshot_id: str
    instance: InstanceIdentity


class RunLimits(TypedDict):
    max_cities: int
    max_start_cities: int
    max_repetitions: int
    max_trials: int
    max_queued_campaigns: int
    max_matrix_bytes: int


class OutputRoot(TypedDict):
    root_id: str
    display_name: str


class RunCapabilities(RunContext):
    capabilities_revision: str
    local_runs_enabled: bool
    implementations: list[Implementation]
    distance_modes: list[DistanceMode]
    limits: RunLimits
    output_root: OutputRoot | None


class CampaignConfiguration(TypedDict):
    implementations: list[Implementation]
    distance_modes: list[DistanceMode]
    start_cities: list[int]
    repetitions: list[int]
    solver_seed: str
    max_matrix_bytes: int


class PreflightRequest(RunContext):
    instance_snapshot_id: str
    capabilities_revision: str
    configuration: CampaignConfiguration


class CampaignPreflight(PreflightRequest):
    # Binds the reviewed snapshot, configuration, capabilities and output root.
    preflight_id: str
    output_root: OutputRoot
    trial_count: int
    estimated_matrix_bytes: int  # Not a total-memory bound.


class JobSubmitRequest(RunContext):
    # Service must persist key+plan: same key/plan returns the same job; changed
    # plan conflicts. request_id only correlates requests. Types do not deduplicate.
    preflight_id: str
    submission_key: str


class JobStatusRequest(RunContext):
    job_id: str


class JobControlRequest(JobStatusRequest):
    expected_revision: str
    action: Literal["cancel_queued", "stop_after_trial", "stop_now"]


class QueueContinueRequest(RunContext):
    expected_queue_revision: str


class QueueStatus(RunContext):
    queue_revision: str
    dispatch: Literal["enabled", "held"]
    active_job_id: str | None
    pending_job_ids: list[str]


class JobStatus(RunContext):
    job_id: str
    revision: str
    submission_key: str
    preflight_id: str
    instance_snapshot_id: str
    campaign_id: str | None
    state: Literal["queued", "held", "preparing", "running", "stop_requested", "stopping",
                   "completed", "failed", "cancelled", "interrupted"]
    queue_position: int | None
    stop_mode: Literal["after_trial", "now"] | None
    planned_trials: int
    recorded_trials: int
    failed_trials: int
    result_handle: str | None
    elapsed_time_s: float | None


class JobResultRequest(JobStatusRequest):
    expected_revision: str
    attempt_id: str


class JobResultBinding(RunContext):
    # Join into existing DetailsRequest/GeometryRequest, not UI-only results.
    job_id: str
    job_revision: str
    campaign_id: str
    attempt_id: str
    instance_snapshot_id: str
    instance: InstanceIdentity
    result_handle: str
    dataset_id: str
    dataset_revision: str


class RunError(TypedDict):
    api_version: Literal[1]
    request_id: str
    code: Literal["invalid_request", "execution_disabled", "snapshot_stale", "preflight_stale",
                  "queue_full", "submission_conflict", "job_stale", "job_not_found",
                  "session_revoked", "owner_pairing_required", "internal_error"]
    message: str
    recovery: Literal["edit", "refresh", "owner_pairing", "none"]
