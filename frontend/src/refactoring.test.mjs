import { test } from 'node:test';
import assert from 'node:assert/strict';
import { harness, clone, reviewed } from './testing/backend.mjs';
import { ApiClient, ApiError } from './results/client.ts';
import { ResultsController } from './results/controller.ts';
import { configurationDraftStore, isConfigurationDraft } from './drafts.ts';
import { Navigation, parseRoute, routeHash } from './navigation.ts';
import { queryIdentity } from './analysis-query.ts';
import { actionLabel, cellText, exactTitle } from './display.ts';

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
};
const storage = () => {
  const values = new Map();
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
};

test('a stalled job read cannot swallow stop intent or overwrite its newer result', async () => {
  const waiting = deferred();
  let hold = false;
  const stopping = {
    ...clone('completed').job,
    state: 'stop_requested',
    result_revision: null,
    revision: 'new-control-revision',
  };
  const { controller, calls } = harness({
    '/api/jobs/query': () => {
      if (hold) {
        hold = false;
        return waiting.promise;
      }
      return { ...clone('jobs'), items: [], next_cursor: null };
    },
    '/api/jobs/control': () => ({ ...clone('completed'), job: stopping }),
  });
  await controller.connect('secret');
  controller.state.selected = { ...stopping, state: 'running' };
  hold = true;
  const reading = controller.refreshJobs();
  assert.equal(controller.state.operations.jobs.busy, 'Refreshing jobs');
  const result = await controller.control('stop_now');
  assert.equal(result.ok, true);
  assert.equal(calls.filter((call) => call.url === '/api/jobs/control').length, 1);
  waiting.resolve(clone('jobs'));
  await reading;
  assert.equal(controller.state.selected.state, 'stop_requested');
  assert.equal(controller.state.operations.jobs.busy, '');
});

test('session and catalog ownership is shared, while disposing a feature never revokes the grant', async () => {
  let revision = 'one';
  const { controller, client, calls } = harness({
    '/api/artifacts/query': (body) => ({
      workspace_id: body.workspace_id,
      revision,
      items: [],
      next_cursor: null,
    }),
    '/api/artifacts/refresh': (body) => ({
      workspace_id: body.workspace_id,
      revision: (revision = 'two'),
    }),
  });
  const saved = new ResultsController(() => {}, client, controller.session);
  await controller.connect('secret');
  assert.equal(saved.state.paired, true);
  assert.equal(saved.state.catalogRevision, 'one');
  await saved.refresh();
  assert.equal(controller.state.catalogRevision, 'two');
  assert.equal(saved.state.catalogRevision, 'two');
  assert.equal(saved.state.busy, '');
  assert.equal(saved.state.artifacts, controller.state.artifacts);
  saved.dispose();
  await client.workspace(new AbortController().signal);
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer token');
  controller.dispose();
  assert.equal(controller.session.state.status, 'ready');
  controller.session.dispose();
});

test('authorization expiry atomically resets both consumers but retains uncertain admission metadata', async () => {
  const { controller, client } = harness({
    '/api/jobs/submit': () => ({ code: 'unauthorized', message: 'expired' }),
  });
  const saved = new ResultsController(() => {}, client, controller.session);
  await reviewed(controller);
  await controller.submit();
  assert.equal(controller.state.connected, false);
  assert.equal(controller.state.capabilities, null);
  assert.equal(saved.state.paired, false);
  assert.equal(saved.state.workspace, null);
  assert.ok(controller.state.pending);
  assert.equal(controller.session.state.status, 'expired');
});

test('invalid retry metadata remains visible across disconnect and requires explicit clearing', async () => {
  const store = storage();
  store.setItem('tsp-hpc-pending-submission-v1', '{broken');
  const { controller, calls } = harness({}, store);
  assert.equal(controller.state.invalidRetry, true);
  controller.disconnect();
  await controller.connect('secret');
  assert.equal(controller.state.invalidRetry, true);
  await controller.submit();
  assert.equal(calls.filter((call) => call.url === '/api/jobs/submit').length, 0);
  const cleared = await controller.abandonPending();
  assert.equal(cleared.ok, true);
  assert.equal(controller.state.invalidRetry, false);
  assert.equal(store.values.size, 0);
});

