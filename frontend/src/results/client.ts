// Same-origin transport and runtime response validation. Credentials stay in memory.
import { PAGE_MAX, RUN_LIST_MAX, type ArtifactPage, type CatalogRefreshResponse, type Dataset, type DatasetRequest,
  type SessionResponse, type TrialPage, type TrialQuery, type TrialRow, type WorkspaceResponse,
  type RunContext, type InstanceIdentity, type InstanceSnapshot, type RunLimits, type OutputRoot,
  type RunCapabilities, type CampaignConfiguration, type CampaignPreflight, type QueueStatus,
  type JobStatus, type JobResultBinding, type RunError } from '../contracts.ts';

export const PAIRING_PATTERN = '[A-Za-z0-9_\\-]+'; // HTML pattern uses Unicode-sets (v) mode.
type RequestBody<T> = Omit<T, 'api_version' | 'request_id'>;
type JsonObject = Record<string, unknown>;
const isObject = (value: unknown): value is JsonObject => value !== null && typeof value === 'object' && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === 'string';
const nullableText = (value: unknown) => value === null || isText(value);
const nullableNumber = (value: unknown) => value === null || (typeof value === 'number' && Number.isFinite(value));
const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

function isSession(data: unknown): data is SessionResponse {
  return isObject(data) && data.token_type === 'Bearer' && isId(data.session_token) && isId(data.session_id);
}

function isWorkspace(data: unknown): data is WorkspaceResponse {
  return isObject(data) && isObject(data.workspace) && isText(data.workspace.workspace_id)
    && isText(data.workspace.display_name) && data.workspace.access === 'read_only';
}

function isRefresh(data: unknown): data is CatalogRefreshResponse {
  return isObject(data) && isText(data.revision);
}

function isCatalog(data: unknown): data is ArtifactPage {
  return isObject(data) && isText(data.revision) && nullableText(data.next_cursor)
    && Array.isArray(data.items) && data.items.length <= PAGE_MAX
    && data.items.every(item => isObject(item) && isText(item.artifact_handle) && isText(item.display_name)
      && item.revision === data.revision && (item.kind === 'json' || item.kind === 'result_shard')
      && item.validation === 'unvalidated');
}

function isDataset(data: unknown): data is Dataset {
  return isObject(data) && isText(data.dataset_id)
    && (data.state === 'ready' ? isText(data.revision)
      : (data.state === 'loading' || data.state === 'failed' || data.state === 'stale') && data.revision === null)
    && isCount(data.diagnostics_total) && typeof data.diagnostics_truncated === 'boolean'
    && Array.isArray(data.diagnostics) && data.diagnostics.length <= 100
    && data.diagnostics.every(item => isObject(item) && isText(item.code) && isText(item.message)
      && nullableText(item.artifact_handle) && nullableText(item.attempt_id));
}

function isTrial(row: unknown): row is TrialRow {
  return isObject(row) && isText(row.attempt_id) && isText(row.instance_id) && nullableText(row.implementation)
    && isCount(row.n_cities) && row.n_cities >= 2 && isText(row.algorithm)
    && (row.outcome === 'completed' || row.outcome === 'failed')
    && nullableNumber(row.best_cost) && nullableNumber(row.search_time_s)
    && typeof row.campaign_complete === 'boolean' && typeof row.duplicate_execution === 'boolean'
    && typeof row.usable === 'boolean';
}

function isTrialPage(data: unknown): data is TrialPage {
  return isObject(data) && isText(data.query_key) && nullableText(data.next_cursor)
    && Array.isArray(data.items) && data.items.length <= PAGE_MAX && data.items.every(isTrial)
    && new Set(data.items.map(row => row.attempt_id)).size === data.items.length;
}

// Local-run response guards. No corresponding endpoint methods are exposed yet.
// Validation/correlation does not establish reference freshness or ownership.
export function matchesRunContext(message: RunContext, request: RunContext): boolean {
  return message.api_version === request.api_version && message.request_id === request.request_id
    && message.workspace_id === request.workspace_id && message.session_id === request.session_id;
}

