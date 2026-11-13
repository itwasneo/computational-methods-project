import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import ts from 'typescript';
import { PAGE_DEFAULT, PAGE_MAX, GEOMETRY_BYTES_MAX } from './contracts.ts';

const wire = JSON.parse(readFileSync(new URL('../../examples/interface-contracts/read-only-v1.json', import.meta.url), 'utf8'));
const runs = JSON.parse(readFileSync(new URL('../../examples/interface-contracts/local-runs-v1.json', import.meta.url), 'utf8'));

// Compile the shared JSON as literal assignments: a cast would hide contract drift.
test('shared wire examples satisfy the TypeScript design types', () => {
  const directory = mkdtempSync(join(tmpdir(), 'tsp-contracts-'));
  try {
    const file = join(directory, 'golden.ts');
    const types = { session: 'SessionResponse', workspace: 'WorkspaceResponse', catalog: 'ArtifactPage', query: 'TrialQuery', page: 'TrialPage',
      dataset_request: 'DatasetRequest', dataset_status_request: 'DatasetStatusRequest', dataset: 'Dataset',
      details: 'TrialDetails', missing_details: 'TrialDetails', geometry: 'Geometry', stale_error: 'ErrorResponse' };
    const source = [
      `import type * as C from ${JSON.stringify(fileURLToPath(new URL('./contracts.ts', import.meta.url)))};`,
    ];
    for (const [name, type] of Object.entries(types)) {
      source.push(`const ${name}: C.${type} = ${JSON.stringify(wire[name])};`);
    }
    for (const [name, entry] of Object.entries(runs.examples)) {
      source.push(`const run_${name}: C.${entry.type} = ${JSON.stringify(entry.value)};`);
    }
    source.push('// @ts-expect-error local-run seeds must also stay strings',
      'const badRunSeed: C.CampaignConfiguration = {...run_preflight.configuration, solver_seed: 42};',
      '// @ts-expect-error arbitrary process IDs confer no control authority',
      'const badStop: C.JobControlRequest = {...run_stop, pid: 123};',
      '// @ts-expect-error submissions reference reviewed plans, not editable configuration',
      'const badSubmit: C.JobSubmitRequest = {...run_submit, configuration: run_preflight.configuration};');
    source.push('// @ts-expect-error seeds must not cross the wire as JS numbers',
      `const badSeed: C.TrialDetails = {...details, solver_seed: 18446744073709551615};`,
      '// @ts-expect-error unsupported API version',
      'const badVersion: C.TrialQuery = {...query, api_version: 2};');
    writeFileSync(file, source.join('\n'));
    const program = ts.createProgram([file], {
      strict: true, noEmit: true, skipLibCheck: true, target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
      allowImportingTsExtensions: true,
    });
    const diagnostics = ts.getPreEmitDiagnostics(program);
    assert.deepEqual(diagnostics.map(item => ts.flattenDiagnosticMessageText(item.messageText, '\n')), []);
  } finally { rmSync(directory, { recursive: true, force: true }); }
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
  for (let offset = 0; offset < raw.byteLength; offset += 8) writer.setFloat64(offset, view.getFloat64(offset, true), true);
  assert.deepEqual(Buffer.from(roundTrip), raw);
  const count = Buffer.alloc(8);
  count.writeBigUInt64BE(BigInt(geometry.instance.n_cities));
  assert.equal(createHash('sha256').update('tsp-coordinates-f64le-n2-v1\0').update(count).update(raw).digest('hex'), geometry.instance.coordinate_checksum);
  const tour = Buffer.from(geometry.tour.data, 'base64');
  assert.equal(tour.length, geometry.instance.n_cities * 4);
  assert.deepEqual(Array.from({ length: 4 }, (_, id) => tour.readUInt32LE(id * 4)), [2, 0, 3, 1]);
});

test('large seeds, missing observations and unknown provenance remain distinct', () => {
  assert.equal(BigInt(wire.details.solver_seed), (1n << 64n) - 1n);
  assert.equal(BigInt(wire.details.trial_seed), (1n << 53n) + 1n);
  assert.deepEqual(wire.details.source, { revision: null, dirty: null });
  assert.equal(wire.details.row.best_cost, 0);
  assert.equal(wire.missing_details.row.best_cost, null);
  assert.equal(wire.missing_details.row.search_time_s, null);
  assert.equal(wire.missing_details.artifact_state, 'missing');
  assert.equal(wire.missing_details.snapshot_id, null);
});
