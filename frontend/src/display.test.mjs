import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactId, duration, cellText, columnLabel, exactTitle } from './display.ts';

test('compact durations retain units, missing/zero distinctions and tiny positive observations', () => {
  for (const [seconds, expected] of [
    [null, '—'],
    [0, '0 ms'],
    [0.000123456, '0.12 ms'],
    [0.0123456, '12.35 ms'],
    [0.1, '100 ms'],
    [1.234567, '1.23 s'],
    [90, '1.5 min'],
    [7200, '2 h'],
    [1e-12, '<0.01 ms'],
  ])
    assert.equal(duration(seconds), expected);
  assert.match(duration(1e308), /^\d\.\d{2}e\+\d+ h$/);
  assert.equal(exactTitle('search_time_s', 0.000123456), 'search_time_s: 0.000123456 s');
  assert.equal(cellText('search_time_s_median', 0.123456), '123.46 ms');
  assert.equal(columnLabel('search_time_s'), 'Search time');
  assert.equal(columnLabel('search_time_s_median'), 'search time median');
});

test('IDs shorten only for display and seeds/costs remain exact', () => {
  const hash = '12345678' + 'a'.repeat(50) + 'abcdef';
  const instance = 'instance-v1-' + hash;
  assert.equal(compactId(instance), '12345678…abcdef');
  assert.equal(compactId('short-id'), 'short-id');
  assert.equal(cellText('attempt_id', hash), '12345678…abcdef');
  assert.equal(exactTitle('instance_id', instance), `instance_id: ${instance}`);
  assert.equal(cellText('solver_seed', '18446744073709551615'), '18446744073709551615');
  assert.equal(cellText('best_cost', 1.123456789), '1.123456789');
});
