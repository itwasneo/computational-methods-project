import { VALUES_PER_FILTER_MAX, type ColumnFilter, type Field } from '../contracts.ts';

export const columns: { field: Field; label: string; numeric?: boolean; nullable?: boolean }[] = [
  { field: 'attempt_id', label: 'Attempt ID' },
  { field: 'instance_id', label: 'Instance ID' },
  { field: 'implementation', label: 'Implementation', nullable: true },
  { field: 'n_cities', label: 'Cities', numeric: true },
  { field: 'algorithm', label: 'Algorithm' },
  { field: 'outcome', label: 'Outcome' },
  { field: 'best_cost', label: 'Cost', numeric: true, nullable: true },
  { field: 'search_time_s', label: 'Search time (s)', numeric: true, nullable: true },
];

/** Parse only presentation input; filtering the dataset remains a server operation. */
export function filterValues(field: Field, input: string, missing: boolean): ColumnFilter {
  const column = columns.find(column => column.field === field);
  if (!column) throw new Error('Unsupported filter column.');
  const values = input.split('\n').filter(line => line !== '').map(line => {
    if (!column.numeric) return line; // Strings are exact and case-sensitive, including spaces.
    if (field === 'n_cities' && !/^[1-9][0-9]*$/.test(line.trim())) {
      throw new Error('Cities must be decimal integers of at least two.');
    }
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      throw new Error('Enter one decimal number per line.');
    }
    if (typeof value !== 'number' || !Number.isFinite(value)
        || (Number.isInteger(value) && !Number.isSafeInteger(value))
        || (field === 'n_cities' && (!Number.isSafeInteger(value) || value < 2))) {
      throw new Error(field === 'n_cities'
        ? 'Cities must be safe integers of at least two.'
        : 'Enter finite numbers; integer values must be in the safe integer range.');
    }
    return value;
  });
  const unique: (string | number | null)[] = [...new Set(values)];
  if (missing && column.nullable) unique.push(null);
  if (unique.length > VALUES_PER_FILTER_MAX) throw new Error(`Select at most ${VALUES_PER_FILTER_MAX} values per column.`);
  return { field, values: unique };
}
