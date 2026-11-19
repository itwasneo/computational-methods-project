import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ApiClient } from './results/client.ts';
import { wireGuard } from './wire.ts';
import { shouldPoll } from './experiments/controller.ts';
import { capture, clone, harness, reviewed } from './testing/backend.mjs';
import { decodeGeometry } from './experiments/geometry.ts';

const analysis = JSON.parse(
  readFileSync(
    new URL('../../examples/interface-contracts/analysis-v1.json', import.meta.url),
    'utf8',
  ),
).examples;
const readOnly = JSON.parse(
  readFileSync(
    new URL('../../examples/interface-contracts/read-only-v1.json', import.meta.url),
    'utf8',
  ),
);

test('runtime schemas accept captured backend/analysis responses and reject missing fields, unsafe integers and extras', () => {
  for (const entry of [...Object.values(capture), ...Object.values(analysis)]) {
    const guard = wireGuard(entry.type);
    assert.equal(guard(entry.value), true, entry.type);
    for (const key of Object.keys(entry.value)) {
      const missing = { ...entry.value };
      delete missing[key];
      assert.equal(guard(missing), false, `${entry.type}.${key}`);
    }
    assert.equal(guard({ ...entry.value, unexpected: true }), false);
  }
  const job = clone('completed');
  job.job.recorded_trials = Number.MAX_SAFE_INTEGER + 1;
  assert.equal(wireGuard('JobEnvelope')(job), false);
  const bad = clone('result');
  bad.details.solver_seed = 42;
  assert.equal(wireGuard('JobDetailsResponse')(bad), false);
});

test('real geometry decoding preserves signed zero and rejects malformed permutations/lengths/nonfinite values', () => {
  const geometry = structuredClone(readOnly.geometry);
  const decoded = decodeGeometry(geometry);
  assert.ok(Object.is(decoded.coordinates[1], -0));
  assert.deepEqual([...decoded.route], [2, 0, 3, 1]);
  for (const damage of [
    (g) => (g.coordinates.data = ''),
    (g) => (g.instance.n_cities = 1000000),
    (g) => (g.tour.data = Buffer.alloc(16).toString('base64')),
    (g) => {
      const raw = Buffer.from(g.coordinates.data, 'base64');
      raw.writeDoubleLE(Infinity);
      g.coordinates.data = raw.toString('base64');
    },
  ]) {
    const bad = structuredClone(geometry);
    damage(bad);
    assert.throws(() => decodeGeometry(bad));
  }
  assert.equal(decodeGeometry({ ...geometry, tour: null }).route, null);
});

test('lost submission response retains the original preflight and key, including reconnect; never automatically resubmits', async () => {
  let attempts = 0;
  const { controller, calls } = harness({
    '/api/jobs/submit': (body) => {
      if (++attempts === 1) throw new Error('lost response');
      return {
        ...clone('submitted'),
        job: {
          ...clone('submitted').job,
          preflight_id: body.preflight_id,
          submission_key: body.submission_key,
        },
      };
    },
  });
  await reviewed(controller);
  await controller.submit();
  const pending = controller.state.pending;
  assert.ok(pending);
  assert.match(controller.state.operations.submission.error, /could not complete/);
  controller.disconnect();
  await controller.connect('fresh secret');
  assert.equal(attempts, 1);
  assert.deepEqual(controller.state.pending, pending);
  await controller.submit();
  assert.equal(controller.state.operations.submission.error, '');
  assert.equal(controller.state.pending, null);
  const submissions = calls.filter((c) => c.url === '/api/jobs/submit');
  assert.equal(submissions.length, 2);
  assert.equal(submissions[0].body.submission_key, submissions[1].body.submission_key);
  assert.equal(submissions[0].body.preflight_id, submissions[1].body.preflight_id);
  controller.disconnect();
});

test('editing invalidates reviewed configuration and cannot silently run a stale plan', async () => {
  const { controller, calls } = harness();
  await reviewed(controller);
  controller.invalidateReview();
  await controller.submit();
  assert.match(controller.state.operations.submission.error, /Review/);
  assert.equal(calls.filter((c) => c.url === '/api/jobs/submit').length, 0);
});

test('late generation cannot restore state after disconnect', async () => {
  let finish;
  const pending = new Promise((resolve) => (finish = resolve));
  const { controller } = harness({ '/api/instances/generate': () => pending });
  await controller.connect('secret');
  const generating = controller.generate({
    generator: 'uniform-v1',
    n_cities: 4,
    seed: '42',
    side_length: 1,
  });
  controller.disconnect();
  finish(clone('snapshot'));
  await generating;
  assert.equal(controller.state.connected, false);
  assert.equal(controller.state.snapshot, null);
});

test('verified publication rejects foreign job/attempt bindings instead of displaying another tour', async () => {
  const { controller } = harness({
    '/api/jobs/result': () => {
      const value = clone('result');
      value.details.attempt_id = 'different-attempt';
      return value;
    },
  });
  await controller.connect('secret');
  controller.state.selected = clone('completed').job;
  await controller.inspect(clone('result').details.attempt_id);
  assert.match(controller.state.operations.inspection.error, /binding mismatch/);
  assert.equal(controller.state.details, null);
  assert.equal(controller.state.geometry, null);
});

