import nodeTest from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture } from '../process/development-helpers.mjs';
import { createStandaloneServer } from '../../src/server/standalone-server.mjs';
import { loadReadContext } from '../../src/development/read-context.mjs';
import { readWork, processStore, workPath } from '../../src/development/journal.mjs';
import { captureInputs, captureSubjects, captureEnvironment, makeReceipt } from '../../src/development/capture.mjs';
import { now, exists, readJson, writeJson } from '../../src/development/io.mjs';
import { bodyHash, sameValue } from '../../src/process/json.mjs';
import { candidateStatus, compatibilityPreview } from '../../src/standalone/process-evidence.mjs';
import { readHandoff } from '../../src/standalone/handoff.mjs';

const test = (name, fn) => nodeTest(name, { timeout: 90000 }, fn);
const context = f => loadReadContext({ projectRoot: f.project, dataRoot: f.data });
const server = f => createStandaloneServer({ projectRoot: f.project, dataRoot: f.data, modelConfig: { available: false, label: 'External' } });
async function request(local, path, body, overrides = {}) {
  const response = await fetch(`${local.origin}/api/standalone/${path}`, { method: body === undefined ? 'GET' : 'POST', headers: { authorization: `Bearer ${local.capability}`, ...(body === undefined ? {} : { origin: local.origin, 'content-type': 'application/json' }), ...overrides }, body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body) });
  return { status: response.status, body: await response.json() };
}
async function ok(local, path, body) { const value = await request(local, path, body); assert.equal(value.status, 200, JSON.stringify(value.body)); return value.body; }
async function syntheticClaim(f, id = 'work:test') {
  const ctx = await context(f), work = await readWork(ctx, id), inputs = await captureInputs(ctx), runner = ctx.local.bootstrap.runners[0];
  return makeReceipt(ctx, work, { before: inputs, after: inputs, subjects: await captureSubjects(ctx, work, inputs), environment: await captureEnvironment(ctx, runner, inputs), execution: { result: 'passed', exitCode: 0, startedAt: now(), finishedAt: now() }, runner, subjectIds: ['source'], criterionIds: ['AC'] });
}
const verdict = assessment => ({ overall: assessment.overall, criteria: assessment.criteria.map(({ criterionId, satisfaction, freshness, missing }) => ({ criterionId, satisfaction, freshness, missing })), rules: assessment.rules.map(({ ruleId, satisfaction, applicability, freshness, missing }) => ({ ruleId, satisfaction, applicability: applicability.status, freshness, missing })) });

test('P4 current preview explains all-evidence scope without erasing an explicit completion selection', async t => {
  const f = await fixture(t, { script: `import {mkdirSync,existsSync,writeFileSync,readFileSync} from 'node:fs'; mkdirSync('out',{recursive:true}); const retry=existsSync('out/attempt'); writeFileSync('out/attempt','attempted'); process.exit(retry && readFileSync('source.txt','utf8') === 'after' ? 0 : 7);` });
  await f.start(); await writeFile(join(f.project, 'source.txt'), 'after');
  const failed = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 2);
  const passed = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  const selection = { receiptIds: [passed.data.receiptId], reason: 'Use the successful retry after a transient failure; retain both results for audit.' };
  await f.call(['finish', '--work', 'work:test', '--input', await f.input('finish', { outcome: { summary: 'Completed with an explicit retry selection.', incomplete: [], resumeNotes: [] }, selection })], 0);
  assert.equal((await f.call(['resume', '--work', 'work:test'], 0)).data.currentProcessReady, true);
  const all = await f.call(['assess', '--work', 'work:test'], 2);
  const local = await server(f);
  try {
    const preview = await ok(local, 'process/preview', { workItemId: 'work:test' });
    assert.deepEqual(verdict(preview.assessment), verdict(all.data.assessment));
    assert.equal(preview.currentEvidenceReady, false);
    assert.equal(preview.evidenceSelection.mode, 'all_registered');
    assert.ok(preview.evidenceSelection.receiptIds.includes(failed.data.receiptId));
    assert.ok(preview.evidenceSelection.receiptIds.includes(passed.data.receiptId));
    assert.deepEqual(preview.evidenceSelection.completionSelection, selection);
    const detail = await ok(local, 'process/work?id=work%3Atest');
    assert.deepEqual(detail.completionSelection, selection);
    const saved = await ok(local, 'process/assess', { workItemId: 'work:test', expectedRevision: detail.revision });
    assert.equal(saved.currentEvidenceReady, false);
    assert.deepEqual((await ok(local, 'process/work?id=work%3Atest')).completionSelection, selection);
    assert.equal((await f.call(['resume', '--work', 'work:test'], 0)).data.currentProcessReady, true);
  } finally { await local.close(); }
});

