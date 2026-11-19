import { readFileSync } from 'node:fs';
import { ApiClient } from '../results/client.ts';
import { ExperimentController } from '../experiments/controller.ts';

export const capture = JSON.parse(
  readFileSync(
    new URL('../../../examples/interface-contracts/backend-v1.json', import.meta.url),
    'utf8',
  ),
).examples;
export const clone = (name) => structuredClone(capture[name].value);
export function harness(overrides = {}, store = null, timeout = 30_000) {
  const calls = [];
  const client = new ApiClient(async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ url, body, headers: options.headers });
    const defaults = {
      '/api/session': {
        token_type: 'Bearer',
        session_token: 'token',
        session_id: clone('execution').session_id,
      },
      '/api/workspace': {
        workspace: {
          workspace_id: clone('execution').workspace_id,
          display_name: 'workspace',
          access: 'read_only',
        },
      },
      '/api/runs/capabilities': clone('capabilities'),
      '/api/artifacts/query': {
        workspace_id: body.workspace_id,
        revision: 'catalog',
        items: [],
        next_cursor: null,
      },
      '/api/artifacts/refresh': { workspace_id: body.workspace_id, revision: 'catalog' },
      '/api/jobs/query': { ...clone('jobs'), items: [], next_cursor: null },
      '/api/jobs/review': clone('accepted_review'),
      '/api/queue/status': clone('queue'),
      '/api/instances/generate': clone('snapshot'),
      '/api/instances/snapshot': clone('snapshot'),
      '/api/runs/preflight': clone('preflight'),
      '/api/jobs/submit': {
        ...clone('submitted'),
        job: {
          ...clone('submitted').job,
          submission_key: body.submission_key,
          preflight_id: body.preflight_id,
        },
      },
      '/api/jobs/result': clone('result'),
      '/api/jobs/geometry': clone('geometry'),
    };
    const data = overrides[url] ? await overrides[url](body, options.signal) : defaults[url];
    return new Response(
      JSON.stringify({ ...data, api_version: 1, request_id: options.headers['X-Request-ID'] }),
      { status: data.code ? 409 : 200 },
    );
  }, timeout);
  const controller = new ExperimentController(() => {}, client, store);
  return { controller, calls, client };
}
export async function reviewed(controller) {
  const connection = await controller.connect('secret');
  if (!connection.ok) throw connection.error;
  const generation = await controller.generate({
    generator: 'uniform-v1',
    n_cities: 4,
    seed: '18446744073709551615',
    side_length: 1,
  });
  if (!generation.ok) throw generation.error;
  const result = await controller.review(clone('preflight_request').configuration);
  if (!result.ok) throw result.error;
  return result.value;
}
