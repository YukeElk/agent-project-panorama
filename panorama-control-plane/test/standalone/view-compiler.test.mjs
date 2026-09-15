import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { analyzeProject } from '../../src/standalone/source-analysis.mjs';
import { compileView } from '../../src/standalone/view-compiler.mjs';

async function fixture(context, contents) {
  const root = await mkdtemp(join(tmpdir(), 'panorama-view-'));
  context.after(() => rm(root, { recursive: true, force: true }));
  for (const [path, content] of Object.entries(contents)) { await mkdir(dirname(join(root, path)), { recursive: true }); await writeFile(join(root, path), content); }
  return analyzeProject({ projectRoot: root });
}

test('different source structures produce different topology; module view groups and drills into actual directories', async (context) => {
  const flat = await fixture(context, { 'one.js': 'export const value = 1;' });
  const nested = await fixture(context, { 'api/server.js': "import { run } from '../worker/task.js';", 'worker/task.js': 'export function run() {}', 'worker/io/file.js': 'export function read() {}' });
  const first = compileView(flat);
  const second = compileView(nested);
  assert.equal(first.nodes.length, 1);
  assert.deepEqual(second.nodes.map((node) => node.label), ['api', 'worker']);
  assert.ok(second.edges.some((edge) => edge.kind === 'imports' && edge.sourceEdgeIds.length));
  const worker = nested.nodes.find((node) => node.kind === 'module' && node.label === 'worker');
  const detail = compileView(nested, { scopeId: worker.id });
  assert.ok(detail.nodes.some((node) => node.label === 'task.js'));
  assert.ok(detail.nodes.some((node) => node.label === 'io'));
  assert.ok(!detail.nodes.some((node) => node.label === 'server.js'));
  assert.equal(compileView(nested, { scopeId: 'unknown' }).nodes.length, 0);
});

test('dependency and deployment projections share source identity and distinguish empty deployment', async (context) => {
  const model = await fixture(context, { 'a.js': 'export const a = 1;', 'compose.yml': 'services:\n  api:\n    image: app\n    depends_on: [db]\n  db:\n    image: postgres\n' });
  const deployment = compileView(model, { kind: 'deployment' });
  const dependencies = compileView(model, { kind: 'dependencies' });
  assert.equal(deployment.nodes.length, 2);
  assert.ok(deployment.nodes.every((node) => dependencies.nodes.some((other) => other.id === node.id)));
  assert.deepEqual(deployment.edges.map((edge) => edge.kind), ['depends-on']);
  assert.equal(deployment.sourceSnapshotId, model.snapshot.id);
  const sourceOnly = await fixture(context, { 'main.py': 'pass' });
  assert.equal(compileView(sourceOnly, { kind: 'deployment' }).nodes.length, 0);
});

test('layout is dynamic, bounded, searchable, collapsible and never mutates source semantics', async (context) => {
  const contents = Object.fromEntries(Array.from({ length: 30 }, (_, index) => [`src/module${index}.js`, `export function function${index}() {}`]));
  const model = await fixture(context, contents);
  const before = JSON.stringify(model);
  const view = compileView(model, { kind: 'dependencies', limit: 15 });
  assert.equal(view.nodes.length, 15);
  assert.equal(view.truncated, true);
  assert.ok(view.totalNodes > 15);
  assert.ok(view.gaps.some((gap) => gap.code === 'VIEW_TRUNCATED'));
  assert.ok(view.width > 900);
  assert.ok(view.nodes.every((node) => node.x + node.width < view.width && node.y + node.height < view.height));
  const search = compileView(model, { kind: 'modules', search: 'module29' });
  assert.ok(search.nodes.some((node) => node.sourcePath === 'src/module29.js'));
  const src = model.nodes.find((node) => node.label === 'src' && node.kind === 'module');
  const collapsed = compileView(model, { kind: 'dependencies', collapsedIds: [src.id] });
  assert.equal(collapsed.nodes.length, 1);
  assert.equal(JSON.stringify(model), before);
});

test('target service is visible in modules and deployment without fabricating source implementation', async (context) => {
  const model = await fixture(context, { 'main.js': 'export const x = 1;' });
  const target = structuredClone(model);
  target.nodes.push({ id: 'target:worker', kind: 'service', label: '通知服务', parentId: null, sourcePath: null, line: null, description: '候选设计', attributes: { observation: 'target', responsibilities: ['通知'], deployment: { environment: 'worker', replicas: 2, ports: [] } }, evidence: [] });
  assert.ok(compileView(target).nodes.some((node) => node.id === 'target:worker'));
  assert.ok(compileView(target, { kind: 'deployment' }).nodes.some((node) => node.id === 'target:worker'));
  assert.ok(!compileView(model).nodes.some((node) => node.id === 'target:worker'));
});
