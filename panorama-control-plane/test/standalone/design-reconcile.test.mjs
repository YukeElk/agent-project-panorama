import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { analyzeProject } from '../../src/standalone/source-analysis.mjs';
import { applyOperations } from '../../src/standalone/design-model.mjs';
import { reconcileCandidate } from '../../src/standalone/reconcile.mjs';

async function project(t) {
  const root = await mkdtemp(join(tmpdir(), 'panorama-reconcile-')); t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}
const candidate = (base, target) => ({ id: 'candidate', baseModel: base, baseSnapshotId: base.snapshot.id, target, version: 1, bindings: [] });

test('real Compose declaration changes reflect only fields actually parsed, never runtime deployment', async t => {
  const root = await project(t); const compose = replicas => `services:\n  api:\n    image: sample:v1\n    ports: [\"8080:80\"]\n    deploy:\n      replicas: ${replicas}\n`;
  await writeFile(join(root, 'compose.yaml'), compose(1)); const base = await analyzeProject({ projectRoot: root }); const service = base.nodes.find(node => node.kind === 'service');
  const target = applyOperations(base, [{ type: 'update-node', id: service.id, changes: { attributes: { deployment: { ...service.attributes.deployment, replicas: 3 } } } }]);
  assert.equal(reconcileCandidate(candidate(base, target), base).status, 'not_reflected');
  await writeFile(join(root, 'compose.yaml'), compose(3)); const current = await analyzeProject({ projectRoot: root }); const result = reconcileCandidate(candidate(base, target), current);
  assert.equal(result.status, 'reflected'); assert.equal(result.items.length, 1); assert.equal(result.items[0].field, 'deployment.replicas'); assert.match(result.items[0].detail, /声明配置/); assert.doesNotMatch(result.items[0].detail, /已经部署|测试通过/);
  const withBusiness = applyOperations(target, [{ type: 'update-node', id: service.id, changes: { attributes: { responsibilities: ['可靠处理订单'], interfaces: [{ name: 'order', protocol: 'http', description: '支付失败补偿' }] } } }]);
  const mixed = reconcileCandidate(candidate(base, withBusiness), current); assert.equal(mixed.status, 'partial'); assert.equal(mixed.items.filter(item => item.status === 'insufficient_evidence').length, 2);
});

test('real new service with same name stays unknown until explicit snapshot binding, semantics remain unproven', async t => {
  const root = await project(t); await writeFile(join(root, 'compose.yaml'), 'services:\n  api:\n    image: api:v1\n'); const base = await analyzeProject({ projectRoot: root });
  const target = applyOperations(base, [{ type: 'add-node', node: { id: 'target:worker', kind: 'service', label: 'worker', attributes: { deployment: { environment: 'compose', replicas: 1, ports: [], image: 'worker:v1' }, responsibilities: ['发通知'] } } }]);
  await writeFile(join(root, 'compose.yaml'), 'services:\n  api:\n    image: api:v1\n  worker:\n    image: worker:v1\n'); const current = await analyzeProject({ projectRoot: root }); const design = candidate(base, target);
  assert.equal(reconcileCandidate(design, current).status, 'insufficient_evidence');
  const worker = current.nodes.find(node => node.kind === 'service' && node.label === 'worker'); design.bindings = [{ targetNodeId: 'target:worker', sourceNodeId: worker.id, sourceSnapshotId: current.snapshot.id }];
  const bound = reconcileCandidate(design, current); assert.equal(bound.status, 'partial'); assert.ok(bound.items.some(item => item.status === 'reflected')); assert.ok(bound.items.some(item => item.field === 'responsibilities' && item.status === 'insufficient_evidence'));
  design.bindings[0].sourceSnapshotId = 'old'; assert.equal(reconcileCandidate(design, current).status, 'insufficient_evidence');
});