test('P3 uninitialized query never initializes process state; all process routes require local authorization', async t => {
  const f = await fixture(t, { init: false }), local = await server(f);
  try {
    assert.equal((await ok(local, 'process')).status, 'not_initialized');
    assert.equal(await exists(join(f.data, 'process')), false);
    assert.equal(await exists(join(f.project, '.structure')), false);
    assert.equal((await request(local, 'process', undefined, { authorization: '' })).body.error, 'CAPABILITY_MISMATCH');
    for (const route of ['preview', 'assess', 'import', 'handoff', 'compatibility']) {
      assert.equal((await request(local, 'process/' + route, {}, { origin: 'https://outside.test' })).body.error, 'ORIGIN_MISMATCH');
    }
    for (const route of ['check', 'run', 'finish', 'init']) assert.equal((await request(local, 'process/' + route, {})).status, 404);
    assert.equal((await request(local, 'process/preview', '{"workItemId":"a","workItemId":"b"}')).body.error, 'JSON_DUPLICATE_KEY');
    assert.equal((await request(local, 'process/preview', { workItemId: 'x', verifiedEvidence: [] })).body.error, 'LOCAL_CONFIG_INVALID');
  } finally { await local.close(); }
});

test('P3 browser and CLI share verdicts; query, preview, stored assessment and handoff never restart business checks', async t => {
  const f = await fixture(t, { script: `import {mkdirSync,appendFileSync,readFileSync} from 'node:fs'; mkdirSync('out',{recursive:true}); appendFileSync('out/starts','one\\n'); process.exit(readFileSync('source.txt','utf8')==='after'?0:7);` });
  await f.start(); await writeFile(join(f.project, 'source.txt'), 'after');
  await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  const cli = await f.call(['assess', '--work', 'work:test']);
  assert.ok(cli.data.assessment);
  const local = await server(f);
  try {
    const beforeIndex = await readFile(join(f.data, 'process/v1/index.json'), 'utf8');
    const beforeWork = await readFile(workPath(await context(f), 'work:test'), 'utf8');
    const catalog = await ok(local, 'process'); assert.equal(catalog.works.length, 1); assert.equal(catalog.works[0].candidate.status, 'unbound');
    const detail = await ok(local, 'process/work?id=work%3Atest'); assert.ok(detail.lastAssessment); assert.equal(detail.pending.runs.length, 0);
    const preview = await ok(local, 'process/preview', { workItemId: 'work:test' });
    assert.deepEqual(verdict(preview.assessment), verdict(cli.data.assessment));
    assert.equal(preview.assessment.criteria[0].satisfaction, 'satisfied');
    assert.equal(await readFile(join(f.data, 'process/v1/index.json'), 'utf8'), beforeIndex);
    assert.equal(await readFile(workPath(await context(f), 'work:test'), 'utf8'), beforeWork);
    const saved = await ok(local, 'process/assess', { workItemId: 'work:test', expectedRevision: detail.revision });
    assert.equal(saved.status, 'recorded'); assert.equal(saved.policy.decision, 'unknown');
    assert.equal((await request(local, 'process/assess', { workItemId: 'work:test', expectedRevision: detail.revision })).status, 409);
    const ws = (await ok(local, 'bootstrap')).workspace;
    const handoff = await ok(local, 'process/handoff', { workItemId: 'work:test', expectedWorkspaceRevision: ws.revision });
    assert.equal(handoff.manifest.formatVersion, 'panorama.handoff.v2'); assert.equal(handoff.manifest.candidate, null);
    assert.deepEqual(handoff.manifest.process.criterionIds, ['AC']); assert.equal(handoff.manifest.process.expectedReceiptFormat, 'panorama.process-receipt.v1');
    assert.equal(readHandoff(handoff.manifest).status, 'external_context');
    const sourceNode = ws.current.nodes.find(node => node.sourcePath === 'check.mjs'); assert.ok(sourceNode);
    const related = await ok(local, `process?nodeId=${encodeURIComponent(sourceNode.id)}`); assert.equal(related.works.length, 1);
    assert.equal((await ok(local, 'process?nodeId=not-a-real-node')).works.length, 0);
    assert.equal(await readFile(join(f.project, 'out/starts'), 'utf8'), 'one\n');
    assert.doesNotMatch(JSON.stringify({ catalog, detail, saved, handoff }), /environmentKey|example-secret-marker/);
  } finally { await local.close(); }
});

