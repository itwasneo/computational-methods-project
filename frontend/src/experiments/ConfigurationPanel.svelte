<script lang="ts">
  import { tick, untrack } from 'svelte';
  import { configurationDraftStore } from '../drafts';
  import type { ExperimentController, ExperimentState } from './controller';
  import type { ScientificConfiguration, DistanceMode } from '../contracts';
  import OperationStatus from '../components/OperationStatus.svelte';
  import { reveal } from '../focus';
  let {
    view,
    controller,
    submit,
  }: { view: ExperimentState; controller: ExperimentController; submit: () => unknown } = $props();
  const store = configurationDraftStore(
    untrack(() => view.capabilities?.output_root?.root_id ?? ''),
  );
  const saved = store.read();
  const availableImplementations = () =>
    view.capabilities?.implementations
      .filter((i) => i.algorithm === 'nn' && i.available === true)
      .map((i) => String(i.implementation)) ?? [];
  const initialChoices = untrack(availableImplementations);
  const missingSaved = saved?.implementations.some((id) => !initialChoices.includes(id));
  let implementations = $state(
      (
        saved?.implementations ?? [
          initialChoices.includes('visited-scan-v1') ? 'visited-scan-v1' : initialChoices[0],
        ]
      ).filter((id): id is string => typeof id === 'string' && initialChoices.includes(id)),
    ),
    modes = $state<DistanceMode[]>(saved?.modes ?? ['dense', 'on_demand']);
  let starts = $state(saved?.starts ?? '0,1,2,3,4,5,6,7'),
    repetitions = $state(saved?.repetitions ?? 5),
    solverSeed = $state(saved?.solverSeed ?? '123');
  let timed = $state(saved?.timed ?? false),
    seconds = $state(saved?.seconds ?? 1),
    history = $state(saved?.history ?? false),
    interval = $state(saved?.interval ?? 0.1),
    points = $state(saved?.points ?? 4096);
  let inputError = $state(''),
    original = $state.raw<ScientificConfiguration | null>(saved?.original ?? null),
    originalSnapshot = $state(saved?.originalSnapshot ?? '');
  function saveDraft() {
    store.save({
      implementations,
      modes,
      starts,
      repetitions,
      solverSeed,
      timed,
      seconds,
      history,
      interval,
      points,
      original,
      originalSnapshot,
      snapshot: view.snapshot?.instance_snapshot_id ?? saved?.snapshot ?? null,
    });
  }
  function edited() {
    controller.invalidateReview();
    saveDraft();
  }
  let heading = $state<HTMLHeadingElement>();
  let remaining = $state(0);
  let available = $derived(availableImplementations());
  let blocked = $derived(
    !!view.pending ||
      view.invalidRetry ||
      !!view.operations.instance.busy ||
      !!view.operations.review.busy ||
      !!view.operations.submission.busy,
  );
  $effect(() => {
    const expires = view.reviewExpiresAt;
    if (!expires) {
      remaining = 0;
      return;
    }
    const update = () => (remaining = Math.max(0, Math.ceil((expires - Date.now()) / 1000)));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  });
  export async function restore(configuration: ScientificConfiguration) {
    original = configuration;
    originalSnapshot = view.snapshot?.instance_snapshot_id ?? '';
    implementations = configuration.implementations.filter((id): id is string => id !== null);
    modes = [...configuration.distance_modes];
    starts = (configuration.budget.start_cities as number[]).join(',');
    repetitions = configuration.repetitions.length;
    solverSeed = configuration.solver_seed;
    timed = configuration.budget.time_limit_s !== undefined;
    seconds = Number(configuration.budget.time_limit_s ?? 1);
    history = configuration.record_history;
    interval = configuration.history_policy?.interval_s ?? 0.1;
    points = configuration.history_policy?.max_points ?? 4096;
    inputError = '';
    saveDraft();
    await tick();
    reveal(heading);
  }
  async function review(event: SubmitEvent) {
    event.preventDefault();
    inputError = '';
    saveDraft();
    try {
      const tokens = starts.trim() ? starts.split(',').map((v) => v.trim()) : [];
      if (tokens.some((v) => !/^(0|[1-9][0-9]*)$/.test(v)))
        throw new Error('Starts must be comma-separated nonnegative integer city IDs.');
      const start_cities = tokens.map(Number);
      if (
        start_cities.some(
          (v) => !Number.isSafeInteger(v) || v >= (view.snapshot?.instance.n_cities ?? 0),
        ) ||
        new Set(start_cities).size !== start_cities.length
      )
        throw new Error('Starts must be distinct and within the instance.');
      if (
        !Number.isSafeInteger(repetitions) ||
        repetitions < 1 ||
        repetitions > (view.capabilities?.limits.max_repetitions ?? 32)
      )
        throw new Error('Invalid repetition count.');
      if (
        !implementations.length ||
        implementations.some((i) => !available.includes(i)) ||
        !modes.length
      )
        throw new Error('Select available implementations and at least one distance mode.');
      const base = originalSnapshot === view.snapshot?.instance_snapshot_id ? original : null;
      const sameStarts =
        base && JSON.stringify(base.budget.start_cities) === JSON.stringify(start_cities);
      const configuration: ScientificConfiguration = {
        algorithm: 'nn',
        parameters: base?.parameters ?? {},
        budget: { start_cities, ...(timed ? { time_limit_s: seconds } : {}) },
        logical_search_ids: sameStarts ? base.logical_search_ids : null,
        implementations,
        distance_modes: modes,
        repetitions:
          base?.repetitions.length === repetitions
            ? base.repetitions
            : Array.from({ length: repetitions }, (_, i) => i),
        solver_seed: solverSeed,
        max_matrix_bytes: base?.max_matrix_bytes ?? view.capabilities?.limits.max_matrix_bytes ?? 0,
        record_history: history,
        history_policy: history ? { interval_s: interval, max_points: points } : null,
      };
      const result = await controller.review(configuration);
      if (result.ok) {
        await tick();
        reveal(heading);
      }
    } catch (error) {
      inputError = error instanceof Error ? error.message : 'Invalid configuration';
    }
  }
