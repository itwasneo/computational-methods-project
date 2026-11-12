import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError, PAIRING_PATTERN } from './client.ts';
import { ResultsController } from './controller.ts';
import { filterValues } from './fields.ts';

test('pairing input pattern supports HTML Unicode-sets regex mode', () => {
  const expression = new RegExp(`^(?:${PAIRING_PATTERN})$`, 'v');
  assert.ok(expression.test('Aa09_-'.repeat(8)));
  assert.ok(!expression.test('invalid secret!'));
});

test('default client binds native fetch to the global receiver', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = function (_url, options) {
      assert.equal(this, globalThis, 'browser fetch must not receive ApiClient as its receiver');
      return Promise.resolve(new Response(JSON.stringify({ api_version: 1,
        request_id: options.headers['X-Request-ID'],
        workspace: { workspace_id: 'workspace', display_name: 'Test', access: 'read_only' } })));
    };
    const client = new ApiClient();
    const response = await client.workspace(new AbortController().signal);
    assert.equal(response.workspace.workspace_id, 'workspace');
  } finally { globalThis.fetch = original; }
});

// Wire fixtures for orchestration tests, not numerical evidence.
const row = { attempt_id: 'attempt', instance_id: 'instance', implementation: null,
  n_cities: 4, algorithm: 'nn', outcome: 'failed', best_cost: null, search_time_s: 0,
  campaign_complete: false, duplicate_execution: true, usable: false };
const dataset = { dataset_id: 'dataset', workspace_id: 'workspace', state: 'ready', revision: 'revision',
  diagnostics: [], diagnostics_total: 0, diagnostics_truncated: false };
const candidate = { artifact_handle: 'shard', display_name: 'runs.jsonl', kind: 'result_shard', revision: 'catalog', validation: 'unvalidated' };
const deferred = () => { let resolve; const promise = new Promise(done => resolve = done); return { promise, resolve }; };
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(overrides = {}) {
  const calls = [];
  const fetcher = async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ url, options, body });
    const context = { api_version: 1, request_id: options.headers['X-Request-ID'],
      ...Object.fromEntries(['workspace_id', 'dataset_id', 'revision'].filter(key => key in body).map(key => [key, body[key]])) };
    const defaults = {
      '/api/session': { token_type: 'Bearer', session_token: 'private-token' },
      '/api/workspace': { workspace: { workspace_id: 'workspace', display_name: 'Test', access: 'read_only' } },
      '/api/artifacts/query': { revision: 'catalog', items: [candidate], next_cursor: null },
      '/api/artifacts/refresh': { revision: 'catalog' },
      '/api/datasets': dataset,
      '/api/datasets/status': dataset,
      '/api/trials/query': { query_key: 'query', items: [row], next_cursor: null },
    };
    const result = overrides[url] ? await overrides[url](body, options) : defaults[url];
    return new Response(JSON.stringify({ ...context, ...result }), { status: result.code ? 409 : 200 });
  };
  const api = new ApiClient(fetcher);
  const controller = new ResultsController(() => {}, api);
  return { controller, api, calls };
}

async function ready(controller) { await controller.connect('private-secret'); await controller.load(['shard'], null); }

test('pair, select, ingest and query use same-origin requests and memory-only credentials', async t => {
  const { controller, calls } = harness(); t.after(() => controller.dispose());
  await ready(controller);
  assert.equal(controller.state.paired, true);
  assert.deepEqual(controller.state.page.items, [row]);
  assert.equal(controller.state.page.items[0].search_time_s, 0);
  assert.equal(controller.state.page.items[0].best_cost, null);
  assert.ok(!JSON.stringify(controller.state).includes('private-'));
  assert.equal(calls[0].options.headers.Authorization, undefined);
  for (const call of calls.slice(1)) {
    assert.equal(call.options.headers.Authorization, 'Bearer private-token');
    assert.ok(!JSON.stringify(call.body).includes('private-secret'));
    assert.ok(call.url.startsWith('/api/'));
    assert.equal(call.options.cache, 'no-store'); assert.equal(call.options.credentials, 'omit');
  }
  const sent = calls.find(call => call.url === '/api/datasets').body;
  assert.deepEqual(sent.result_handles, ['shard']); assert.equal(sent.expected_manifest_handle, null);
  assert.equal(sent.artifact_catalog_revision, 'catalog');
});

