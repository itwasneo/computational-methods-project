/** Presentation only: never use these strings for identities, filters or exports. */
export function compactId(value: string): string {
  if (value.length <= 22) return value;
  const hash = value.match(/([a-f0-9]{32,64})$/i)?.[1] ?? value;
  return `${hash.slice(0, 8)}…${hash.slice(-6)}`;
}
export const isSeconds = (field: string) => /_s(?:_|$)/.test(field);
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (!Number.isFinite(seconds)) return '—';
  const magnitude = Math.abs(seconds);
  if (magnitude > 0 && magnitude < 0.00001) return `${seconds < 0 ? '−' : ''}<0.01 ms`;
  const [value, unit] = magnitude < 1 ? [seconds * 1000, 'ms']
    : magnitude < 60 ? [seconds, 's'] : magnitude < 3600 ? [seconds / 60, 'min'] : [seconds / 3600, 'h'];
  const rounded = Math.abs(value) >= 10000 ? value.toExponential(2)
    : value.toFixed(2).replace(/\.?0+$/, '');
  return `${rounded} ${unit}`;
}
export function rawText(value: unknown): string {
  return value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
}
export function cellText(field: string, value: unknown): string {
  if (field === 'metric' && typeof value === 'string') return columnLabel(value);
  if (field.endsWith('_id') && typeof value === 'string') return compactId(value);
  if (isSeconds(field) && typeof value === 'number') return duration(value);
  return rawText(value);
}
export function columnLabel(field: string): string {
  const labels: Record<string, string> = { attempt_id: 'Attempt', baseline_attempt_id: 'Baseline attempt',
    instance_id: 'Instance', search_time_s: 'Search time', end_to_end_time_s: 'Total time',
    search_elapsed_s: 'Search elapsed', elapsed_s: 'Elapsed', logical_id: 'Logical search' };
  if (labels[field]) return labels[field];
  return isSeconds(field) ? field.replace(/_s(?=_|$)/, '').replaceAll('_', ' ') : field;
}
export function exactTitle(field: string, value: unknown): string {
  return `${field}: ${rawText(value)}${isSeconds(field) && typeof value === 'number' ? ' s' : ''}`;
}
