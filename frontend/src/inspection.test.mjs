import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPreview, nextPickedCity } from './inspection.ts';
import { Scene } from './rendering/model.mjs';

test('viewer preview owns deterministic synthetic coordinates and a valid permutation', () => {
  const first = createPreview();
  const second = createPreview();
  assert.deepEqual(first.coordinates, second.coordinates);
  assert.equal(first.coordinates.length / 2, 128);
  assert.equal(new Set(first.route).size, 128);
  assert.ok(first.route.every(id => id < 128));
  first.coordinates[0] = 99;
  assert.notEqual(createPreview().coordinates[0], 99);
});

test('coincident city picking cycles IDs while canonical coordinates remain unchanged', () => {
  const inspection = createPreview();
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