test('filters remain typed and server requests, not current-page transformations', async t => {
  assert.deepEqual(filterValues('best_cost', '0\n4.5\n0', true), { field: 'best_cost', values: [0, 4.5, null] });
  assert.deepEqual(filterValues('implementation', ' A \na', false).values, [' A ', 'a']);
  assert.deepEqual(filterValues('outcome', '', false).values, []);
  for (const text of ['true', 'null', '0x10', 'NaN', '1e999']) assert.throws(() => filterValues('best_cost', text, false));
  assert.throws(() => filterValues('n_cities', '2.5', false));
  assert.throws(() => filterValues('n_cities', '2.00000000000000001', false));
  assert.throws(() => filterValues('n_cities', '9007199254740992', false));
  assert.throws(() => filterValues('attempt_id', Array.from({ length: 501 }, (_, i) => String(i)).join('\n'), false));
  const { controller, calls } = harness(); t.after(() => controller.dispose()); await ready(controller);
  const filters = [filterValues('best_cost', '0', true)];
  const sort = { field: 'best_cost', direction: 'descending' };
  await controller.query(filters, sort);
  assert.deepEqual(calls.at(-1).body.filters, filters); assert.deepEqual(calls.at(-1).body.sort, sort);
  assert.deepEqual(controller.state.page.items, [row]); // No browser scientific filtering.
});

test('pagination sends opaque cursors and resets them when query changes', async t => {
  const { controller, calls } = harness({ '/api/trials/query': body => ({ query_key: 'query',
    items: body.cursor ? [] : [row], next_cursor: body.cursor ? null : 'next-cursor' }) });
  t.after(() => controller.dispose()); await ready(controller);
  await controller.navigate(1);
  assert.equal(calls.at(-1).body.cursor, 'next-cursor');
  assert.equal(controller.state.pageIndex, 1); assert.deepEqual(controller.state.page.items, []);
  await controller.navigate(0); assert.equal(calls.at(-1).body.cursor, null);
  await controller.query([], { field: 'attempt_id', direction: 'ascending' });
  assert.equal(calls.at(-1).body.cursor, null); assert.equal(controller.state.pageIndex, 0);
});

test('source selection and refresh immediately discard the visible dataset', async t => {
  const { controller } = harness(); t.after(() => controller.dispose()); await ready(controller);
  controller.selectionChanged(); assert.equal(controller.state.page, null); assert.equal(controller.state.dataset, null);
  await controller.load(['shard'], null);
  const refresh = controller.refresh();
  assert.equal(controller.state.page, null); assert.equal(controller.state.dataset, null);
  await refresh; assert.equal(controller.state.catalogRevision, 'catalog');
});

test('late requests cannot replace a newer query or restore a disconnected session', async t => {
  const pending = deferred(); let delayed = false;
  const { controller } = harness({ '/api/trials/query': () => delayed ? pending.promise : { query_key: 'query', items: [row], next_cursor: null } });
  t.after(() => controller.dispose()); await ready(controller);
  delayed = true; const old = controller.query(); delayed = false;
  await controller.query([], { field: 'algorithm', direction: 'ascending' });
  pending.resolve({ query_key: 'obsolete', items: [], next_cursor: null }); await old;
  assert.equal(controller.state.page.query_key, 'query');
  const pairing = deferred();
  const other = harness({ '/api/session': () => pairing.promise }); t.after(() => other.controller.dispose());
  const connect = other.controller.connect('private-secret'); other.controller.disconnect();
  pairing.resolve({ token_type: 'Bearer', session_token: 'obsolete-token' }); await connect;
  assert.equal(other.controller.state.paired, false); assert.equal(other.controller.state.workspace, null);
  await other.api.workspace(new AbortController().signal);
  assert.equal(other.calls.at(-1).options.headers.Authorization, undefined);
});

