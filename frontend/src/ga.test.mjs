import assert from 'node:assert/strict';
import test from 'node:test';
import { gaDefaults, gaWork, restoreGa } from './experiments/ga.ts';

test('generation zero, time-only and combined GA budgets are distinct', () => {
  const fields = { ...gaDefaults, populations: 'second, first', generations: 0 };
  for (const [generationLimit, seconds, budget] of [
    [true, null, { max_generations: 0 }],
    [false, 0.1, { time_limit_s: 0.1 }],
    [true, 0.1, { max_generations: 0, time_limit_s: 0.1 }],
  ]) {
    const work = gaWork({ ...fields, generationLimit }, seconds, 2);
    assert.deepEqual(work.budget, budget);
    assert.deepEqual(work.logical_search_ids, ['second', 'first']);
    assert.deepEqual(gaWork(restoreGa(work), seconds, 2), work);
  }
});

test('invalid GA work is not silently normalized', () => {
  for (const changes of [
    { populations: '' },
    { populations: 'a,a' },
    { populations: 'a,b,c' },
    { populations: 'a b' },
    { populations: 'a'.repeat(129) },
    { population_size: 1 },
    { tournament_size: 1.5 },
    { elite_count: 128 },
    { crossover_probability: -1 },
    { mutation_probability: 1.2 },
    { generations: -1 },
    { generationLimit: false },
  ])
    assert.throws(() => gaWork({ ...gaDefaults, ...changes }, null, 2));
});
