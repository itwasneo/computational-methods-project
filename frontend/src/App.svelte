<script lang="ts">
  import { trials } from './fixtures';
  import InstanceInspection from './InstanceInspection.svelte';
  import { applyTheme, type Theme } from './theme';

  let theme = $state<Theme>(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  function toggleTheme() {
    theme = theme === 'light' ? 'dark' : 'light';
    applyTheme(theme);
  }
  import { sortTrials } from './sorting';
  import { filterTrials, resetColumn, resetTableState } from './table-state';

  const columns = [
    { key: 'id', label: 'Experiment ID', numeric: false },
    { key: 'instance', label: 'Instance', numeric: false },
    { key: 'implementation', label: 'Implementation', numeric: false },
    { key: 'cities', label: 'Cities', numeric: true },
    { key: 'algorithm', label: 'Algorithm', numeric: false },
    { key: 'outcome', label: 'Outcome', numeric: false },
    { key: 'cost', label: 'Cost', numeric: true },
    { key: 'time', label: 'Search time (s)', numeric: true },
  ] as const;
  type ColumnKey = typeof columns[number]['key'];

  function positionFilter(event: MouseEvent, key: ColumnKey) {
    const button = event.currentTarget as HTMLButtonElement;
    const panel = document.getElementById(`filter-${key}`);
    if (!panel) return;
    const rect = button.getBoundingClientRect();
    panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 272))}px`;
    panel.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 328))}px`;
  }

  let { filters, sort } = $state(resetTableState());
  let selectedId = $state<string | null>(null);
  const columnValues = Object.fromEntries(columns.map(column => [
    column.key,
    [...new Set(trials.map(trial => String(trial[column.key])))].sort((a, b) =>
      a.localeCompare(b, undefined, { numeric: true })),
  ])) as Record<ColumnKey, string[]>;

  let visible = $derived(sortTrials(filterTrials(trials, filters), sort));

  function resetTable() {
    ({ filters, sort } = resetTableState());
  }
</script>

<main>
  <div class="page-header">
    <h1>Results</h1>
    <button class="theme-toggle" onclick={toggleTheme} aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`} title={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
        {#if theme === 'light'}
          <path d="M20.5 13.2A8.5 8.5 0 0 1 10.8 3.5a8.5 8.5 0 1 0 9.7 9.7Z" />
        {:else}
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
        {/if}
      </svg>
    </button>
  </div>

  <div class="toolbar">
    <button
      class="reset-filters"
      onclick={resetTable}
      disabled={sort === null && !columns.some(column => filters[column.key].length > 0)}
      aria-label="Reset all filters and sorting"
      title="Reset all filters and sorting"
    >
      <span aria-hidden="true">↺</span> Reset
    </button>
  </div>

  <!-- The horizontally scrollable table region must be keyboard focusable. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div class="table-region" role="region" aria-label="Experiment results" tabindex="0">
    <table>
      <caption class="sr-only">Column filter buttons allow multiple values. Select an experiment ID to expand or collapse its details.</caption>
      <colgroup>
        <col style="width: 14%" />
        <col style="width: 16%" />
        <col style="width: 19%" />
        <col style="width: 9%" />
        <col style="width: 10%" />
        <col style="width: 10%" />
        <col style="width: 9%" />
        <col style="width: 13%" />
      </colgroup>
      <thead>
        <tr>
          {#each columns as column}
            <th scope="col" class:numeric={column.numeric} aria-sort={sort?.key === column.key ? sort.direction : undefined}>
              <div class="column-heading">
                <span>{column.label}</span>
                <button
                  class="filter-button"
                  class:active={filters[column.key].length > 0}
                  popovertarget={`filter-${column.key}`}
                  aria-label={`Sort and filter ${column.label}: ${filters[column.key].length ? `${filters[column.key].length} values selected` : 'all values'}`}
                  title={`Sort and filter ${column.label}`}
                  onclick={(event) => positionFilter(event, column.key)}
                >
                  <span class="filter-symbol" aria-hidden="true">
                    {#if sort?.key === column.key}
                      {sort.direction === 'ascending' ? '↑' : '↓'}
                    {:else}
                      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 3h12L9.5 8v5l-3-1V8Z" /></svg>
                    {/if}
                  </span>
                  <span class="filter-count" class:inactive={filters[column.key].length === 0} aria-hidden="true">{filters[column.key].length}</span>
                </button>
              </div>
              <div id={`filter-${column.key}`} popover="auto" class="filter-panel" role="group" aria-label={`Filter ${column.label}`}>
                <div class="filter-panel-heading">
                  <strong>{column.label}</strong>
                  <button
                    disabled={filters[column.key].length === 0 && sort?.key !== column.key}
                    onclick={() => {
                      ({ filters, sort } = resetColumn({ filters, sort }, column.key));
                    }}
                  >Reset column</button>
                </div>
                <div class="sort-controls" role="group" aria-label={`Sort ${column.label}`}>
                  <button aria-pressed={sort?.key === column.key && sort.direction === 'ascending'} onclick={() => sort = { key: column.key, direction: 'ascending' }}>↑ Ascending</button>
                  <button aria-pressed={sort?.key === column.key && sort.direction === 'descending'} onclick={() => sort = { key: column.key, direction: 'descending' }}>↓ Descending</button>
                </div>
                {#each columnValues[column.key] as value}
                  <label class="filter-option">
                    <input type="checkbox" {value} bind:group={filters[column.key]} />
                    <span>{column.key === 'cities' ? Number(value).toLocaleString() : value}</span>
                  </label>
                {/each}
              </div>
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each visible as trial (trial.id)}
          <tr class:selected={selectedId === trial.id}>
            <td>
              <button
                class="experiment-button"
                aria-expanded={selectedId === trial.id}
                aria-controls={`details-${trial.id}`}
                onclick={() => selectedId = selectedId === trial.id ? null : trial.id}
              >
                <span aria-hidden="true">{selectedId === trial.id ? '▾' : '▸'}</span>
                {trial.id}
              </button>
            </td>
            <td>{trial.instance}</td>
            <td>{trial.implementation}</td>
            <td class="numeric">{trial.cities.toLocaleString()}</td>
            <td>{trial.algorithm}</td>
            <td class:failed={trial.outcome === 'Failed'}>{trial.outcome}</td>
            <td class="numeric">{trial.cost}</td>
            <td class="numeric">{trial.time}</td>
          </tr>
          <tr id={`details-${trial.id}`} hidden={selectedId !== trial.id} class="detail-row">
            <td colspan={columns.length}>
              <section aria-label={`Details for ${trial.id}`}>
                <dl>
                  <div><dt>Distance mode</dt><dd>On demand</dd></div>
                  <div><dt>Work budget</dt><dd>Fixed starts</dd></div>
                  <div><dt>Comparison</dt><dd>{trial.comparison}</dd></div>
                </dl>
                <p>{trial.explanation}</p>
              </section>
              {#if selectedId === trial.id}
                <InstanceInspection {trial} {theme} />
              {/if}
            </td>
          </tr>
        {/each}
        {#if visible.length === 0}
          <tr><td colspan={columns.length} class="empty-state">No matching experiments.</td></tr>
        {/if}
      </tbody>
    </table>
  </div>
</main>