test('real static dependency removal can be proved despite informational boundaries, while parse gaps prevent absence claims', async t => {
  const root = await project(t); await writeFile(join(root, 'a.ts'), 'import { value } from "./b"; export const a = value;'); await writeFile(join(root, 'b.ts'), 'export const value = 1;');
  const base = await analyzeProject({ projectRoot: root }); const dependency = base.edges.find(edge => edge.kind === 'imports'); assert.ok(dependency);
  const design = candidate(base, applyOperations(base, [{ type: 'remove-edge', id: dependency.id }]));
  assert.equal(reconcileCandidate(design, base).status, 'not_reflected');
  await writeFile(join(root, 'a.ts'), 'export const a = 1;'); const current = await analyzeProject({ projectRoot: root }); assert.ok(current.gaps.some(gap => gap.code === 'STATIC_ANALYSIS_BOUNDARY'));
  assert.equal(reconcileCandidate(design, current).status, 'reflected');
  const incomplete = structuredClone(current); incomplete.gaps.push({ code: 'FILE_COUNT_LIMIT', path: '', message: 'scan incomplete' }); assert.equal(reconcileCandidate(design, incomplete).status, 'insufficient_evidence');
  const differentConfig = structuredClone(current); differentConfig.analysisConfig = { maxFiles: 1 }; assert.equal(reconcileCandidate(design, differentConfig).status, 'insufficient_evidence');
});

test('deleting final language file is observable; unsupported files or scope gaps cannot prove deletion', async t => {
  const root = await project(t); await writeFile(join(root, 'gone.ts'), 'export const value = 1;'); const base = await analyzeProject({ projectRoot: root });
  const target = applyOperations(base, [...base.edges.map(edge => ({ type: 'remove-edge', id: edge.id })), ...base.nodes.map(node => ({ type: 'remove-node', id: node.id }))]); const design = candidate(base, target);
  await rm(join(root, 'gone.ts')); const current = await analyzeProject({ projectRoot: root }); assert.equal(reconcileCandidate(design, current).status, 'reflected');
  await writeFile(join(root, 'unknown.rs'), 'pub fn example() {}'); const unsupported = await analyzeProject({ projectRoot: root }); assert.equal(reconcileCandidate(design, unsupported).status, 'insufficient_evidence');
});

test('target async and calls relationships are not established by matching static imports or copied labels', async t => {
  const root = await project(t); await mkdir(join(root, 'lib')); await writeFile(join(root, 'main.ts'), 'import { value } from "./lib/value";'); await writeFile(join(root, 'lib', 'value.ts'), 'export const value = 1;');
  const base = await analyzeProject({ projectRoot: root }); const dependency = base.edges.find(edge => edge.kind === 'imports');
  const target = applyOperations(base, [{ type: 'add-edge', edge: { id: 'target:event', from: dependency.from, to: dependency.to, kind: 'publishes', label: dependency.label, attributes: { communication: 'async', channel: 'events' } } }]);
  const result = reconcileCandidate(candidate(base, target), base); assert.equal(result.status, 'insufficient_evidence'); assert.ok(result.items.every(item => item.status !== 'reflected'));
});

test('type-only and dynamic literal imports remain their own observable static relationships', async t => {
  const root = await project(t); await writeFile(join(root, 'a.ts'), 'import type { T } from "./b"; const p = import("./b");'); await writeFile(join(root, 'b.ts'), 'export interface T { id: string }');
  const base = await analyzeProject({ projectRoot: root }); const edges = base.edges.filter(edge => ['type-import', 'dynamic-import'].includes(edge.kind)); assert.equal(edges.length, 2);
  const target = applyOperations(base, edges.map(edge => ({ type: 'remove-edge', id: edge.id }))); const design = candidate(base, target);
  await writeFile(join(root, 'a.ts'), 'export const empty = 1;'); const current = await analyzeProject({ projectRoot: root }); assert.equal(reconcileCandidate(design, current).status, 'reflected');
});
