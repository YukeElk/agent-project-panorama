import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm, symlink, realpath } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { WorkspaceStore } from '../../src/standalone/design-workspace.mjs';

function sourceModel(root, snapshotId = 'snapshot:one') {
  const ev = path => [{ path, line: 1, kind: 'declaration', detail: 'Static source declaration' }];
  return {
    schemaVersion: 'panorama.source.v1', project: { id: 'project:one', name: 'sample', root },
    snapshot: { id: snapshotId, contentDigest: snapshotId, parserVersion: 'test-parser', generatedAt: '2026-09-12T00:00:00.000Z' },
    nodes: [
      { id: 'orders', kind: 'module', label: 'Orders', parentId: null, sourcePath: 'orders.ts', line: 1, description: '', attributes: { responsibilities: ['订单', '通知'], stateOwnership: ['orders'] }, evidence: ev('orders.ts') },
      { id: 'payments', kind: 'module', label: 'Payments', parentId: null, sourcePath: 'payments.ts', line: 1, description: '', attributes: {}, evidence: ev('payments.ts') },
    ],
    edges: [{ id: 'imports', from: 'orders', to: 'payments', kind: 'imports', label: '', evidence: ev('orders.ts'), confidence: 'high', resolution: 'resolved' }],
    files: [{ path: 'orders.ts', digest: 'one', size: 12, language: 'typescript' }, { path: 'payments.ts', digest: 'two', size: 10, language: 'typescript' }],
    capabilities: [{ language: 'typescript', status: 'parsed', files: 2, detail: 'test parser' }], gaps: [],
  };
}
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'panorama-design-')); const root = join(directory, 'source'); const data = join(directory, 'state');
  await mkdir(root); const canonicalRoot = await realpath(root); await writeFile(join(root, 'sentinel.txt'), 'business source unchanged');
  let model = sourceModel(canonicalRoot); let analysisError = null; const instances = [];
  const create = (overrides = {}) => { const store = new WorkspaceStore({ projectRoot: root, dataRoot: data, analyzeProject: async () => { if (analysisError) throw analysisError; return structuredClone(model); }, ...overrides }); instances.push(store); return store; };
  t.after(async () => { for (const store of instances) await store.close().catch(() => {}); await rm(directory, { recursive: true, force: true }); });
  const store = create(); await store.init();
  const command = (type, input = {}, revision = store.snapshot().revision) => store.command(type, input, revision);
  return { directory, root, data, store, command, create, get model() { return model; }, setModel(value) { model = value; }, failAnalysis(error) { analysisError = error; } };
}
async function candidate(f) {
  const session = (await f.command('create-session', { title: '架构讨论', kind: 'architecture', question: '拆分通知', constraints: '保持订单接口兼容', unknowns: ['队列交付方式'] })).output.id;
  const id = (await f.command('create-candidate', { sessionId: session, title: '方案一', description: '渐进迁移' })).output.id;
  return { session, id };
}
const currentCandidate = (f, id) => f.store.snapshot().candidates.find(item => item.id === id);
const apply = (f, id, operations) => { const c = currentCandidate(f, id); return f.command('apply-operations', { candidateId: id, candidateVersion: c.version, baseSnapshotId: c.baseSnapshotId, operations }); };

