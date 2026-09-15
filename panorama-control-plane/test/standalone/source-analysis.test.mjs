import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { analyzeProject, readSourceEvidence } from '../../src/standalone/source-analysis.mjs';

async function fixture(context, contents) {
  const root = await mkdtemp(join(tmpdir(), 'panorama-source-'));
  context.after(() => rm(root, { recursive: true, force: true }));
  for (const [path, content] of Object.entries(contents)) {
    await mkdir(dirname(join(root, path)), { recursive: true });
    await writeFile(join(root, path), content, 'utf8');
  }
  return root;
}

test('TS AST extracts genuine declarations and typed/static/dynamic local edges with evidence', async (context) => {
  const root = await fixture(context, {
    'src/main.ts': `import type { Model } from './model';\nexport { work } from './worker';\nconst text = "import fake from './fake'";\n// import absent from './missing';\nexport class Application {}\nexport const arrow = () => 1;\nasync function boot() { return import('./worker'); }\nconst helper = require('./helper');\nimport(variable);\n`,
    'src/model.ts': 'export interface Model { id: string }',
    'src/worker.ts': 'export function work() {}',
    'src/helper.js': 'module.exports = 1;',
  });
  const model = await analyzeProject({ projectRoot: root });
  const byId = new Map(model.nodes.map((node) => [node.id, node]));
  const imports = model.edges.filter((edge) => ['imports', 'type-import', 'dynamic-import'].includes(edge.kind));
  assert.equal(imports.length, 4);
  assert.equal(imports.filter((edge) => edge.kind === 'type-import').length, 1);
  assert.equal(imports.filter((edge) => edge.kind === 'dynamic-import').length, 1);
  assert.ok(imports.every((edge) => byId.get(edge.from).kind === 'file' && byId.get(edge.to).kind === 'file'));
  assert.ok(imports.every((edge) => edge.evidence[0].path === 'src/main.ts' && edge.evidence[0].line > 0 && edge.attributes.runtimeObserved === false));
  assert.ok(model.nodes.some((node) => node.kind === 'symbol' && node.label === 'Application'));
  assert.ok(model.nodes.some((node) => node.kind === 'symbol' && node.label === 'arrow'));
  assert.ok(model.nodes.some((node) => node.kind === 'symbol' && node.label === 'Model'));
  assert.ok(!model.nodes.some((node) => /fake|absent|missing/.test(node.label)));
  assert.ok(model.gaps.some((gap) => gap.code === 'DYNAMIC_IMPORT'));
});

test('TS explicit JSONC aliases resolve within project and unknown alias stays unresolved', async (context) => {
  const root = await fixture(context, {
    'tsconfig.json': '{ // only local configuration\n"compilerOptions":{"baseUrl":".","paths":{"@core/*":["src/core/*"]}}}',
    'src/main.ts': 'import { helper } from "@core/helper";\nimport missing from "@/unknown";',
    'src/core/helper.ts': 'export const helper = 1;',
  });
  const model = await analyzeProject({ projectRoot: root });
  const target = model.nodes.find((node) => node.sourcePath === 'src/core/helper.ts' && node.kind === 'file');
  assert.ok(model.edges.some((edge) => edge.to === target.id && edge.kind === 'imports'));
  assert.ok(model.edges.some((edge) => edge.resolution === 'unresolved' && edge.label === '@/unknown'));
  assert.ok(model.gaps.some((gap) => gap.code === 'IMPORT_UNRESOLVED'));
});

test('Express entry syntax requires an identified router and retains source line', async (context) => {
  const root = await fixture(context, { 'server.js': "import express from 'express';\nconst app = express();\napp.get('/orders', handler);\nother.get('/not-a-route');\napp.post(dynamicPath, handler);" });
  const model = await analyzeProject({ projectRoot: root });
  const routes = model.nodes.filter((node) => node.kind === 'route');
  assert.deepEqual(routes.map((node) => node.label), ['GET /orders']);
  assert.equal(routes[0].line, 3);
  assert.equal(routes[0].attributes.observation, 'declared');
  assert.ok(model.gaps.some((gap) => gap.code === 'DYNAMIC_ROUTE'));
});

test('Python AST resolves relative imports, symbols and literal framework route without executing source', async (context) => {
  const root = await fixture(context, {
    'app/__init__.py': '',
    'app/routes.py': "from .service import calculate\nfrom fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/orders')\nasync def orders():\n    raise RuntimeError('source must never run')\nclass Order: pass\n",
    'app/service.py': 'def calculate():\n    return 42\n',
    'entry.py': "import app.routes\n__import__(module_name)\n",
  });
  const model = await analyzeProject({ projectRoot: root });
  assert.equal(model.capabilities.find((capability) => capability.language === 'python').status, 'partial', 'This test requires a working Python 3.9+; set PANORAMA_PYTHON. A dynamic import is intentionally unresolved.');
  const routeFile = model.nodes.find((node) => node.kind === 'file' && node.sourcePath === 'app/routes.py');
  const serviceFile = model.nodes.find((node) => node.kind === 'file' && node.sourcePath === 'app/service.py');
  assert.ok(model.edges.some((edge) => edge.from === routeFile.id && edge.to === serviceFile.id && edge.kind === 'imports'));
  assert.ok(model.nodes.some((node) => node.kind === 'route' && node.label === 'GET /orders' && node.line === 4));
  assert.ok(model.nodes.some((node) => node.kind === 'symbol' && node.label === 'Order'));
  assert.ok(model.gaps.some((gap) => gap.code === 'DYNAMIC_IMPORT'));
  assert.ok(!model.edges.some((edge) => edge.kind === 'calls'));
});

