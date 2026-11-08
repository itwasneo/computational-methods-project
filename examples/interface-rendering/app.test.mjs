import test from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as tick} from 'node:timers/promises';
import {deferred, globals, gpuEnvironment, gpuDevice, gpuCanvas} from './test-support.mjs';

function dom(t) {
  const elements = new Map(), frames = [], listeners = new Map();
  const element = () => ({value: '', checked: false, disabled: false, textContent: '',
    clientWidth: 1000, clientHeight: 600, addEventListener() {}, setAttribute() {}, replaceChildren() {}});
  for (const id of ['api', 'count', 'kind', 'load', 'measure', 'surface', 'city', 'x', 'y', 'status', 'report', 'tour', 'fit', 'inspect', 'move', 'export']) {
    elements.set(id, element());
  }
  const $ = id => elements.get(id);
  $('api').value = 'WebGPU'; $('count').value = '1000'; $('kind').value = 'uniform';
  globals(t, {
    document: {getElementById: $, createElement: () => Object.assign(element(), gpuCanvas()),
      addEventListener(name, callback) { listeners.set(name, callback); }},
    window: {addEventListener() {}}, ResizeObserver: class { observe() {} },
    matchMedia: () => ({addEventListener() {}}), devicePixelRatio: 1, innerWidth: 1440, innerHeight: 900,
    requestAnimationFrame(callback) { frames.push(callback); },
  });
  return {$, frames, listeners};
}

for (const completion of ['resolve', 'reject']) {
  test(`late initialization ${completion} cannot overwrite a successful retry`, async t => {
    const compilation = deferred(), old = gpuDevice({compilation: compilation.promise}), current = gpuDevice();
    gpuEnvironment(t, [old, current]);
    const {$, frames, listeners} = dom(t);
    await import(`./app.mjs?completion=${completion}`); await tick();
    assert.equal($('inspect').disabled, true);
    old.error('initialization failed');
    assert.equal($('load').disabled, false);
    assert.equal($('inspect').disabled, true);
    $('count').value = '10000';
    await $('load').onclick();
    const ready = $('status').textContent;
    assert.match(ready, /Ready: WebGPU, 10000/);
    if (completion === 'resolve') compilation.resolve({messages: []});
    else compilation.reject(new Error('late failure'));
    await tick();
    assert.equal($('status').textContent, ready);
    assert.equal($('city').max, 9999);
    assert.equal($('measure').disabled, false);
    assert.equal(old.destroyed, 1); assert.equal(current.destroyed, 0);
    assert.ok(old.buffers.every(buffer => buffer.destroyed === 1));
    frames.shift()(performance.now());
    assert.equal(old.draws.length, 0); assert.ok(current.draws.length > 0);

    // A new/invalidated diagnostic must never leave an old export available.
    $('measure').onclick(); frames.shift()(performance.now() + 10001);
    assert.match($('report').textContent, /NOT EVALUATED/);
    assert.equal($('export').disabled, false);
    $('measure').onclick();
    assert.equal($('report').textContent, ''); assert.equal($('export').disabled, true);
    document.hidden = true; listeners.get('visibilitychange')();
    assert.match($('status').textContent, /invalidated/);
    assert.equal($('export').disabled, true); assert.equal($('report').textContent, '');
    assert.equal(current.destroyed, 1);
    await tick(); // Intentional disposal must not overwrite the failure status.
    assert.match($('status').textContent, /invalidated/);
  });
}
