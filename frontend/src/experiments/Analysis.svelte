<script lang="ts">
  import { untrack } from 'svelte';
  import type {
    AnalysisQuery,
    DistanceMode,
    JobAnalysisResponse,
    ScientificConfiguration,
  } from '../contracts';
  import { selection } from './controller';
  import { queryIdentity } from '../analysis-query';
  import { cellText, columnLabel, exactTitle, compactId, rawText, actionLabel } from '../display';
  import { preferredColumns } from '../columns';
  import { download } from '../download';
  import TableRegion from '../components/TableRegion.svelte';
  let {
    page,
    applied,
    configuration,
    busy,
    inspecting,
    instanceId,
    implementations,
    references,
    analyze,
    inspect,
  }: {
    page: JobAnalysisResponse | null;
    applied: AnalysisQuery | null;
    configuration: ScientificConfiguration;
    busy: boolean;
    inspecting: boolean;
    instanceId: string;
    implementations: string[];
    references: { artifact_handle: string; display_name: string; revision: string }[];
    analyze: (query: AnalysisQuery) => unknown;
    inspect: (id: string) => unknown;
  } = $props();
  let table = $state<AnalysisQuery['table']>(untrack(() => applied?.table ?? 'trials'));
  let baseline = $state(
    untrack(() => configuration.implementations.find((id) => id !== null) ?? 'visited-scan-v1'),
  );
  let mode = $state<DistanceMode>(
    untrack(() =>
      configuration.distance_modes.includes('on_demand')
        ? 'on_demand'
        : configuration.distance_modes[0],
    ),
  );
  let metric = $state<'search_time_s' | 'end_to_end_time_s'>('end_to_end_time_s');
  let target = $state(0),
    reference = $state(''),
    full = $state(false),
    allColumns = $state(false);
  let referenceRevision = $state('');
  let baselineOptions = $derived([
    ...new Set([
      ...configuration.implementations.filter((id): id is string => id !== null),
      ...implementations,
    ]),
  ]);
  let columns = $derived(
    page
      ? allColumns
        ? page.columns
        : (preferredColumns[page.table] ?? page.columns).filter((c) => page.columns.includes(c))
      : [],
  );
  let ratios = $derived(
    page?.table === 'comparisons' ? page.items.filter((r) => typeof r.speedup === 'number') : [],
  );
  let ratioMax = $derived(Math.max(1, ...ratios.map((r) => Number(r.speedup))));
  let points = $derived(
    page?.table === 'progress'
      ? page.items.filter(
          (r) => typeof r.search_elapsed_s === 'number' && typeof r.best_cost === 'number',
        )
      : [],
  );
  let attempts = $derived([...new Set(points.map((row) => String(row.attempt_id)))]);
  let xmax = $derived(Math.max(1e-12, ...points.map((r) => Number(r.search_elapsed_s))));
  let ymin = $derived(Math.min(...points.map((r) => Number(r.best_cost))));
  let ymax = $derived(Math.max(...points.map((r) => Number(r.best_cost))));
  const chartNumber = (value: number) => {
    const [coefficient, exponent] = value.toPrecision(4).split('e');
    const compact = coefficient.includes('.') ? coefficient.replace(/\.?0+$/, '') : coefficient;
    return exponent ? `${compact}e${exponent}` : compact;
  };
  const series = (attempt: unknown) => `var(--series-${attempts.indexOf(String(attempt)) % 4})`;
  function query(): AnalysisQuery {
    switch (table) {
      case 'comparisons':
        return {
          ...selection,
          table,
          baseline_implementation: baseline,
          baseline_distance_mode: mode,
          metric,
        };
      case 'progress':
        return { ...selection, table, max_points: 100000 };
      case 'targets':
        return {
          ...selection,
          table,
          instance_id: instanceId,
          target_cost: target,
          max_points: 100000,
        };
      case 'references':
        return {
          ...selection,
          table,
          reference_handle: reference,
          artifact_catalog_revision: referenceRevision,
        };
      case 'facets':
        return { ...selection, table, field: 'implementation' };
      default:
        return { ...selection, table };
    }
  }
  export function restore(query: AnalysisQuery) {
    table = query.table;
    if (query.table === 'comparisons') {
      baseline = query.baseline_implementation;
      mode = query.baseline_distance_mode ?? 'on_demand';
      metric = query.metric;
    }
    if (query.table === 'targets') target = query.target_cost;
    if (query.table === 'references') {
      reference = query.reference_handle;
      referenceRevision = query.artifact_catalog_revision;
    }
  }
  let draftChanged = $derived(!!applied && queryIdentity(applied) !== queryIdentity(query()));
  function rowKey(row: Record<string, unknown>, index: number) {
    if (page?.table === 'trials') return String(row.attempt_id);
    if (page?.table === 'progress')
      return JSON.stringify([row.attempt_id, row.logical_index, row.sample_index]);
    return `${page?.query_key}:${index}`;
  }