test('loading is observable, failed diagnostics persist, stale errors remove rows', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { controller } = harness({ '/api/datasets': () => ({ ...dataset, state: 'loading', revision: null }),
    '/api/datasets/status': () => ({ ...dataset, state: 'failed', revision: null,
      diagnostics: [{ code: 'invalid_artifact', message: 'Invalid shard.', artifact_handle: 'shard', attempt_id: null }], diagnostics_total: 1 }) });
  t.after(() => controller.dispose()); await controller.connect('private-secret');
  const load = controller.load(['shard'], null); await flush();
  assert.equal(controller.state.dataset.state, 'loading'); assert.equal(controller.state.page, null);
  t.mock.timers.tick(500); await load;
  assert.equal(controller.state.dataset.state, 'failed'); assert.equal(controller.state.dataset.diagnostics[0].message, 'Invalid shard.');
  const other = harness({ '/api/datasets/status': () => ({ ...dataset, state: 'stale', revision: null }) });
  t.after(() => other.controller.dispose()); await ready(other.controller);
  t.mock.timers.tick(5000); await flush();
  assert.equal(other.controller.state.page, null); assert.equal(other.controller.state.errorCode, 'stale_revision');
});

test('API rejects mismatched context, unsafe metadata, unsupported shapes and malformed responses', async () => {
  for (const result of [
    { query_key: 'query', items: [row], next_cursor: null, revision: 'obsolete' },
    { query_key: 'query', items: [{ ...row, n_cities: 2**53 }], next_cursor: null },
    { query_key: 'query', items: [{ ...row, usable: 'yes' }], next_cursor: null },
    { query_key: 'query', items: [row], next_cursor: null, request_id: 'obsolete' },
  ]) {
    const { api } = harness({ '/api/trials/query': () => result });
    await assert.rejects(api.trials({ workspace_id: 'workspace', dataset_id: 'dataset', revision: 'revision', filters: [], sort: null, limit: 100, cursor: null }, new AbortController().signal),
      error => error instanceof ApiError && error.code === 'invalid_response');
  }
});

test('authorization loss clears session data and transport failures are explicit', async t => {
  let denied = false;
  const { controller } = harness({ '/api/trials/query': () => denied
    ? { code: 'unauthorized', message: 'Session expired.' }
    : { query_key: 'query', items: [row], next_cursor: null } });
  t.after(() => controller.dispose()); await ready(controller); denied = true;
  await controller.query();
  assert.equal(controller.state.paired, false); assert.equal(controller.state.dataset, null);
  assert.equal(controller.state.workspace, null); assert.equal(controller.state.page, null);
  const api = new ApiClient(async () => { throw new Error('private transport detail'); });
  await assert.rejects(api.workspace(new AbortController().signal), error =>
    error.code === 'connection_failed' && !error.message.includes('private transport detail'));
});

test('catalog pages must share revision and query pages must share query key', async t => {
  const { controller } = harness({ '/api/artifacts/query': body => body.cursor
    ? { revision: 'changed', items: [], next_cursor: null }
    : { revision: 'catalog', items: [candidate], next_cursor: 'more' } });
  t.after(() => controller.dispose()); await controller.connect('private-secret');
  assert.equal(controller.state.errorCode, 'stale_revision'); assert.equal(controller.state.catalogRevision, null);
  const other = harness({ '/api/trials/query': body => ({ query_key: body.cursor ? 'changed' : 'query', items: [row], next_cursor: 'more' }) });
  t.after(() => other.controller.dispose()); await ready(other.controller); await other.controller.navigate(1);
  assert.equal(other.controller.state.errorCode, 'invalid_response'); assert.equal(other.controller.state.page, null);
});
