import type { AnalysisQuery } from './contracts.ts';
/** Ignore cursor position and object insertion order, but preserve ordered selections and exact values. */
export function queryIdentity(query: AnalysisQuery | null): string {
  if (!query) return '';
  return JSON.stringify(
    Object.fromEntries(
      Object.entries({ ...query, cursor: null }).sort(([a], [b]) => a.localeCompare(b)),
    ),
  );
}
