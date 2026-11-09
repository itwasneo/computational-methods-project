import test from 'node:test';
import assert from 'node:assert/strict';
import {webgl} from './webgl.mjs';

// Check draw ordering/resource ownership, not shader compilation or GPU speed.
function canvas({failShader = 0} = {}) {
  const resources = [], draws = [], listeners = new Map();
  let shaders = 0, offset = 0, style;
  const resource = () => { const item = {deleted: 0}; resources.push(item); return item; };
  const remove = item => { item.deleted++; };
  const gl = {
    ...Object.fromEntries(['VERTEX_SHADER', 'FRAGMENT_SHADER', 'COMPILE_STATUS', 'LINK_STATUS', 'ARRAY_BUFFER',
      'DYNAMIC_DRAW', 'FLOAT', 'COLOR_BUFFER_BIT', 'LINE_LOOP', 'TRIANGLES'].map(key => [key, key])),
    createShader() { shaders++; return resource(); }, createProgram: resource, createBuffer: resource, createVertexArray: resource,
    deleteShader: remove, deleteProgram: remove, deleteBuffer: remove, deleteVertexArray: remove,
    shaderSource() {}, compileShader() {}, attachShader() {}, linkProgram() {},
    getShaderParameter() { return shaders !== failShader; }, getShaderInfoLog() { return 'shader failure'; },
    getProgramParameter() { return true; }, getUniformLocation() { return {}; },
    bindVertexArray() {}, bindBuffer() {}, bufferData() {}, bufferSubData() {}, enableVertexAttribArray() {},
    vertexAttribPointer(...args) { offset = args[5]; }, vertexAttribDivisor() {},
    viewport() {}, clearColor() {}, clear() {}, useProgram() {}, uniform4fv() {}, uniform4f(...args) { style = args.slice(1); },
    drawArrays(...args) { draws.push({args, offset}); }, drawArraysInstanced(...args) { draws.push({args, offset, style}); },
  };
  return {width: 1000, height: 600, resources, draws, listeners, getContext() { return gl; },
    addEventListener(name, callback) { listeners.set(name, callback); }, removeEventListener(name) { listeners.delete(name); }};
}

const points = new Float32Array([0, 0, 0, 0]);
test('selection draws last, offsets reset for later tours, and disposal is idempotent', () => {
  const surface = canvas(), renderer = webgl(surface, points, assert.fail);
  renderer.draw([1, 1, 0, 0], 1, false, 1);
  renderer.draw([1, 1, 0, 0], -1, true, 1);
  assert.deepEqual(surface.draws.map(({args, offset}) => [args, offset]), [
    [['TRIANGLES', 0, 6, 2], 0], [['TRIANGLES', 0, 6, 1], 8],
    [['LINE_LOOP', 0, 2], 0], [['TRIANGLES', 0, 6, 2], 0],
  ]);
  assert.deepEqual(surface.draws[1].style.slice(2), [1, 1]);
  renderer.destroy(); renderer.destroy();
  assert.ok(surface.resources.every(item => item.deleted === 1));
  assert.equal(surface.listeners.size, 0);
});

test('shader failure releases partial and earlier programs', () => {
  const surface = canvas({failShader: 4});
  assert.throws(() => webgl(surface, points, assert.fail), /shader failure/);
  assert.ok(surface.resources.every(item => item.deleted === 1));
  assert.equal(surface.listeners.size, 0);
});
