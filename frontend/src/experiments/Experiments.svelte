<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import { ApiClient } from '../results/client';
  import { ExperimentController, initialState, terminal, shouldPoll } from './controller';
  import { decodeGeometry } from './geometry';
  import { compactId } from '../display';
  import Analysis from './Analysis.svelte';
  import InstanceInspection from '../InstanceInspection.svelte';
  import type { Theme } from '../theme';
  import type { ScientificConfiguration, DistanceMode } from '../contracts';
  let { theme, api, disconnectVersion, onSession, active }: { theme: Theme; api: ApiClient; disconnectVersion: number; onSession: (connected: boolean) => void; active: boolean } = $props();
  let view = $state(initialState());
  const controller = new ExperimentController(value => view = value, untrack(() => api));
  $effect(() => { disconnectVersion; untrack(() => controller.disconnect()); });
  let visible = $state(document.visibilityState === 'visible');
  onMount(() => {
    const changed = () => visible = document.visibilityState === 'visible';
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  });
  $effect(() => {
    if (!active || !visible || !shouldPoll(view)) return;
    const timer = setTimeout(() => void controller.poll(), 1500);
    return () => clearTimeout(timer);
  });
  onDestroy(() => controller.disconnect());
  let secret = $state('');
  let generator = $state<'uniform-v1' | 'clustered-v1'>('uniform-v1');
  let cities = $state(256), side = $state(100), seed = $state('42'), clusters = $state(4), spread = $state(5);
  let artifact = $state('');
  let implementations = $state(['visited-scan-v1']);
  let modes = $state<DistanceMode[]>(['dense', 'on_demand']);
  let starts = $state('0,1,2,3,4,5,6,7');
  let repetitions = $state(5), solverSeed = $state('123');
  let timed = $state(false), seconds = $state(1), history = $state(false), interval = $state(0.1), points = $state(4096);
  let inputError = $state('');
  let stopConfirmed = $state(false);
  let cityPage = $state(0);
  function download(blob: Blob, name: string) {
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function rerun() {
    await controller.rerun();
    const config = controller.state.review?.configuration;
    if (!config) return;
    implementations = config.implementations.filter((id): id is string => id !== null);
    modes = config.distance_modes; starts = (config.budget.start_cities as number[]).join(',');
    repetitions = config.repetitions.length; solverSeed = config.solver_seed;
    timed = config.budget.time_limit_s !== undefined; seconds = Number(config.budget.time_limit_s ?? 1);
    history = config.record_history; interval = config.history_policy?.interval_s ?? 0.1; points = config.history_policy?.max_points ?? 4096;
    document.querySelector('[aria-label="Reviewed experiment"]')?.scrollIntoView({ behavior: 'smooth' });
  }
  let capabilities = $derived(view.capabilities?.implementations.filter(i => i.algorithm === 'nn' && i.available === true) ?? []);
  let available = $derived(capabilities.map(i => String(i.implementation)));
  let inspection = $derived.by(() => { try { return view.geometry ? { data: decodeGeometry(view.geometry.geometry), error: '' } : null; }
    catch (e) { return { data: null, error: e instanceof Error ? e.message : 'Invalid geometry' }; } });
  async function connect(event: SubmitEvent) { event.preventDefault(); const value = secret; secret = ''; await controller.connect(value); onSession(controller.state.connected); }
  function generate(event: SubmitEvent) {
    event.preventDefault();
    void controller.generate(generator === 'uniform-v1' ? { generator, n_cities: cities, seed, side_length: side }
      : { generator, n_cities: cities, seed, side_length: side, n_clusters: clusters, standard_deviation: spread });
  }
  function review(event: SubmitEvent) {
    event.preventDefault(); inputError = '';
    try {
      const tokens = starts.trim() ? starts.split(',').map(v => v.trim()) : [];
      if (tokens.some(v => !/^(0|[1-9][0-9]*)$/.test(v))) throw new Error('Starts must be comma-separated nonnegative integer city IDs.');
      const start_cities = tokens.map(Number);
      if (start_cities.some(v => !Number.isSafeInteger(v) || v >= (view.snapshot?.instance.n_cities ?? 0)) || new Set(start_cities).size !== start_cities.length) throw new Error('Starts must be distinct and within the instance.');
      if (!Number.isSafeInteger(repetitions) || repetitions < 1 || repetitions > (view.capabilities?.limits.max_repetitions ?? 32)) throw new Error('Invalid repetition count.');
      if (!implementations.length || implementations.some(i => !available.includes(i)) || !modes.length) throw new Error('Select available implementations and at least one distance mode.');
      const configuration: ScientificConfiguration = { algorithm: 'nn', parameters: {},
        budget: { start_cities, ...(timed ? { time_limit_s: seconds } : {}) }, logical_search_ids: null,
        implementations, distance_modes: modes, repetitions: Array.from({ length: repetitions }, (_, i) => i),
        solver_seed: solverSeed, max_matrix_bytes: view.capabilities?.limits.max_matrix_bytes ?? 0,
        record_history: history, history_policy: history ? { interval_s: interval, max_points: points } : null };
      void controller.review(configuration);
    } catch (error) { inputError = error instanceof Error ? error.message : 'Invalid configuration'; }
  }
</script>
<main class="experiment-app">
  <h1>Experiment workspace</h1>
  <p>Generate or load an instance, compare NN implementations and distance modes, then inspect validated results. No hand-written manifest or copied IDs.</p>
  {#if !view.connected}
    <form class="app-card" onsubmit={connect}>
      <h2>Connect to your local service</h2>
      <p>Start <code>tools/local_app.py</code> with your read workspace and execution output directories. Enter the same private pairing secret here.</p>
      <label>Pairing secret<input type="password" bind:value={secret} required minlength="43" maxlength="128" pattern="[A-Za-z0-9_\-]+" autocomplete="off" /></label>
      <button disabled={!!view.busy}>Connect</button>
    </form>
  {:else}
    <div class="source-actions"><strong>Output: {view.capabilities?.output_root?.display_name ?? 'Read-only'}</strong><button onclick={() => { controller.disconnect(); onSession(false); }}>Disconnect</button></div>
    {#if !view.capabilities?.local_runs_enabled}
      <p role="status">Execution is disabled. Restart with both owner execution flags, or use the Saved results tab. HTTP cannot enable execution.</p>
    {:else}
      {#if view.pending}<section class="app-card"><h2>Submission acknowledgement uncertain</h2><p>Do not create another campaign to retry. Reuse submission key <code>{view.pending.key}</code>, retained in tab session storage when available. Check jobs after reconnecting.</p><button disabled={!!view.busy} onclick={() => controller.submit()}>Retry original submission</button>
        <button disabled={!!view.busy} onclick={() => { if (window.confirm('Have you inspected jobs in the original output root? Clearing this reference does NOT cancel accepted work. A fresh submission may duplicate that work.')) void controller.abandonPending(); }}>Clear retry reference after inspection</button>
      </section>{/if}
      <section class="app-card">
        <h2>1. Create or load an instance</h2>
        <form onsubmit={generate}><fieldset class="form-grid" disabled={!!view.busy || !!view.pending}>
          <label>Generator<select bind:value={generator}><option value="uniform-v1">Uniform square</option><option value="clustered-v1">Gaussian clusters</option></select></label>
          <label>Cities<input type="number" min="2" max={view.capabilities.limits.max_cities} required bind:value={cities} /></label>
          <label>Instance seed (uint64)<input required pattern={'0|[1-9][0-9]{0,19}'} bind:value={seed} /></label>
          <label>Side length<input type="number" min="0.000000001" step="any" required bind:value={side} /></label>
          {#if generator === 'clustered-v1'}<label>Clusters<input type="number" min="1" max={cities} required bind:value={clusters} /></label><label>Standard deviation<input type="number" min="0" step="any" required bind:value={spread} /></label>{/if}
          <button>Generate instance</button>
        </fieldset></form>
        <div class="source-actions"><label>Or load saved instance<select bind:value={artifact}><option value="">Select instance JSON</option>{#each view.artifacts.filter(a => a.kind === 'json') as item}<option value={item.artifact_handle}>{item.display_name}</option>{/each}</select></label>
          <button disabled={!artifact || !!view.busy || !!view.pending} onclick={() => controller.load(artifact)}>Load instance</button>
          <button disabled={!!view.busy} onclick={() => controller.refreshCatalog()}>Refresh files</button></div>
        <p>JSON candidates are not necessarily instances; the shared loader validates your selection. Generation and execution seeds are separate.</p>
        {#if view.snapshot}<p role="status">Instance ready: {view.snapshot.instance.n_cities} cities · {view.snapshot.instance.distance_kind}</p><button disabled={!!view.busy} onclick={() => controller.exportInstance(blob => download(blob, 'instance.json'))}>Download canonical instance</button><details><summary>Instance identity</summary><pre>{JSON.stringify(view.snapshot.instance, null, 2)}</pre></details>{/if}
      </section>
      <section class="app-card">
        <h2>2. Configure NN comparison</h2>
        {#if !available.length}<p>No native NN implementation is available.</p><pre>{JSON.stringify(view.capabilities.implementations, null, 2)}</pre>{/if}
        <form onsubmit={review} oninput={() => controller.invalidateReview()} onchange={() => controller.invalidateReview()}>
          <fieldset disabled={!view.snapshot || !!view.busy || !!view.pending || !available.length}>
            <div class="form-grid"><div>Implementations{#each available as id}<label><input type="checkbox" value={id} bind:group={implementations} /> {id}</label>{/each}</div>
              <div>Distance modes<label><input type="checkbox" value="dense" bind:group={modes} /> Dense matrix</label><label><input type="checkbox" value="on_demand" bind:group={modes} /> On-demand</label></div>
              <label>Ordered start cities<input bind:value={starts} aria-describedby="starts-help" /></label>
              <label>Repetitions<input type="number" min="1" max={view.capabilities.limits.max_repetitions} required bind:value={repetitions} /></label>
              <label>Solver seed (uint64)<input required pattern={'0|[1-9][0-9]{0,19}'} bind:value={solverSeed} /></label>
            </div>
            <p id="starts-help">Comma-separated city IDs. Both modes use exactly the same input and ordered starts. Empty starts mean zero work. NN is deterministic.</p>
            <label><input type="checkbox" bind:checked={timed} /> Add a per-invocation time limit</label>
            {#if timed}<label>Seconds<input type="number" min="0.000001" step="any" required bind:value={seconds} /></label><p>Timed runs compare quality under a budget, not implementation-speed ratios. Safe checkpoints can overshoot.</p>{/if}
            <label><input type="checkbox" bind:checked={history} /> Record best-so-far history</label>
            {#if history}<div class="form-grid"><label>Sampling interval (seconds)<input type="number" min="0" step="any" required bind:value={interval} /></label><label>Maximum points per invocation<input type="number" min="2" max="100000" required bind:value={points} /></label></div>{/if}
            <button>Review experiment</button>
          </fieldset>
        </form>
        {#if inputError}<p role="alert">{inputError}</p>{/if}
        {#if view.review}<section aria-label="Reviewed experiment"><h3>Review before execution</h3>
          <p>{view.review.trial_count} trials · {view.review.configuration.distance_modes.join(' / ')} · {view.review.configuration.implementations.join(', ')}</p>
          <p>Matrix estimate: {view.review.estimated_matrix_bytes} bytes. Output estimate: {view.review.estimated_output_bytes} bytes. Job ceiling: {view.review.job_time_ceiling_s} s. Review valid for {view.review.valid_for_s} s.</p>
          <p>Fresh setup is charged per trial. The estimates are not total RSS guarantees. Nothing has executed yet.</p>
          <details><summary>Exact reviewed plan, seeds and limits</summary><pre>{JSON.stringify(view.review, null, 2)}</pre></details>
          <button disabled={!!view.busy || !!view.pending} onclick={() => controller.submit()}>Run reviewed experiment</button>
        </section>{/if}
      </section>
      <section class="app-card">
        <h2>3. Jobs and recovery</h2><div class="source-actions"><button disabled={!!view.busy} onclick={() => controller.refreshJobs()}>Refresh jobs</button><span>Dispatch: {view.queue?.dispatch ?? 'unknown'}</span>
        {#if view.queue?.dispatch === 'held'}<button disabled={!!view.busy} onclick={() => controller.continueQueue()}>Continue held queue</button>{/if}</div>
        <p>Disconnecting does not cancel work. Restart holds dispatch. Completed campaigns may contain failed trials; inspect the counts.</p>
        <div class="table-region"><table><thead><tr><th>Job</th><th>State</th><th>Recorded / planned</th><th>Failed</th></tr></thead><tbody>
          {#each view.jobs as job}<tr><td><button title={job.job_id} aria-label={job.job_id} disabled={!!view.busy} aria-pressed={view.selected?.job_id === job.job_id} onclick={() => { stopConfirmed = false; void controller.select(job); }}>{compactId(job.job_id)}</button></td><td>{job.state}</td><td>{job.recorded_trials} / {job.planned_trials}</td><td>{job.failed_trials}</td></tr>{:else}<tr><td colspan="4">No submitted experiments.</td></tr>{/each}
        </tbody></table></div>
        {#if view.selected}<h3>Selected job: {view.selected.state}</h3>
          {#if view.selected.failure_code}<p role="alert">Operational failure: {view.selected.failure_code}. Valid recorded evidence may still be available.</p>{/if}
          <div class="source-actions">
            {#if ['queued', 'held'].includes(view.selected.state)}<button disabled={!!view.busy} onclick={() => controller.control('cancel_queued')}>Cancel queued job</button>
            {:else if !terminal(view.selected)}<button disabled={!!view.busy} onclick={() => controller.control('stop_after_trial')}>Stop after trial</button><label><input type="checkbox" bind:checked={stopConfirmed} /> Confirm immediate interruption</label><button disabled={!stopConfirmed || !!view.busy} onclick={() => { stopConfirmed = false; void controller.control('stop_now'); }}>Stop now</button>{/if}
            {#if terminal(view.selected)}<button disabled={!!view.busy || !!view.pending} onclick={rerun}>Review fresh rerun</button>{/if}
          </div>
          {#if terminal(view.selected) && !view.selected.result_revision}<p>No validated result source is available. No result or completion is fabricated.</p>{/if}
        {/if}
      </section>
      {#if view.analysis && view.selected}
        <Analysis page={view.analysis} busy={!!view.busy} instanceId={view.resultInstance?.instance.instance_id ?? ''}
          implementations={available} references={view.artifacts.filter(a => a.kind === 'json')} analyze={q => controller.analyze(q)} inspect={id => { cityPage = 0; void controller.inspect(id); }} />
        <div class="source-actions"><button disabled={!!view.busy || !view.query?.cursor} onclick={() => view.query && controller.analyze({ ...view.query, cursor: null })}>First analysis page</button><button disabled={!!view.busy || !view.analysis.next_cursor} onclick={() => view.query && controller.analyze({ ...view.query, cursor: view.analysis!.next_cursor })}>Next analysis page</button></div>
      {/if}
      {#if view.details}
        <section class="app-card"><h2>Verified final result</h2><p>Attempt: {view.details.details.attempt_id}</p>
          <p>Cost: {view.details.details.result.verified_cost ?? 'No final solution'} · Native verified: {String(view.details.details.native_verified)} · Usable: {String(view.details.details.row.usable)}</p>
          <p>Campaign complete: {String(view.details.details.row.campaign_complete)} · Duplicate: {String(view.details.details.row.duplicate_execution)}</p>
          {#if inspection?.error}<p role="alert">{inspection.error}</p>{:else if inspection?.data}{#key view.details.details.geometry_revision}<InstanceInspection inspection={inspection.data} {theme} />{/key}{/if}
          {#if !view.geometry?.geometry.tour}<p>No aggregate final tour. Coordinates are not a fabricated solution.</p>{/if}
          {#if inspection?.data}
            <details><summary>Accessible coordinates and final permutation</summary>
              <table><thead><tr><th>City ID</th><th>X</th><th>Y</th></tr></thead><tbody>
                {#each Array.from({ length: Math.min(100, inspection.data.coordinates.length / 2 - cityPage * 100) }, (_, i) => cityPage * 100 + i) as city}
                  <tr><td>{city}</td><td>{Object.is(inspection.data.coordinates[city * 2], -0) ? '-0' : inspection.data.coordinates[city * 2]}</td><td>{Object.is(inspection.data.coordinates[city * 2 + 1], -0) ? '-0' : inspection.data.coordinates[city * 2 + 1]}</td></tr>
                {/each}
              </tbody></table>
              <button disabled={cityPage === 0} onclick={() => cityPage--}>Previous cities</button><button disabled={(cityPage + 1) * 100 >= inspection.data.coordinates.length / 2} onclick={() => cityPage++}>Next cities</button>
              <p>Final city permutation (implicit closure):</p><pre>{inspection.data.route ? Array.from(inspection.data.route).join(', ') : 'No final tour'}</pre>
            </details>
            <button onclick={() => download(new Blob([JSON.stringify(view.geometry, null, 2)], { type: 'application/json' }), 'verified-geometry.json')}>Download verified geometry</button>
          {/if}
          <details><summary>Configuration, logical results and timings</summary><pre>{JSON.stringify(view.details.details, null, 2)}</pre></details>
        </section>
      {/if}
    {/if}
  {/if}
  {#if view.busy}<p role="status">{view.busy}…</p>{/if}
  {#if view.error}<div role="alert" class="app-error"><strong>{view.error}</strong><p>Refresh stale state before trying again. For authorization loss, disconnect and enable fresh pairing in the owner terminal. An uncertain submission must retry its original key.</p></div>{/if}
</main>
<style>
  :global(.app-card) { border: 1px solid #8886; border-radius: .7rem; padding: 1rem; margin: 1rem 0; }
  :global(.form-grid) { display: flex; flex-wrap: wrap; gap: 1rem; align-items: end; }
  :global(.app-card label) { display: block; margin: .5rem 0; }
  :global(.app-card input:not([type=checkbox])), :global(.app-card select) { display: block; max-width: 100%; padding: .4rem; }
  :global(.app-card pre) { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 24rem; overflow: auto; }
  :global(.app-card fieldset) { border: 0; padding: 0; }
  :global(.app-card .table-region) { min-height: 0; max-height: 32rem; overflow: auto; }
  :global(.app-card table) { table-layout: auto; min-width: 100%; white-space: normal; }
  :global(.app-card td button) { white-space: normal; overflow-wrap: anywhere; max-width: 100%; }
  :global(.app-card td), :global(.app-card th) { padding: .5rem; text-align: left; border-bottom: 1px solid #8884; max-width: 30rem; overflow-wrap: anywhere; }
  :global(.app-card svg) { max-width: 800px; width: 100%; }
  .app-error { padding: 1rem; border: 2px solid #c55; }
</style>
