// API v1 wire types; see docs/interface.md for routes and transport rules.
// Mirrors tsp_hpc.interface.contracts; all fields are required, even when nullable.
// Static types do not validate responses or grant authority.
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
  kind: 'result_shard' | 'json';
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
export interface InstanceDescriptor {
  instance_id: string;
  coordinate_checksum: string;
  n_cities: number;
  distance_kind: 'euclidean_2d' | 'tsplib_euc_2d';
  instance_seed: string | null;
}
export interface Coordinates { encoding: 'f64-le-base64'; data: string; }
export interface Tour { encoding: 'u32-le-base64'; data: string; }
export interface ErrorResponse {
  api_version: 1;
  request_id: string;
  code: 'invalid_request' | 'unauthorized' | 'workspace_unavailable' | 'artifact_not_found' |
    'invalid_artifact' | 'dataset_not_ready' | 'stale_revision' | 'cursor_invalid' |
    'snapshot_mismatch' | 'geometry_too_large' | 'internal_error' | 'catalog_busy' |
    'catalog_limit_exceeded' | 'artifact_too_large' | 'request_too_large' |
    'dataset_not_found' | 'ingestion_busy' | 'dataset_limit_exceeded' |
    'execution_disabled' | 'execution_unavailable' | 'execution_busy' | 'resource_limit' |
    'snapshot_not_found' | 'snapshot_stale' | 'unsupported_execution' | 'invalid_plan' |
    'preflight_stale' | 'queue_full' | 'submission_conflict' | 'job_stale' | 'job_not_found' |
    'job_state_invalid' | 'queue_stale' | 'result_unavailable';
  message: string;
  retryable: boolean;
}

// Local-run contracts: handles are references, not paths or authority.
export type DistanceMode = 'dense' | 'on_demand';
export interface RunContext extends CatalogRefreshRequest {
  session_id: string; // Changes on owner-authorized re-pairing; jobs do not.
}
export interface UniformGeneration { generator: 'uniform-v1'; n_cities: number; seed: string; side_length: number; }
export interface ClusteredGeneration {
  generator: 'clustered-v1'; n_cities: number; seed: string; side_length: number;
  n_clusters: number; standard_deviation: number;
}
export interface InstanceLoadRequest extends RunContext { artifact_handle: string; artifact_revision: string; }
export interface InstanceGenerateRequest extends RunContext { generation: UniformGeneration | ClusteredGeneration; }
export interface OutputRoot { root_id: string; display_name: string; }
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
export interface ScientificConfiguration {
  algorithm: 'nn' | 'ga' | 'sa';
  parameters: Record<string, number>;
  budget: Record<string, number | number[]>;
  logical_search_ids: string[] | null;
  implementations: (string | null)[];
  distance_modes: DistanceMode[];
  repetitions: number[];
  solver_seed: string;
  max_matrix_bytes: number;
  record_history: boolean;
  history_policy: Record<string, number> | null;
}
export interface ExecutionPreflightRequest extends RunContext {
  instance_snapshot_id: string;
  capabilities_revision: string;
  configuration: ScientificConfiguration;
}
export interface ExecutionControlRequest extends JobControlRequest { confirm_stop_now: boolean; }
export interface SnapshotRequest extends RunContext { instance_snapshot_id: string; }
export interface ExecutionInfo extends RunContext {
  local_runs_enabled: boolean;
  limits: Record<string, number>;
  output_root: OutputRoot | null;
}
export interface StoredInstance extends RunContext {
  instance_snapshot_id: string;
  content_sha256: string;
  instance: InstanceDescriptor;
  source: { kind: 'artifact' | 'generation'; sha256: string | null };
}
export interface ExecutionCapabilities extends ExecutionInfo {
  capabilities_revision: string;
  implementations: Record<string, unknown>[];
  budget_scope: 'per-logical-invocation';
  worker_processes: 1;
}
export interface ReviewedExecution extends ExecutionPreflightRequest {
  preflight_id: string;
  snapshot_sha256: string;
  budget_scope: 'per-logical-invocation';
  trial_count: number;
  trials: {
    search_id: string; requested_execution_id: string; implementation: string;
    distance_mode: DistanceMode; repetition: number; trial_seed: string;
    invocations: { kind: string; logical_id: string | null; seed: string }[];
  }[];
  estimated_matrix_bytes: number;
  maximum_history_points: number;
  estimated_output_bytes: number;
  summed_search_allowance_s: number | null;
  job_time_ceiling_s: number;
  valid_for_s: number;
  output_root: OutputRoot;
}
export interface JobRecord {
  job_id: string; revision: string; submission_key: string; preflight_id: string; instance_snapshot_id: string;
  campaign_id: string;
  state: 'queued' | 'held' | 'preparing' | 'running' | 'recovering' | 'stop_requested' | 'stopping' |
    'completed' | 'failed' | 'cancelled' | 'interrupted';
  queue_position: number | null; stop_mode: 'after_trial' | 'now' | null;
  planned_trials: number; recorded_trials: number; failed_trials: number;
  attempt_ids: string[];
  result_handle: string | null; result_revision: string | null;
  elapsed_time_s: number | null; failure_code: string | null;
}
export interface JobEnvelope extends RunContext { job: JobRecord; }
export interface JobReview extends JobEnvelope { plan: Omit<ReviewedExecution, keyof RunContext>; }
export interface JobSubmission extends JobEnvelope { deduplicated: boolean; }
export interface JobsQueryRequest extends RunContext { limit: number; cursor: string | null; }
export interface JobsPage extends RunContext {
  queue_revision: string; items: JobRecord[]; next_cursor: string | null;
}
export interface JobPublicationRequest extends JobStatusRequest {
  expected_revision: string; result_revision: string; instance_snapshot_id: string;
  campaign_id: string; attempt_id: string;
}
export interface JobGeometryRequest extends JobPublicationRequest { geometry_revision: string; }
export interface TrialPublicationRequest extends Context {
  campaign_id: string; attempt_id: string; instance_handle: string; artifact_catalog_revision: string;
}
export interface TrialGeometryRequest extends TrialPublicationRequest { geometry_revision: string; }
export interface WorkObservation {
  nn_starts_completed: number | string; ga_generations_completed: number | string;
  sa_proposals_evaluated: number | string; full_tour_evaluations: number | string;
}
export interface FinalObservation {
  status: 'ok' | 'no_solution' | 'failed'; best_cost: number | null; verified_cost: number | null;
  work: WorkObservation | null; termination_reason: string | null; best_logical_index: number | null;
}
export interface LogicalObservation {
  logical_index: number; kind: string; logical_id: string | null; seed: string;
  status: 'ok' | 'no_solution'; best_cost: number | null; verified_cost: number | null;
  work: WorkObservation; termination_reason: string; search_time_s: number; history_points: number | null;
}
export interface VerifiedDetails {
  row: TrialRow; campaign_id: string; attempt_id: string; search_id: string;
  requested_execution_id: string; execution_id: string | null; instance: InstanceDescriptor;
  snapshot_sha256: string; geometry_revision: string; solver_seed: string; trial_seed: string;
  variant: string; parameters: Record<string, unknown>; budget: Record<string, unknown>;
  budget_scope: 'per-logical-invocation' | 'legacy-nn-batch'; record_history: boolean;
  history_policy: Record<string, unknown> | null; logical_search_ids: string[] | null;
  logical_results: LogicalObservation[] | null; result: FinalObservation; timings: Record<string, number>;
  error_phase: string | null; execution_kind: string; native_verified: boolean | null;
}
export interface VerifiedGeometry {
  campaign_id: string; attempt_id: string; instance: InstanceDescriptor;
  snapshot_sha256: string; geometry_revision: string;
  coordinates: { encoding: 'f64-le-base64'; data: string };
  tour: { encoding: 'u32-le-base64'; data: string } | null;
}
export interface JobPublicationContext extends RunContext {
  job_id: string; job_revision: string; result_revision: string; instance_snapshot_id: string;
}
export interface JobDetailsResponse extends JobPublicationContext { details: VerifiedDetails; }
export interface JobGeometryResponse extends JobPublicationContext { geometry: VerifiedGeometry; }
export interface TrialPublicationContext extends Context { instance_handle: string; artifact_catalog_revision: string; }
export interface TrialDetailsResponse extends TrialPublicationContext { details: VerifiedDetails; }
export interface TrialGeometryResponse extends TrialPublicationContext { geometry: VerifiedGeometry; }