test('failed storage removal does not hide an unresolved retry reference', async () => {
  const store = storage();
  store.setItem('tsp-hpc-pending-submission-v1', '{broken');
  store.removeItem = () => {
    throw new Error('storage disabled');
  };
  const { controller } = harness({}, store);
  const result = await controller.abandonPending();
  assert.equal(result.ok, false);
  assert.equal(controller.state.invalidRetry, true);
});

test('expired reviews cannot admit work, while an existing pending key bypasses only the local expiry check', async () => {
  let lose = true;
  const { controller, calls } = harness({
    '/api/jobs/submit': (body) => {
      if (lose) {
        lose = false;
        throw new Error('lost');
      }
      const response = clone('submitted');
      Object.assign(response.job, {
        submission_key: body.submission_key,
        preflight_id: body.preflight_id,
      });
      return response;
    },
  });
  await reviewed(controller);
  controller.state.reviewExpiresAt = 0;
  assert.equal((await controller.submit()).ok, false);
  assert.equal(calls.filter((call) => call.url === '/api/jobs/submit').length, 0);
  await controller.review(clone('preflight_request').configuration);
  await controller.submit();
  const pending = controller.state.pending;
  controller.state.reviewExpiresAt = 0;
  controller.state.review = null;
  await controller.submit();
  const submissions = calls.filter((call) => call.url === '/api/jobs/submit');
  assert.equal(submissions.length, 2);
  assert.equal(submissions[1].body.submission_key, pending.key);
});

test('acknowledged admission refreshes queue authority without redundant job-list polling', async () => {
  let admitted = false,
    continued = false;
  const { controller, calls } = harness({
    '/api/jobs/submit': (body) => {
      admitted = true;
      const response = clone('submitted');
      Object.assign(response.job, {
        submission_key: body.submission_key,
        preflight_id: body.preflight_id,
        state: 'held',
      });
      return response;
    },
    '/api/queue/status': () => ({
      ...clone('queue'),
      dispatch: 'held',
      queue_revision: admitted ? 'after-admission' : 'before-admission',
    }),
    '/api/queue/continue': (body) => {
      assert.equal(body.expected_queue_revision, 'after-admission');
      continued = true;
      return { ...clone('queue'), dispatch: 'enabled', queue_revision: 'continued' };
    },
  });
  await reviewed(controller);
  const reads = calls.filter((call) => call.url === '/api/jobs/query').length;
  assert.equal((await controller.submit()).ok, true);
  assert.equal(controller.state.queue.queue_revision, 'after-admission');
  assert.equal(calls.filter((call) => call.url === '/api/jobs/query').length, reads);
  assert.equal(continued, false, 'admission never implicitly continues held dispatch');
  assert.equal((await controller.continueQueue()).ok, true);
  assert.equal(continued, true);
});

test('deadlines release read state, preserve structured error metadata and never automatically retry', async () => {
  let count = 0;
  const api = new ApiClient(
    (_url, { signal }) =>
      new Promise((_resolve, reject) => {
        count++;
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      }),
    5,
  );
  await assert.rejects(
    api.workspace(new AbortController().signal),
    (error) =>
      error instanceof ApiError &&
      error.code === 'request_timeout' &&
      error.retryable &&
      error.requestId.length > 0,
  );
  assert.equal(count, 1);
});

test('a submission deadline retains the exact durable retry key and releases its own busy state', async () => {
  const { controller, calls } = harness(
    {
      '/api/jobs/submit': (_body, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(signal.reason), { once: true });
        }),
    },
    null,
    20,
  );
  await reviewed(controller);
  const result = await controller.submit();
  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'request_timeout');
  assert.ok(controller.state.pending);
  assert.equal(controller.state.operations.submission.busy, '');
  assert.equal(calls.filter((call) => call.url === '/api/jobs/submit').length, 1);
});

test('a shared stale result source clears its analysis and geometry, unlike unrelated input errors', async () => {
  const { controller } = harness({
    '/api/jobs/analysis': () => ({ code: 'job_stale', message: 'Source changed' }),
  });
  await controller.connect('secret');
  controller.state.selected = clone('completed').job;
  await controller.inspect(clone('result').details.attempt_id);
  assert.ok(controller.state.geometry);
  const query = { table: 'summary', attempt_ids: null, cursor: null, limit: 100 };
  const result = await controller.analyze(query);
  assert.equal(result.ok, false);
  assert.equal(result.cancelled, false);
  assert.equal(controller.state.geometry, null);
  assert.equal(controller.state.details, null);
  assert.equal(controller.state.analysis, null);
  assert.deepEqual(controller.state.analysisRequested, query);
});

