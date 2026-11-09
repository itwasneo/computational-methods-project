import type { Trial } from './fixtures';
import type { Sort, SortKey } from './sorting';

export type Filters = Record<SortKey, string[]>;
export type TableState = { filters: Filters; sort: Sort | null };

export function resetTableState(): TableState {
  return {
    filters: { id: [], instance: [], implementation: [], cities: [], algorithm: [], outcome: [], cost: [], time: [] },
    sort: null,
  };
}

export function resetColumn(state: TableState, key: SortKey): TableState {
  return {
    filters: { ...state.filters, [key]: [] },
    sort: state.sort?.key === key ? null : state.sort,
  };
}

export function filterTrials(rows: Trial[], filters: Filters): Trial[] {
  const keys = Object.keys(filters) as SortKey[];
  return rows.filter(row => keys.every(key =>
    filters[key].length === 0 || filters[key].includes(String(row[key]))));
}
