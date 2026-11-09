import type { Trial } from './fixtures';

export type SortKey = 'id' | 'instance' | 'implementation' | 'cities' | 'algorithm' | 'outcome' | 'cost' | 'time';
export type Sort = { key: SortKey; direction: 'ascending' | 'descending' };

/** Presentation-only sorting; missing numeric observations stay last in either direction. */
export function sortTrials(rows: Trial[], sort: Sort | null): Trial[] {
  if (!sort) return [...rows];
  const numeric = ['cities', 'cost', 'time'].includes(sort.key);
  const direction = sort.direction === 'ascending' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (numeric) {
      const left = Number(a[sort.key]);
      const right = Number(b[sort.key]);
      const leftMissing = !Number.isFinite(left);
      const rightMissing = !Number.isFinite(right);
      if (leftMissing || rightMissing) {
        return leftMissing === rightMissing ? 0 : leftMissing ? 1 : -1;
      }
      return (left - right) * direction;
    }
    return String(a[sort.key]).localeCompare(String(b[sort.key]), undefined, { numeric: true }) * direction;
  });
}
