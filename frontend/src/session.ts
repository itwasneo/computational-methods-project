import { ApiClient, ApiError } from './results/client.ts';
import type { Artifact, Workspace } from './contracts.ts';

export type Outcome<T = void> =
  { ok: true; value: T } | { ok: false; error: Error; cancelled?: boolean };
export interface SessionState {
  status: 'disconnected' | 'connecting' | 'ready' | 'expired';
  sessionId: string;
  workspace: Workspace | null;
  artifacts: Artifact[];
  catalogRevision: string | null;
  catalogBusy: boolean;
  error: string;
  errorCode: string;
}
const initial = (): SessionState => ({
  status: 'disconnected',
  sessionId: '',
  workspace: null,
  artifacts: [],
  catalogRevision: null,
  catalogBusy: false,
  error: '',
  errorCode: '',
});

/** The application, not a mounted feature, owns authority and catalog revisions. */
export class WorkspaceSession {
  state = initial();
  readonly api: ApiClient;
  #listeners = new Set<(state: SessionState) => void>();
  #abort = new AbortController();
  #unsubscribe: () => void;
  #refresh: Promise<Outcome> | null = null;
  constructor(api = new ApiClient()) {
    this.api = api;
    this.#unsubscribe = api.onUnauthorized((error) => this.#reset(error));
  }
  subscribe(listener: (state: SessionState) => void) {
    this.#listeners.add(listener);
    listener(this.state);
    return () => {
      this.#listeners.delete(listener);
    };
  }
  #set(patch: Partial<SessionState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.#listeners) listener(this.state);
  }
  #reset(error?: Error) {
    this.#abort.abort();
    this.#abort = new AbortController();
    this.api.clear();
    this.#refresh = null;
    this.#set({
      ...initial(),
      status:
        error instanceof ApiError && error.code === 'unauthorized' ? 'expired' : 'disconnected',
      error: error?.message ?? '',
      errorCode: error instanceof ApiError ? error.code : error ? 'connection_failed' : '',
    });
  }
  disconnect() {
    this.#reset();
  }
  dispose() {
    this.disconnect();
    this.#unsubscribe();
    this.#listeners.clear();
  }
  async connect(secret: string): Promise<Outcome> {
    this.#reset();
    const signal = this.#abort.signal;
    this.#set({ status: 'connecting' });
    try {
      const sessionId = await this.api.pair(secret, signal);
      const { workspace } = await this.api.workspace(signal);
      const catalog = await this.#catalog(workspace.workspace_id, signal);
      signal.throwIfAborted();
      this.#set({ status: 'ready', sessionId, workspace, ...catalog, error: '' });
      return { ok: true, value: undefined };
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error('Connection failed.');
      const cancelled = signal.aborted;
      if (!cancelled) this.#reset(error);
      return { ok: false, error, cancelled };
    }
  }
  async #catalog(workspace: string, signal: AbortSignal) {
    const artifacts: Artifact[] = [],
      seen = new Set<string>();
    let cursor: string | null = null,
      catalogRevision: string | null = null;
    do {
      const page = await this.api.catalog(workspace, cursor, signal);
      signal.throwIfAborted();
      if (
        (catalogRevision !== null && catalogRevision !== page.revision) ||
        artifacts.length + page.items.length > 10_000 ||
        (page.next_cursor && page.items.length === 0)
      )
        throw new ApiError('stale_revision', 'Catalog changed or exceeded its bound; refresh it.');
      for (const artifact of page.items) {
        if (seen.has(artifact.artifact_handle))
          throw new ApiError('invalid_response', 'Catalog repeated an artifact.');
        seen.add(artifact.artifact_handle);
        artifacts.push(artifact);
      }
      catalogRevision = page.revision;
      cursor = page.next_cursor;
    } while (cursor !== null);
    return { artifacts, catalogRevision };
  }
  refresh(): Promise<Outcome> {
    if (this.#refresh) return this.#refresh;
    const signal = this.#abort.signal,
      workspace = this.state.workspace;
    if (!workspace) return Promise.resolve({ ok: false, error: new Error('Connect first.') });
    // Once refresh is requested, old handles cannot safely be reused, even if its acknowledgement is lost.
    this.#set({ catalogBusy: true, artifacts: [], catalogRevision: null, error: '' });
    const pending = (async (): Promise<Outcome> => {
      try {
        await this.api.refresh(workspace.workspace_id, signal);
        const catalog = await this.#catalog(workspace.workspace_id, signal);
        signal.throwIfAborted();
        this.#set(catalog);
        return { ok: true, value: undefined };
      } catch (cause) {
        const error = cause instanceof Error ? cause : new Error('Catalog refresh failed.');
        if (!signal.aborted)
          this.#set({
            error: error.message,
            errorCode: error instanceof ApiError ? error.code : 'catalog_failed',
          });
        return { ok: false, error, cancelled: signal.aborted };
      } finally {
        if (!signal.aborted) {
          this.#set({ catalogBusy: false });
          this.#refresh = null;
        }
      }
    })();
    this.#refresh = pending;
    return pending;
  }
}
