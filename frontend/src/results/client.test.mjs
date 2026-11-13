import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as c from './client.ts';
import { RUN_LIST_MAX } from '../contracts.ts';

const wire = JSON.parse(readFileSync(new URL('../../../examples/interface-contracts/local-runs-v1.json', import.meta.url), 'utf8'));
const example = name => structuredClone(wire.examples[name].value);
const guards = {
  InstanceSnapshot: c.isInstanceSnapshot, RunCapabilities: c.isRunCapabilities,
  CampaignPreflight: c.isCampaignPreflight, QueueStatus: c.isQueueStatus,
  JobStatus: c.isJobStatus, JobResultBinding: c.isJobResultBinding, RunError: c.isRunError,
};

test('local-run responses validate exactly; missing/extra fields and non-objects fail', () => {
  for (const [name, entry] of Object.entries(wire.examples)) {
    const check = guards[entry.type];
    if (!check) continue;
    assert.equal(check(entry.value), true, name);
    for (const field of Object.keys(entry.value)) {
      const missing = example(name);
      delete missing[field];
      assert.equal(check(missing), false, `${name}.${field}`);
    }
    for (const bad of [null, [], true, 'response', 1, { ...entry.value, path: '/not-authority' }]) {
      assert.equal(check(bad), false, name);
    }
  }
});

test('malformed response fields and inconsistent lifecycle snapshots are rejected', () => {
  for (const entry of wire.invalid) {
    const value = example(entry.base);
    let target = value;
    for (const key of entry.path.slice(0, -1)) target = target[key];
    target[entry.path.at(-1)] = entry.value;
    assert.equal(guards[wire.examples[entry.base].type](value), false, `${entry.base}.${entry.path.join('.')}`);
  }
});

test('strict transport keeps large seeds, empty work, finite numbers and Unicode bounds', () => {
  const preflight = example('preflight');
  assert.equal(BigInt(preflight.configuration.solver_seed), (1n << 64n) - 1n);
  preflight.configuration.start_cities = [];
  preflight.configuration.max_matrix_bytes = 0;
  assert.equal(c.isCampaignPreflight(preflight), true);
  for (const bad of [true, '1', -1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(c.isCampaignPreflight({ ...preflight, estimated_matrix_bytes: bad }), false);
  }
  const caps = example('capabilities');
  caps.output_root.display_name = '🌍'.repeat(200);
  assert.equal(c.isRunCapabilities(caps), true);
  caps.output_root.display_name += 'x';
  assert.equal(c.isRunCapabilities(caps), false);
  assert.equal(c.isRunCapabilities({ ...caps, local_runs_enabled: false, output_root: null }), true);
  const oversized = example('preflight');
  oversized.configuration.start_cities = Array.from({ length: RUN_LIST_MAX + 1 }, (_, i) => i);
  assert.equal(c.isCampaignPreflight(oversized), false);
});

test('job lifecycle snapshots distinguish stop requests, completion, failed trials and partial results', () => {
  const pending = example('job');
  for (const state of ['queued', 'held']) assert.equal(c.isJobStatus({ ...pending, state }), true);
  const active = { ...pending, queue_position: null, campaign_id: 'campaign-fixture', elapsed_time_s: 0 };
  for (const state of ['preparing', 'running', 'failed', 'cancelled', 'interrupted']) {
    assert.equal(c.isJobStatus({ ...active, state }), true, state);
  }
  for (const [state, stop_mode] of [['stop_requested', 'after_trial'], ['stopping', 'now']]) {
    const value = { ...active, state, stop_mode, recorded_trials: 1 };
    assert.equal(c.isJobStatus(value), true);
    assert.equal(c.isJobStatus({ ...value, stop_mode: null }), false);
    assert.equal(c.isJobStatus({ ...value, result_handle: 'still-being-written' }), false);
  }
  const completed = { ...active, state: 'completed', recorded_trials: 8, failed_trials: 1, result_handle: 'shard' };
  assert.equal(c.isJobStatus(completed), true);
  for (const [field, value] of [['campaign_id', null], ['result_handle', null], ['recorded_trials', 7], ['failed_trials', 9], ['elapsed_time_s', NaN]]) {
    assert.equal(c.isJobStatus({ ...completed, [field]: value }), false, field);
  }
  assert.equal(c.isJobStatus({ ...active, state: 'interrupted', recorded_trials: 2, result_handle: 'partial-shard' }), true);
});

test('response correlation rejects old sessions/workspaces/requests, not just malformed shapes', () => {
  const request = example('preflight_request');
  const response = example('preflight');
  assert.equal(c.matchesRunContext(response, request), true);
  for (const field of ['request_id', 'workspace_id', 'session_id']) {
    const stale = { ...response, [field]: 'old-context' };
    assert.equal(c.isCampaignPreflight(stale), true); // Shape is not freshness or permission.
    assert.equal(c.matchesRunContext(stale, request), false);
  }
  assert.equal(c.isRunError(example('revoked')), true);
  assert.equal(example('revoked').recovery, 'owner_pairing');
  assert.equal(example('result').instance_snapshot_id, response.instance_snapshot_id);
  assert.notEqual(example('result').attempt_id, example('result').job_id);
});
