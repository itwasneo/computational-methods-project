import { test } from 'node:test';
import assert from 'node:assert/strict';
import { trials } from './fixtures.ts';
import { inspectFixture, nextPickedCity } from './inspection.ts';
import { Scene } from '../../examples/interface-rendering/model.mjs';

test('inspection resolves explicit fixture IDs, not names or missing artifacts', () => {
  const first = inspectFixture(trials[0]);
  const second = inspectFixture(trials[1]);
  assert.equal(first.fixtureId, second.fixtureId);
  assert.deepEqual(first.coordinates, second.coordinates);
  assert.equal(first.coordinates.length / 2, trials[0].cities);
  assert.equal(new Set(first.route).size, trials[0].cities);
  assert.ok(first.route.every(id => id < trials[0].cities));
  assert.equal(second.route, null);
  assert.equal(inspectFixture(trials[2]), null);
  assert.equal(inspectFixture({ ...trials[0], id: 'unknown' }), null);
  assert.throws(() => inspectFixture({ ...trials[0], cities: 2 }), /city count/);
  first.coordinates[0] = 99;
  assert.notEqual(inspectFixture(trials[0]).coordinates[0], 99);
});

test('coincident city picking cycles IDs while canonical coordinates remain unchanged', () => {
  const inspection = inspectFixture(trials[0]);
  const before = inspection.coordinates.slice();
  const scene = new Scene(inspection.coordinates);
  const [x, y] = scene.normalized(0);
  const hits = scene.pick(x, y, 0);
  assert.deepEqual(hits, [0, 1]);
  assert.equal(nextPickedCity(hits, 0), 1);
  assert.equal(nextPickedCity(hits, 1), 0);
  assert.equal(nextPickedCity([], 1), 1);
  assert.deepEqual(inspection.coordinates, before);
});
