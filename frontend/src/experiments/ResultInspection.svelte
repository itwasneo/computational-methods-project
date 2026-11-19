<script lang="ts">
  import { tick } from 'svelte';
  import type { ExperimentController, ExperimentState } from './controller';
  import type { Theme } from '../theme';
  import { decodeGeometry } from './geometry';
  import { download } from '../download';
  import { reveal } from '../focus';
  import InstanceInspection from '../InstanceInspection.svelte';
  import TableRegion from '../components/TableRegion.svelte';
  import OperationStatus from '../components/OperationStatus.svelte';
  let {
    view,
    controller,
    theme,
    active,
  }: {
    view: ExperimentState;
    controller: ExperimentController;
    theme: Theme;
    active: boolean;
  } = $props();
  let geometry = $derived(view.geometry);
  // This narrow identity boundary is essential: unrelated view snapshots must not decode/rebuild the scene.
  let inspection = $derived.by(() => {
    try {
      return geometry ? { data: decodeGeometry(geometry.geometry), error: '' } : null;
    } catch (error) {
      return { data: null, error: error instanceof Error ? error.message : 'Invalid geometry' };
    }
  });
  let panel = $state<HTMLDivElement>(),
    heading = $state<HTMLHeadingElement>();
  let viewer = $state<InstanceInspection>();
  let height = $state(0),
    cityPage = $state(0),
    requested = $state('');
  export async function inspect(id: string, moveFocus = true) {
    const trigger = document.activeElement;
    height = panel?.getBoundingClientRect().height ?? 0;
    cityPage = 0;
    requested = id;
    const result = await controller.inspect(id);
    if (requested !== id) return result;
    height = 0;
    await tick();
    if (result.ok) {
      if (
        moveFocus &&
        active &&
        (document.activeElement === trigger || document.activeElement === document.body)
      )
        reveal(heading);
    }
    return result;
  }
</script>

<div
  class="result-slot"
  bind:this={panel}
  style:min-height={`${height}px`}
  aria-busy={!!view.operations.inspection.busy}
>
  <OperationStatus
    operation={view.operations.inspection}
    retry={requested ? () => inspect(requested) : undefined}
  />
  {#if view.details}
    <section class="app-card" aria-labelledby="result-heading">
      <h2 id="result-heading" bind:this={heading} tabindex="-1">Verified final result</h2>
      <p>Attempt: {view.details.details.attempt_id}</p>
      <p>
        Cost: {view.details.details.result.verified_cost ?? 'No final solution'} · Native verified: {String(
          view.details.details.native_verified,
        )} · Usable: {String(view.details.details.row.usable)}
      </p>
      <p>
        Campaign complete: {String(view.details.details.row.campaign_complete)} · Duplicate: {String(
          view.details.details.row.duplicate_execution,
        )}
      </p>
      {#if inspection?.error}<p role="alert">{inspection.error}</p>
      {:else if inspection?.data}
        {#key view.details.details.geometry_revision}<InstanceInspection
            bind:this={viewer}
            inspection={inspection.data}
            {theme}
          />{/key}
      {/if}
      {#if !geometry?.geometry.tour}<p>
          No aggregate final tour. Coordinates are not a fabricated solution.
        </p>{/if}
      {#if inspection?.data}
        <details>
          <summary>Accessible coordinates and final permutation</summary>
          <TableRegion label="Instance coordinates">
            <table>
              <thead>
                <tr>
                  <th scope="col">City ID</th>
                  <th scope="col">X</th>
                  <th scope="col">Y</th>
                </tr>
              </thead>
              <tbody>
                {#each Array.from({ length: Math.max(0, Math.min(100, inspection.data.coordinates.length / 2 - cityPage * 100)) }, (_, i) => cityPage * 100 + i) as city (city)}
                  <tr>
                    <td>
                      <button
                        aria-label={`Select city ${city}`}
                        onclick={() => viewer?.selectCity(city)}
                      >
                        {city}
                      </button>
                    </td>
                    <td class="numeric">
                      {Object.is(inspection.data.coordinates[city * 2], -0)
                        ? '-0'
                        : inspection.data.coordinates[city * 2]}
                    </td>
                    <td class="numeric">
                      {Object.is(inspection.data.coordinates[city * 2 + 1], -0)
                        ? '-0'
                        : inspection.data.coordinates[city * 2 + 1]}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </TableRegion>
          <button disabled={cityPage === 0} onclick={() => cityPage--}>Previous cities</button>
          <button
            disabled={(cityPage + 1) * 100 >= inspection.data.coordinates.length / 2}
            onclick={() => cityPage++}
          >
            Next cities
          </button>
          <p>Final city permutation (implicit closure):</p>
          <pre>{inspection.data.route
              ? Array.from(inspection.data.route).join(', ')
              : 'No final tour'}</pre>
        </details>
        <button
          onclick={() =>
            download(
              new Blob([JSON.stringify(geometry, null, 2)], { type: 'application/json' }),
              'verified-geometry.json',
            )}
        >
          Download verified geometry
        </button>
      {/if}
      <details>
        <summary>Configuration, logical results and timings</summary>
        <pre>{JSON.stringify(view.details.details, null, 2)}</pre>
      </details>
    </section>
  {/if}
</div>

<style>
  .result-slot {
    display: flow-root;
  }
</style>