test('tab configuration drafts are root-scoped, bounded, and contain no reviewed execution authority', () => {
  const data = {
    implementations: ['visited-scan-v1'],
    modes: ['dense'],
    starts: '0,1',
    repetitions: 2,
    solverSeed: '18446744073709551615',
    timed: false,
    seconds: 1,
    history: true,
    interval: 0,
    points: 10,
    original: null,
    originalSnapshot: '',
    snapshot: 'snapshot',
  };
  const store = storage(),
    first = configurationDraftStore('root-a', store);
  assert.equal(first.save(data), true);
  assert.deepEqual(first.read(), data);
  assert.equal(configurationDraftStore('root-b', store).read(), null);
  assert.equal(isConfigurationDraft({ ...data, preflight_id: 'not-authority' }), false);
  assert.equal(isConfigurationDraft({ ...data, token: 'secret' }), false);
  assert.equal(first.save({ ...data, starts: '0'.repeat(20_000) }), false);
  assert.equal(configurationDraftStore('root-a', null).save(data), false);
  store.setItem('tsp-hpc-configuration-draft-v1:root-a', '{bad');
  assert.equal(first.read(), null);
});

test('navigation remembers a completed experiment without navigating the active view', () => {
  const previous = globalThis.window;
  const events = new EventTarget();
  const location = { hash: '#saved' };
  globalThis.window = {
    location,
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    history: { pushState: (_state, _title, hash) => (location.hash = hash) },
  };
  try {
    const navigation = new Navigation();
    const notices = [];
    navigation.subscribe((route, navigated) => notices.push({ route, navigated }));
    const experiment = { view: 'experiments', root: 'root-a', job: 'acknowledged-job' };
    navigation.rememberExperiment(experiment);
    assert.equal(location.hash, '#saved');
    assert.equal(navigation.state.view, 'saved');
    assert.deepEqual(notices.at(-1), { route: { view: 'saved' }, navigated: false });
    navigation.go(navigation.experiments);
    assert.equal(parseRoute(location.hash).job, 'acknowledged-job');
    navigation.dispose();
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
});

test('connection failure is not misreported as user cancellation by cleanup', async () => {
  const { controller } = harness({
    '/api/session': () => {
      throw new Error('offline');
    },
  });
  const outcome = await controller.connect('secret');
  assert.equal(outcome.ok, false);
  assert.equal(outcome.cancelled, false);
  assert.match(controller.session.state.error, /could not complete/);
});

test('navigation contains only durable context and validates query shape without granting execution', () => {
  const route = {
    view: 'experiments',
    root: 'root',
    job: 'job',
    attempt: 'attempt',
    analysis: { table: 'summary', cursor: null, limit: 100, attempt_ids: null },
  };
  assert.deepEqual(parseRoute(routeHash(route)), route);
  assert.deepEqual(parseRoute('#saved?token=secret'), { view: 'saved' });
  assert.deepEqual(parseRoute('#experiments?token=secret&preflight_id=p'), { view: 'experiments' });
  const invalid =
    '#experiments?analysis=' +
    encodeURIComponent(
      JSON.stringify({ ...route.analysis, baseline_implementation: 'wrong-query' }),
    );
  assert.equal(parseRoute(invalid).analysis, undefined);
  assert.equal(parseRoute('#' + 'x'.repeat(9000)).view, 'experiments');
  assert.equal(
    queryIdentity(route.analysis),
    queryIdentity({ limit: 100, attempt_ids: null, cursor: 'page-2', table: 'summary' }),
  );
});

test('duration metadata excludes counts and accessible action names include both visible and full identities', () => {
  assert.equal(cellText('search_time_s_count', 100), '100');
  assert.equal(exactTitle('search_time_s_count', 100), 'search_time_s_count: 100');
  assert.equal(cellText('search_time_s_mean', 0.001), '1 ms');
  const id = 'a'.repeat(32);
  assert.equal(actionLabel('Inspect attempt', id), `Inspect attempt aaaaaaaa…aaaaaa (${id})`);
  assert.equal(actionLabel('Inspect attempt', 'short'), 'Inspect attempt short');
});
