import { ApiClient, ApiError } from './client.ts';
import { PAGE_DEFAULT, type Artifact, type ColumnFilter, type Dataset, type Sort, type TrialPage, type Workspace } from '../contracts.ts';

export interface ResultsState {
  workspace: Workspace | null;
  artifacts: Artifact[];
  catalogRevision: string | null;
  dataset: Dataset | null;
  page: TrialPage | null;
  filters: ColumnFilter[];
  sort: Sort | null;
  pageIndex: number;
  busy: string;
  error: string;
  errorCode: string;
  paired: boolean;
}

export function initialState(): ResultsState {
  return { workspace: null, artifacts: [], catalogRevision: null, dataset: null, page: null,
    filters: [], sort: null, pageIndex: 0, busy: '', error: '', errorCode: '', paired: false };
}

type Operation = { epoch: number; signal: AbortSignal };

function pause(signal: AbortSignal, milliseconds: number): Promise<void> {
  return new Promise(resolve => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, milliseconds);
    signal.addEventListener('abort', done, { once: true });
    if (signal.aborted) done();
  });
}

/** Owns one foreground operation and a revision watcher, never scientific data processing. */
export class ResultsController {
  #state = initialState();
  #api: ApiClient;
  #change: (state: ResultsState) => void;
  #epoch = 0;
  #abort = new AbortController();
  #timer: ReturnType<typeof setTimeout> | undefined;
  #cursors: (string | null)[] = [null];
  #queryKey: string | null = null;

  constructor(change: (state: ResultsState) => void, api = new ApiClient()) {
    this.#change = change;
    this.#api = api;
  }

  get state(): Readonly<ResultsState> { return this.#state; }

  #set(values: Partial<ResultsState>) {
    this.#state = { ...this.#state, ...values };
    this.#change(this.#state);
  }