test('P3 imported local producer claims cannot grant provenance; rejected imports leave both stores unchanged', async t => {
  const f = await fixture(t); await f.start(); const claim = await syntheticClaim(f), local = await server(f);
  try {
    const detail = await ok(local, 'process/work?id=work%3Atest');
    const duplicate = JSON.stringify(claim).replace('"purpose":"record"', '"purpose":"record","purpose":"record"');
    assert.equal((await request(local, 'process/preview', { workItemId: 'work:test', receiptJson: duplicate })).body.error, 'JSON_DUPLICATE_KEY');
    const preview = await ok(local, 'process/preview', { workItemId: 'work:test', receipt: claim });
    assert.equal(preview.assessment.criteria[0].satisfaction, 'insufficient');
    assert.ok(preview.assessment.criteria[0].missing.some(item => item.startsWith('EVIDENCE_SOURCE_UNVERIFIED')));
    const imported = await ok(local, 'process/import', { workItemId: 'work:test', receipt: claim, expectedRevision: detail.revision });
    assert.equal(imported.receipts[0].origin, 'external_import'); assert.equal(imported.receipts[0].locallyAttested, false);
    assert.equal(imported.receipts[0].receipt.producer.kind, 'local_capture');
    const repeat = await ok(local, 'process/import', { workItemId: 'work:test', receipt: claim, expectedRevision: imported.revision });
    assert.equal(repeat.revision, imported.revision);
    assert.ok(sameValue(await ok(local, `process/receipt?id=${encodeURIComponent(claim.receiptId)}`), claim));
    const before = await readFile(join(f.data, 'process/v1/index.json'), 'utf8');
    for (const mutate of [r => r.binding.checkoutId = 'foreign', r => r.workItemRef.id = 'work:other', r => r.candidateBinding = { candidateId: 'candidate:foreign', candidateVersion: 1, baselineSnapshotId: 'snapshot:foreign' }, r => r.purpose = 'contract_example', r => r.subjects[0].locator = { rootId: 'project', path: '../private' }]) {
      const bad = structuredClone(claim); mutate(bad); bad.receiptHash = bodyHash(bad, 'receiptHash');
      assert.notEqual((await request(local, 'process/import', { workItemId: 'work:test', receipt: bad, expectedRevision: imported.revision })).status, 200);
    }
    assert.equal(await readFile(join(f.data, 'process/v1/index.json'), 'utf8'), before);
    assert.equal((await ok(local, 'process/preview', { workItemId: 'work:test' })).assessment.criteria[0].satisfaction, 'insufficient');
  } finally { await local.close(); }
});

test('P3 current re-observation detects changed dependency inputs and preserves historical receipt bytes', async t => {
  const f = await fixture(t); await f.start(); await writeFile(join(f.project, 'source.txt'), 'after');
  const run = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0), local = await server(f);
  try {
    const original = await ok(local, `process/receipt?id=${encodeURIComponent(run.data.receiptId)}`);
    assert.equal((await ok(local, 'process/preview', { workItemId: 'work:test' })).assessment.criteria[0].freshness.status, 'current');
    await writeFile(join(f.project, 'check.mjs'), '// dependency changed\n' + await readFile(join(f.project, 'check.mjs'), 'utf8'));
    const next = await ok(local, 'process/preview', { workItemId: 'work:test' });
    assert.equal(next.assessment.criteria[0].freshness.status, 'stale'); assert.equal(next.currentEvidenceReady, false);
    assert.deepEqual(await ok(local, `process/receipt?id=${encodeURIComponent(run.data.receiptId)}`), original);
  } finally { await local.close(); }
});

test('P3 missing criteria remain visible without receipts, and pending collectors are never recovered by the page', async t => {
  const f = await fixture(t); await f.start(); const ctx = await context(f), work = await readWork(ctx, 'work:test');
  work.runs.push({ executionId: 'execution:pending', runnerId: 'test', status: 'pending', ownerPid: 999999 }); await writeJson(workPath(ctx, 'work:test'), work);
  const local = await server(f);
  try {
    const preview = await ok(local, 'process/preview', { workItemId: 'work:test' });
    assert.equal(preview.pendingRuns, 1); assert.equal(preview.currentEvidenceReady, false);
    assert.match(preview.assessment.criteria[0].missing.join(' '), /EVIDENCE_MISSING/);
    assert.ok(sameValue((await readWork(ctx, 'work:test')).runs, work.runs));
    assert.equal(await exists(join(f.data, 'process/v1/development/runs')), false);
  } finally { await local.close(); }
});

