// API v1 wire types. Workspace/session/catalog/datasets/trial queries are implemented.
// Facets/details/geometry/local runs remain planned. Response guards live in results/client.ts.
// Mirrors tsp_hpc.interface.contracts; all fields are required, even when nullable.
export const API_VERSION = 1;
export const PAGE_DEFAULT = 100;
export const PAGE_MAX = 500;
export const VALUES_PER_FILTER_MAX = 500;
export const SHARDS_MAX = 64;
export const RUN_LIST_MAX = 1000; // Wire ceiling, not an execution/resource allowance.
export const GEOMETRY_BYTES_MAX = 16 * 1024 * 1024;

export type Field = 'attempt_id' | 'instance_id' | 'implementation' | 'n_cities' |
  'algorithm' | 'outcome' | 'best_cost' | 'search_time_s';
export type Scalar = string | number | null;
export interface Workspace {
  workspace_id: string;
  display_name: string;
  access: 'read_only';
}
export interface Artifact {
  artifact_handle: string;
  display_name: string;
  kind: 'result_shard' | 'json' | 'instance' | 'manifest' | 'reference';
  revision: string;
  validation: 'unvalidated';
}
export interface SessionRequest { api_version: 1; request_id: string; secret: string; }
export interface SessionResponse {
  api_version: 1; request_id: string; token_type: 'Bearer'; session_token: string;
  session_id: string; // Correlation identity, not authority; changes on re-pairing.
}
export interface CatalogRefreshRequest { api_version: 1; request_id: string; workspace_id: string; }
export interface CatalogRefreshResponse extends CatalogRefreshRequest { revision: string; }
export interface WorkspaceResponse {
  api_version: 1;
  request_id: string;
  workspace: Workspace;
}
export interface ArtifactQuery {
  api_version: 1;
  request_id: string;
  workspace_id: string;
  limit: number;
  cursor: string | null;
}
export interface ArtifactPage {
  api_version: 1;
  request_id: string;
  workspace_id: string;
  revision: string;
  items: Artifact[];
  next_cursor: string | null;
}
export interface DatasetRequest {
  api_version: 1;
  request_id: string;
  workspace_id: string;
  artifact_catalog_revision: string;
  result_handles: string[];
  expected_manifest_handle: string | null;
}
export interface Diagnostic {
  code: string;
  artifact_handle: string | null;
  attempt_id: string | null;
  message: string;
}
export interface Dataset {
  api_version: 1;
  request_id: string;
  workspace_id: string;
  dataset_id: string;
  revision: string | null;
  state: 'loading' | 'ready' | 'failed' | 'stale';
  diagnostics: Diagnostic[];
  diagnostics_total: number;
  diagnostics_truncated: boolean;
}
export interface DatasetStatusRequest {
  api_version: 1;
  request_id: string;
  workspace_id: string;
  dataset_id: string;
}
export interface Context {
  api_version: 1;
  request_id: string;
  workspace_id: string;
  dataset_id: string;
  revision: string;
}
export interface ColumnFilter { field: Field; values: Scalar[]; }
export interface Sort { field: Field; direction: 'ascending' | 'descending'; }
export interface TrialQuery extends Context {
  filters: ColumnFilter[];
  sort: Sort | null;
  limit: number;
  cursor: string | null;
}
export interface FacetQuery extends Context { field: Field; limit: number; cursor: string | null; }
export interface TrialRow {
  attempt_id: string;
  instance_id: string;
  implementation: string | null;
  n_cities: number;
  algorithm: string;
  outcome: 'completed' | 'failed';
  best_cost: number | null;
  search_time_s: number | null;
  campaign_complete: boolean;
  duplicate_execution: boolean;
  usable: boolean;
}
export interface TrialPage extends Context { query_key: string; items: TrialRow[]; next_cursor: string | null; }
export interface FacetPage extends Context { field: Field; values: Scalar[]; next_cursor: string | null; }
export interface InstanceDescriptor {
  instance_id: string;
  coordinate_checksum: string;
  n_cities: number;
  distance_kind: 'euclidean_2d' | 'tsplib_euc_2d';
  instance_seed: string | null;
}
export interface SourceProvenance { revision: string | null; dirty: boolean | null; }
export interface TrialDetails extends Context {
  row: TrialRow;
  campaign_id: string;
  requested_execution_id: string;
  execution_id: string | null;
  search_id: string;
  instance: InstanceDescriptor;
  solver_seed: string;
  trial_seed: string;
  source: SourceProvenance;
  artifact_state: 'available' | 'missing' | 'ambiguous' | 'mismatched' | 'stale';
  artifact_handle: string | null;
  snapshot_id: string | null;
}
export interface DetailsRequest extends Context { attempt_id: string; artifact_handle: string | null; }
export interface GeometryRequest extends Context { attempt_id: string; artifact_handle: string; snapshot_id: string; }
export interface Coordinates { encoding: 'f64-le-base64'; data: string; }
export interface Tour { encoding: 'u32-le-base64'; data: string; }
export interface Geometry extends Context {
  attempt_id: string;
  artifact_handle: string;
  snapshot_id: string;
  instance: InstanceDescriptor;
  coordinates: Coordinates;
  tour: Tour | null;
}
export interface ErrorResponse {
  api_version: 1;
  request_id: string;
  code: 'invalid_request' | 'unauthorized' | 'workspace_unavailable' | 'artifact_not_found' |
    'invalid_artifact' | 'dataset_not_ready' | 'stale_revision' | 'cursor_invalid' |
    'snapshot_mismatch' | 'geometry_too_large' | 'internal_error' | 'catalog_busy' |
    'catalog_limit_exceeded' | 'artifact_too_large' | 'request_too_large';
  message: string;
  retryable: boolean;
}

