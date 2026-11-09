import { test } from 'node:test';
import assert from 'node:assert/strict';
import { trials } from './fixtures.ts';
import { filterTrials, resetColumn, resetTableState } from './table-state.ts';
import { sortTrials } from './sorting.ts';

const ids = rows => rows.map(row => row.id);

test('filters combine values with OR and columns with AND using exact matches', () => {
  const { filters } = resetTableState();
  filters.cities = ['128', '256'];
  assert.deepEqual(ids(filterTrials(trials, filters)), ['preview-01', 'preview-02', 'preview-03']);
  filters.implementation = ['visited-scan-v1'];
  assert.deepEqual(ids(filterTrials(trials, filters)), ['preview-01', 'preview-03']);
  filters.cities = ['12'];
  assert.deepEqual(filterTrials(trials, filters), []);
});

test('global reset clears filters and sorting and returns fresh state', () => {
  let state = resetTableState();
  state.filters.outcome = ['Success'];
  state.sort = { key: 'id', direction: 'descending' };
  assert.deepEqual(ids(sortTrials(filterTrials(trials, state.filters), state.sort)), ['preview-04', 'preview-02', 'preview-01']);
  state = resetTableState();
  assert.equal(state.sort, null);
  assert.ok(Object.values(state.filters).every(values => values.length === 0));
  assert.deepEqual(sortTrials(filterTrials(trials, state.filters), state.sort), trials);
  const other = resetTableState();
  state.filters.id.push('preview-01');
  assert.deepEqual(other.filters.id, []);
});

test('column reset clears only its filter and its own active sort', () => {
  const state = resetTableState();
  state.filters.instance = ['Uniform / small'];
  state.filters.outcome = ['Success'];
  state.sort = { key: 'outcome', direction: 'ascending' };
  const next = resetColumn(state, 'instance');
  assert.deepEqual(next.filters.instance, []);
  assert.deepEqual(next.filters.outcome, ['Success']);
  assert.deepEqual(next.sort, state.sort);
  const final = resetColumn(next, 'outcome');
  assert.equal(final.sort, null);
  assert.deepEqual(final.filters.outcome, []);
  assert.deepEqual(state.filters.instance, ['Uniform / small']);
});