test('two text alternatives, selection reason, two conversation turns and view persist across restart', async t => {
  const f = await fixture(t); const { session, id } = await candidate(f);
  const other = (await f.command('create-candidate', { sessionId: session, title: '整体替换', description: '完整迁移到候选框架，待做兼容性实验' })).output.id;
  await f.command('update-candidate', { candidateId: id, description: '分模块迁移并保留兼容层' });
  const version = currentCandidate(f, id).version;
  await f.command('select-candidate', { sessionId: session, candidateId: id, reason: '先降低迁移范围，另一方案仍保留供比较' });
  for (const [message, answer] of [['先确认需求', '用户是否需要离线使用？'], ['需要离线，沿用刚才方案', '已保留离线约束，下一步确认同步冲突处理。']]) {
    await f.command('record-conversation', { sessionId: session, message, response: { kind: 'clarification', text: answer, operations: [] }, candidateId: id, candidateVersion: version, baseSnapshotId: f.model.snapshot.id });
  }
  await f.command('save-view', { title: '订单范围', kind: 'dependencies', scopeId: 'orders', search: '', collapsedIds: ['payments'] });
  await f.command('save-candidate', { candidateId: id });
  assert.equal(currentCandidate(f, id).version, version);
  const before = f.store.snapshot(); await f.store.close(); const reopened = f.create(); await reopened.init(); const after = reopened.snapshot();
  assert.equal(after.sessions[0].messages.length, 4); assert.equal(after.sessions[0].messages[3].role, 'assistant'); assert.match(after.sessions[0].messages[3].content, /离线约束/);
  assert.equal(after.sessions[0].selectedCandidateId, id); assert.match(after.sessions[0].decisionReason, /迁移范围/); assert.equal(after.candidates[1].id, other);
  assert.deepEqual(after.views, before.views); assert.deepEqual(after.candidates, before.candidates);
  assert.equal(await readFile(join(f.root, 'sentinel.txt'), 'utf8'), 'business source unchanged');
});

test('candidate history restores text and graph while semantic versions remain monotonic', async t => {
  const f = await fixture(t); const { id } = await candidate(f);
  await apply(f, id, [{ type: 'update-node', id: 'orders', changes: { label: 'Order domain' } }]);
  await f.command('update-candidate', { candidateId: id, title: '新名称', description: '新描述' });
  assert.equal(currentCandidate(f, id).version, 2);
  await f.command('undo', { candidateId: id }); let c = currentCandidate(f, id); assert.equal(c.version, 3); assert.equal(c.title, '方案一'); assert.equal(c.target.nodes[0].label, 'Order domain');
  await f.command('undo', { candidateId: id }); c = currentCandidate(f, id); assert.equal(c.version, 4); assert.equal(c.target.nodes[0].label, 'Orders');
  await f.command('redo', { candidateId: id }); c = currentCandidate(f, id); assert.equal(c.version, 5); assert.equal(c.target.nodes[0].label, 'Order domain');
  await f.command('update-candidate', { candidateId: id, description: '新的分支' }); await assert.rejects(f.command('redo', { candidateId: id }), /Nothing to redo/);
  assert.equal(currentCandidate(f, id).history.length, 3);
});

test('concurrent revisions and stale previews reject atomically, including after undo', async t => {
  const f = await fixture(t); const { id } = await candidate(f); const initial = currentCandidate(f, id); const revision = f.store.snapshot().revision;
  const commands = await Promise.allSettled([
    f.command('save-view', { title: 'First', kind: 'modules' }, revision),
    f.command('save-view', { title: 'Second', kind: 'modules' }, revision),
  ]);
  assert.equal(commands.filter(item => item.status === 'fulfilled').length, 1); assert.equal(commands.find(item => item.status === 'rejected').reason.statusCode, 409);
  await apply(f, id, [{ type: 'update-node', id: 'orders', changes: { label: 'Changed' } }]); await f.command('undo', { candidateId: id }); const before = f.store.snapshot();
  await assert.rejects(f.command('apply-operations', { candidateId: id, candidateVersion: initial.version, baseSnapshotId: initial.baseSnapshotId, operations: [{ type: 'update-node', id: 'orders', changes: { label: 'Stale response' } }] }), error => error.statusCode === 409);
  assert.deepEqual(f.store.snapshot(), before);
});