// Local-run contracts: handles are references, not paths or authority.
export type Implementation = 'visited-scan-v1' | 'remaining-array-v1';
export type DistanceMode = 'dense' | 'on_demand';
export interface RunContext extends CatalogRefreshRequest {
  session_id: string; // Changes on owner-authorized re-pairing; jobs do not.
}
export interface InstanceIdentity extends InstanceDescriptor { distance_kind: 'euclidean_2d'; }
export interface UniformGeneration { generator: 'uniform-v1'; n_cities: number; seed: string; side_length: number; }
export interface ClusteredGeneration {
  generator: 'clustered-v1'; n_cities: number; seed: string; side_length: number;
  n_clusters: number; standard_deviation: number;
}
export interface InstanceLoadRequest extends RunContext { artifact_handle: string; artifact_revision: string; }
export interface InstanceGenerateRequest extends RunContext { generation: UniformGeneration | ClusteredGeneration; }
export interface InstanceSnapshot extends RunContext { instance_snapshot_id: string; instance: InstanceIdentity; }
export interface RunLimits {
  max_cities: number; max_start_cities: number; max_repetitions: number;
  max_trials: number; max_queued_campaigns: number; max_matrix_bytes: number;
}
export interface OutputRoot { root_id: string; display_name: string; }
export interface RunCapabilities extends RunContext {
  capabilities_revision: string; local_runs_enabled: boolean;
  implementations: Implementation[]; distance_modes: DistanceMode[];
  limits: RunLimits; output_root: OutputRoot | null;
}
export interface CampaignConfiguration {
  implementations: Implementation[]; distance_modes: DistanceMode[];
  start_cities: number[]; repetitions: number[]; solver_seed: string; max_matrix_bytes: number;
}
export interface PreflightRequest extends RunContext {
  instance_snapshot_id: string; capabilities_revision: string; configuration: CampaignConfiguration;
}
export interface CampaignPreflight extends PreflightRequest {
  preflight_id: string; output_root: OutputRoot; trial_count: number; estimated_matrix_bytes: number;
}
// Service must persist key+plan: same key/plan returns the same job; changed plan
// conflicts. request_id only correlates requests. Types do not deduplicate.
export interface JobSubmitRequest extends RunContext { preflight_id: string; submission_key: string; }
export interface JobStatusRequest extends RunContext { job_id: string; }
export interface JobControlRequest extends JobStatusRequest {
  expected_revision: string; action: 'cancel_queued' | 'stop_after_trial' | 'stop_now';
}
export interface QueueContinueRequest extends RunContext { expected_queue_revision: string; }
export interface QueueStatus extends RunContext {
  queue_revision: string; dispatch: 'enabled' | 'held'; active_job_id: string | null; pending_job_ids: string[];
}
export type JobState = 'queued' | 'held' | 'preparing' | 'running' | 'stop_requested' | 'stopping' |
  'completed' | 'failed' | 'cancelled' | 'interrupted';
export interface JobStatus extends RunContext {
  job_id: string; revision: string; submission_key: string; preflight_id: string; instance_snapshot_id: string;
  campaign_id: string | null; state: JobState; queue_position: number | null; stop_mode: 'after_trial' | 'now' | null;
  planned_trials: number; recorded_trials: number; failed_trials: number;
  result_handle: string | null; elapsed_time_s: number | null;
}
export interface JobResultRequest extends JobStatusRequest { expected_revision: string; attempt_id: string; }
export interface JobResultBinding extends RunContext {
  job_id: string; job_revision: string; campaign_id: string; attempt_id: string;
  instance_snapshot_id: string; instance: InstanceIdentity; result_handle: string;
  dataset_id: string; dataset_revision: string;
}
export interface RunError {
  api_version: 1; request_id: string;
  code: 'invalid_request' | 'execution_disabled' | 'snapshot_stale' | 'preflight_stale' | 'queue_full' |
    'submission_conflict' | 'job_stale' | 'job_not_found' | 'session_revoked' | 'owner_pairing_required' | 'internal_error';
  message: string; recovery: 'edit' | 'refresh' | 'owner_pairing' | 'none';
}