test('missing Python explicitly degrades to file inventory instead of claiming AST support', async (context) => {
  const root = await fixture(context, { 'main.py': 'def run(): pass' });
  const model = await analyzeProject({ projectRoot: root, pythonPath: join(root, 'not-an-interpreter') });
  assert.equal(model.capabilities[0].status, 'unavailable');
  assert.ok(model.gaps.some((gap) => gap.code === 'PYTHON_UNAVAILABLE'));
  assert.ok(!model.nodes.some((node) => node.kind === 'symbol'));
});

test('Compose preserves declared services, dependencies and resource relationships but no environment values', async (context) => {
  const root = await fixture(context, { 'compose.yaml': `services:\n  api:\n    image: example/api:1\n    build:\n      context: .\n      args:\n        TOKEN: hidden-build-value\n    ports: ["8080:80"]\n    environment:\n      TOKEN: hidden-environment-value\n    depends_on:\n      db:\n        condition: service_healthy\n    networks: [backend]\n  db:\n    image: postgres:16\n    environment: [PASSWORD=hidden-list-value]\n    volumes: ["data:/var/lib/postgresql/data"]\n    networks: [backend]\nvolumes:\n  data: {}\nnetworks:\n  backend: {}\n` });
  const model = await analyzeProject({ projectRoot: root });
  const api = model.nodes.find((node) => node.kind === 'service' && node.label === 'api');
  const db = model.nodes.find((node) => node.kind === 'service' && node.label === 'db');
  assert.deepEqual(api.attributes.environmentKeys, ['TOKEN']);
  assert.deepEqual(api.attributes.deployment.ports, ['8080:80']);
  assert.deepEqual(api.attributes.build, { context: '.', dockerfile: null });
  assert.ok(model.edges.some((edge) => edge.from === api.id && edge.to === db.id && edge.kind === 'depends-on'));
  assert.ok(model.nodes.some((node) => node.kind === 'resource' && node.label === 'data'));
  assert.ok(model.nodes.some((node) => node.kind === 'resource' && node.label === 'backend'));
  assert.doesNotMatch(JSON.stringify(model), /hidden-(environment|build|list)-value/);
  assert.ok(model.edges.every((edge) => edge.attributes.runtimeObserved === false));
});

test('content refresh changes snapshot, retains stable node/edge identity, and observes added/deleted files', async (context) => {
  const root = await fixture(context, { 'a.ts': "import { b } from './b';\nexport const a = 1;", 'b.ts': 'export const b = 2;' });
  const first = await analyzeProject({ projectRoot: root });
  const second = await analyzeProject({ projectRoot: root });
  assert.equal(first.snapshot.id, second.snapshot.id);
  await writeFile(join(root, 'a.ts'), "import { b } from './b';\nexport const a = 3;");
  const changed = await analyzeProject({ projectRoot: root });
  assert.notEqual(changed.snapshot.id, first.snapshot.id);
  assert.deepEqual(changed.nodes.map((node) => node.id), first.nodes.map((node) => node.id));
  assert.deepEqual(changed.edges.map((edge) => edge.id), first.edges.map((edge) => edge.id));
  await writeFile(join(root, 'c.py'), 'value = 1');
  const added = await analyzeProject({ projectRoot: root });
  assert.equal(added.files.length, 3);
  await rm(join(root, 'b.ts'));
  const removed = await analyzeProject({ projectRoot: root });
  assert.ok(!removed.files.some((file) => file.path === 'b.ts'));
  assert.ok(removed.edges.some((edge) => edge.resolution === 'unresolved'));
});

test('publication rejects drift that occurs during analysis, including same-size edits', async (context) => {
  const root = await fixture(context, { 'main.js': 'export const x = 1;' });
  await assert.rejects(analyzeProject({ projectRoot: root, beforeVerify: () => writeFile(join(root, 'main.js'), 'export const x = 2;') }), /SOURCE_CHANGED_DURING_SCAN/);
});

