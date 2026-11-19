<script lang="ts">
  import { onDestroy } from 'svelte';
  import { getSession } from '../application';
  import TableRegion from '../components/TableRegion.svelte';
  import { anchorPopover } from '../popover';
  import { initialState, ResultsController } from './controller';
  import { compactId, duration, exactTitle, actionLabel } from '../display';
  import { columns, filterValues } from './fields';
  const session = getSession();
  import { PAGE_DEFAULT, SHARDS_MAX, type Field, type Sort } from '../contracts';

  let view = $state.raw(initialState());
  const controller = new ResultsController((state) => (view = state), session.api, session);
  onDestroy(() => controller.dispose());
  const filterButtons: Partial<Record<Field, HTMLButtonElement>> = {};
  let selected = $state<string[]>([]);
  let manifest = $state('');
  let selectedAttempt = $state<string | null>(null);
  let drafts = $state<Record<string, string>>({});
  let missing = $state<Record<string, boolean>>({});
  let filterError = $state('');
  let setupError = $state('');

  function selectionChanged() {
    selectedAttempt = null;
    drafts = {};
    missing = {};
    filterError = '';
    setupError = '';
    controller.selectionChanged();
  }
  async function refresh() {
    selected = [];
    manifest = '';
    selectedAttempt = null;
    drafts = {};
    missing = {};
    filterError = '';
    setupError = '';
    await controller.refresh();
  }
  async function load() {
    setupError = '';
    if (selected.length < 1 || selected.length > SHARDS_MAX) {
      setupError = `Select between 1 and ${SHARDS_MAX} result shards.`;
      return;
    }
    selectedAttempt = null;
    drafts = {};
    missing = {};
    filterError = '';
    await controller.load(selected, manifest || null);
  }
  async function applyFilter(field: Field) {
    filterError = '';
    selectedAttempt = null;
    try {
      const column = filterValues(field, drafts[field] ?? '', missing[field] ?? false);
      const filters = view.filters.filter((filter) => filter.field !== field);
      if (column.values.length) filters.push(column);
      await controller.query(filters, view.sort);
    } catch (error) {
      filterError = error instanceof Error ? error.message : 'Invalid filter values.';
    }
  }
  function sortBy(sort: Sort) {
    selectedAttempt = null;
    void controller.query(view.filters, sort);
  }
  function resetColumn(field: Field) {
    drafts[field] = '';
    missing[field] = false;
    filterError = '';
    selectedAttempt = null;
    void controller.query(
      view.filters.filter((filter) => filter.field !== field),
      view.sort?.field === field ? null : view.sort,
    );
  }
  function resetAll() {
    drafts = {};
    missing = {};
    filterError = '';
    selectedAttempt = null;
    void controller.query([], null);
  }
</script>

