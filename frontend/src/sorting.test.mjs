import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sortTrials } from './sorting.ts';
import { trials } from './fixtures.ts';

const ids = rows => rows.map(row => row.id);

test('numeric sorting keeps missing values last in both directions', () => {
  const rows = ['10', '2', '—', '2'].map((cost, index) => ({ ...trials[0], id: String(index), cost }));
  assert.deepEqual(ids(sortTrials(rows, { key: 'cost', direction: 'ascending' })), ['1', '3', '0', '2']);
  assert.deepEqual(ids(sortTrials(rows, { key: 'cost', direction: 'descending' })), ['0', '1', '3', '2']);
  assert.deepEqual(ids(rows), ['0', '1', '2', '3']);
});

test('text sorting and clearing preserve stable source order', () => {
  assert.deepEqual(ids(sortTrials(trials, { key: 'id', direction: 'descending' })), ids(trials).reverse());
  assert.deepEqual(sortTrials(trials, null), trials);
  assert.notEqual(sortTrials(trials, null), trials);
  assert.deepEqual(sortTrials([], { key: 'cities', direction: 'ascending' }), []);
});
