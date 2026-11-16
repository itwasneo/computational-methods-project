<script lang="ts">
  import type { ExperimentController, ExperimentState } from './controller';
  import OperationStatus from '../components/OperationStatus.svelte';
  import { download } from '../download';
  let { view, controller }: { view: ExperimentState; controller: ExperimentController } = $props();
  let generator = $state<'uniform-v1' | 'clustered-v1'>('uniform-v1');
  let cities = $state(256),
    side = $state(100),
    seed = $state('42'),
    clusters = $state(4),
    spread = $state(5);
  let artifact = $state(''),
    artifactRevision = $state('');
  let busy = $derived(
    !!view.operations.instance.busy ||
      !!view.operations.review.busy ||
      !!view.operations.submission.busy ||
      !!view.pending ||
      view.invalidRetry,
  );
  function generate(event: SubmitEvent) {
    event.preventDefault();
    void controller.generate(
      generator === 'uniform-v1'
        ? { generator, n_cities: cities, seed, side_length: side }
        : {
            generator,
            n_cities: cities,
            seed,
            side_length: side,
            n_clusters: clusters,
            standard_deviation: spread,
          },
    );
  }
</script>

<section class="app-card" aria-labelledby="instance-heading">
  <h2 id="instance-heading">1. Create or load an instance</h2>
  <form onsubmit={generate}>
    <fieldset class="form-grid" disabled={busy}>
      <label>
        Generator
        <select bind:value={generator}>
          <option value="uniform-v1">Uniform square</option>
          <option value="clustered-v1">Gaussian clusters</option>
        </select>
      </label>
      <label>
        Cities
        <input
          type="number"
          min="2"
          max={view.capabilities?.limits.max_cities}
          required
          bind:value={cities}
        />
      </label>
      <label>
        Instance seed (uint64)
        <input required pattern={'0|[1-9][0-9]{0,19}'} bind:value={seed} />
      </label>
      <label>
        Side length
        <input type="number" min="0.000000001" step="any" required bind:value={side} />
      </label>
      {#if generator === 'clustered-v1'}
        <label>
          Clusters
          <input type="number" min="1" max={cities} required bind:value={clusters} />
        </label>
        <label>
          Standard deviation
          <input type="number" min="0" step="any" required bind:value={spread} />
        </label>
      {/if}
      <button>Generate instance</button>
    </fieldset>
  </form>
  {#key view.catalogRevision}
    <div class="source-actions">
      <label>
        Or load saved instance
        <select
          value=""
          onchange={(event) => {
            artifact = event.currentTarget.value;
            artifactRevision = view.catalogRevision;
          }}
          disabled={view.catalogBusy}
        >
          <option value="">Select instance JSON</option>
          {#each view.artifacts.filter((a) => a.kind === 'json') as item (item.artifact_handle)}<option
              value={item.artifact_handle}
            >
              {item.display_name}
            </option>{/each}
        </select>
      </label>
      <button
        disabled={artifactRevision !== view.catalogRevision ||
          !view.artifacts.some((item) => item.artifact_handle === artifact) ||
          busy ||
          view.catalogBusy}
        onclick={() => controller.load(artifact)}
      >
        Load instance
      </button>
      <button disabled={view.catalogBusy} onclick={() => controller.refreshCatalog()}>
        Refresh files
      </button>
    </div>
  {/key}
  <p>
    JSON candidates are unvalidated; the loader checks your selection. Generation and execution
    seeds are separate.
  </p>
  <OperationStatus operation={view.operations.catalog} retry={() => controller.refreshCatalog()} />
  <OperationStatus operation={view.operations.instance} />
  {#if view.snapshot}
    <p role="status">
      Instance ready: {view.snapshot.instance.n_cities} cities · {view.snapshot.instance
        .distance_kind}
    </p>
    <button
      disabled={!!view.operations.export.busy}
      onclick={() => controller.exportInstance((blob) => download(blob, 'instance.json'))}
    >
      Download canonical instance
    </button>
    <details>
      <summary>Instance identity</summary>
      <pre>{JSON.stringify(view.snapshot.instance, null, 2)}</pre>
    </details>
  {/if}
  <OperationStatus operation={view.operations.export} />
</section>