test('bounded scan reports omissions and unsupported input while skipping secret and generated files', async (context) => {
  const root = await fixture(context, { 'main.js': 'export const x = 1;', 'other.rs': 'fn main() {}', '.env': 'SECRET=never-read', 'credentials.json': '{"secret":"never-read"}', 'dist/generated.js': 'throw new Error("never-read")', 'node_modules/pkg/main.js': 'never-read', 'large.ts': 'x'.repeat(300) });
  const model = await analyzeProject({ projectRoot: root, limits: { maxFileBytes: 128 } });
  assert.deepEqual(model.files.map((file) => file.path), ['main.js', 'other.rs']);
  assert.ok(model.gaps.some((gap) => gap.code === 'FILE_SIZE_LIMIT'));
  assert.ok(model.gaps.some((gap) => gap.code === 'ADAPTER_UNSUPPORTED'));
  assert.equal(model.capabilities.find((capability) => capability.language === 'unsupported:rs').status, 'unsupported');
  assert.doesNotMatch(JSON.stringify(model), /never-read/);
  const limited = await analyzeProject({ projectRoot: root, limits: { maxFiles: 1 } });
  assert.equal(limited.files.length, 1);
  assert.ok(limited.gaps.some((gap) => gap.code === 'FILE_COUNT_LIMIT'));
});

test('source viewer only reads inventoried paths and reports stale content with bounded lines', async (context) => {
  const root = await fixture(context, { 'main.js': 'one\ntwo\nthree\nfour', 'README.md': 'not indexed' });
  const model = await analyzeProject({ projectRoot: root });
  const initial = await readSourceEvidence(model, { path: 'main.js', line: 2, limit: 2 });
  assert.equal(initial.text, 'two\nthree');
  assert.equal(initial.stale, false);
  await writeFile(join(root, 'main.js'), 'changed\nsource');
  assert.equal((await readSourceEvidence(model, { path: 'main.js' })).stale, true);
  await assert.rejects(readSourceEvidence(model, { path: '../main.js' }), /SOURCE_NOT_REGISTERED/);
  await assert.rejects(readSourceEvidence(model, { path: 'README.md' }), /SOURCE_NOT_REGISTERED/);
  await assert.rejects(readSourceEvidence(model, { path: 'main.js', limit: 500 }), /SOURCE_RANGE_INVALID/);
});

test('directory links are never traversed and replacing a registered ancestor by a link blocks source read', async (context) => {
  const root = await fixture(context, { 'src/a.js': 'export const a = 1;' });
  const outside = await fixture(context, { 'outside.js': 'secret outside source' });
  await symlink(outside, join(root, 'alias'), 'junction');
  const model = await analyzeProject({ projectRoot: root });
  assert.ok(model.gaps.some((gap) => gap.code === 'LINK_SKIPPED'));
  assert.ok(!model.files.some((file) => file.path.startsWith('alias/')));
  await rm(join(root, 'src'), { recursive: true });
  await symlink(outside, join(root, 'src'), 'junction');
  await assert.rejects(readSourceEvidence(model, { path: 'src/a.js' }), /SOURCE_LINK_REJECTED/);
});

test('analysis leaves every registered business source byte unchanged', async (context) => {
  const source = { 'main.js': 'export const x = 1;', 'compose.yaml': 'services:\n  web:\n    image: nginx\n' };
  const root = await fixture(context, source);
  const first = await analyzeProject({ projectRoot: root });
  await readSourceEvidence(first, { path: 'main.js' });
  await analyzeProject({ projectRoot: root });
  for (const [path, text] of Object.entries(source)) assert.equal(await readFile(join(root, path), 'utf8'), text);
});

test('invalid configuration produces explicit gaps rather than fabricated deployment values', async (context) => {
  const root = await fixture(context, { 'tsconfig.json': 'null', 'compose.yaml': 'services:\n  web:\n    networks: invalid-string\n    environment: invalid-string\n    ports: invalid-string\n    deploy:\n      replicas: ${COUNT}\nnetworks: invalid-string\n' });
  const model = await analyzeProject({ projectRoot: root });
  assert.ok(model.gaps.some((gap) => gap.code === 'CONFIG_PARSE_ERROR'));
  assert.ok(model.gaps.some((gap) => gap.code === 'COMPOSE_FIELD_INVALID'));
  assert.ok(model.gaps.some((gap) => gap.code === 'COMPOSE_FIELD_UNRESOLVED'));
  assert.ok(!model.nodes.some((node) => node.kind === 'resource'));
  const web = model.nodes.find((node) => node.kind === 'service');
  assert.equal(web.attributes.deployment.replicas, undefined);
  assert.deepEqual(web.attributes.environmentKeys, []);
  assert.equal(model.capabilities.find((item) => item.language === 'compose').status, 'partial');
});

test('invalid UTF-8 source is omitted with a coverage gap', async (context) => {
  const root = await fixture(context, { 'bad.js': Buffer.from([0xff, 0xfe, 0xff]), 'valid.js': 'export const x = 1;' });
  const model = await analyzeProject({ projectRoot: root });
  assert.deepEqual(model.files.map((file) => file.path), ['valid.js']);
  assert.ok(model.gaps.some((gap) => gap.code === 'ENCODING_UNSUPPORTED'));
});
