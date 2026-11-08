import test from 'node:test';
import assert from 'node:assert/strict';
import {webgl, webgpu} from './renderers.mjs';
import {gpuEnvironment, gpuDevice, gpuCanvas, glCanvas} from './test-support.mjs';

const points = new Float32Array([0, 0, 0, 0]);

test('WebGL selection overlays all instances and restores vertex offset for the next tour', () => {
  const canvas = glCanvas(), renderer = webgl(canvas, points, assert.fail);
  renderer.draw([1, 1, 0, 0], 1, false, 1);
  renderer.draw([1, 1, 0, 0], -1, true, 1);
  assert.deepEqual(canvas.draws.map(({args, offset}) => [args, offset]), [
    [['TRIANGLES', 0, 6, 2], 0], [['TRIANGLES', 0, 6, 1], 8],
    [['LINE_LOOP', 0, 2], 0], [['TRIANGLES', 0, 6, 2], 0],
  ]);
  assert.deepEqual(canvas.draws[1].style.slice(2), [1, 1]);
  renderer.destroy(); renderer.destroy();
  assert.ok(canvas.resources.every(item => item.deleted === 1));
  assert.equal(canvas.listeners.size, 0);
});

test('WebGL releases a partial program and earlier programs on shader failure', () => {
  const canvas = glCanvas({failShader: 4});
  assert.throws(() => webgl(canvas, points, assert.fail), /shader failure/);
  assert.ok(canvas.resources.every(item => item.deleted === 1));
  assert.equal(canvas.listeners.size, 0);
});

test('WebGPU selection is last, and intentional cleanup does not report device loss', async t => {
  const device = gpuDevice(), canvas = gpuCanvas();
  gpuEnvironment(t, [device]);
  const renderer = await webgpu(canvas, points, assert.fail);
  renderer.draw([1, 1, 0, 0], 0, true, 1);
  assert.deepEqual(device.draws, [
    {pipeline: 'line', args: [3]}, {pipeline: 'dots', args: [6, 2]}, {pipeline: 'dots', args: [6, 1, 0, 0]},
  ]);
  renderer.destroy(); renderer.destroy(); await Promise.resolve();
  assert.equal(device.destroyed, 1); assert.equal(canvas.context.unconfigured, 1);
  assert.ok(device.buffers.every(buffer => buffer.destroyed === 1));
});

test('WebGPU cleans up shader and partially completed pipeline initialization failures', async t => {
  for (const options of [{compilation: Promise.resolve({messages: [{type: 'error', message: 'shader failure'}]})}, {failPipeline: 2}]) {
    await t.test(JSON.stringify(Object.keys(options)), async t => {
      const device = gpuDevice(options), canvas = gpuCanvas();
      gpuEnvironment(t, [device]);
      await assert.rejects(webgpu(canvas, points, assert.fail), /failure/);
      await Promise.resolve();
      assert.equal(device.destroyed, 1); assert.equal(canvas.context.unconfigured, 1);
      assert.ok(device.buffers.every(buffer => buffer.destroyed === 1));
    });
  }
});
