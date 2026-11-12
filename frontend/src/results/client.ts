// Same-origin transport and runtime response validation. Credentials stay in memory.
import { PAGE_MAX, type ArtifactPage, type CatalogRefreshResponse, type Dataset, type DatasetRequest,
  type SessionResponse, type TrialPage, type TrialQuery, type TrialRow, type WorkspaceResponse } from '../contracts.ts';

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
  return isObject(data) && data.token_type === 'Bearer' && isText(data.session_token);
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

  async pair(secret: string, signal: AbortSignal): Promise<void> {
    const data = await this.#request('/session', { secret }, isSession, signal);
    signal.throwIfAborted();
    this.#token = data.session_token;
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
