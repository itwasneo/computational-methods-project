// Read-only API v1 design types. Not runtime validation or frontend API wiring.
// Mirrors tsp_hpc.interface.contracts; all fields are required, even when nullable.
export const API_VERSION = 1;
export const PAGE_DEFAULT = 100;
export const PAGE_MAX = 500;
export const VALUES_PER_FILTER_MAX = 500;
export const SHARDS_MAX = 64;
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
  kind: 'result_shard' | 'instance' | 'manifest' | 'reference';
  revision: string;
}
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
    'snapshot_mismatch' | 'geometry_too_large' | 'internal_error';
  message: string;
  retryable: boolean;
}