</script>

<section class="app-card" aria-labelledby="configuration-heading">
  <h2 id="configuration-heading">2. Configure NN comparison</h2>
  {#if missingSaved}<p>
      Unavailable saved implementations were deselected. Choose an available implementation and
      review again.
    </p>{/if}
  {#if !available.length}<p>No native NN implementation is available.</p>
    <pre>{JSON.stringify(view.capabilities?.implementations, null, 2)}</pre>{/if}
  <p>
    Valid non-secret configuration drafts are remembered in this tab when storage is available.
    Pairing grants and reviewed plans are never saved.
  </p>
  {#if saved?.snapshot && !view.snapshot}<p>
      Restore the last configured input, or create/load a different instance. The snapshot is
      revalidated by this output root; you must review again.
    </p>
    <button disabled={blocked} onclick={() => controller.restoreSnapshot(saved.snapshot!)}>
      Restore saved input snapshot
    </button>{/if}
  <form onsubmit={review} oninput={edited} onchange={edited}>
    <fieldset disabled={!view.snapshot || blocked || !available.length}>
      <div class="form-grid">
        <div>
          Implementations{#each available as id (id)}<label>
              <input type="checkbox" value={id} bind:group={implementations} />
              {id}
            </label>{/each}
        </div>
        <div>
          Distance modes
          <label>
            <input type="checkbox" value="dense" bind:group={modes} />
            Dense matrix
          </label>
          <label>
            <input type="checkbox" value="on_demand" bind:group={modes} />
            On-demand
          </label>
        </div>
        <label>
          Ordered start cities
          <input bind:value={starts} aria-describedby="starts-help" />
        </label>
        <label>
          Repetitions
          <input
            type="number"
            min="1"
            max={view.capabilities?.limits.max_repetitions}
            required
            bind:value={repetitions}
          />
        </label>
        <label>
          Solver seed (uint64)
          <input required pattern={'0|[1-9][0-9]{0,19}'} bind:value={solverSeed} />
        </label>
      </div>
      <p id="starts-help">
        Comma-separated city IDs. Both modes use the same input and ordered starts. Empty starts
        mean zero work. NN is deterministic.
      </p>
      <label>
        <input type="checkbox" bind:checked={timed} />
        Add a per-invocation time limit
      </label>
      {#if timed}<label>
          Seconds
          <input type="number" min="0.000001" step="any" required bind:value={seconds} />
        </label>
        <p>
          Timed runs compare quality under a budget, not speed ratios. Safe checkpoints can
          overshoot.
        </p>{/if}
      <label>
        <input type="checkbox" bind:checked={history} />
        Record best-so-far history
      </label>
      {#if history}<div class="form-grid">
          <label>
            Sampling interval (seconds)
            <input type="number" min="0" step="any" required bind:value={interval} />
          </label>
          <label>
            Maximum points per invocation
            <input type="number" min="2" max="100000" required bind:value={points} />
          </label>
        </div>{/if}
      <button>Review experiment</button>
    </fieldset>
  </form>
  {#if original && originalSnapshot === view.snapshot?.instance_snapshot_id}<p>
      Restored the original configuration. Existing repetition IDs and logical IDs are preserved
      while their corresponding count and starts remain unchanged. Changing them creates new IDs;
      inspect the exact reviewed plan.
    </p>{/if}
  {#if inputError}<p role="alert">{inputError}</p>{/if}
  <OperationStatus operation={view.operations.review} />
  {#if view.review}
    <section aria-label="Reviewed experiment">
      <h3 bind:this={heading} tabindex="-1">Review before execution</h3>
      <p>
        {view.review.trial_count} trials · {view.review.configuration.distance_modes.join(' / ')} · {view.review.configuration.implementations.join(
          ', ',
        )}
      </p>
      <p>
        Matrix estimate: {view.review.estimated_matrix_bytes} bytes. Output estimate: {view.review
          .estimated_output_bytes} bytes. Job ceiling: {view.review.job_time_ceiling_s} s.
      </p>
      <p>
        {remaining > 0
          ? `Review expires in ${remaining} seconds.`
          : 'Review expired. Review the configuration again before running.'} The server validates final
        eligibility.
      </p>
      <p>
        Fresh setup is charged per trial. Estimates are not total RSS guarantees. Nothing has
        executed yet.
      </p>
      <details>
        <summary>Exact reviewed plan, seeds and limits</summary>
        <pre>{JSON.stringify(view.review, null, 2)}</pre>
      </details>
      <button disabled={blocked || remaining <= 0} onclick={submit}>Run reviewed experiment</button>
    </section>
  {/if}
  <OperationStatus operation={view.operations.submission} />
</section>