const keys = (v: JsonObject, names: string[]) => Object.keys(v).length === names.length && names.every(k => Object.hasOwn(v, k));
const boundedText = (v: unknown, max: number): v is string => isText(v) && v.length > 0
  && v.length <= max * 2 && [...v].length <= max; // Match Python's Unicode character bounds.
const fullMatch = (pattern: RegExp, v: string) => pattern.exec(v)?.[0] === v;
const isId = (v: unknown): v is string => boundedText(v, 128);
const boundedCount = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => isCount(v) && v >= min && v <= max;
const nonnegative = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const nullableId = (v: unknown) => v === null || isId(v);
const isSeed = (v: unknown) => isText(v) && fullMatch(/^(0|[1-9][0-9]{0,19})$/, v)
  && BigInt(v) <= (1n << 64n) - 1n;
const distinctList = (v: unknown, check: (x: unknown) => boolean, min: number, max: number): v is unknown[] =>
  Array.isArray(v) && v.length >= min && v.length <= max && v.every(check) && new Set(v).size === v.length;
const implementation = (v: unknown) => v === 'visited-scan-v1' || v === 'remaining-array-v1';
const distance = (v: unknown) => v === 'dense' || v === 'on_demand';
const contextKeys = ['api_version', 'request_id', 'workspace_id', 'session_id'];
function isEnvelope(v: unknown): v is JsonObject {
  return isObject(v) && v.api_version === 1 && boundedText(v.request_id, 80) && fullMatch(/^[A-Za-z0-9_-]+$/, v.request_id);
}
function isRunEnvelope(v: unknown, fields: string[]): v is JsonObject {
  return isEnvelope(v) && keys(v, [...contextKeys, ...fields]) && isId(v.workspace_id) && isId(v.session_id);
}
function isInstanceIdentity(v: unknown): v is InstanceIdentity {
  return isObject(v) && keys(v, ['instance_id', 'coordinate_checksum', 'n_cities', 'distance_kind', 'instance_seed'])
    && isId(v.instance_id) && isText(v.coordinate_checksum) && /^[0-9a-f]{64}$/.test(v.coordinate_checksum)
    && v.coordinate_checksum.length === 64 && boundedCount(v.n_cities, 2) && v.distance_kind === 'euclidean_2d'
    && (v.instance_seed === null || isSeed(v.instance_seed));
}
function isOutputRoot(v: unknown): v is OutputRoot {
  return isObject(v) && keys(v, ['root_id', 'display_name']) && isId(v.root_id) && boundedText(v.display_name, 200);
}
function isRunLimits(v: unknown): v is RunLimits {
  return isObject(v) && keys(v, ['max_cities', 'max_start_cities', 'max_repetitions', 'max_trials', 'max_queued_campaigns', 'max_matrix_bytes'])
    && boundedCount(v.max_cities, 2) && boundedCount(v.max_start_cities, 0, RUN_LIST_MAX) && boundedCount(v.max_repetitions, 1, RUN_LIST_MAX)
    && boundedCount(v.max_trials, 1) && boundedCount(v.max_queued_campaigns, 1, RUN_LIST_MAX) && boundedCount(v.max_matrix_bytes);
}
function isCampaignConfiguration(v: unknown): v is CampaignConfiguration {
  return isObject(v) && keys(v, ['implementations', 'distance_modes', 'start_cities', 'repetitions', 'solver_seed', 'max_matrix_bytes'])
    && distinctList(v.implementations, implementation, 1, 2) && distinctList(v.distance_modes, distance, 1, 2)
    && distinctList(v.start_cities, x => isCount(x), 0, RUN_LIST_MAX) && distinctList(v.repetitions, x => isCount(x), 1, RUN_LIST_MAX)
    && isSeed(v.solver_seed) && isCount(v.max_matrix_bytes);
}
export function isInstanceSnapshot(v: unknown): v is InstanceSnapshot {
  return isRunEnvelope(v, ['instance_snapshot_id', 'instance']) && isId(v.instance_snapshot_id) && isInstanceIdentity(v.instance);
}
export function isRunCapabilities(v: unknown): v is RunCapabilities {
  return isRunEnvelope(v, ['capabilities_revision', 'local_runs_enabled', 'implementations', 'distance_modes', 'limits', 'output_root'])
    && isId(v.capabilities_revision) && typeof v.local_runs_enabled === 'boolean'
    && distinctList(v.implementations, implementation, 1, 2) && distinctList(v.distance_modes, distance, 1, 2)
    && isRunLimits(v.limits) && (v.local_runs_enabled ? isOutputRoot(v.output_root) : v.output_root === null);
}
export function isCampaignPreflight(v: unknown): v is CampaignPreflight {
  return isRunEnvelope(v, ['instance_snapshot_id', 'capabilities_revision', 'configuration', 'preflight_id', 'output_root', 'trial_count', 'estimated_matrix_bytes'])
    && isId(v.instance_snapshot_id) && isId(v.capabilities_revision) && isId(v.preflight_id) && isOutputRoot(v.output_root)
    && isCampaignConfiguration(v.configuration) && boundedCount(v.trial_count, 1) && isCount(v.estimated_matrix_bytes)
    && v.trial_count === v.configuration.implementations.length * v.configuration.distance_modes.length * v.configuration.repetitions.length;
}
export function isQueueStatus(v: unknown): v is QueueStatus {
  return isRunEnvelope(v, ['queue_revision', 'dispatch', 'active_job_id', 'pending_job_ids']) && isId(v.queue_revision)
    && (v.dispatch === 'enabled' || v.dispatch === 'held') && nullableId(v.active_job_id)
    && distinctList(v.pending_job_ids, isId, 0, RUN_LIST_MAX) && !v.pending_job_ids.includes(v.active_job_id);
}
export function isJobStatus(v: unknown): v is JobStatus {
  if (!isRunEnvelope(v, ['job_id', 'revision', 'submission_key', 'preflight_id', 'instance_snapshot_id', 'campaign_id', 'state',
    'queue_position', 'stop_mode', 'planned_trials', 'recorded_trials', 'failed_trials', 'result_handle', 'elapsed_time_s'])) return false;
  if (![v.job_id, v.revision, v.submission_key, v.preflight_id, v.instance_snapshot_id].every(isId)
    || !nullableId(v.campaign_id) || !nullableId(v.result_handle)
    || !isText(v.state) || !['queued', 'held', 'preparing', 'running', 'stop_requested', 'stopping', 'completed', 'failed', 'cancelled', 'interrupted'].includes(v.state)
    || !(v.queue_position === null || boundedCount(v.queue_position, 1))
    || !(v.stop_mode === null || v.stop_mode === 'after_trial' || v.stop_mode === 'now')
    || !isCount(v.planned_trials) || v.planned_trials < 1 || !isCount(v.recorded_trials) || !isCount(v.failed_trials)
    || v.failed_trials > v.recorded_trials || v.recorded_trials > v.planned_trials
    || !(v.elapsed_time_s === null || nonnegative(v.elapsed_time_s))) return false;
  const pending = v.state === 'queued' || v.state === 'held';
  if (pending !== (v.queue_position !== null)) return false;
  if (pending && (v.campaign_id !== null || v.recorded_trials !== 0 || v.elapsed_time_s !== null)) return false;
  if (v.recorded_trials > 0 && v.campaign_id === null) return false;
  if (v.state === 'stop_requested' && v.stop_mode !== 'after_trial') return false;
  if (v.state === 'stopping' && v.stop_mode !== 'now') return false;
  if (!['stop_requested', 'stopping', 'cancelled', 'interrupted', 'failed'].includes(v.state) && v.stop_mode !== null) return false;
  if (v.result_handle !== null && !['completed', 'failed', 'cancelled', 'interrupted'].includes(v.state)) return false;
  return v.state !== 'completed' || (v.recorded_trials === v.planned_trials && v.campaign_id !== null && v.result_handle !== null);
}
export function isJobResultBinding(v: unknown): v is JobResultBinding {
  return isRunEnvelope(v, ['job_id', 'job_revision', 'campaign_id', 'attempt_id', 'instance_snapshot_id', 'instance', 'result_handle', 'dataset_id', 'dataset_revision'])
    && [v.job_id, v.job_revision, v.campaign_id, v.attempt_id, v.instance_snapshot_id, v.result_handle, v.dataset_id, v.dataset_revision].every(isId)
    && isInstanceIdentity(v.instance);
}
export function isRunError(v: unknown): v is RunError {
  return isEnvelope(v) && keys(v, ['api_version', 'request_id', 'code', 'message', 'recovery'])
    && isText(v.code) && ['invalid_request', 'execution_disabled', 'snapshot_stale', 'preflight_stale', 'queue_full',
      'submission_conflict', 'job_stale', 'job_not_found', 'session_revoked', 'owner_pairing_required', 'internal_error'].includes(v.code)
    && boundedText(v.message, 200) && isText(v.recovery) && ['edit', 'refresh', 'owner_pairing', 'none'].includes(v.recovery)
    && (['session_revoked', 'owner_pairing_required'].includes(v.code) === (v.recovery === 'owner_pairing'));
}