test('P3 candidate revision and baseline changes invalidate bindings without rebinding history', async () => {
  const binding = { candidateId: 'candidate:a', candidateVersion: 2, baselineSnapshotId: 'snapshot:a' };
  const workspace = { current: { snapshot: { id: 'snapshot:a' } }, candidates: [{ id: 'candidate:a', version: 2, baseSnapshotId: 'snapshot:a' }] };
  assert.equal(candidateStatus({ candidateBinding: binding }, workspace).status, 'current');
  workspace.candidates[0].version++;
  assert.equal(candidateStatus({ candidateBinding: binding }, workspace).status, 'stale');
  workspace.candidates[0].version--; workspace.current.snapshot.id = 'snapshot:b';
  assert.equal(candidateStatus({ candidateBinding: binding }, workspace).status, 'stale');
  assert.equal(binding.candidateVersion, 2); assert.equal(binding.baselineSnapshotId, 'snapshot:a');
});

test('P3 edited candidate stays stale in HTTP assessment and v2 handoff after server restart', async t => {
  const f = await fixture(t); await writeFile(join(f.project, 'source.txt'), 'after');
  let local = await server(f);
  try {
    let workspace = (await ok(local, 'bootstrap')).workspace;
    workspace = (await ok(local, 'command', { type: 'create-session', input: { title: '候选测试', kind: 'requirements' }, expectedRevision: workspace.revision })).workspace;
    workspace = (await ok(local, 'command', { type: 'create-candidate', input: { sessionId: workspace.sessions[0].id, title: '第一版' }, expectedRevision: workspace.revision })).workspace;
    const candidate = workspace.candidates[0];
    f.work.candidateBinding = { candidateId: candidate.id, candidateVersion: candidate.version, baselineSnapshotId: candidate.baseSnapshotId };
    await f.start(); await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
    const before = await ok(local, 'process/preview', { workItemId: 'work:test' }); assert.equal(before.candidate.status, 'current'); assert.equal(before.assessment.criteria[0].satisfaction, 'satisfied');
    const detail = await ok(local, 'process/work?id=work%3Atest'), hashes = detail.receipts.map(item => item.receipt.receiptHash);
    await ok(local, 'process/assess', { workItemId: 'work:test', expectedRevision: detail.revision });
    workspace = (await ok(local, 'command', { type: 'update-candidate', input: { candidateId: candidate.id, title: '第二版' }, expectedRevision: workspace.revision })).workspace;
    await local.close(); local = await server(f);
    const after = await ok(local, 'process/preview', { workItemId: 'work:test' }); assert.equal(after.candidate.status, 'stale'); assert.equal(after.currentEvidenceReady, false);
    const restored = await ok(local, 'process/work?id=work%3Atest'); assert.ok(restored.lastAssessment); assert.ok(hashes.every(hash => restored.receipts.some(item => item.receipt.receiptHash === hash)));
    workspace = (await ok(local, 'bootstrap')).workspace;
    const handoff = await ok(local, 'process/handoff', { workItemId: 'work:test', expectedWorkspaceRevision: workspace.revision });
    assert.equal(handoff.manifest.stale, true); assert.equal(handoff.manifest.candidate, null); assert.equal(handoff.manifest.process.candidateBinding.candidateVersion, 0); assert.match(handoff.markdown, /不得把旧 Receipt 重绑/);
  } finally { await local.close(); }
});

test('P3 reads handoff v1 and Standard Pack v0.1 as legacy unknown without upgrading their claims', () => {
  const handoff = { formatVersion: 'panorama.handoff.v1', purpose: 'implementation', project: { id: 'project:a' }, results: [{ summary: 'all done' }] };
  const legacy = { formatVersion: 'standard-pack.v0.1', id: 'old', rules: [{ id: 'MANUAL', severity: 'high', evaluator: { id: 'manual' } }] };
  assert.equal(compatibilityPreview(handoff).status, 'legacy_unknown');
  const preview = compatibilityPreview(legacy); assert.equal(preview.status, 'legacy_unknown'); assert.equal(preview.gaps.length, 5);
  assert.ok(sameValue(legacy, preview.document)); assert.equal(legacy.rules[0].strength, undefined);
});

test('P3 process API dependency tree excludes core invocation and business command execution modules', async () => {
  const visited = new Set();
  async function walk(url) {
    if (visited.has(url.href)) return; visited.add(url.href);
    assert.doesNotMatch(url.pathname, /\/(?:config|core|service|check-worker)\.mjs$/);
    const code = await readFile(url, 'utf8');
    assert.doesNotMatch(code, /(?:from\s*|import\s*\()['"](?:node:)?child_process/);
    for (const [, path] of code.matchAll(/(?:from\s*|import\s*\()['"]([^'"]+)['"]/g)) if (path.startsWith('.')) await walk(new URL(path, url));
  }
  await walk(new URL('../../src/standalone/process-evidence.mjs', import.meta.url));
  assert.ok(visited.size > 10);
});
