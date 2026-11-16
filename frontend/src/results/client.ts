// Same-origin, bounded transport. Credentials and revocation notifications stay in memory.
import { PAGE_MAX, type DatasetRequest, type TrialQuery } from '../contracts.ts';
import { endpoints, type Endpoint, type Input, type Output } from '../endpoints.ts';
import { wireGuard } from '../wire.ts';

export const PAIRING_PATTERN = '[A-Za-z0-9_\\-]+';
type RequestBody<T> = Omit<T, 'api_version' | 'request_id'>;
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

async function boundedBytes(
  response: Response,
  signal: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Missing response body');
  const cancel = () => {
    void reader.cancel().catch(() => {});
  };
  signal.addEventListener('abort', cancel, { once: true });
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 4 * 1024 * 1024) throw new Error('Response too large');
      chunks.push(value);
    }
    signal.throwIfAborted();
  } finally {
    signal.removeEventListener('abort', cancel);
    await reader.cancel();
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

export class ApiError extends Error {
  code: string;
  status: number;
  requestId: string;
  retryable: boolean;
  constructor(code: string, message: string, status = 0, requestId = '', retryable = false) {
    super(message);
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.retryable = retryable;
  }
}

export class ApiClient {
  #token = '';
  #epoch = 0;
  #fetch: typeof fetch;
  #timeout: number;
  #revoked = new Set<(error: ApiError) => void>();

  constructor(fetcher: typeof fetch = globalThis.fetch.bind(globalThis), timeoutMs = 30_000) {
    this.#fetch = fetcher;
    this.#timeout = timeoutMs;
  }
  clear() {
    this.#token = '';
    this.#epoch++;
  }
  onUnauthorized(listener: (error: ApiError) => void) {
    this.#revoked.add(listener);
    return () => {
      this.#revoked.delete(listener);
    };
  }
  operation<P extends Endpoint>(path: P, body: Input<P>, signal: AbortSignal): Promise<Output<P>> {
    return this.#request<Output<P>>(
      path,
      body,
      wireGuard<(typeof endpoints)[P][1]>(endpoints[path][1]),
      signal,
    );
  }

  async #exchange(path: string, body: object | null, caller: AbortSignal) {
    const request_id = crypto.randomUUID(),
      epoch = this.#epoch;
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), this.#timeout);
    const signal = AbortSignal.any([caller, timeout.signal]);
    const headers: Record<string, string> = { 'X-Request-ID': request_id };
    if (this.#token) headers.Authorization = `Bearer ${this.#token}`;
    if (body) headers['Content-Type'] = 'application/json';
    try {
      let response: Response;
      try {
        response = await this.#fetch(`/api${path}`, {
          method: body ? 'POST' : 'GET',
          headers,
          body: body ? JSON.stringify({ ...body, api_version: 1, request_id }) : undefined,
          signal,
          cache: 'no-store',
          credentials: 'omit',
          redirect: 'error',
        });
      } catch (error) {
        if (signal.aborted) throw error;
        throw new ApiError(
          'connection_failed',
          'The browser could not complete the API request. Check the local servers.',
          0,
          request_id,
        );
      }
      let bytes: Uint8Array<ArrayBuffer>;
      try {
        bytes = await boundedBytes(response, signal);
      } catch (error) {
        if (signal.aborted) throw error;
        throw new ApiError(
          'invalid_response',
          'The local API returned an invalid response.',
          response.status,
          request_id,
        );
      }
      signal.throwIfAborted();
      if (epoch !== this.#epoch) throw new DOMException('Obsolete session', 'AbortError');
      return { response, bytes, request_id };
    } catch (error) {
      if (caller.aborted) throw caller.reason;
      if (timeout.signal.aborted)
        throw new ApiError(
          'request_timeout',
          path === '/jobs/submit'
            ? 'The request timed out. Execution may still have been accepted; inspect jobs and retain the original submission key.'
            : 'The request timed out. Inspect the current state before retrying explicitly; accepted server work is not cancelled.',
          0,
          request_id,
          true,
        );
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  #json(bytes: Uint8Array, requestId: string): Record<string, unknown> {
    let data: unknown;
    try {
      data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    } catch {
      throw new ApiError(
        'invalid_response',
        'The local API returned an invalid response.',
        0,
        requestId,
      );
    }
    if (!object(data) || data.api_version !== 1 || data.request_id !== requestId) {
      throw new ApiError(
        'invalid_response',
        'The local API response does not match this request.',
        0,
        requestId,
      );
    }
    return data;
  }
  #error(data: Record<string, unknown>, status: number, requestId: string): never {
    const error = new ApiError(
      typeof data.code === 'string' ? data.code : 'invalid_response',
      typeof data.message === 'string' ? data.message : 'The local API request failed.',
      status,
      requestId,
      data.retryable === true,
    );
    if (error.code === 'unauthorized') {
      this.clear();
      for (const listener of this.#revoked) listener(error);
    }
    throw error;
  }
  async #request<T>(
    path: string,
    body: object | null,
    validate: (data: unknown) => data is T,
    signal: AbortSignal,
  ): Promise<T> {
    const { response, bytes, request_id } = await this.#exchange(path, body, signal);
    signal.throwIfAborted();
    const data = this.#json(bytes, request_id);
    if (!response.ok) this.#error(data, response.status, request_id);
    for (const key of ['workspace_id', 'session_id', 'dataset_id', 'revision']) {
      if (object(body) && key in body && data[key] !== body[key]) {
        throw new ApiError(
          'invalid_response',
          'The local API response has obsolete context.',
          response.status,
          request_id,
        );
      }
    }
    if (!validate(data))
      throw new ApiError(
        'invalid_response',
        'The local API response has an unsupported shape.',
        response.status,
        request_id,
      );
    return data;
  }

  async exportInstance(
    body: { workspace_id: string; session_id: string; instance_snapshot_id: string },
    signal: AbortSignal,
  ): Promise<Blob> {
    const { response, bytes, request_id } = await this.#exchange('/instances/export', body, signal);
    if (!response.ok) this.#error(this.#json(bytes, request_id), response.status, request_id);
    if (
      response.headers.get('x-request-id') !== request_id ||
      !response.headers.get('content-type')?.startsWith('application/octet-stream')
    ) {
      throw new ApiError(
        'invalid_response',
        'Instance export response does not match the request.',
        response.status,
        request_id,
      );
    }
    return new Blob([bytes], { type: 'application/json' }); // Never round-trip canonical uint64/signed-zero bytes through JSON.
  }
  async pair(secret: string, signal: AbortSignal): Promise<string> {
    const data = await this.#request('/session', { secret }, wireGuard('SessionResponse'), signal);
    if (
      !data.session_token ||
      [...data.session_token].length > 128 ||
      !data.session_id ||
      [...data.session_id].length > 128
    )
      throw new ApiError('invalid_response', 'Invalid session identity.');
    signal.throwIfAborted();
    this.#token = data.session_token;
    this.#epoch++;
    return data.session_id;
  }
  workspace(signal: AbortSignal) {
    return this.#request('/workspace', null, wireGuard('WorkspaceResponse'), signal);
  }
  async catalog(workspace_id: string, cursor: string | null, signal: AbortSignal) {
    const page = await this.#request(
      '/artifacts/query',
      { workspace_id, cursor, limit: PAGE_MAX },
      wireGuard('ArtifactPage'),
      signal,
    );
    if (page.items.length > PAGE_MAX || page.items.some((item) => item.revision !== page.revision))
      throw new ApiError('invalid_response', 'Invalid catalog page.');
    return page;
  }
  refresh(workspace_id: string, signal: AbortSignal) {
    return this.#request(
      '/artifacts/refresh',
      { workspace_id },
      wireGuard('CatalogRefreshResponse'),
      signal,
    );
  }
  async ingest(body: RequestBody<DatasetRequest>, signal: AbortSignal) {
    return this.#dataset(await this.#request('/datasets', body, wireGuard('Dataset'), signal));
  }
  async status(workspace_id: string, dataset_id: string, signal: AbortSignal) {
    return this.#dataset(
      await this.#request(
        '/datasets/status',
        { workspace_id, dataset_id },
        wireGuard('Dataset'),
        signal,
      ),
    );
  }
  #dataset(data: import('../contracts').Dataset) {
    if (
      data.diagnostics_total < 0 ||
      data.diagnostics.length > 100 ||
      (data.state === 'ready' ? data.revision === null : data.revision !== null)
    )
      throw new ApiError('invalid_response', 'Invalid dataset state.');
    return data;
  }
  async trials(body: RequestBody<TrialQuery>, signal: AbortSignal) {
    const page = await this.#request('/trials/query', body, wireGuard('TrialPage'), signal);
    if (
      page.items.length > body.limit ||
      new Set(page.items.map((row) => row.attempt_id)).size !== page.items.length ||
      page.items.some((row) => row.n_cities < 2)
    )
      throw new ApiError('invalid_response', 'Invalid trial page.');
    return page;
  }
}