test('partial review applies exactly selected independent operations and invalid subsets roll back decisions', async t => {
  const f = await fixture(t); const { id } = await candidate(f); let c = currentCandidate(f, id);
  const review = (await f.command('import-review', { candidateId: id, candidateVersion: c.version, baseSnapshotId: c.baseSnapshotId, source: 'external reviewer', markdown: '第一条可接受，第二条后续评估', operations: [{ type: 'update-node', id: 'orders', changes: { label: 'Order domain' } }, { type: 'update-node', id: 'payments', changes: { label: 'Payment domain' } }] })).output.id;
  assert.equal(currentCandidate(f, id).target.nodes[0].label, 'Orders');
  await f.command('decide-review', { reviewId: review, decision: 'accepted', reason: '仅采用第一条命名调整', applyOperations: true, selectedOperationIndices: [0] });
  let state = f.store.snapshot(); assert.equal(state.reviews[0].decision, 'partially_accepted'); assert.deepEqual(state.reviews[0].appliedOperationIndices, [0]); assert.deepEqual(state.reviews[0].unappliedOperationIndices, [1]); assert.equal(state.reviews[0].stale, true);
  c = currentCandidate(f, id); assert.equal(c.target.nodes[0].label, 'Order domain'); assert.equal(c.target.nodes[1].label, 'Payments');
  const dependent = (await f.command('import-review', { candidateId: id, candidateVersion: c.version, baseSnapshotId: c.baseSnapshotId, source: 'reviewer', markdown: '整体删除', operations: [{ type: 'remove-edge', id: 'imports' }, { type: 'remove-node', id: 'payments' }] })).output.id;
  const before = f.store.snapshot(); await assert.rejects(f.command('decide-review', { reviewId: dependent, decision: 'accepted', reason: '只选删除节点', applyOperations: true, selectedOperationIndices: [1] }), /Dangling edge/);
  assert.deepEqual(f.store.snapshot(), before);
});

test('Markdown acceptance, save, export and imported external result never imply source execution', async t => {
  const f = await fixture(t); const { id } = await candidate(f); const c = currentCandidate(f, id);
  const review = (await f.command('import-review', { candidateId: id, candidateVersion: c.version, baseSnapshotId: c.baseSnapshotId, source: 'Codex copy/paste', markdown: '<script>untrusted raw review</script>' })).output.id;
  await f.command('decide-review', { reviewId: review, decision: 'accepted', reason: '意见记录，尚未转为结构变化', applyOperations: false });
  await f.command('import-result', { candidateId: id, source: 'external', summary: '声称完成测试（尚未核验）', references: ['orders.ts'] });
  const before = f.store.snapshot();
  for (const purpose of ['design', 'review', 'implementation']) {
    const result = await f.command('export-handoff', { candidateId: id, purpose }); assert.equal(result.workspace.revision, before.revision); assert.equal(result.output.manifest.purpose, purpose); assert.ok(result.output.manifest.target); assert.ok(result.output.manifest.diff); assert.match(result.output.markdown, /保持订单接口兼容/); assert.match(result.output.markdown, /源码/);
  }
  assert.equal(currentCandidate(f, id).version, c.version); assert.deepEqual(f.store.snapshot().current, before.current);
  assert.equal((await f.command('reconcile', { candidateId: id })).output.status, 'insufficient_evidence');
  assert.equal(await readFile(join(f.root, 'sentinel.txt'), 'utf8'), 'business source unchanged');
});

test('refresh and reopening preserve historical target and mark candidate/review stale', async t => {
  const f = await fixture(t); const { id } = await candidate(f); await apply(f, id, [{ type: 'update-node', id: 'orders', changes: { label: 'Target' } }]); let c = currentCandidate(f, id);
  const review = (await f.command('import-review', { candidateId: id, candidateVersion: c.version, baseSnapshotId: c.baseSnapshotId, source: 'reviewer', markdown: 'review' })).output.id;
  const next = structuredClone(f.model); next.snapshot.id = 'snapshot:two'; next.snapshot.contentDigest = 'changed'; next.nodes[0].label = 'Current code'; f.setModel(next); await f.command('refresh');
  c = currentCandidate(f, id); assert.equal(c.target.nodes[0].label, 'Target'); assert.equal(c.stale, true); assert.equal(f.store.snapshot().reviews[0].stale, true);
  const before = f.store.snapshot(); await assert.rejects(f.command('decide-review', { reviewId: review, decision: 'accepted', reason: 'old advice', applyOperations: false }), error => error.statusCode === 409); assert.deepEqual(f.store.snapshot(), before);
  await assert.rejects(apply(f, id, []), error => error.statusCode === 409);
  await f.command('decide-review', { reviewId: review, decision: 'rejected', reason: 'source changed', applyOperations: false });
  await f.store.close(); next.snapshot.id = 'snapshot:three'; f.setModel(next); const reopened = f.create(); await reopened.init(); assert.equal(reopened.snapshot().current.snapshot.id, 'snapshot:three'); assert.equal(reopened.snapshot().candidates[0].baseSnapshotId, 'snapshot:one');
});

