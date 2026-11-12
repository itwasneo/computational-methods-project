<script lang="ts">
  import { onDestroy } from 'svelte';
  import { initialState, ResultsController } from './controller';
  import { columns, filterValues } from './fields';
  import { PAIRING_PATTERN } from './client';
  import { PAGE_DEFAULT, SHARDS_MAX, type Field, type Sort } from '../contracts';

  let view = $state(initialState());
  const controller = new ResultsController(state => view = state);
  onDestroy(() => controller.dispose());
  let secret = $state('');
  let selected = $state<string[]>([]);
  let manifest = $state('');
  let selectedAttempt = $state<string | null>(null);
  let drafts = $state<Record<string, string>>({});
  let missing = $state<Record<string, boolean>>({});
  let filterError = $state('');
  let setupError = $state('');

  async function connect(event: SubmitEvent) {
    event.preventDefault();
    const credential = secret;
    selected = []; manifest = ''; drafts = {}; missing = {}; selectedAttempt = null;
    secret = ''; // Clear the password input before the asynchronous exchange.
    await controller.connect(credential);
  }
  function selectionChanged() {
    selectedAttempt = null; drafts = {}; missing = {}; filterError = ''; setupError = '';
    controller.selectionChanged();
  }
  async function refresh() {
    selected = []; manifest = ''; selectedAttempt = null; drafts = {}; missing = {};
    filterError = ''; setupError = '';
    await controller.refresh();
  }
  async function load() {
    setupError = '';
    if (selected.length < 1 || selected.length > SHARDS_MAX) { setupError = `Select between 1 and ${SHARDS_MAX} result shards.`; return; }
    selectedAttempt = null; drafts = {}; missing = {}; filterError = '';
    await controller.load(selected, manifest || null);
  }
  async function applyFilter(field: Field) {
    filterError = ''; selectedAttempt = null;
    try {
      const column = filterValues(field, drafts[field] ?? '', missing[field] ?? false);
      const filters = view.filters.filter(filter => filter.field !== field);
      if (column.values.length) filters.push(column);
      await controller.query(filters, view.sort);
    } catch (error) { filterError = error instanceof Error ? error.message : 'Invalid filter values.'; }
  }
  function sortBy(sort: Sort) { selectedAttempt = null; void controller.query(view.filters, sort); }
  function resetColumn(field: Field) {
    drafts[field] = ''; missing[field] = false; filterError = ''; selectedAttempt = null;
    void controller.query(view.filters.filter(filter => filter.field !== field), view.sort?.field === field ? null : view.sort);
  }
  function resetAll() {
    drafts = {}; missing = {}; filterError = ''; selectedAttempt = null; void controller.query([], null);
  }
  function positionFilter(event: MouseEvent, field: Field) {
    const panel = document.getElementById(`live-filter-${field}`);
    if (!panel) return;
    const rect = (event.currentTarget as HTMLButtonElement).getBoundingClientRect();
    panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 280))}px`;
    panel.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 360))}px`;
  }
</script>

