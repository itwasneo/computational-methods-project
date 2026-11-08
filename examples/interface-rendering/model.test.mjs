import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture, bounds, Scene, percentile} from './model.mjs';

test('fixtures are deterministic float64 and cover degenerate bounds', () => {
  for (const kind of ['uniform', 'clustered', 'duplicates', 'collinear', 'coincident']) {
    const points = fixture(1000, kind);
    assert.ok(points instanceof Float64Array);
    assert.deepEqual(points, fixture(1000, kind));
    assert.ok([...points].every(Number.isFinite));
    const scene = new Scene(points);
    assert.ok([...scene.display].every(Number.isFinite));
  }
  assert.deepEqual(bounds(fixture(10, 'coincident')), {x: 0, y: 0, span: 1});
  assert.throws(() => fixture(1)); assert.throws(() => fixture(2, 'unknown'));
});

test('projection never replaces canonical float64 or aliases its input', () => {
  const input = new Float64Array([0, -0, 1 / 3, 1]);
  const scene = new Scene(input);
  assert.deepEqual(scene.points, input);
  scene.update(1, [Math.PI, -Math.E]);
  assert.equal(input[2], 1 / 3);
  assert.equal(scene.points[2], Math.PI);
  assert.ok(Object.is(scene.points[1], -0));
});

test('spatial picks match a brute-force oracle before and after edits', () => {
  const scene = new Scene(fixture(10000, 'clustered'));
  for (const radius of [0, 0.001, 0.02, 2]) {
    for (let i = 0; i < 20; i++) {
      const id = i * 123;
      scene.update(id, [i / 20, -i / 20]);
      const [x, y] = scene.normalized(id);
      const oracle = [];
      for (let j = 0; j < scene.points.length / 2; j++) {
        const [px, py] = scene.normalized(j);
        const distance = Math.hypot(px - x, py - y);
        if (distance <= radius) oracle.push({id: j, distance});
      }
      oracle.sort((a, b) => a.distance - b.distance || a.id - b.id);
      assert.deepEqual(scene.pick(x, y, radius), oracle.map(hit => hit.id));
    }
  }
});

test('coincident city IDs remain individually selectable in stable order', () => {
  const scene = new Scene(fixture(1000, 'coincident'));
  assert.deepEqual(scene.pick(0, 0, 0), Array.from({length: 1000}, (_, i) => i));
  scene.update(100, [1, 1]);
  assert.deepEqual(scene.pick(1, 1, 0), [100]);
  assert.equal(scene.pick(0, 0, 0).length, 999);
});

test('moving one city does not retain empty grid cells or copy display storage', () => {
  const scene = new Scene(fixture(2, 'coincident')), buffer = scene.display;
  for (let i = 0; i < 10000; i++) scene.update(1, [i, i]);
  assert.equal(scene.grid.size, 2);
  assert.equal(scene.display, buffer);
});

test('percentiles use nearest rank and do not mutate samples', () => {
  const values = [4, 1, 3, 2];
  assert.equal(percentile(values, 0.5), 2);
  assert.equal(percentile(values, 0.95), 4);
  assert.equal(percentile([], 0.95), null);
  assert.deepEqual(values, [4, 1, 3, 2]);
});
