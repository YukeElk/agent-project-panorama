import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture } from '../process/development-helpers.mjs';
import { createStandaloneServer } from '../../src/server/standalone-server.mjs';
import { loadReadContext } from '../../src/development/read-context.mjs';
import { workPath } from '../../src/development/journal.mjs';
import { inputDelta } from '../../src/standalone/process-evolution.mjs';
import { bodyHash } from '../../src/process/json.mjs';

const server = f => createStandaloneServer({ projectRoot: f.project, dataRoot: f.data, modelConfig: { available: false, label: 'External' } });
async function ok(local, path, body) {
  const response = await fetch(local.origin + '/api/standalone/' + path, { method: body === undefined ? 'GET' : 'POST',
    headers: { authorization: 'Bearer ' + local.capability, ...(body === undefined ? {} : { origin: local.origin, 'content-type': 'application/json' }) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const value = await response.json(); assert.equal(response.status, 200, JSON.stringify(value)); return value;
}
const preview = local => ok(local, 'process/preview', { workItemId: 'work:test' });
const detail = local => ok(local, 'process/work?id=work%3Atest');

test('P8 incomplete or withdrawn input is unknown, while exact file changes retain root and digests', () => {
  const a = [{ id: 'input:a', selectionDigest: 'scope', snapshot: { files: [{ rootId: 'a', path: 'a.txt', state: 'present', digest: 'old' }] } }];
  const b = structuredClone(a); b[0].snapshot.files[0].digest = 'new';
  assert.equal(inputDelta(a, b).files[0].kind, 'modified');
  assert.deepEqual(inputDelta(a, []).unknownInputSetIds, ['input:a']);
  b[0].complete = false; b[0].snapshot.files = [];
  assert.deepEqual(inputDelta(a, b).files, []);
  b[0].complete = true; assert.equal(inputDelta(a, b).files[0].kind, 'removed');
});

test('P8 module drill-down preserves failed retry, source delta, versions, log references and historical assessment', { timeout: 120000 }, async t => {
  const f = await fixture(t); await f.start();
  const failed = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 2);
  await writeFile(join(f.project, 'source.txt'), 'after');
  const passed = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  await f.call(['finish', '--work', 'work:test', '--input', await f.input('done', { outcome: { summary: 'Fixed and verified', incomplete: [], resumeNotes: [] } })], 0);
  const local = await server(f); t.after(() => local.close());
  const ctx = await loadReadContext({ projectRoot: f.project, dataRoot: f.data });
  const before = await readFile(workPath(ctx, 'work:test')), index = await readFile(join(f.data, 'process/v1/index.json'));
  const ws = (await ok(local, 'bootstrap')).workspace, node = ws.current.nodes.find(row => row.sourcePath === 'check.mjs');
  const catalog = await ok(local, `process?nodeId=${encodeURIComponent(node.id)}`); assert.equal(catalog.works.length, 1);
  const d = await detail(local), current = await preview(local);
  assert.equal(d.evolution.scope, 'historical'); assert.equal(d.evolution.current, null); assert.equal(d.evolution.design.target, null);
  const events = d.evolution.events, execution = events.find(row => row.id === passed.data.receiptId);
  assert.ok(execution.changes.files.some(row => row.path === 'source.txt' && row.kind === 'modified'));
  assert.equal(execution.checks[0].checker.versionStatus, 'registered_original_configuration');
  assert.ok(execution.logReferences.some(row => row.stream === 'stdout' && row.digest.length === 64));
  const failure = current.evolution.checks.find(row => row.receiptId === failed.data.receiptId);
  assert.equal(failure.execution.result, 'failed'); assert.equal(failure.criteria[0].freshness.status, 'stale');
  assert.ok(failure.explanations.some(row => row.sinceCheck?.files.some(file => file.path === 'source.txt')));
  assert.equal(current.assessment.criteria[0].satisfaction, 'satisfied');
  const saved = events.find(row => row.kind === 'assessment'); assert.equal((await ok(local, `process/assessment?id=${encodeURIComponent(saved.id)}`)).assessmentHash, saved.ref.hash);
  const handoff = await ok(local, 'process/handoff', { workItemId: 'work:test', expectedWorkspaceRevision: ws.revision });
  assert.equal(handoff.manifest.process.evolution.scope, 'historical');
  assert.deepEqual(await readFile(workPath(ctx, 'work:test')), before); assert.deepEqual(await readFile(join(f.data, 'process/v1/index.json')), index);
  assert.doesNotMatch(JSON.stringify({ d, current, handoff }), /example-secret-marker|environmentKey/);
  await writeFile(join(f.project, 'source.txt'), 'damaged');
  const changed = await preview(local); assert.equal(changed.assessment.criteria[0].freshness.status, 'stale');
  assert.equal((await detail(local)).lastAssessment.assessmentHash, d.lastAssessment.assessmentHash);
});

test('P8 all-current opposite results stay conflict, with original finish selection and no execution side effects', { timeout: 120000 }, async t => {
  const f = await fixture(t, { script: `import {existsSync,mkdirSync,writeFileSync,appendFileSync} from 'node:fs';mkdirSync('out',{recursive:true});const retry=existsSync('out/retry');writeFileSync('out/retry','1');appendFileSync('out/starts','run\\n');process.exit(retry?0:7);` });
  await f.start(); const failed = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 2), passed = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  const selection = { receiptIds: [passed.data.receiptId], reason: 'Explicit retry selection; the failure stays in history.' };
  await f.call(['finish', '--work', 'work:test', '--input', await f.input('finish', { selection, outcome: { summary: 'Retried', incomplete: [], resumeNotes: [] } })], 0);
  const local = await server(f); t.after(() => local.close());
  const p = await preview(local); assert.equal(p.assessment.criteria[0].satisfaction, 'conflict');
  assert.deepEqual(p.evolution.checks.map(row => row.receiptId).sort(), [failed.data.receiptId, passed.data.receiptId].sort());
  assert.ok(p.evolution.checks.every(row => row.criteria[0].freshness.status === 'current'));
  const d = await detail(local), saved = await ok(local, 'process/assess', { workItemId: 'work:test', expectedRevision: d.revision });
  assert.equal(saved.assessment.criteria[0].satisfaction, 'conflict');
  const next = await detail(local); assert.deepEqual(next.completionSelection, selection);
  assert.equal(next.evolution.events.filter(row => row.kind === 'assessment').length, 2);
  assert.equal(await readFile(join(f.project, 'out/starts'), 'utf8'), 'run\nrun\n');
});

test('P8 explicit candidate identity is shown without inventing formal requirements or rebinding edited targets', { timeout: 90000 }, async t => {
  const f = await fixture(t), local = await server(f); t.after(() => local.close());
  let ws = (await ok(local, 'bootstrap')).workspace;
  async function command(type, input) { const value = await ok(local, 'command', { type, input, expectedRevision: ws.revision }); ws = value.workspace; return value.output; }
  const session = await command('create-session', { title: 'Design context', kind: 'requirements', question: 'Keep source behavior inspectable' });
  const candidate = await command('create-candidate', { sessionId: session.id, title: 'Explicit target' });
  await command('select-candidate', { sessionId: session.id, candidateId: candidate.id, reason: 'Small first implementation' });
  const target = ws.candidates[0]; f.work.candidateBinding = { candidateId: target.id, candidateVersion: target.version, baselineSnapshotId: target.baseSnapshotId }; await f.start();
  let d = await detail(local); assert.equal(d.evolution.design.target.status, 'declared_design');
  assert.equal(d.evolution.design.references[0].text, 'Keep source behavior inspectable');
  assert.ok(d.evolution.design.gaps.some(row => row.includes('不是正式 Requirement')));
  assert.equal((await ok(local, `process?candidateId=${encodeURIComponent(target.id)}`)).works.length, 1);
  await command('update-candidate', { candidateId: target.id, title: 'Changed target' });
  d = await detail(local); assert.equal(d.candidate.status, 'stale'); assert.equal(d.evolution.design.target, null);
  assert.equal(d.evolution.design.binding.candidateVersion, 0);
});

test('P8 imported extension declarations cannot break projection or acquire local provenance', { timeout: 90000 }, async t => {
  const f = await fixture(t); await f.start(); await f.call(['check', '--work', 'work:test', '--runner', 'test'], 2);
  const local = await server(f); t.after(() => local.close()); const original = await detail(local);
  const receipt = structuredClone(original.receipts.find(row => row.origin === 'local_execution').receipt);
  receipt.receiptId = 'receipt:external-extension'; receipt.executionId = 'execution:external-extension';
  receipt.extensions['panorama.development.capture'] = { configurationDigest: { unexpected: true }, logReferences: 'not an array' };
  receipt.receiptHash = bodyHash(receipt, 'receiptHash');
  const imported = await ok(local, 'process/import', { workItemId: 'work:test', receipt, expectedRevision: original.revision });
  const event = imported.evolution.events.find(row => row.id === receipt.receiptId);
  assert.equal(event.origin, 'external_import'); assert.equal(event.configurationDigest, null); assert.deepEqual(event.logReferences, []);
  assert.equal(imported.receipts.find(row => row.receipt.receiptId === receipt.receiptId).locallyAttested, false);
  assert.deepEqual((await ok(local, 'process/receipt?id=receipt%3Aexternal-extension')).extensions, receipt.extensions);
});
