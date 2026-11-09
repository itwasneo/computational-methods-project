import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
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

test('large finite pick positions terminate, including unsafe cell indices', () => {
  // A separate process ensures a regression cannot hang the test runner itself.
  const probe = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import {Scene, fixture} from ${JSON.stringify(new URL('./model.mjs', import.meta.url).href)};
    const scene = new Scene(fixture(1000));
    for (const value of [1e20, -1e20, Number.MAX_SAFE_INTEGER / 50]) {
      scene.update(0, [value, value]);
      const [x, y] = scene.normalized(0);
      assert.deepEqual(scene.pick(x, y, 0.01), [0]);
    }
    assert.deepEqual(scene.pick(Number.MAX_VALUE, Number.MAX_VALUE, 0), []);
  `], {timeout: 5000, encoding: 'utf8'});
  assert.ifError(probe.error);
  assert.equal(probe.status, 0, probe.stderr);
});

test('extreme initial bounds stay finite and unsupported edits are atomic', () => {
  const limit = Number.MAX_VALUE;
  const extreme = new Scene(new Float64Array([-limit, -limit, limit, limit]));
  assert.deepEqual([...extreme.display], [-1, -1, 1, 1]);
  const shifted = new Scene(new Float64Array([-limit, -limit, -limit / 2, -limit / 2]));
  shifted.update(0, [limit, limit]); // Subtraction overflows, but the scaled projection is representable.
  assert.ok(shifted.display.every(Number.isFinite));
  assert.deepEqual(shifted.pick(...shifted.normalized(0), 0), [0]);
  const scene = new Scene(fixture(10)), before = structuredClone({points: scene.points, display: scene.display, grid: scene.grid});
  for (const coordinates of [[limit, limit], [Infinity, 0]]) {
    assert.throws(() => scene.update(0, coordinates), /projection|finite/);
    assert.deepEqual({points: scene.points, display: scene.display, grid: scene.grid}, before);
  }
  assert.throws(() => scene.update(-1, [0, 0]), /city ID/);
  assert.throws(() => scene.pick(0, 0, -1), /radius/);
});

test('percentiles use nearest rank and do not mutate samples', () => {
  const values = [4, 1, 3, 2];
  assert.equal(percentile(values, 0.5), 2);
  assert.equal(percentile(values, 0.95), 4);
  assert.equal(percentile([], 0.95), null);
  assert.deepEqual(values, [4, 1, 3, 2]);
});