  #begin(busy: string): Operation {
    clearTimeout(this.#timer);
    this.#abort.abort();
    this.#abort = new AbortController();
    const epoch = ++this.#epoch;
    this.#set({ busy, error: '', errorCode: '' });
    return { epoch, signal: this.#abort.signal };
  }

  #check(operation: Operation) {
    if (operation.epoch !== this.#epoch) throw new DOMException('Obsolete request', 'AbortError');
    operation.signal.throwIfAborted();
  }

  #fail(error: unknown) {
    const code = error instanceof ApiError ? error.code : 'request_failed';
    if (code === 'unauthorized') {
      this.#api.clear();
      this.#set(initialState());
    } else if (['stale_revision', 'dataset_not_found', 'workspace_unavailable'].includes(code)) {
      this.#set({ dataset: null, catalogRevision: null });
    }
    this.#set({ page: null, errorCode: code,
      error: error instanceof ApiError ? error.message : 'The request failed; retry or refresh the catalog.' });
  }

  async #run(label: string, action: (operation: Operation) => Promise<void>) {
    const operation = this.#begin(label);
    try {
      await action(operation);
    } catch (error) {
      if (operation.epoch === this.#epoch) this.#fail(error);
    } finally {
      if (operation.epoch === this.#epoch) {
        this.#set({ busy: '' });
        this.#watch(operation);
      }
    }
  }

  #resetPages() {
    this.#cursors = [null];
    this.#queryKey = null;
    this.#set({ page: null, pageIndex: 0 });
  }

  #workspace(): Workspace {
    if (!this.#state.paired || !this.#state.workspace) throw new ApiError('unauthorized', 'Pair with a workspace first.');
    return this.#state.workspace;
  }

  #readyContext() {
    const dataset = this.#state.dataset;
    if (!dataset || dataset.state !== 'ready' || dataset.revision === null) {
      throw new ApiError('dataset_not_ready', 'Load a ready dataset first.');
    }
    return { workspace_id: this.#workspace().workspace_id, dataset_id: dataset.dataset_id, revision: dataset.revision };
  }

  disconnect() {
    this.#begin('');
    this.#api.clear();
    this.#resetPages();
    this.#set(initialState());
  }

  dispose() { this.disconnect(); }

  selectionChanged() {
    this.#begin('');
    this.#resetPages();
    this.#set({ dataset: null, filters: [], sort: null });
  }

  connect(secret: string) {
    return this.#run('Pairing', async operation => {
      this.#api.clear();
      this.#resetPages();
      this.#set({ ...initialState(), busy: 'Pairing' });
      await this.#api.pair(secret, operation.signal);
      this.#check(operation);
      this.#set({ paired: true });
      const response = await this.#api.workspace(operation.signal);
      this.#check(operation);
      this.#set({ workspace: response.workspace });
      await this.#catalog(operation);
    });
  }

  async #catalog(operation: Operation) {
    const workspace = this.#workspace();
    const artifacts: Artifact[] = [];
    let cursor: string | null = null;
    let revision: string | null = null;
    const seen = new Set<string>();
    do {
      const page = await this.#api.catalog(workspace.workspace_id, cursor, operation.signal);
      this.#check(operation);
      if ((revision !== null && revision !== page.revision) || artifacts.length + page.items.length > 10_000) {
        throw new ApiError('stale_revision', 'The catalog changed while loading; refresh it.');
      }
      for (const item of page.items) {
        if (seen.has(item.artifact_handle)) throw new ApiError('invalid_response', 'The catalog repeated an artifact.');
        seen.add(item.artifact_handle);
        artifacts.push(item);
      }
      revision = page.revision;
      cursor = page.next_cursor;
      if (cursor !== null && page.items.length === 0) throw new ApiError('invalid_response', 'The catalog did not advance.');
    } while (cursor !== null);
    this.#set({ artifacts, catalogRevision: revision });
  }

  refresh() {
    return this.#run('Refreshing catalog', async operation => {
      this.#resetPages();
      this.#set({ dataset: null, artifacts: [], catalogRevision: null });
      if (!this.#state.workspace) {
        const response = await this.#api.workspace(operation.signal);
        this.#check(operation);
        this.#set({ workspace: response.workspace });
      }
      await this.#api.refresh(this.#workspace().workspace_id, operation.signal);
      this.#check(operation);
      await this.#catalog(operation);
    });
  }

  load(handles: string[], manifest: string | null) {
    return this.#run('Ingesting selected shards', async operation => {
      this.#resetPages();
      this.#set({ dataset: null, filters: [], sort: null });
      const workspace_id = this.#workspace().workspace_id;
      const revision = this.#state.catalogRevision;
      if (!revision) throw new ApiError('stale_revision', 'Refresh the catalog before selecting sources.');
      let dataset = await this.#api.ingest({ workspace_id, artifact_catalog_revision: revision,
        result_handles: [...handles], expected_manifest_handle: manifest }, operation.signal);
      while (true) {
        this.#check(operation);
        this.#set({ dataset });
        if (dataset.state !== 'loading') break;
        await pause(operation.signal, 500);
        this.#check(operation);
        try {
          dataset = await this.#api.status(workspace_id, dataset.dataset_id, operation.signal);
        } catch (error) {
          if (!(error instanceof ApiError && error.code === 'catalog_busy')) throw error;
        }
      }
      if (dataset.state === 'ready') await this.#page(0, operation);
      else if (dataset.state === 'stale') throw new ApiError('stale_revision', 'Sources changed; refresh the catalog and reload.');
    });
  }

  query(filters = this.#state.filters, sort = this.#state.sort) {
    return this.#run('Loading trials', async operation => {
      this.#resetPages();
      this.#set({ filters: filters.map(filter => ({ ...filter, values: [...filter.values] })), sort: sort && { ...sort } });
      await this.#page(0, operation);
    });
  }

  async navigate(index: number) {
    if (index < 0 || index >= this.#cursors.length) return;
    await this.#run('Loading trials', async operation => {
      this.#set({ page: null });
      await this.#page(index, operation);
    });
  }

  async #page(index: number, operation: Operation) {
    const page = await this.#api.trials({ ...this.#readyContext(), filters: this.#state.filters,
      sort: this.#state.sort, limit: PAGE_DEFAULT, cursor: this.#cursors[index] }, operation.signal);
    this.#check(operation);
    if (this.#queryKey !== null && page.query_key !== this.#queryKey) {
      throw new ApiError('invalid_response', 'The query changed between pages; reload the first page.');
    }
    this.#queryKey = page.query_key;
    this.#cursors = this.#cursors.slice(0, index + 1);
    if (page.next_cursor) this.#cursors.push(page.next_cursor);
    this.#set({ page, pageIndex: index });
  }

  #watch(operation: Operation) {
    const dataset = this.#state.dataset;
    if (operation.epoch !== this.#epoch || dataset?.state !== 'ready' || this.#state.error) return;
    this.#timer = setTimeout(async () => {
      try {
        const status = await this.#api.status(dataset.workspace_id, dataset.dataset_id, operation.signal);
        this.#check(operation);
        if (status.state !== 'ready' || status.revision !== dataset.revision) {
          throw new ApiError('stale_revision', 'Sources changed; refresh the catalog and reload.');
        }
      } catch (error) {
        if (operation.epoch === this.#epoch && !(error instanceof ApiError && error.code === 'catalog_busy')) this.#fail(error);
      }
      this.#watch(operation);
    }, 5000);
  }
}
