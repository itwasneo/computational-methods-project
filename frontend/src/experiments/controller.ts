import { ApiClient, ApiError } from '../results/client.ts';
import { WorkspaceSession, type Outcome, type SessionState } from '../session.ts';
import type { Endpoint, Input, Parameters } from '../endpoints.ts';
import type {
  Artifact,
  ExecutionCapabilities,
  StoredInstance,
  ScientificConfiguration,
  ReviewedExecution,
  JobRecord,
  QueueStatus,
  JobSubmission,
  JobDetailsResponse,
  JobGeometryResponse,
  JobAnalysisResponse,
  AnalysisQuery,
  UniformGeneration,
  ClusteredGeneration,
} from '../contracts.ts';

export const terminal = (job: JobRecord) =>
  ['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state);
export const selection = { attempt_ids: null, limit: 100, cursor: null };
export type Scope =
  | 'session'
  | 'catalog'
  | 'instance'
  | 'review'
  | 'submission'
  | 'jobs'
  | 'control'
  | 'selection'
  | 'analysis'
  | 'inspection'
  | 'export';
export interface OperationState {
  busy: string;
  error: string;
  code: string;
  requestId: string;
}
const idle = (): OperationState => ({ busy: '', error: '', code: '', requestId: '' });
const operations = () =>
  Object.fromEntries(
    [
      'session',
      'catalog',
      'instance',
      'review',
      'submission',
      'jobs',
      'control',
      'selection',
      'analysis',
      'inspection',
      'export',
    ].map((s) => [s, idle()]),
  ) as Record<Scope, OperationState>;
export function initialState() {
  return {
    connected: false,
    workspace: '',
    session: '',
    capabilities: null as ExecutionCapabilities | null,
    artifacts: [] as Artifact[],
    catalogRevision: '',
    catalogBusy: false,
    snapshot: null as StoredInstance | null,
    review: null as ReviewedExecution | null,
    reviewExpiresAt: 0,
    jobs: [] as JobRecord[],
    queue: null as QueueStatus | null,
    selected: null as JobRecord | null,
    resultInstance: null as StoredInstance | null,
    selectedConfiguration: null as ScientificConfiguration | null,
    analysis: null as JobAnalysisResponse | null,
    query: null as AnalysisQuery | null,
    analysisRequested: null as AnalysisQuery | null,
    analysisPage: 0,
    details: null as JobDetailsResponse | null,
    geometry: null as JobGeometryResponse | null,
    inspectionAttempt: null as string | null,
    pending: null as { preflight: string; key: string; root: string } | null,
    invalidRetry: false,
    operations: operations(),
  };
}
export type ExperimentState = ReturnType<typeof initialState>;
export function shouldPoll(state: ExperimentState): boolean {
  const progressing = (job: JobRecord) => !terminal(job) && job.state !== 'held';
  return (
    state.connected &&
    !!state.capabilities?.local_runs_enabled &&
    !state.operations.jobs.busy &&
    !state.operations.jobs.error &&
    !state.operations.control.busy &&
    !state.operations.selection.busy &&
    (state.jobs.some(progressing) || (!!state.selected && progressing(state.selected)))
  );
}
type RetryStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const RETRY_KEY = 'tsp-hpc-pending-submission-v1';
function browserStore(): RetryStore | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

export class ExperimentController {
  state = initialState();
  readonly api: ApiClient;
  readonly session: WorkspaceSession;
  private notify: (state: ExperimentState) => void;
  private active = new Map<Scope, AbortController>();
  private retryStore: RetryStore | null;
  private unsubscribe: () => void;
  private cursors: (string | null)[] = [null];
  constructor(
    notify: (state: ExperimentState) => void,
    api = new ApiClient(),
    retryStore = browserStore(),
    session = new WorkspaceSession(api),
  ) {
    this.notify = notify;
    this.api = api;
    this.retryStore = retryStore;
    this.session = session;
    try {
      const raw = retryStore?.getItem(RETRY_KEY);
      if (raw) {
        if (raw.length > 1024) throw new Error('Invalid retry record');
        const value = JSON.parse(raw);
        if (
          !value ||
          Object.keys(value).sort().join(',') !== 'key,preflight,root' ||
          !Object.values(value).every(
            (v) => typeof v === 'string' && v.length > 0 && v.length <= 128,
          )
        )
          throw new Error('Invalid retry record');
        this.state.pending = value;
      }
    } catch {
      this.state.invalidRetry = true;
    }
    this.unsubscribe = session.subscribe((value) => this.adopt(value));
  }
  private update(patch: Partial<ExperimentState>) {
    if ('pending' in patch && this.retryStore) {
      // Storage failure must never discard an in-memory uncertain submission reference.
      try {
        if (patch.pending) this.retryStore.setItem(RETRY_KEY, JSON.stringify(patch.pending));
        else this.retryStore.removeItem(RETRY_KEY);
      } catch {
        if (!patch.pending)
          throw new Error('Could not clear saved retry metadata. Enable tab storage and retry.');
      }
    }
    this.state = { ...this.state, ...patch };
    this.notify(this.state);
  }
  private adopt(session: SessionState) {
    if (
      session.sessionId !== this.state.session ||
      session.status === 'disconnected' ||
      session.status === 'expired'
    ) {
      for (const scope of this.active.keys()) this.cancel(scope);
      const { pending, invalidRetry } = this.state;
      this.state = { ...initialState(), pending, invalidRetry };
      this.cursors = [null];
    }
    this.update({
      connected: session.status === 'ready',
      workspace: session.workspace?.workspace_id ?? '',
      session: session.sessionId,
      artifacts: session.artifacts,
      catalogRevision: session.catalogRevision ?? '',
      catalogBusy: session.catalogBusy,
    });
  }
  private commit(signal: AbortSignal, patch: Partial<ExperimentState>) {
    signal.throwIfAborted();
    this.update(patch);
  }
  private call<P extends Endpoint>(path: P, body: Parameters<P>, signal: AbortSignal) {
    signal.throwIfAborted();
    return this.api.operation(
      path,
      { workspace_id: this.state.workspace, session_id: this.state.session, ...body } as Input<P>,
      signal,
    );
  }
  private op(scope: Scope, patch: Partial<OperationState>) {
    this.update({
      operations: {
        ...this.state.operations,
        [scope]: { ...this.state.operations[scope], ...patch },
      },
    });
  }
  private cancel(scope: Scope) {
    this.active.get(scope)?.abort();
    this.active.delete(scope);
    this.op(scope, { busy: '' });
  }
  private async action<T>(
    scope: Scope,
    label: string,
    work: (signal: AbortSignal) => Promise<T>,
  ): Promise<Outcome<T>> {
    if (this.active.has(scope))
      return { ok: false, error: new Error(`${label} is already in progress.`) };
    const abort = new AbortController();
    this.active.set(scope, abort);
    this.op(scope, { ...idle(), busy: label });
    try {
      const value = await work(abort.signal);
      abort.signal.throwIfAborted();
      return { ok: true, value };
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error('Request failed.');
      if (!abort.signal.aborted) {
        // A stale shared result binding invalidates every publication from that source.
        // An unrelated input/catalog failure never invalidates it.
        if (
          ['analysis', 'inspection', 'selection', 'control'].includes(scope) &&
          error instanceof ApiError &&
          [
            'job_stale',
            'job_not_found',
            'result_unavailable',
            'snapshot_stale',
            'snapshot_not_found',
            'snapshot_mismatch',
            'stale_revision',
          ].includes(error.code)
        ) {
          if (scope !== 'analysis') this.cancel('analysis');
          if (scope !== 'inspection') this.cancel('inspection');
          this.update({ analysis: null, details: null, geometry: null });
        }
        if (scope === 'analysis') this.update({ analysis: null });
        if (scope === 'inspection') this.update({ details: null, geometry: null });
        this.op(scope, {
          error: error.message,
          code: error instanceof ApiError ? error.code : 'request_failed',
          requestId: error instanceof ApiError ? error.requestId : '',
        });
      }
      return { ok: false, error, cancelled: abort.signal.aborted };
    } finally {
      if (this.active.get(scope) === abort) {
        this.active.delete(scope);
        this.op(scope, { busy: '' });
      }
    }
  }
  disconnect() {
    this.session.disconnect();
  }
  dispose() {
    for (const scope of this.active.keys()) this.cancel(scope);
    this.unsubscribe();
  }
  async connect(secret: string): Promise<Outcome> {
    const result = await this.session.connect(secret);
    if (!result.ok) return result;
    return this.initialize();
  }
  initialize() {
    return this.action('session', 'Loading execution capabilities', async (signal) => {
      const capabilities = await this.call('/runs/capabilities', {}, signal);
      this.commit(signal, { capabilities });
      if (capabilities.local_runs_enabled) await this.refreshJobs();
    });
  }
  refreshCatalog() {
    return this.action('catalog', 'Refreshing catalog', async () => {
      const result = await this.session.refresh();
      if (!result.ok) throw result.error;
    });
  }
  invalidateReview() {
    this.cancel('review');
    this.update({ review: null, reviewExpiresAt: 0 });
  }
  private setupAvailable() {
    if (this.state.pending || this.state.invalidRetry || this.active.has('submission'))
      throw new Error('Resolve the original submission reference before changing setup.');
    if (this.active.has('instance')) throw new Error('Wait for the instance operation to finish.');
  }
  exportInstance(save: (blob: Blob) => void) {
    return this.action('export', 'Exporting instance', async (signal) => {
      if (!this.state.snapshot) throw new Error('Create or load an instance first.');
      const blob = await this.api.exportInstance(
        {
          workspace_id: this.state.workspace,
          session_id: this.state.session,
          instance_snapshot_id: this.state.snapshot.instance_snapshot_id,
        },
        signal,
      );
      signal.throwIfAborted();
      save(blob);
    });
  }
  generate(generation: UniformGeneration | ClusteredGeneration) {
    if (this.active.has('instance'))
      return Promise.resolve<Outcome>({
        ok: false,
        error: new Error('Instance operation in progress.'),
      });
    this.invalidateReview();
    return this.action('instance', 'Generating instance', async (signal) => {
      if (this.state.pending || this.state.invalidRetry || this.active.has('submission'))
        throw new Error('Resolve the original submission before changing inputs.');
      const snapshot = await this.call('/instances/generate', { generation }, signal);
      this.commit(signal, { snapshot });
    });
  }
  load(artifact_handle: string) {
    this.invalidateReview();
    return this.action('instance', 'Loading instance', async (signal) => {
      if (this.state.pending || this.state.invalidRetry || this.active.has('submission'))
        throw new Error('Resolve the original submission before changing inputs.');
      if (!this.state.catalogRevision || this.state.catalogBusy)
        throw new Error('Refresh the catalog before selecting an instance.');
      const snapshot = await this.call(
        '/instances/load',
        { artifact_handle, artifact_revision: this.state.catalogRevision },
        signal,
      );
      this.commit(signal, { snapshot });
    });
  }
  restoreSnapshot(instance_snapshot_id: string) {
    this.invalidateReview();
    return this.action('instance', 'Restoring saved input', async (signal) => {
      if (this.state.pending || this.state.invalidRetry || this.active.has('submission'))
        throw new Error('Resolve the original submission before changing inputs.');
      const snapshot = await this.call('/instances/snapshot', { instance_snapshot_id }, signal);
      if (snapshot.instance_snapshot_id !== instance_snapshot_id)
        throw new Error('Restored snapshot identity mismatch.');
      this.commit(signal, { snapshot });
    });
  }
  review(configuration: ScientificConfiguration) {
    this.invalidateReview();
    return this.action('review', 'Reviewing experiment', async (signal) => {
      this.setupAvailable();
      const snapshot = this.state.snapshot,
        capabilities = this.state.capabilities;
      if (!snapshot || !capabilities) throw new Error('Create or load an instance first.');
      const review = await this.call(
        '/runs/preflight',
        {
          configuration,
          instance_snapshot_id: snapshot.instance_snapshot_id,
          capabilities_revision: capabilities.capabilities_revision,
        },
        signal,
      );
      if (
        review.instance_snapshot_id !== snapshot.instance_snapshot_id ||
        review.capabilities_revision !== capabilities.capabilities_revision
      )
        throw new Error('Review context mismatch.');
      this.commit(signal, { review, reviewExpiresAt: Date.now() + review.valid_for_s * 1000 });
      return review;
    });
  }
  abandonPending() {
    return this.action('submission', 'Clearing retry reference', async () => {
      this.update({ pending: null, review: null, reviewExpiresAt: 0, invalidRetry: false });
    });
  }
  submit() {
    return this.action('submission', 'Submitting experiment', async (signal) => {
      const selectedAtStart = this.state.selected?.job_id;
      if (this.state.invalidRetry)
        throw new Error(
          'Inspect existing jobs and clear invalid retry metadata before submitting.',
        );
      let pending = this.state.pending;
      if (pending && pending.root !== this.state.capabilities?.output_root?.root_id)
        throw new Error(
          'Pending submission belongs to a different output root. Reconnect to that root first.',
        );
      if (!pending) {
        if (!this.state.review || !this.state.capabilities?.output_root)
          throw new Error('Review the experiment first.');
        if (Date.now() >= this.state.reviewExpiresAt) {
          this.invalidateReview();
          throw new Error('Review expired. Review the experiment again.');
        }
        if (this.active.has('review') || this.active.has('instance'))
          throw new Error('Wait for setup to finish.');
        pending = {
          preflight: this.state.review.preflight_id,
          key: crypto.randomUUID(),
          root: this.state.capabilities.output_root.root_id,
        };
        this.update({ pending });
      }
      let response: JobSubmission;
      try {
        response = await this.call(
          '/jobs/submit',
          { preflight_id: pending.preflight, submission_key: pending.key },
          signal,
        );
      } catch (error) {
        if (
          !signal.aborted &&
          error instanceof ApiError &&
          ['preflight_stale', 'queue_full'].includes(error.code)
        )
          this.commit(signal, { pending: null, review: null, reviewExpiresAt: 0 });
        throw error;
      }
      if (
        response.job.submission_key !== pending.key ||
        response.job.preflight_id !== pending.preflight
      )
        throw new Error('Submission response mismatch.');
      signal.throwIfAborted();
      this.update({
        review: null,
        reviewExpiresAt: 0,
        jobs: [
          ...this.state.jobs.filter((job) => job.job_id !== response.job.job_id),
          response.job,
        ],
      });
      // Admission itself supplies the job. Refresh only queue authority, whose revision
      // changed at admission and is required for explicit held-queue continuation.
      await this.refreshQueue();
      signal.throwIfAborted();
      // Do not replace a different job the owner selected while admission was pending.
      if (this.state.selected?.job_id === selectedAtStart) await this.select(response.job);
      // Preserve retry metadata through an interrupted post-admission handoff as well.
      this.commit(signal, { pending: null });
    });
  }
  private refreshQueue() {
    this.cancel('jobs');
    this.update({ queue: null });
    return this.action('jobs', 'Refreshing queue authority', async (signal) => {
      const queue = await this.call('/queue/status', {}, signal);
      this.commit(signal, { queue });
    });
  }
  private clearResult() {
    this.cancel('analysis');
    this.cancel('inspection');
    this.op('analysis', idle());
    this.op('inspection', idle());
    this.cursors = [null];
    this.update({
      resultInstance: null,
      analysis: null,
      query: null,
      analysisRequested: null,
      analysisPage: 0,
      details: null,
      geometry: null,
      inspectionAttempt: null,
    });
  }
  private async jobs(signal: AbortSignal) {
    const page = await this.call('/jobs/query', { limit: 500, cursor: null }, signal);
    if (
      page.next_cursor ||
      page.items.length > 500 ||
      new Set(page.items.map((j) => j.job_id)).size !== page.items.length
    )
      throw new Error('Job list exceeds its bound or repeats jobs.');
    const queue = await this.call('/queue/status', {}, signal);
    signal.throwIfAborted();
    const selected =
      page.items.find((j) => j.job_id === this.state.selected?.job_id) ?? this.state.selected;
    const changed = selected?.revision !== this.state.selected?.revision;
    if (changed) {
      const query = this.state.query;
      this.clearResult();
      this.update({ query: query ? { ...query, cursor: null } : null });
    }
    this.update({ jobs: page.items, queue, selected });
    if (
      selected &&
      this.state.resultInstance?.instance_snapshot_id !== selected.instance_snapshot_id
    ) {
      const resultInstance = await this.call(
        '/instances/snapshot',
        { instance_snapshot_id: selected.instance_snapshot_id },
        signal,
      );
      if (resultInstance.instance_snapshot_id !== selected.instance_snapshot_id)
        throw new Error('Job instance mismatch.');
      this.commit(signal, { resultInstance });
    }
    if (
      selected?.result_revision &&
      terminal(selected) &&
      !this.state.analysis &&
      !this.active.has('analysis')
    ) {
      await this.analyze(this.state.query ?? { ...selection, table: 'trials' });
    }
  }
  poll() {
    if (shouldPoll(this.state)) return this.refreshJobs();
  }
  refreshJobs() {
    return this.action('jobs', 'Refreshing jobs', (signal) => this.jobs(signal));
  }
  clearSelection() {
    this.cancel('selection');
    this.cancel('jobs');
    this.clearResult();
    this.update({ selected: null, selectedConfiguration: null });
  }
  select(job: JobRecord) {
    this.cancel('jobs');
    this.cancel('selection');
    this.clearResult();
    return this.action('selection', 'Loading job', async (signal) => {
      this.update({ selected: job, selectedConfiguration: null });
      const previous = await this.call('/jobs/review', { job_id: job.job_id }, signal);
      if (
        previous.job.job_id !== job.job_id ||
        previous.plan.instance_snapshot_id !== job.instance_snapshot_id
      )
        throw new Error('Selected job configuration mismatch.');
      this.commit(signal, { selectedConfiguration: previous.plan.configuration });
      const resultInstance = await this.call(
        '/instances/snapshot',
        { instance_snapshot_id: job.instance_snapshot_id },
        signal,
      );
      if (resultInstance.instance_snapshot_id !== job.instance_snapshot_id)
        throw new Error('Job instance mismatch.');
      this.commit(signal, { resultInstance });
      if (job.result_revision && terminal(job))
        await this.analyze({ ...selection, table: 'trials' });
    });
  }
  control(action: 'cancel_queued' | 'stop_after_trial' | 'stop_now') {
    this.cancel('jobs');
    return this.action('control', 'Updating job', async (signal) => {
      const job = this.state.selected;
      if (!job) throw new Error('Select a job first.');
      const response = await this.call(
        '/jobs/control',
        {
          job_id: job.job_id,
          expected_revision: job.revision,
          action,
          confirm_stop_now: action === 'stop_now',
        },
        signal,
      );
      signal.throwIfAborted();
      if (response.job.job_id !== job.job_id) throw new Error('Job response mismatch.');
      if (this.state.selected?.job_id === job.job_id)
        this.commit(signal, { selected: response.job });
      await this.refreshJobs();
    });
  }
  continueQueue() {
    return this.action('control', 'Continuing queue', async (signal) => {
      if (
        this.active.has('submission') ||
        this.active.has('jobs') ||
        this.state.operations.jobs.error
      )
        throw new Error('Wait for admission and refresh jobs before continuing the queue.');
      if (!this.state.queue) throw new Error('Refresh jobs to read the current queue first.');
      await this.call(
        '/queue/continue',
        { expected_queue_revision: this.state.queue.queue_revision },
        signal,
      );
      signal.throwIfAborted();
      await this.refreshJobs();
    });
  }
  rerun() {
    this.invalidateReview();
    return this.action('review', 'Reviewing fresh rerun', async (signal) => {
      this.setupAvailable();
      const job = this.state.selected;
      if (!job) throw new Error('Select a job first.');
      const previous = await this.call('/jobs/review', { job_id: job.job_id }, signal);
      if (
        previous.job.job_id !== job.job_id ||
        previous.plan.instance_snapshot_id !== job.instance_snapshot_id
      )
        throw new Error('Job review mismatch.');
      const snapshot = await this.call(
        '/instances/snapshot',
        { instance_snapshot_id: previous.plan.instance_snapshot_id },
        signal,
      );
      if (snapshot.instance_snapshot_id !== job.instance_snapshot_id)
        throw new Error('Rerun snapshot mismatch.');
      const capabilities = await this.call('/runs/capabilities', {}, signal);
      const review = await this.call(
        '/runs/preflight',
        {
          configuration: previous.plan.configuration,
          instance_snapshot_id: snapshot.instance_snapshot_id,
          capabilities_revision: capabilities.capabilities_revision,
        },
        signal,
      );
      if (
        review.instance_snapshot_id !== snapshot.instance_snapshot_id ||
        review.capabilities_revision !== capabilities.capabilities_revision
      )
        throw new Error('Rerun review mismatch.');
      this.commit(signal, {
        snapshot,
        capabilities,
        review,
        reviewExpiresAt: Date.now() + review.valid_for_s * 1000,
      });
      return review;
    });
  }
  private binding() {
    const job = this.state.selected;
    if (!job || !terminal(job) || !job.result_revision)
      throw new Error('Select a job with a validated terminal result.');
    return {
      job_id: job.job_id,
      expected_revision: job.revision,
      result_revision: job.result_revision,
    };
  }
  private checkBinding(binding: ReturnType<ExperimentController['binding']>) {
    const current = this.binding();
    if (JSON.stringify(current) !== JSON.stringify(binding))
      throw new Error('Selected result changed; reload it.');
  }
  analyze(query: AnalysisQuery, pageIndex = 0) {
    this.cancel('analysis');
    return this.action('analysis', 'Loading analysis', async (signal) => {
      this.commit(signal, { analysisRequested: query });
      const binding = this.binding(),
        previousKey = this.state.analysis?.query_key;
      const page = await this.call('/jobs/analysis', { ...binding, query }, signal);
      signal.throwIfAborted();
      this.checkBinding(binding);
      if (
        page.job_id !== binding.job_id ||
        page.job_revision !== binding.expected_revision ||
        page.result_revision !== binding.result_revision ||
        page.table !== query.table ||
        page.items.length > query.limit ||
        (query.cursor && page.query_key !== previousKey)
      )
        throw new Error('Analysis context changed; reload the first page.');
      this.cursors = pageIndex === 0 ? [null] : this.cursors.slice(0, pageIndex + 1);
      if (page.next_cursor) this.cursors.push(page.next_cursor);
      this.commit(signal, { analysis: page, query, analysisPage: pageIndex });
    });
  }
  navigateAnalysis(index: number) {
    if (this.state.query && index >= 0 && index < this.cursors.length)
      return this.analyze({ ...this.state.query, cursor: this.cursors[index] }, index);
  }
  closeInspection() {
    this.cancel('inspection');
    this.update({ details: null, geometry: null, inspectionAttempt: null });
  }
  inspect(attempt_id: string) {
    this.cancel('inspection');
    return this.action('inspection', 'Verifying final tour', async (signal) => {
      this.update({ details: null, geometry: null, inspectionAttempt: attempt_id });
      const binding = this.binding(),
        job = this.state.selected!;
      const body = {
        ...binding,
        instance_snapshot_id: job.instance_snapshot_id,
        campaign_id: job.campaign_id,
        attempt_id,
      };
      const details = await this.call('/jobs/result', body, signal);
      if (
        details.job_id !== job.job_id ||
        details.job_revision !== job.revision ||
        details.result_revision !== job.result_revision ||
        details.instance_snapshot_id !== job.instance_snapshot_id ||
        details.details.attempt_id !== attempt_id ||
        details.details.campaign_id !== job.campaign_id
      )
        throw new Error('Result binding mismatch.');
      const geometry = await this.call(
        '/jobs/geometry',
        { ...body, geometry_revision: details.details.geometry_revision },
        signal,
      );
      this.checkBinding(binding);
      if (
        geometry.job_id !== job.job_id ||
        geometry.job_revision !== job.revision ||
        geometry.result_revision !== job.result_revision ||
        geometry.instance_snapshot_id !== job.instance_snapshot_id ||
        geometry.geometry.attempt_id !== attempt_id ||
        geometry.geometry.campaign_id !== job.campaign_id ||
        geometry.geometry.geometry_revision !== details.details.geometry_revision ||
        geometry.geometry.snapshot_sha256 !== details.details.snapshot_sha256 ||
        (
          [
            'instance_id',
            'coordinate_checksum',
            'distance_kind',
            'instance_seed',
            'n_cities',
          ] as const
        ).some((key) => geometry.geometry.instance[key] !== details.details.instance[key])
      )
        throw new Error('Geometry binding mismatch.');
      this.commit(signal, { details, geometry });
      return details;
    });
  }
}