test('failed refresh, failed write and mutable snapshots do not corrupt committed memory', async t => {
  const f = await fixture(t); await candidate(f); const before = f.store.snapshot(); const copy = f.store.snapshot(); copy.sessions[0].title = 'tampered'; assert.notEqual(f.store.snapshot().sessions[0].title, 'tampered');
  f.failAnalysis(new Error('SOURCE_CHANGED_DURING_SCAN')); await assert.rejects(f.command('refresh'), /SOURCE_CHANGED_DURING_SCAN/); assert.deepEqual(f.store.snapshot(), before);
  const file = join(f.data, 'workspace.json'); const original = await readFile(file); await rm(file); await mkdir(file);
  await assert.rejects(f.command('create-session', { title: 'must not commit', kind: 'requirements' })); assert.deepEqual(f.store.snapshot(), before);
  await rm(file, { recursive: true }); await writeFile(file, original);
});

test('active writer lock rejects second instance and stale lock recovers only confirmed absent process', async t => {
  const f = await fixture(t); const second = f.create(); await assert.rejects(second.init(), error => error.code === 'WORKSPACE_LOCKED');
  await f.store.close(); const result = await promisify(execFile)(process.execPath, ['-e', 'process.stdout.write(String(process.pid))']);
  const deadPid = Number(result.stdout); await writeFile(join(f.data, '.panorama.lock'), JSON.stringify({ pid: deadPid, token: 'old-process-token-12345' }));
  const recovered = f.create(); await recovered.init(); assert.equal(recovered.snapshot().revision, 0);
});

test('corrupt/unknown/mismatched persisted data rejects without replacing its bytes', async t => {
  const f = await fixture(t); await candidate(f); await f.store.close(); const file = join(f.data, 'workspace.json'); const valid = JSON.parse(await readFile(file, 'utf8'));
  for (const content of ['{"truncated":', JSON.stringify({ ...valid, formatVersion: 'future.v999' }), JSON.stringify({ ...valid, projectRoot: `${f.root}-other` }), JSON.stringify({ ...valid, candidates: [{ ...valid.candidates[0], historyIndex: 42 }] })]) {
    await writeFile(file, content); const instance = f.create(); await assert.rejects(instance.init(), error => error.code === 'WORKSPACE_CORRUPT'); assert.equal(await readFile(file, 'utf8'), content);
  }
});

test('data directory canonicalization rejects direct and linked source descendants before creating them', async t => {
  const f = await fixture(t); const inside = f.create({ dataRoot: join(f.root, 'not-created') }); await assert.rejects(inside.init(), error => error.code === 'DATA_ROOT_INSIDE_PROJECT');
  const alias = join(f.directory, 'source-alias'); await symlink(f.root, alias, process.platform === 'win32' ? 'junction' : 'dir');
  const linked = f.create({ dataRoot: join(alias, 'nested', 'state') }); await assert.rejects(linked.init(), error => error.code === 'DATA_ROOT_INSIDE_PROJECT');
  await assert.rejects(readFile(join(f.root, 'nested', 'state', 'workspace.json')), error => error.code === 'ENOENT');
});

test('binding validates snapshot and kind, survives persistence but expires after source changes', async t => {
  const f = await fixture(t); const { id } = await candidate(f); await apply(f, id, [{ type: 'add-node', node: { id: 'new-target', kind: 'module', label: 'Orders' } }]);
  await assert.rejects(f.command('bind-target', { candidateId: id, targetNodeId: 'new-target', sourceNodeId: 'orders', sourceSnapshotId: 'old' }), error => error.statusCode === 409);
  await f.command('bind-target', { candidateId: id, targetNodeId: 'new-target', sourceNodeId: 'orders', sourceSnapshotId: f.model.snapshot.id });
  assert.equal((await f.command('reconcile', { candidateId: id })).output.status, 'reflected');
  const next = structuredClone(f.model); next.snapshot.id = 'new-snapshot'; f.setModel(next); await f.command('refresh');
  assert.equal((await f.command('reconcile', { candidateId: id })).output.status, 'insufficient_evidence');
});