<main>
  <h1>Local experiment results</h1>
  <p class="notice">Read-only results from your selected workspace. No solver execution or real-data geometry yet.</p>
  {#if !view.paired}
    <form class="connection-panel" onsubmit={connect}>
      <p>Start the local backend on port 8000, then enter the same random secret supplied at its startup prompt.</p>
      <label>Session pairing secret
        <input type="password" bind:value={secret} required minlength="43" maxlength="128" pattern={PAIRING_PATTERN}
          autocomplete="off" spellcheck="false" disabled={!!view.busy} />
      </label>
      <button disabled={!!view.busy}>Pair with local backend</button>
      <p class="notice">Credentials are kept only in memory. Reloading or disconnecting requires restarting the backend before pairing again.</p>
    </form>
  {:else}
    <section class="connection-panel" aria-label="Workspace sources">
      <div class="source-actions">
        <strong>Workspace: {view.workspace?.display_name ?? 'Loading'}</strong>
        <button disabled={!!view.busy} onclick={refresh}>Refresh catalog</button>
        <button onclick={() => { controller.disconnect(); selected = []; manifest = ''; secret = ''; }}>Disconnect</button>
      </div>
      <fieldset disabled={!!view.busy || !view.catalogRevision}>
        <legend>Result shards (1–{SHARDS_MAX})</legend>
        <div class="candidate-list">
          {#each view.artifacts.filter(item => item.kind === 'result_shard') as item (item.artifact_handle)}
            <label><input type="checkbox" value={item.artifact_handle} bind:group={selected} onchange={selectionChanged} /> {item.display_name}</label>
          {:else}
            <p>No JSONL candidates found. Choose a workspace containing your result files, or refresh after adding them.</p>
          {/each}
        </div>
        <label>Expected manifest (optional)
          <select bind:value={manifest} onchange={selectionChanged}>
            <option value="">None — wholly missing executions cannot be inferred</option>
            {#each view.artifacts.filter(item => item.kind === 'json') as item (item.artifact_handle)}
              <option value={item.artifact_handle}>{item.display_name}</option>
            {/each}
          </select>
        </label>
        <p class="notice">Candidates are unvalidated. Select a manifest JSON, not an instance or reference, if checking expected executions.</p>
        <button type="button" onclick={load} disabled={selected.length < 1 || selected.length > SHARDS_MAX}>Load {selected.length} selected shard(s)</button>
      </fieldset>
    </section>
  {/if}

  {#if setupError}<p role="alert" class="failed">{setupError}</p>{/if}
  {#if view.busy}<p role="status">{view.busy}…</p>{/if}
  {#if view.error}
    <div role="alert" class="error-panel">
      <p>{view.error}</p>
      {#if view.errorCode === 'unauthorized'}<p>Restart the backend before pairing again.</p>{/if}
      {#if view.paired && view.dataset?.state === 'ready'}
        <button disabled={!!view.busy} onclick={() => controller.query()}>Retry first page</button>
      {/if}
    </div>
  {/if}
  {#if view.dataset}
    <section class="dataset-status" aria-label="Dataset status">
      <p>Dataset: <strong>{view.dataset.state}</strong></p>
      {#if view.dataset.state === 'failed'}<p role="alert">Ingestion failed. Review the diagnostics, change the selection or refresh, then load again.</p>{/if}
      {#if view.dataset.diagnostics.length}
        <ul>{#each view.dataset.diagnostics as diagnostic}<li>{diagnostic.message}</li>{/each}</ul>
        {#if view.dataset.diagnostics_truncated}<p>Showing {view.dataset.diagnostics.length} of {view.dataset.diagnostics_total} diagnostics.</p>{/if}
      {/if}
    </section>
  {/if}

  {#if view.dataset?.state === 'ready'}
    <div class="source-actions">
      <p class="notice">Server-side filters use exact values (one per line). Blank selections mean unrestricted. Full-dataset value lists are not available yet.</p>
      <button disabled={!!view.busy} onclick={resetAll}>Reset filters and sorting</button>
    </div>
    {#if filterError}<p role="alert" class="failed">{filterError}</p>{/if}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div class="table-region" role="region" aria-label="Actual trial results" tabindex="0" aria-busy={!!view.busy}>
      <table class="live-table">
        <caption class="sr-only">Actual trial attempts. Select an attempt ID to inspect exclusion flags. All filtering and sorting applies to the entire dataset on the server.</caption>
        <thead><tr>
          {#each columns as column}
            <th scope="col" class:numeric={column.numeric} aria-sort={view.sort?.field === column.field ? view.sort.direction : undefined}>
              <div class="column-heading"><span>{column.label}</span>
                <button disabled={!!view.busy} popovertarget={`live-filter-${column.field}`} class="filter-button"
                  class:active={view.filters.some(filter => filter.field === column.field)}
                  aria-label={`Sort and filter ${column.label}`} onclick={event => positionFilter(event, column.field)}>
                  {view.sort?.field === column.field ? (view.sort.direction === 'ascending' ? '↑' : '↓') : '⋮'}
                </button>
              </div>
              <div id={`live-filter-${column.field}`} popover="auto" class="filter-panel" role="group" aria-label={`Filter ${column.label}`}>
                <strong>{column.label}</strong>
                <div class="sort-controls">
                  <button disabled={!!view.busy} onclick={() => sortBy({ field: column.field, direction: 'ascending' })}>↑ Ascending</button>
                  <button disabled={!!view.busy} onclick={() => sortBy({ field: column.field, direction: 'descending' })}>↓ Descending</button>
                </div>
                <label>Exact values, one per line
                  <textarea rows="4" disabled={!!view.busy} bind:value={drafts[column.field]}></textarea>
                </label>
                {#if column.nullable}
                  <label class="filter-option"><input type="checkbox" disabled={!!view.busy} bind:checked={missing[column.field]} /> Include missing values</label>
                {/if}
                <button disabled={!!view.busy} onclick={() => applyFilter(column.field)}>Apply column filter</button>
                <button disabled={!!view.busy} onclick={() => resetColumn(column.field)}>Reset column</button>
              </div>
            </th>
          {/each}
        </tr></thead>
        <tbody>
          {#each view.page?.items ?? [] as row (row.attempt_id)}
            <tr class:selected={selectedAttempt === row.attempt_id}>
              <td><button class="experiment-button" aria-expanded={selectedAttempt === row.attempt_id}
                onclick={() => selectedAttempt = selectedAttempt === row.attempt_id ? null : row.attempt_id}>{row.attempt_id}</button></td>
              <td>{row.instance_id}</td><td>{row.implementation ?? '—'}</td>
              <td class="numeric">{row.n_cities}</td><td>{row.algorithm}</td>
              <td class:failed={row.outcome === 'failed'}>{row.outcome}</td>
              <td class="numeric">{row.best_cost ?? '—'}</td><td class="numeric">{row.search_time_s ?? '—'}</td>
            </tr>
            {#if selectedAttempt === row.attempt_id}
              <tr class="detail-row"><td colspan={columns.length}>
                <dl><div><dt>Campaign complete</dt><dd>{row.campaign_complete ? 'Yes' : 'No'}</dd></div>
                  <div><dt>Duplicate execution</dt><dd>{row.duplicate_execution ? 'Yes' : 'No'}</dd></div>
                  <div><dt>Usable in shared analysis</dt><dd>{row.usable ? 'Yes' : 'No'}</dd></div></dl>
                <p>Usable does not establish paired-comparison eligibility or verified producer provenance.</p>
                <p>Geometry unavailable: original-instance and tour binding is not implemented. No fixture geometry is substituted.</p>
              </td></tr>
            {/if}
          {:else}
            <tr><td colspan={columns.length} class="empty-state">{view.busy ? 'Loading trials…' : view.page ? 'No matching trial attempts.' : 'No current page. Retry or reload the dataset.'}</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="source-actions" aria-label="Trial pages">
      <button disabled={!!view.busy || !view.page || view.pageIndex === 0} onclick={() => { selectedAttempt = null; void controller.navigate(view.pageIndex - 1); }}>Previous</button>
      <span>Page {view.pageIndex + 1} · {view.page?.items.length ?? 0} rows · up to {PAGE_DEFAULT} per page</span>
      <button disabled={!!view.busy || !view.page?.next_cursor} onclick={() => { selectedAttempt = null; void controller.navigate(view.pageIndex + 1); }}>Next</button>
    </div>
  {/if}
</main>