export interface AnalysisSelection { attempt_ids: string[] | null; limit: number; cursor: string | null; }
export interface ScalarAnalysisQuery extends AnalysisSelection { table: 'trials' | 'summary' | 'diagnostics'; }
export interface ProgressAnalysisQuery extends AnalysisSelection { table: 'progress'; max_points: number; }
export interface TargetAnalysisQuery extends AnalysisSelection {
  table: 'targets'; instance_id: string; target_cost: number; max_points: number;
}
export interface ComparisonAnalysisQuery extends AnalysisSelection {
  table: 'comparisons'; baseline_implementation: string; baseline_distance_mode: DistanceMode | null;
  metric: 'search_time_s' | 'end_to_end_time_s';
}
export interface ReferenceAnalysisQuery extends AnalysisSelection {
  table: 'references'; reference_handle: string; artifact_catalog_revision: string;
}
export interface FacetAnalysisQuery extends AnalysisSelection { table: 'facets'; field: Field; }
export type AnalysisQuery = ScalarAnalysisQuery | ProgressAnalysisQuery | TargetAnalysisQuery | ComparisonAnalysisQuery | ReferenceAnalysisQuery | FacetAnalysisQuery;
export interface DatasetAnalysisRequest extends Context { query: AnalysisQuery; }
export interface JobAnalysisRequest extends JobStatusRequest { expected_revision: string; result_revision: string; query: AnalysisQuery; }
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export interface AnalysisPage {
  table: 'trials' | 'summary' | 'diagnostics' | 'progress' | 'targets' | 'comparisons' | 'references' | 'facets';
  query_key: string; columns: string[]; items: Record<string, JsonValue>[]; total_rows: number; next_cursor: string | null;
  history_coverage: { selected_attempts: number; recorded_attempts: number; missing_attempts: number; points: number };
  reference: { artifact_handle: string; artifact_catalog_revision: string; sha256: string } | null;
  clock: 'logical_and_concatenated_search' | 'first_observed_concatenated_search_crossing' | null;
  validation: 'shared-records';
}
export interface DatasetAnalysisResponse extends Context, AnalysisPage {}
export interface JobAnalysisResponse extends JobPublicationContext, AnalysisPage {}
