<script lang="ts">
  import type { AnalysisQuery, DistanceMode, JobAnalysisResponse } from '../contracts';
  import { selection } from './controller';
  import { cellText, columnLabel, exactTitle } from '../display';
  let { page, busy, instanceId, implementations, references, analyze, inspect }:
    { page: JobAnalysisResponse; busy: boolean; instanceId: string; implementations: string[];
      references: { artifact_handle: string; display_name: string; revision: string }[];
      analyze: (query: AnalysisQuery) => void; inspect: (id: string) => void } = $props();
  let table = $state<AnalysisQuery['table']>('trials');
  let baseline = $state('visited-scan-v1');
  let mode = $state<DistanceMode>('on_demand');
  let metric = $state<'search_time_s' | 'end_to_end_time_s'>('end_to_end_time_s');
  let target = $state(0);
  let reference = $state('');
  let full = $state(false);
  let job = $state('');
  $effect(() => {
    if (page.job_id !== job) {
      job = page.job_id; table = 'trials';
      const implementation = page.items.find(r => typeof r.implementation === 'string')?.implementation;
      if (typeof implementation === 'string') baseline = implementation;
      mode = page.items.some(r => r.distance_mode === 'on_demand') ? 'on_demand' : 'dense';
    }
  });
  let ratios = $derived(page.table === 'comparisons' ? page.items.filter(r => typeof r.speedup === 'number') : []);
  let ratioMax = $derived(Math.max(1, ...ratios.map(r => Number(r.speedup))));
  const preferred: Record<string, string[]> = {
    trials: ['attempt_id', 'implementation', 'distance_mode', 'repetition', 'outcome', 'best_cost', 'search_time_s', 'end_to_end_time_s', 'campaign_complete', 'duplicate_execution', 'usable', 'native_verified'],
    summary: ['implementation', 'distance_mode', 'attempt_count', 'failed_count', 'incomplete_count', 'duplicate_count', 'usable_count', 'best_cost_median', 'search_time_s_median', 'end_to_end_time_s_median'],
    comparisons: ['attempt_id', 'baseline_attempt_id', 'metric', 'speedup', 'reason'],
    progress: ['attempt_id', 'logical_id', 'elapsed_s', 'search_elapsed_s', 'best_cost', 'trial_best_cost', 'usable'],
  };
  let columns = $derived(full ? page.columns : (preferred[page.table] ?? page.columns).filter(c => page.columns.includes(c)));
  let points = $derived(page.table === 'progress' ? page.items.filter(row => typeof row.search_elapsed_s === 'number' && typeof row.best_cost === 'number') : []);
  let xmax = $derived(Math.max(1e-12, ...points.map(r => Number(r.search_elapsed_s))));
  let ymin = $derived(Math.min(...points.map(r => Number(r.best_cost))));
  let ymax = $derived(Math.max(...points.map(r => Number(r.best_cost))));
  const text = (v: unknown) => v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
  function load() {
    let query: AnalysisQuery;
    switch (table) {
      case 'comparisons': query = { ...selection, table, baseline_implementation: baseline, baseline_distance_mode: mode, metric }; break;
      case 'progress': query = { ...selection, table, max_points: 100000 }; break;
      case 'targets': query = { ...selection, table, instance_id: instanceId, target_cost: target, max_points: 100000 }; break;
      case 'references': query = { ...selection, table, reference_handle: reference, artifact_catalog_revision: references.find(r => r.artifact_handle === reference)?.revision ?? '' }; break;
      case 'facets': query = { ...selection, table, field: 'implementation' }; break;
      default: query = { ...selection, table };
    }
    analyze(query);
  }
  function download() {
    const blob = new Blob([JSON.stringify(page, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = `${page.table}-page.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
</script>
<section class="app-card" aria-label="Experiment analysis">
  <h2>4. Analyze results</h2>
  <p>Shared Python analysis. Failures, duplicates and incomplete attempts remain visible; filtering does not repair exclusions.</p>
  <form onsubmit={e => { e.preventDefault(); load(); }}>
    <fieldset disabled={busy} class="form-grid">
      <label>Analysis table<select bind:value={table}>
        <option value="trials">Trials and timings</option><option value="summary">Summary by configuration</option>
        <option value="comparisons">Paired comparison</option><option value="progress">Recorded history</option>
        <option value="targets">Observed time to target</option><option value="references">Reference gaps</option>
        <option value="diagnostics">Diagnostics</option><option value="facets">Implementation counts</option>
      </select></label>
      {#if table === 'comparisons'}
        <label>Baseline implementation<select bind:value={baseline}>{#each implementations as id}<option value={id}>{id}</option>{/each}</select></label>
        <label>Baseline distance mode<select bind:value={mode}><option value="on_demand">On-demand</option><option value="dense">Dense</option></select></label>
        <label>Timing metric<select bind:value={metric}><option value="end_to_end_time_s">End-to-end trial time</option><option value="search_time_s">Search only</option></select></label>
      {/if}
      {#if table === 'targets'}<label>Target cost<input type="number" min="0" step="any" required bind:value={target} /></label>{/if}
      {#if table === 'references'}<label>Reference JSON<select bind:value={reference} required><option value="">Select authorized reference</option>{#each references as ref}<option value={ref.artifact_handle}>{ref.display_name}</option>{/each}</select></label>{/if}
      <button>Show analysis</button>
    </fieldset>
  </form>
  {#if page.table === 'comparisons'}<p>Ratio = baseline time / target time. Above 1 means the target was faster. Missing ratios retain rejection reasons. Timed runs are not speed comparisons; local observations are not controlled performance claims.</p>{/if}
  {#if page.table === 'progress' || page.table === 'targets'}
    <p>Clock: {page.clock}. Search clocks exclude setup/validation/persistence. Targets are first observed crossings, not exact first hitting times.</p>
    <p>History: {page.history_coverage.recorded_attempts} recorded, {page.history_coverage.missing_attempts} missing; {page.history_coverage.points} total points. Empty recorded history is not missing history.</p>
  {/if}
  {#if ratios.length}
    <figure><svg viewBox={`0 0 640 ${40 + 28 * ratios.length}`} role="img" aria-label="Server-calculated paired time ratios on this page">
      {#each ratios as row, i}<rect x="150" y={i * 28 + 8} width={440 * (Number(row.speedup) / ratioMax)} height="18" fill="currentColor"><title>{text(row.attempt_id)}: {text(row.speedup)}</title></rect><text x="5" y={i * 28 + 22} fill="currentColor">Pair {i + 1}: {Number(row.speedup).toPrecision(4)}</text>{/each}
      <path d={`M${150 + 440 / ratioMax} 0V${28 * ratios.length + 8}`} stroke="currentColor" stroke-dasharray="3 3" />
    </svg><figcaption>Baseline / target duration; dashed line = 1. Exact pair identities and rejected comparisons appear below.</figcaption></figure>
  {/if}
  {#if points.length}
    <figure><svg viewBox="0 0 640 220" role="img" aria-label="Recorded cost observations on this page, without interpolation">
      <path d="M40 10V190H630" fill="none" stroke="currentColor" />
      {#each points as point}<circle cx={40 + 580 * (Number(point.search_elapsed_s) / xmax)} cy={180 - 160 * ((Number(point.best_cost) - ymin) / (ymax - ymin || 1))} r="3" fill="currentColor"><title>{text(point.attempt_id)}: {text(point.best_cost)} at {text(point.search_elapsed_s)} s</title></circle>{/each}
      <text x="300" y="215" fill="currentColor">Concatenated search seconds</text>
    </svg><figcaption>Recorded observations on this page only; no connecting lines or interpolation. Exact values and attempt IDs are in the table.</figcaption></figure>
  {/if}
  <div class="source-actions"><strong>{page.table}: {page.total_rows} rows · displaying {page.items.length}</strong>
    <label><input type="checkbox" bind:checked={full} /> Full values and all columns</label><button onclick={download}>Download this page (JSON)</button>
  </div>
  <div class="table-region"><table><thead><tr>{#each columns as column}<th title={column}>{full ? column : columnLabel(column)}</th>{/each}</tr></thead><tbody>
    {#each page.items as row}<tr>{#each columns as column}<td title={exactTitle(column, row[column])}>{#if column === 'attempt_id' && typeof row[column] === 'string'}<button aria-label={String(row[column])} disabled={busy} onclick={() => inspect(String(row[column]))}>{full ? text(row[column]) : cellText(column, row[column])}</button>{:else}{full ? text(row[column]) : cellText(column, row[column])}{/if}</td>{/each}</tr>{:else}<tr><td colspan={columns.length || 1}>No rows for this selection.</td></tr>{/each}
  </tbody></table></div>
</section>