export class ApiClient {
  #token = '';
  #fetch: typeof fetch;

  constructor(fetcher: typeof fetch = globalThis.fetch.bind(globalThis)) {
    this.#fetch = fetcher;
  }

  clear() { this.#token = ''; }

  async #request<T>(path: string, body: object | null, validate: (data: unknown) => data is T, signal: AbortSignal): Promise<T> {
    const request_id = crypto.randomUUID();
    const headers: Record<string, string> = { 'X-Request-ID': request_id };
    if (this.#token) headers.Authorization = `Bearer ${this.#token}`;
    if (body) headers['Content-Type'] = 'application/json';
    let response: Response;
    try {
      response = await this.#fetch(`/api${path}`, {
        method: body ? 'POST' : 'GET', headers,
        body: body ? JSON.stringify({ ...body, api_version: 1, request_id }) : undefined,
        signal, cache: 'no-store', credentials: 'omit', redirect: 'error',
      });
    } catch (error) {
      if (signal.aborted) throw error;
      throw new ApiError('connection_failed', 'The browser could not complete the API request. Check the local servers and browser console.');
    }
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      throw new ApiError('invalid_response', 'The local API returned an invalid response.');
    }
    signal.throwIfAborted();
    if (!isObject(data) || data.api_version !== 1 || data.request_id !== request_id) {
      throw new ApiError('invalid_response', 'The local API response does not match this request.');
    }
    if (!response.ok) {
      if (data.code === 'unauthorized') this.clear();
      throw new ApiError(isText(data.code) ? data.code : 'invalid_response',
        isText(data.message) ? data.message : 'The local API request failed.');
    }
    for (const key of ['workspace_id', 'dataset_id', 'revision']) {
      if (isObject(body) && key in body && data[key] !== body[key]) {
        throw new ApiError('invalid_response', 'The local API response has obsolete context.');
      }
    }
    if (!validate(data)) throw new ApiError('invalid_response', 'The local API response has an unsupported shape.');
    return data;
  }

  async pair(secret: string, signal: AbortSignal): Promise<string> {
    const data = await this.#request('/session', { secret }, isSession, signal);
    signal.throwIfAborted();
    this.#token = data.session_token;
    return data.session_id;
  }

  workspace(signal: AbortSignal) {
    return this.#request('/workspace', null, isWorkspace, signal);
  }

  catalog(workspace_id: string, cursor: string | null, signal: AbortSignal) {
    return this.#request('/artifacts/query', { workspace_id, cursor, limit: PAGE_MAX }, isCatalog, signal);
  }

  refresh(workspace_id: string, signal: AbortSignal) {
    return this.#request('/artifacts/refresh', { workspace_id }, isRefresh, signal);
  }

  ingest(body: RequestBody<DatasetRequest>, signal: AbortSignal) {
    return this.#request('/datasets', body, isDataset, signal);
  }

  status(workspace_id: string, dataset_id: string, signal: AbortSignal) {
    return this.#request('/datasets/status', { workspace_id, dataset_id }, isDataset, signal);
  }

  async trials(body: RequestBody<TrialQuery>, signal: AbortSignal) {
    const page = await this.#request('/trials/query', body, isTrialPage, signal);
    if (page.items.length > body.limit) throw new ApiError('invalid_response', 'The local API exceeded the requested page size.');
    return page;
  }
}