test('retry metadata survives reload without secrets and rejects a different root', async () => {
  const saved = new Map();
  const store = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, value),
    removeItem: (key) => saved.delete(key),
  };
  const first = harness(
    {
      '/api/jobs/submit': () => {
        throw new Error('lost');
      },
    },
    store,
  );
  await reviewed(first.controller);
  await first.controller.submit();
  const pending = first.controller.state.pending;
  assert.equal(saved.size, 1);
  assert.deepEqual(Object.keys(JSON.parse([...saved.values()][0])).sort(), [
    'key',
    'preflight',
    'root',
  ]);
  assert.ok(![...saved.values()][0].includes('secret'));
  first.controller.disconnect();
  const second = harness({}, store);
  assert.deepEqual(second.controller.state.pending, pending);
  await second.controller.connect('new secret');
  second.controller.state.capabilities.output_root.root_id = 'other-root';
  await second.controller.submit();
  assert.match(second.controller.state.operations.submission.error, /different output root/);
  assert.equal(second.calls.filter((c) => c.url === '/api/jobs/submit').length, 0);
  second.controller.state.capabilities.output_root.root_id = pending.root;
  await second.controller.submit();
  assert.equal(second.controller.state.operations.submission.error, '');
  assert.equal(saved.size, 0);
});

test('only definitive pre-admission rejections discard retry keys', async () => {
  for (const code of [
    'preflight_stale',
    'queue_full',
    'resource_limit',
    'execution_unavailable',
    'unauthorized',
  ]) {
    const { controller } = harness({ '/api/jobs/submit': () => ({ code, message: code }) });
    await reviewed(controller);
    await controller.submit();
    assert.equal(
      controller.state.operations.submission.error || controller.session.state.error,
      code,
    );
    assert.equal(
      controller.state.pending === null,
      ['preflight_stale', 'queue_full'].includes(code),
    );
  }
});

test('bounded transport cancels oversized bodies instead of buffering without a ceiling', async () => {
  let cancelled = false;
  const client = new ApiClient(
    async () =>
      new Response(
        new ReadableStream({
          pull(controller) {
            controller.enqueue(new Uint8Array(1024 * 1024));
          },
          cancel() {
            cancelled = true;
          },
        }),
      ),
  );
  await assert.rejects(client.workspace(new AbortController().signal), /invalid response/);
  assert.equal(cancelled, true);
});

test('polling only observes progressing jobs, never idle, terminal, held, busy or failed views', async () => {
  const { controller, calls } = harness();
  await controller.connect('secret');
  const count = calls.length;
  await controller.poll();
  assert.equal(calls.length, count);
  for (const state of ['completed', 'failed', 'cancelled', 'interrupted', 'held']) {
    controller.state.jobs = [{ ...clone('completed').job, state }];
    await controller.poll();
    assert.equal(calls.length, count, state);
  }
  for (const state of ['queued', 'preparing', 'running', 'stop_requested', 'recovering']) {
    controller.state.jobs = [{ ...clone('completed').job, state }];
    assert.equal(shouldPoll(controller.state), true, state);
  }
  controller.state.operations.jobs.busy = 'Loading';
  assert.equal(shouldPoll(controller.state), false);
  controller.state.operations.jobs.busy = '';
  controller.state.operations.jobs.error = 'Offline';
  assert.equal(shouldPoll(controller.state), false);
  controller.state.operations.jobs.error = '';
  controller.state.operations.instance.error = 'Invalid instance';
  assert.equal(shouldPoll(controller.state), true, 'unrelated errors must not pause polling');
  await controller.poll();
  assert.ok(calls.length > count);
});

test('failed fresh-rerun review cannot leave a previous plan executable', async () => {
  const { controller } = harness({
    '/api/jobs/review': () => ({ code: 'job_stale', message: 'Review changed' }),
  });
  await reviewed(controller);
  controller.state.selected = clone('completed').job;
  const result = await controller.rerun();
  assert.equal(result.ok, false);
  assert.equal(controller.state.review, null);
});

test('an unrelated input error preserves verified analysis and inspection', async () => {
  const { controller } = harness({
    '/api/instances/load': () => ({ code: 'invalid_artifact', message: 'Not an instance' }),
  });
  await reviewed(controller);
  const analysis = { table: 'trials' },
    geometry = clone('geometry');
  controller.state.analysis = analysis;
  controller.state.geometry = geometry;
  await controller.load('invalid');
  assert.equal(controller.state.analysis, analysis);
  assert.equal(controller.state.geometry, geometry);
});

test('snapshot download preserves raw uint64 and signed-zero artifact bytes', async () => {
  const raw = '{"seed":18446744073709551615,"coordinates":[[0.0,-0.0]]}';
  const client = new ApiClient(
    async (_url, options) =>
      new Response(raw, {
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Request-ID': options.headers['X-Request-ID'],
        },
      }),
  );
  const blob = await client.exportInstance(
    { workspace_id: 'w', session_id: 's', instance_snapshot_id: 'i' },
    new AbortController().signal,
  );
  assert.equal(await blob.text(), raw);
});
