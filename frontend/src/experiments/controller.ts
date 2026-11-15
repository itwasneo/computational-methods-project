import { ApiClient, ApiError } from '../results/client.ts';
import { wireGuard, type WireName } from '../wire.ts';
import type { Artifact, ExecutionCapabilities, StoredInstance, ScientificConfiguration, ReviewedExecution,
  JobRecord, JobsPage, QueueStatus, JobEnvelope, JobSubmission, JobReview, JobDetailsResponse,
  JobGeometryResponse, JobAnalysisResponse, AnalysisQuery, UniformGeneration, ClusteredGeneration } from '../contracts.ts';

export const terminal = (job: JobRecord) => ['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state);
export const selection = { attempt_ids: null, limit: 100, cursor: null };
export function initialState() {
  return { connected: false, busy: '', error: '', workspace: '', session: '', capabilities: null as ExecutionCapabilities | null,
    artifacts: [] as Artifact[], catalogRevision: '', snapshot: null as StoredInstance | null,
    review: null as ReviewedExecution | null, jobs: [] as JobRecord[], queue: null as QueueStatus | null,
    selected: null as JobRecord | null, resultInstance: null as StoredInstance | null, analysis: null as JobAnalysisResponse | null,
    query: null as AnalysisQuery | null, details: null as JobDetailsResponse | null, geometry: null as JobGeometryResponse | null,
    pending: null as { preflight: string; key: string; root: string } | null };
}
export type ExperimentState = ReturnType<typeof initialState>;
type RetryStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const RETRY_KEY = 'tsp-hpc-pending-submission-v1';
function browserStore(): RetryStore | null { try { return globalThis.sessionStorage ?? null; } catch { return null; } }

export class ExperimentController {
  state = initialState();
  api: ApiClient;
  private notify: (state: ExperimentState) => void;
  private abort = new AbortController();
  private epoch = 0;
  private retryStore: RetryStore | null;
  private invalidRetry = false;
  constructor(notify: (state: ExperimentState) => void, api = new ApiClient(), retryStore = browserStore()) {
    this.notify = notify; this.api = api; this.retryStore = retryStore;
    try {
      const raw = retryStore?.getItem(RETRY_KEY);
      if (raw) {
        if (raw.length > 1024) throw new Error('Invalid retry record');
        const value = JSON.parse(raw);
        if (!value || Object.keys(value).sort().join(',') !== 'key,preflight,root'
          || !Object.values(value).every(v => typeof v === 'string' && v.length > 0 && v.length <= 128)) throw new Error('Invalid retry record');
        this.state.pending = value;
      }
    } catch { this.invalidRetry = true; this.state.error = 'Invalid saved retry metadata. Inspect jobs before clearing this tab’s session storage.'; }
  }
  private update(patch: Partial<ExperimentState>) {
    if ('pending' in patch && this.retryStore) {
      if (patch.pending) this.retryStore.setItem(RETRY_KEY, JSON.stringify(patch.pending));
      else this.retryStore.removeItem(RETRY_KEY);
    }
    this.state = { ...this.state, ...patch }; this.notify(this.state);
  }
  private context() { return { workspace_id: this.state.workspace, session_id: this.state.session }; }
  private call<T>(path: string, name: WireName, body: object = {}) {
    return this.api.operation<T>(path, { ...this.context(), ...body }, wireGuard<T>(name), this.abort.signal);
  }
  private async action(label: string, work: () => Promise<void>) {
    if (this.state.busy) return;
    const epoch = this.epoch;
    this.update({ busy: label, error: '' });
    try { await work(); }
    catch (error) {
      if (epoch === this.epoch) this.update({ error: error instanceof Error ? error.message : 'Request failed.',
        analysis: null, details: null, geometry: null });
    } finally { if (epoch === this.epoch) this.update({ busy: '' }); }
  }
  disconnect() {
    this.epoch++; this.abort.abort(); this.abort = new AbortController(); this.api.clear();
    const pending = this.state.pending;
    this.state = { ...initialState(), pending }; this.notify(this.state);
  }
  connect(secret: string) {
    return this.action('Connecting', async () => {
      const session = await this.api.pair(secret, this.abort.signal);
      const workspace = (await this.api.workspace(this.abort.signal)).workspace.workspace_id;
      this.update({ session, workspace });
      const capabilities = await this.call<ExecutionCapabilities>('/runs/capabilities', 'ExecutionCapabilities');
      this.update({ connected: true, capabilities });
      await this.catalog();
      if (capabilities.local_runs_enabled) await this.jobs();
    });
  }
  private async catalog() {
    const artifacts: Artifact[] = []; let cursor: string | null = null, revision = '';
    do {
      const page = await this.api.catalog(this.state.workspace, cursor, this.abort.signal);
      if (revision && revision !== page.revision) throw new Error('Catalog changed; refresh and try again.');
      revision = page.revision; artifacts.push(...page.items); cursor = page.next_cursor;
      if (artifacts.length > 10000 || (cursor && page.items.length === 0)
        || new Set(artifacts.map(a => a.artifact_handle)).size !== artifacts.length) throw new Error('Catalog exceeds its bound or did not advance.');
    } while (cursor);
    this.update({ artifacts, catalogRevision: revision });
  }
  refreshCatalog() { return this.action('Refreshing catalog', async () => {
    await this.api.refresh(this.state.workspace, this.abort.signal); await this.catalog();
  }); }
  invalidateReview() { this.update({ review: null }); }
  exportInstance(save: (blob: Blob) => void) { return this.action('Exporting instance', async () => {
    if (!this.state.snapshot) throw new Error('Create or load an instance first.');
    const blob = await this.api.exportInstance({ ...this.context(), instance_snapshot_id: this.state.snapshot.instance_snapshot_id }, this.abort.signal);
    save(blob);
  }); }
  generate(generation: UniformGeneration | ClusteredGeneration) {
    return this.action('Generating instance', async () => {
      this.update({ review: null });
      const snapshot = await this.call<StoredInstance>('/instances/generate', 'StoredInstance', { generation });
      this.update({ snapshot });
    });
  }
  load(artifact_handle: string) { return this.action('Loading instance', async () => {
    this.update({ review: null });
    const snapshot = await this.call<StoredInstance>('/instances/load', 'StoredInstance', {
      artifact_handle, artifact_revision: this.state.catalogRevision });
    this.update({ snapshot });
  }); }
  review(configuration: ScientificConfiguration) { return this.action('Reviewing experiment', async () => {
    this.update({ review: null });
    if (!this.state.snapshot || !this.state.capabilities) throw new Error('Create or load an instance first.');
    const review = await this.call<ReviewedExecution>('/runs/preflight', 'ReviewedExecution', {
      configuration, instance_snapshot_id: this.state.snapshot.instance_snapshot_id,
      capabilities_revision: this.state.capabilities.capabilities_revision });
    if (review.instance_snapshot_id !== this.state.snapshot.instance_snapshot_id
      || review.capabilities_revision !== this.state.capabilities.capabilities_revision) throw new Error('Review context mismatch.');
    this.update({ review });
  }); }
  abandonPending() { return this.action('Clearing retry reference', async () => {
    this.update({ pending: null, review: null }); // Explicit owner decision; never cancels/deletes accepted work.
    this.invalidRetry = false;
  }); }
  submit() { return this.action('Submitting experiment', async () => {
    if (this.invalidRetry) throw new Error('Inspect existing jobs and clear invalid retry metadata before submitting.');
    let pending = this.state.pending;
    if (pending && pending.root !== this.state.capabilities?.output_root?.root_id) throw new Error('Pending submission belongs to a different output root. Reconnect to that root first.');
    if (!pending) {
      if (!this.state.review || !this.state.capabilities?.output_root) throw new Error('Review the experiment first.');
      pending = { preflight: this.state.review.preflight_id, key: crypto.randomUUID(), root: this.state.capabilities.output_root.root_id };
      this.update({ pending }); // Retain BEFORE sending: an ambiguous response must reuse the key.
    }
    let response: JobSubmission;
    try {
      response = await this.call<JobSubmission>('/jobs/submit', 'JobSubmission', { preflight_id: pending.preflight, submission_key: pending.key });
    } catch (error) {
      // These server rejections occur after durable-key lookup but before admission.
      // Transport/503/authorization/unknown failures remain ambiguous and keep the key.
      if (error instanceof ApiError && ['preflight_stale', 'queue_full'].includes(error.code)) {
        this.update({ pending: null, review: null });
      }
      throw error;
    }
    if (response.job.submission_key !== pending.key || response.job.preflight_id !== pending.preflight) throw new Error('Submission response mismatch.');
    this.update({ pending: null, review: null, selected: response.job, analysis: null, query: null, details: null, geometry: null });
    await this.jobs();
  }); }
  private async jobs() {
    const page = await this.call<JobsPage>('/jobs/query', 'JobsPage', { limit: 500, cursor: null });
    if (page.next_cursor || page.items.length > 500 || new Set(page.items.map(j => j.job_id)).size !== page.items.length) throw new Error('Job list exceeds its bound or repeats jobs.');
    const queue = await this.call<QueueStatus>('/queue/status', 'QueueStatus');
    const selected = page.items.find(j => j.job_id === this.state.selected?.job_id) ?? this.state.selected;
    const changed = selected?.revision !== this.state.selected?.revision;
    this.update({ jobs: page.items, queue, selected, ...(changed ? { analysis: null, details: null, geometry: null } : {}) });
    if (selected && this.state.resultInstance?.instance_snapshot_id !== selected.instance_snapshot_id) {
      const resultInstance = await this.call<StoredInstance>('/instances/snapshot', 'StoredInstance', { instance_snapshot_id: selected.instance_snapshot_id });
      if (resultInstance.instance_snapshot_id !== selected.instance_snapshot_id) throw new Error('Job instance mismatch.');
      this.update({ resultInstance });
    }
    if (selected?.result_revision && !this.state.analysis) await this.analysis({ ...selection, table: 'trials' });
  }
  poll() { if (!this.state.connected || !this.state.capabilities?.local_runs_enabled || this.state.busy || this.state.error) return;
    return this.action('Refreshing jobs', () => this.jobs()); }
  refreshJobs() { return this.action('Refreshing jobs', () => this.jobs()); }
  select(job: JobRecord) { return this.action('Loading job', async () => {
    this.update({ selected: job, analysis: null, query: null, details: null, geometry: null });
    const resultInstance = await this.call<StoredInstance>('/instances/snapshot', 'StoredInstance', { instance_snapshot_id: job.instance_snapshot_id });
    if (resultInstance.instance_snapshot_id !== job.instance_snapshot_id) throw new Error('Job instance mismatch.');
    this.update({ resultInstance });
    if (job.result_revision) await this.analysis({ ...selection, table: 'trials' });
  }); }
  control(action: 'cancel_queued' | 'stop_after_trial' | 'stop_now') { return this.action('Updating job', async () => {
    const job = this.state.selected; if (!job) return;
    const response = await this.call<JobEnvelope>('/jobs/control', 'JobEnvelope', { job_id: job.job_id,
      expected_revision: job.revision, action, confirm_stop_now: action === 'stop_now' });
    if (response.job.job_id !== job.job_id) throw new Error('Job response mismatch.');
    this.update({ selected: response.job }); await this.jobs();
  }); }
  continueQueue() { return this.action('Continuing queue', async () => {
    await this.call<QueueStatus>('/queue/continue', 'QueueStatus', { expected_queue_revision: this.state.queue?.queue_revision });
    await this.jobs();
  }); }
  rerun() { return this.action('Reviewing fresh rerun', async () => {
    const job = this.state.selected; if (!job) return;
    const previous = await this.call<JobReview>('/jobs/review', 'JobReview', { job_id: job.job_id });
    if (previous.job.job_id !== job.job_id || previous.plan.instance_snapshot_id !== job.instance_snapshot_id) throw new Error('Job review mismatch.');
    const snapshot = await this.call<StoredInstance>('/instances/snapshot', 'StoredInstance', { instance_snapshot_id: previous.plan.instance_snapshot_id });
    if (snapshot.instance_snapshot_id !== job.instance_snapshot_id) throw new Error('Rerun snapshot mismatch.');
    const capabilities = await this.call<ExecutionCapabilities>('/runs/capabilities', 'ExecutionCapabilities');
    const review = await this.call<ReviewedExecution>('/runs/preflight', 'ReviewedExecution', {
      configuration: previous.plan.configuration, instance_snapshot_id: snapshot.instance_snapshot_id,
      capabilities_revision: capabilities.capabilities_revision });
    if (review.instance_snapshot_id !== snapshot.instance_snapshot_id || review.capabilities_revision !== capabilities.capabilities_revision) throw new Error('Rerun review mismatch.');
    this.update({ snapshot, capabilities, review });
  }); }
  private binding() {
    const job = this.state.selected;
    if (!job || !terminal(job) || !job.result_revision) throw new Error('Select a job with a validated terminal result.');
    return { job_id: job.job_id, expected_revision: job.revision, result_revision: job.result_revision };
  }
  private async analysis(query: AnalysisQuery) {
    const binding = this.binding();
    const page = await this.call<JobAnalysisResponse>('/jobs/analysis', 'JobAnalysisResponse', { ...binding, query });
    if (page.job_id !== binding.job_id || page.job_revision !== binding.expected_revision || page.result_revision !== binding.result_revision
      || page.table !== query.table || page.items.length > query.limit
      || (query.cursor && page.query_key !== this.state.analysis?.query_key)) throw new Error('Analysis context changed; reload the first page.');
    this.update({ analysis: page, query });
  }
  analyze(query: AnalysisQuery) { return this.action('Loading analysis', () => this.analysis(query)); }
  inspect(attempt_id: string) { return this.action('Verifying final tour', async () => {
    this.update({ details: null, geometry: null });
    const binding = this.binding(), job = this.state.selected!;
    const body = { ...binding, instance_snapshot_id: job.instance_snapshot_id, campaign_id: job.campaign_id, attempt_id };
    const details = await this.call<JobDetailsResponse>('/jobs/result', 'JobDetailsResponse', body);
    if (details.job_id !== job.job_id || details.job_revision !== job.revision || details.result_revision !== job.result_revision
      || details.instance_snapshot_id !== job.instance_snapshot_id || details.details.attempt_id !== attempt_id || details.details.campaign_id !== job.campaign_id) throw new Error('Result binding mismatch.');
    const geometry = await this.call<JobGeometryResponse>('/jobs/geometry', 'JobGeometryResponse', { ...body, geometry_revision: details.details.geometry_revision });
    if (geometry.job_id !== job.job_id || geometry.job_revision !== job.revision || geometry.result_revision !== job.result_revision
      || geometry.instance_snapshot_id !== job.instance_snapshot_id || geometry.geometry.attempt_id !== attempt_id || geometry.geometry.campaign_id !== job.campaign_id
      || geometry.geometry.geometry_revision !== details.details.geometry_revision || geometry.geometry.snapshot_sha256 !== details.details.snapshot_sha256
      || (['instance_id', 'coordinate_checksum', 'distance_kind', 'instance_seed', 'n_cities'] as const)
        .some(key => geometry.geometry.instance[key] !== details.details.instance[key])) throw new Error('Geometry binding mismatch.');
    this.update({ details, geometry });
  }); }
}
