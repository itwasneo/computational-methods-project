import type { ScientificConfiguration } from '../contracts.ts';

export const gaDefaults = {
  population_size: 128,
  tournament_size: 3,
  elite_count: 1,
  crossover_probability: 0.9,
  mutation_probability: 0.2,
  generations: 100,
  generationLimit: true,
  populations: 'population-1',
};
export type GaFields = typeof gaDefaults;

export function validGaFields(value: unknown): value is GaFields {
  if (!value || typeof value !== 'object') return false;
  const v = value as GaFields;
  return (
    Object.keys(v).length === Object.keys(gaDefaults).length &&
    Number.isSafeInteger(v.population_size) &&
    v.population_size >= 2 &&
    Number.isSafeInteger(v.tournament_size) &&
    v.tournament_size >= 1 &&
    Number.isSafeInteger(v.elite_count) &&
    v.elite_count >= 0 &&
    v.elite_count < v.population_size &&
    [v.crossover_probability, v.mutation_probability].every(
      (p) => Number.isFinite(p) && p >= 0 && p <= 1,
    ) &&
    typeof v.generationLimit === 'boolean' &&
    Number.isSafeInteger(v.generations) &&
    v.generations >= 0 &&
    typeof v.populations === 'string' &&
    v.populations.length <= 16511
  );
}

export function gaWork(fields: GaFields, seconds: number | null, maxPopulations: number) {
  if (!validGaFields(fields))
    throw new Error(
      'Invalid GA fields: elite count must be smaller than population size, counts must be safe integers and probabilities must lie in [0, 1].',
    );
  const { populations, generationLimit, generations, ...parameters } = fields;
  const ids = populations.split(',').map((id) => id.trim());
  if (
    ids.length > maxPopulations ||
    new Set(ids).size !== ids.length ||
    ids.some((id) => !/^[A-Za-z0-9_-]{1,128}$/.test(id))
  )
    throw new Error(
      'Supply distinct, ordered population IDs using letters, digits, underscores or hyphens, within the advertised limit.',
    );
  if (!generationLimit && seconds === null)
    throw new Error('GA requires a generation limit, a time limit, or both.');
  return {
    parameters,
    logical_search_ids: ids,
    budget: {
      ...(generationLimit ? { max_generations: generations } : {}),
      ...(seconds === null ? {} : { time_limit_s: seconds }),
    },
  };
}

export function restoreGa(config: ScientificConfiguration): GaFields {
  return {
    ...gaDefaults,
    ...config.parameters,
    generationLimit: config.budget.max_generations !== undefined,
    generations: Number(config.budget.max_generations ?? gaDefaults.generations),
    populations: config.logical_search_ids!.join(','),
  };
}
