import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import ts from 'typescript';
import { PAGE_DEFAULT, PAGE_MAX, GEOMETRY_BYTES_MAX } from './contracts.ts';

const wire = JSON.parse(
  readFileSync(
    new URL('../../examples/interface-contracts/read-only-v1.json', import.meta.url),
    'utf8',
  ),
);
const backend = JSON.parse(
  readFileSync(
    new URL('../../examples/interface-contracts/backend-v1.json', import.meta.url),
    'utf8',
  ),
);
const analysis = JSON.parse(
  readFileSync(
    new URL('../../examples/interface-contracts/analysis-v1.json', import.meta.url),
    'utf8',
  ),
);

// Compile the shared JSON as literal assignments: a cast would hide contract drift.
test('shared wire examples satisfy the TypeScript API types', () => {
  const directory = mkdtempSync(join(tmpdir(), 'tsp-contracts-'));
  try {
    const file = join(directory, 'golden.ts');
    const types = {
      session: 'SessionResponse',
      workspace: 'WorkspaceResponse',
      catalog: 'ArtifactPage',
      query: 'TrialQuery',
      page: 'TrialPage',
      dataset_request: 'DatasetRequest',
      dataset_status_request: 'DatasetStatusRequest',
      dataset: 'Dataset',
      geometry: 'VerifiedGeometry',
      stale_error: 'ErrorResponse',
    };
    const source = [
      `import type * as C from ${JSON.stringify(fileURLToPath(new URL('./contracts.ts', import.meta.url)))};`,
    ];
    for (const [name, type] of Object.entries(types)) {
      source.push(`const ${name}: C.${type} = ${JSON.stringify(wire[name])};`);
    }
    for (const [name, entry] of Object.entries(backend.examples)) {
      source.push(`const backend_${name}: C.${entry.type} = ${JSON.stringify(entry.value)};`);
    }
    for (const [name, entry] of Object.entries(analysis.examples)) {
      source.push(`const analysis_${name}: C.${entry.type} = ${JSON.stringify(entry.value)};`);
    }
    source.push(
      `import { ApiClient } from ${JSON.stringify(fileURLToPath(new URL('./results/client.ts', import.meta.url)))};`,
      `import { wireGuard } from ${JSON.stringify(fileURLToPath(new URL('./wire.ts', import.meta.url)))};`,
      'const client = new ApiClient(); const signal = new AbortController().signal;',
      '// @ts-expect-error endpoint responses are determined by the route, not a caller-provided type',
      'const wrongResponse: Promise<C.QueueStatus> = client.operation("/jobs/submit", {workspace_id: "w", session_id: "s", preflight_id: "p", submission_key: "k"}, signal);',
      '// @ts-expect-error control requests require explicit interruption confirmation',
      'client.operation("/jobs/control", {workspace_id: "w", session_id: "s", job_id: "j", expected_revision: "r", action: "stop_now"}, signal);',
      '// @ts-expect-error a schema name cannot be paired with an unrelated claimed response type',
      'wireGuard<C.JobSubmission>("QueueStatus");',
    );
    source.push(
      '// @ts-expect-error comparison settings cannot masquerade as scalar-table options',
      'const badAnalysisOptions: C.ScalarAnalysisQuery = {table: "summary", attempt_ids: null, limit: 1, cursor: null, baseline_implementation: "x"};',
      '// @ts-expect-error references are handles, not arbitrary filesystem paths',
      'const badReferencePath: C.ReferenceAnalysisQuery = {...analysis_references_request.query, path: "/etc/passwd"};',
    );
    source.push(
      '// @ts-expect-error verified result seeds remain strings even when small',
      'const badVerifiedSeed: C.VerifiedDetails = {...backend_result.details, trial_seed: 42};',
      '// @ts-expect-error geometry has no caller-selected record paths',
      'const badGeometryPath: C.JobGeometryRequest = {...backend_geometry_request, path: "outside.json"};',
    );
    source.push(
      '// @ts-expect-error scientific seeds must stay strings',
      'const badRunSeed: C.ScientificConfiguration = {...backend_preflight.configuration, solver_seed: 42};',
      '// @ts-expect-error arbitrary process IDs confer no control authority',
      'const badStop: C.ExecutionControlRequest = {...backend_preflight_request, job_id: "j", expected_revision: "r", action: "stop_now", confirm_stop_now: true, pid: 123};',
      '// @ts-expect-error submissions reference reviewed plans, not editable configuration',
      'const badSubmit: C.JobSubmitRequest = {...backend_preflight_request, preflight_id: "p", submission_key: "key", configuration: backend_preflight.configuration};',
      '// @ts-expect-error admitted jobs always have a campaign identity',
      'const badCampaign: C.JobRecord = {...backend_completed.job, campaign_id: null};',
      '// @ts-expect-error unsupported API version',
      'const badVersion: C.TrialQuery = {...query, api_version: 2};',
    );
    writeFileSync(file, source.join('\n'));
    const program = ts.createProgram([file], {
      strict: true,
      noEmit: true,
      skipLibCheck: true,
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      moduleResolution: ts.ModuleResolutionKind.Bundler,
      allowImportingTsExtensions: true,
      resolveJsonModule: true,
    });
    const diagnostics = ts.getPreEmitDiagnostics(program);
    assert.deepEqual(
      diagnostics.map((item) => ts.flattenDiagnosticMessageText(item.messageText, '\n')),
      [],
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
  assert.equal(PAGE_DEFAULT, 100);
  assert.equal(PAGE_MAX, 500);
  assert.equal(GEOMETRY_BYTES_MAX, 16 * 1024 * 1024);
});

test('float64 and uint32 binary envelopes preserve signed zero, order and checksum', () => {
  const { geometry } = wire;
  const raw = Buffer.from(geometry.coordinates.data, 'base64');
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  assert.equal(raw.byteLength, geometry.instance.n_cities * 16);
  assert.ok(Object.is(view.getFloat64(8, true), -0));
  assert.ok(Object.is(view.getFloat64(0, true), 0));
  const roundTrip = new Uint8Array(raw.byteLength);
  const writer = new DataView(roundTrip.buffer);
  for (let offset = 0; offset < raw.byteLength; offset += 8)
    writer.setFloat64(offset, view.getFloat64(offset, true), true);
  assert.deepEqual(Buffer.from(roundTrip), raw);
  const count = Buffer.alloc(8);
  count.writeBigUInt64BE(BigInt(geometry.instance.n_cities));
  assert.equal(
    createHash('sha256')
      .update('tsp-coordinates-f64le-n2-v1\0')
      .update(count)
      .update(raw)
      .digest('hex'),
    geometry.instance.coordinate_checksum,
  );
  const tour = Buffer.from(geometry.tour.data, 'base64');
  assert.equal(tour.length, geometry.instance.n_cities * 4);
  assert.deepEqual(
    Array.from({ length: 4 }, (_, id) => tour.readUInt32LE(id * 4)),
    [2, 0, 3, 1],
  );
});

test('large seeds and missing observations remain distinct from numbers and zero', () => {
  assert.equal(BigInt(backend.examples.result.value.details.solver_seed), (1n << 64n) - 1n);
  assert.equal(wire.page.items[0].best_cost, 0);
  assert.equal(wire.page.items[1].best_cost, null);
  assert.equal(wire.page.items[1].search_time_s, null);
  assert.equal(wire.page.items[1].usable, false);
});