<section aria-labelledby="saved-heading">
  <h1 id="saved-heading">Local experiment results</h1>
  <p class="notice">
    Read-only saved results. Use Experiments to generate inputs, run jobs and inspect verified
    tours.
  </p>
  {#if !view.paired}
    <p>
      <a href="#experiments">Connect in Experiments</a>
      to browse this workspace. Credentials remain in memory; disconnecting does not stop jobs.
    </p>
  {:else}
    <section class="connection-panel" aria-label="Workspace sources">
      <div class="source-actions">
        <strong>Workspace: {view.workspace?.display_name ?? 'Loading'}</strong>
        <button disabled={!!view.busy} onclick={refresh}>Refresh catalog</button>
        <button onclick={() => controller.disconnect()}>Disconnect</button>
      </div>
      <fieldset disabled={!!view.busy || !view.catalogRevision}>
        <legend>Result shards (1–{SHARDS_MAX})</legend>
        <div class="candidate-list">
          {#each view.artifacts.filter((item) => item.kind === 'result_shard') as item (item.artifact_handle)}
            <label>
              <input
                type="checkbox"
                value={item.artifact_handle}
                bind:group={selected}
                onchange={selectionChanged}
              />
              {item.display_name}
            </label>
          {:else}
            <p>
              No JSONL candidates found. Choose a workspace containing your result files, or refresh
              after adding them.
            </p>
          {/each}
        </div>
        <label>
          Expected manifest (optional)
          <select bind:value={manifest} onchange={selectionChanged}>
            <option value="">None — wholly missing executions cannot be inferred</option>
            {#each view.artifacts.filter((item) => item.kind === 'json') as item (item.artifact_handle)}
              <option value={item.artifact_handle}>{item.display_name}</option>
            {/each}
          </select>
        </label>
        <p class="notice">
          Candidates are unvalidated. Select a manifest JSON, not an instance or reference, if
          checking expected executions.
        </p>
        <button
          type="button"
          onclick={load}
          disabled={selected.length < 1 || selected.length > SHARDS_MAX}
        >
          Load {selected.length} selected shard(s)
        </button>
      </fieldset>
    </section>
  {/if}

  {#if setupError}<p role="alert" class="failed">{setupError}</p>{/if}
  {#if view.busy}<p role="status">{view.busy}…</p>{/if}
  {#if view.error}
    <div role="alert" class="error-panel">
      <p>{view.error}</p>
      {#if view.errorCode === 'unauthorized'}<p>
          Ask the owner to enable fresh pairing in the backend terminal, then enter the new secret.
        </p>{/if}
      {#if view.paired && view.dataset?.state === 'ready'}
        <button disabled={!!view.busy} onclick={() => controller.query()}>Retry first page</button>
      {/if}
    </div>
  {/if}
  {#if view.dataset}
    <section class="dataset-status" aria-label="Dataset status">
      <p>
        Dataset: <strong>{view.dataset.state}</strong>
      </p>
      {#if view.dataset.state === 'failed'}<p role="alert">
          Ingestion failed. Review the diagnostics, change the selection or refresh, then load
          again.
        </p>{/if}
      {#if view.dataset.diagnostics.length}
        <ul>
          {#each view.dataset.diagnostics as diagnostic, index (index)}<li>
              {diagnostic.message}
            </li>{/each}
        </ul>
        {#if view.dataset.diagnostics_truncated}<p>
            Showing {view.dataset.diagnostics.length} of {view.dataset.diagnostics_total} diagnostics.
          </p>{/if}
      {/if}
    </section>
  {/if}

  {#if view.dataset?.state === 'ready'}
    <div class="source-actions">
      <p class="notice">
        Server-side filters use full IDs and exact values (one per line); time filters use seconds.
        Blank selections mean unrestricted. Full-dataset value lists are not available yet.
      </p>
      <button disabled={!!view.busy} onclick={resetAll}>Reset filters and sorting</button>
    </div>
    {#if filterError}<p role="alert" class="failed">{filterError}</p>{/if}
    <TableRegion label="Actual trial results" busy={!!view.busy}>
      <table class="live-table">
        <caption class="sr-only">
          Actual trial attempts. Select an attempt ID to inspect exclusion flags. All filtering and
          sorting applies to the entire dataset on the server.
        </caption>
        <thead>
          <tr>
            {#each columns as column (column.field)}
              <th
                scope="col"
                class:numeric={column.numeric}
                aria-sort={view.sort?.field === column.field ? view.sort.direction : undefined}
              >
                <div class="column-heading">
                  <span>{column.field === 'search_time_s' ? 'Search time' : column.label}</span>
                  <button
                    disabled={!!view.busy}
                    popovertarget={`live-filter-${column.field}`}
                    class="filter-button"
                    class:active={view.filters.some((filter) => filter.field === column.field)}
                    bind:this={filterButtons[column.field]}
                    aria-label={`Sort and filter ${column.label}`}
                  >
                    {view.sort?.field === column.field
                      ? view.sort.direction === 'ascending'
                        ? '↑'
                        : '↓'
                      : '⋮'}
                  </button>
                </div>
                <div
                  id={`live-filter-${column.field}`}
                  popover="auto"
                  class="filter-panel"
                  role="group"
                  aria-label={`Filter ${column.label}`}
                  use:anchorPopover={() => filterButtons[column.field]}
                >
                  <strong>{column.label}</strong>
                  <div class="sort-controls">
                    <button
                      disabled={!!view.busy}
                      onclick={() => sortBy({ field: column.field, direction: 'ascending' })}
                    >
                      ↑ Ascending
                    </button>
                    <button
                      disabled={!!view.busy}
                      onclick={() => sortBy({ field: column.field, direction: 'descending' })}
                    >
                      ↓ Descending
                    </button>
                  </div>
                  <label>
                    Exact values, one per line
                    <textarea
                      rows="4"
                      disabled={!!view.busy}
                      bind:value={drafts[column.field]}></textarea>
                  </label>
                  {#if column.nullable}
                    <label class="filter-option">
                      <input
                        type="checkbox"
                        disabled={!!view.busy}
                        bind:checked={missing[column.field]}
                      />
                      Include missing values
                    </label>
                  {/if}
                  <button disabled={!!view.busy} onclick={() => applyFilter(column.field)}>
                    Apply column filter
                  </button>
                  <button disabled={!!view.busy} onclick={() => resetColumn(column.field)}>
                    Reset column
                  </button>
                </div>
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each view.page?.items ?? [] as row (row.attempt_id)}
            <tr class:selected={selectedAttempt === row.attempt_id}>
              <td title={row.attempt_id}>
                <button
                  class="experiment-button"
                  aria-label={actionLabel('Inspect flags for attempt', row.attempt_id)}
                  aria-expanded={selectedAttempt === row.attempt_id}
                  onclick={() =>
                    (selectedAttempt = selectedAttempt === row.attempt_id ? null : row.attempt_id)}
                >
                  {compactId(row.attempt_id)}
                </button>
              </td>
              <td title={row.instance_id}>{compactId(row.instance_id)}</td>
              <td>{row.implementation ?? '—'}</td>
              <td class="numeric">{row.n_cities}</td>
              <td>{row.algorithm}</td>
              <td class:failed={row.outcome === 'failed'}>{row.outcome}</td>
              <td class="numeric">{row.best_cost ?? '—'}</td>
              <td class="numeric" title={exactTitle('search_time_s', row.search_time_s)}>
                {duration(row.search_time_s)}
              </td>
            </tr>
            {#if selectedAttempt === row.attempt_id}
              <tr class="detail-row">
                <td colspan={columns.length}>
                  <dl>
                    <div>
                      <dt>Full attempt ID</dt>
                      <dd>{row.attempt_id}</dd>
                    </div>
                    <div>
                      <dt>Full instance ID</dt>
                      <dd>{row.instance_id}</dd>
                    </div>
                    <div>
                      <dt>Search time (seconds, full precision)</dt>
                      <dd>{row.search_time_s ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>Campaign complete</dt>
                      <dd>{row.campaign_complete ? 'Yes' : 'No'}</dd>
                    </div>
                    <div>
                      <dt>Duplicate execution</dt>
                      <dd>{row.duplicate_execution ? 'Yes' : 'No'}</dd>
                    </div>
                    <div>
                      <dt>Usable in shared analysis</dt>
                      <dd>{row.usable ? 'Yes' : 'No'}</dd>
                    </div>
                  </dl>
                  <p>
                    Usable does not establish paired-comparison eligibility or verified producer
                    provenance.
                  </p>
                  <p>
                    This table does not display geometry. Use the verified-results API with the
                    original instance; no fixture geometry is substituted.
                  </p>
                </td>
              </tr>
            {/if}
          {:else}
            <tr>
              <td colspan={columns.length} class="empty-state">
                {view.busy
                  ? 'Loading trials…'
                  : view.page
                    ? 'No matching trial attempts.'
                    : 'No current page. Retry or reload the dataset.'}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </TableRegion>
    <div class="source-actions" aria-label="Trial pages">
      <button
        disabled={!!view.busy || !view.page || view.pageIndex === 0}
        onclick={() => {
          selectedAttempt = null;
          void controller.navigate(view.pageIndex - 1);
        }}
      >
        Previous
      </button>
      <span>
        Page {view.pageIndex + 1} · {view.page?.items.length ?? 0} rows · up to {PAGE_DEFAULT} per page
      </span>
      <button
        disabled={!!view.busy || !view.page?.next_cursor}
        onclick={() => {
          selectedAttempt = null;
          void controller.navigate(view.pageIndex + 1);
        }}
      >
        Next
      </button>
    </div>
  {/if}
</section>