</script>

<section class="app-card" aria-label="Experiment analysis">
  <h2>4. Analyze results</h2>
  <p>
    Shared Python analysis. Failures, duplicates and incomplete attempts remain visible; filtering
    does not repair exclusions.
  </p>
  <form
    onsubmit={(event) => {
      event.preventDefault();
      void analyze(query());
    }}
  >
    <fieldset disabled={busy} class="form-grid">
      <label>
        Analysis table
        <select bind:value={table}>
          <option value="trials">Trials and timings</option>
          <option value="summary">Summary by configuration</option>
          <option value="comparisons">Paired comparison</option>
          <option value="progress">Recorded history</option>
          <option value="targets">Observed time to target</option>
          <option value="references">Reference gaps</option>
          <option value="diagnostics">Diagnostics</option>
          <option value="facets">Implementation counts</option>
        </select>
      </label>
      {#if table === 'comparisons'}
        <label>
          Baseline implementation
          <select bind:value={baseline}>
            {#each baselineOptions as id (id)}<option value={id}>{id}</option>{/each}
          </select>
        </label>
        <label>
          Baseline distance mode
          <select bind:value={mode}>
            <option value="on_demand">On-demand</option>
            <option value="dense">Dense</option>
          </select>
        </label>
        <label>
          Timing metric
          <select bind:value={metric}>
            <option value="end_to_end_time_s">End-to-end trial time</option>
            <option value="search_time_s">Search only</option>
          </select>
        </label>
      {/if}
      {#if table === 'targets'}<label>
          Target cost
          <input type="number" min="0" step="any" required bind:value={target} />
        </label>{/if}
      {#if table === 'references'}<label>
          Reference JSON
          <select
            value={references.some(
              (r) => r.artifact_handle === reference && r.revision === referenceRevision,
            )
              ? reference
              : ''}
            onchange={(event) => {
              reference = event.currentTarget.value;
              referenceRevision =
                references.find((r) => r.artifact_handle === reference)?.revision ?? '';
            }}
            required
          >
            <option value="">Select authorized reference</option>
            {#each references as ref (ref.artifact_handle)}<option value={ref.artifact_handle}>
                {ref.display_name}
              </option>{/each}
          </select>
        </label>{/if}
      <button>Show analysis</button>
    </fieldset>
  </form>
  {#if draftChanged}<p role="status">
      Draft changes are not applied yet. The table and page controls still use the last applied
      query.
    </p>{/if}
  {#if page}
    {#if page.table === 'comparisons'}<p>
        Ratio = baseline time / target time. Above 1 means the target was faster. Missing ratios
        retain rejection reasons. Timed runs are not speed comparisons; local observations are not
        controlled performance claims.
      </p>{/if}
    {#if page.table === 'progress' || page.table === 'targets'}
      <p>
        Clock: {page.clock}. Search clocks exclude setup/validation/persistence. Targets are first
        observed crossings, not exact first hitting times.
      </p>
      <p>
        History: {page.history_coverage.recorded_attempts} recorded, {page.history_coverage
          .missing_attempts} missing; {page.history_coverage.points} total points. Empty recorded history
        is not missing history.
      </p>
    {/if}
    {#if ratios.length}
      <figure>
        <svg
          class="analysis-chart"
          viewBox={`0 0 680 ${50 + 28 * ratios.length}`}
          role="img"
          aria-label="Server-calculated paired time ratios on this page"
        >
          {#each ratios as row, i (`${row.attempt_id}:${row.baseline_attempt_id}:${i}`)}
            <rect
              x="180"
              y={i * 28 + 8}
              width={440 * (Number(row.speedup) / ratioMax)}
              height="18"
              fill="currentColor"
            >
              <title>
                {rawText(row.attempt_id)} / baseline {rawText(row.baseline_attempt_id)}: {rawText(
                  row.speedup,
                )}
              </title>
            </rect>
            <text x="5" y={i * 28 + 22} fill="currentColor">
              {compactId(String(row.attempt_id))}
            </text>
          {/each}
          <path
            d={`M${180 + 440 / ratioMax} 0V${28 * ratios.length + 8}`}
            stroke="currentColor"
            stroke-dasharray="3 3"
          />
          <text x="180" y={28 * ratios.length + 30} fill="currentColor">0</text>
          <text x="620" y={28 * ratios.length + 30} text-anchor="end" fill="currentColor">
            {chartNumber(ratioMax)}×
          </text>
        </svg>
        <figcaption>
          Baseline / target duration; dashed line = 1. Rows identify target attempts; exact pair
          identities, ratios and rejections appear below.
        </figcaption>
      </figure>
    {/if}
    {#if points.length}
      <figure>
        <svg
          class="analysis-chart"
          viewBox="0 0 680 240"
          role="img"
          aria-label="Recorded cost observations on this page, without interpolation"
        >
          <path d="M90 10V190H650" fill="none" stroke="currentColor" />
          {#each ymin === ymax ? [0] : [0, 0.5, 1] as tick (tick)}<text
              x="80"
              y={185 - 160 * tick}
              text-anchor="end"
              fill="currentColor"
            >
              {chartNumber(ymin + (ymax - ymin) * tick)}
            </text>{/each}
          {#each [0, 0.5, 1] as tick (tick)}<text
              x={90 + 550 * tick}
              y="210"
              text-anchor="middle"
              fill="currentColor"
            >
              {chartNumber(xmax * tick)}
            </text>{/each}
          {#each points as point (`${point.attempt_id}:${point.logical_index}:${point.sample_index}`)}
            <circle
              cx={90 + 550 * (Number(point.search_elapsed_s) / xmax)}
              cy={180 - 160 * ((Number(point.best_cost) - ymin) / (ymax - ymin || 1))}
              r="3"
              fill={series(point.attempt_id)}
            >
              <title>
                {rawText(point.attempt_id)}: {rawText(point.best_cost)} at {rawText(
                  point.search_elapsed_s,
                )} s
              </title>
            </circle>
          {/each}
          <text x="300" y="235" fill="currentColor">Concatenated search seconds</text>
        </svg>
        <ul class="chart-legend" aria-label="History attempts">
          {#each attempts as attempt (attempt)}<li>
              <span style:background={series(attempt)} aria-hidden="true"></span>
              <button
                title={attempt}
                aria-label={actionLabel('Inspect attempt', attempt)}
                disabled={inspecting}
                onclick={() => inspect(attempt)}
              >
                {compactId(attempt)}
              </button>
            </li>{/each}
        </ul>
        <figcaption>
          Vertical axis: cost. Observations on this page only; no interpolation. Colors may repeat;
          exact attempt identities and values are in the table.
        </figcaption>
      </figure>
    {/if}
    <div class="source-actions">
      <strong>{page.table}: {page.total_rows} rows · displaying {page.items.length}</strong>
      <label>
        <input type="checkbox" bind:checked={full} />
        Full-precision values
      </label>
      <label>
        <input type="checkbox" bind:checked={allColumns} />
        All columns
      </label>
      <button
        onclick={() =>
          download(
            new Blob([JSON.stringify(page, null, 2)], { type: 'application/json' }),
            `${page!.table}-page.json`,
          )}
      >
        Download this page (JSON)
      </button>
    </div>
    <TableRegion label={`${page.table} analysis results`} {busy}>
      <table>
        <caption class="sr-only">
          {page.table} analysis from the shared backend. Inspect an attempt to verify its final result.
        </caption>
        <thead>
          <tr>
            {#each columns as column (column)}<th scope="col" title={column}>
                {full ? column : columnLabel(column)}
              </th>{/each}
          </tr>
        </thead>
        <tbody>
          {#each page.items as row, i (rowKey(row, i))}<tr>
              {#each columns as column (column)}
                <td
                  class:numeric={typeof row[column] === 'number'}
                  title={exactTitle(column, row[column])}
                >
                  {#if column === 'attempt_id' && typeof row[column] === 'string'}<button
                      aria-label={actionLabel('Inspect attempt', String(row[column]))}
                      disabled={inspecting}
                      onclick={() => inspect(String(row[column]))}
                    >
                      {full ? rawText(row[column]) : cellText(column, row[column])}
                    </button>
                  {:else}{full ? rawText(row[column]) : cellText(column, row[column])}{/if}
                </td>
              {/each}
            </tr>{:else}<tr>
              <td colspan={columns.length || 1}>No rows for this selection.</td>
            </tr>{/each}
        </tbody>
      </table>
    </TableRegion>
  {:else}<p>
      No current analysis page. Your query controls are retained; use Show analysis to retry.
    </p>{/if}
</section>

<style>
  .analysis-chart {
    width: 100%;
    max-width: 900px;
    font-size: 12px;
  }
  .chart-legend {
    display: flex;
    flex-wrap: wrap;
    list-style: none;
    padding: 0;
    gap: 1rem;
  }
  .chart-legend li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .chart-legend span {
    width: 0.8rem;
    height: 0.8rem;
    border: 1px solid currentColor;
  }
</style>
