import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir, networkInterfaces } from 'node:os';
import { request as httpRequest } from 'node:http';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { basename, dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { createStandaloneServer } from '../../src/server/standalone-server.mjs';

async function setup(context, extra = {}) {
  const area = await mkdtemp(join(tmpdir(), 'panorama-standalone-api-'));
  const projectRoot = join(area, 'project');
  const dataRoot = join(area, 'data');
  const staticRoot = join(area, 'web');
  await mkdir(projectRoot);
  await mkdir(staticRoot);
  await writeFile(join(projectRoot, 'orders.ts'), 'export function order() { return 1; }\n');
  await writeFile(join(projectRoot, '.env'), 'PRIVATE_VALUE=never-export-this\n');
  await writeFile(join(staticRoot, 'index.html'), '<!doctype html><title>Panorama test</title>');
  const servers = [];
  context.after(async () => {
    for (const server of servers) await server.close();
    assert.equal(dirname(resolve(area)), resolve(tmpdir()));
    assert.ok(basename(area).startsWith('panorama-standalone-api-'));
    await rm(area, { recursive: true, force: true });
  });
  const options = { projectRoot, dataRoot, staticRoot, modelConfig: { available: false, label: '外部 Agent 交接' }, ...extra };
  const start = async (overrides = {}) => { const local = await createStandaloneServer({ ...options, ...overrides }); servers.push(local); return local; };
  const local = await start();
  return { area, local, start, projectRoot, dataRoot };
}

async function request(local, path, body, headerOverrides = {}) {
  const response = await fetch(`${local.origin}/api/standalone/${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { authorization: `Bearer ${local.capability}`, ...(body ? { origin: local.origin, 'content-type': 'application/json' } : {}), ...headerOverrides },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: await response.json(), headers: response.headers };
}

async function design(local) {
  let workspace = (await request(local, 'bootstrap')).body.workspace;
  let result = await request(local, 'command', { type: 'create-session', input: { title: '需求分析', kind: 'requirements', question: '增加通知', constraints: '订单不中断' }, expectedRevision: workspace.revision });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  workspace = result.body.workspace;
  const sessionId = workspace.sessions.at(-1).id;
  result = await request(local, 'command', { type: 'create-candidate', input: { sessionId, title: '独立通知' }, expectedRevision: workspace.revision });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  workspace = result.body.workspace;
  return { workspace, sessionId, candidate: workspace.candidates.at(-1) };
}

test('default standalone APIs isolate execution, enforce origin and expose registered source evidence', async (context) => {
  const { local, projectRoot } = await setup(context);
  const before = await readFile(join(projectRoot, 'orders.ts'), 'utf8');
  const bootstrap = await request(local, 'bootstrap');
  assert.equal(bootstrap.status, 200);
  assert.equal(bootstrap.body.service.mode, 'standalone');
  assert.equal(bootstrap.headers.get('cache-control'), 'no-store');
  assert.ok(bootstrap.body.workspace.current.nodes.length > 0);
  assert.doesNotMatch(JSON.stringify(bootstrap.body), /never-export-this/);
  assert.equal((await request(local, 'bootstrap', null, { authorization: '' })).status, 400);
  assert.equal((await request(local, 'command', { type: 'refresh', input: {}, expectedRevision: 0 }, { origin: 'https://untrusted.example' })).status, 400);
  const old = await fetch(`${local.origin}/api/v1/commands`, { method: 'POST', headers: { origin: local.origin, authorization: `Bearer ${local.capability}`, 'content-type': 'application/json' }, body: JSON.stringify({ type: 'run-codex' }) });
  assert.equal(old.status, 404);
  const source = await request(local, 'source?path=orders.ts&line=1');
  assert.equal(source.status, 200);
  assert.match(source.body.text, /export function order/);
  assert.equal((await request(local, 'source?path=../data/workspace.json')).status, 400);
  assert.equal((await request(local, 'source?path=.env')).status, 400);
  assert.equal(await readFile(join(projectRoot, 'orders.ts'), 'utf8'), before);
});

test('default listener permits actual non-loopback HTTP reads and design writes with the launch capability', async (context) => {
  const { local } = await setup(context);
  assert.equal(local.server.address().address, '0.0.0.0');
  assert.equal(local.listenHost, '0.0.0.0');
  const address = Object.values(networkInterfaces()).flat().find((item) => item?.family === 'IPv4' && !item.internal)?.address;
  assert.ok(address, 'This integration test requires a non-loopback IPv4 interface');
  const remote = { ...local, origin: `http://${address}:${local.port}` };
  assert.ok(local.launchUrls.includes(`${remote.origin}/#cap=${local.capability}`));
  const peers = [];
  local.server.on('request', (incoming) => peers.push(incoming.socket.remoteAddress));
  assert.equal((await fetch(remote.origin)).status, 200);
  const bootstrap = await request(remote, 'bootstrap');
  assert.equal(bootstrap.status, 200);
  const body = { type: 'create-session', input: { title: '远程设计', kind: 'architecture' }, expectedRevision: bootstrap.body.workspace.revision };
  assert.equal((await request(remote, 'command', body, { origin: local.origin })).body.error, 'ORIGIN_MISMATCH');
  assert.equal((await request(remote, 'command', body, { origin: '' })).body.error, 'ORIGIN_MISMATCH');
  assert.equal((await request(remote, 'bootstrap', null, { authorization: '' })).body.error, 'CAPABILITY_MISMATCH');
  assert.equal((await request(remote, 'bootstrap', null, { authorization: 'Bearer invalid' })).body.error, 'CAPABILITY_MISMATCH');
  assert.equal((await request(remote, 'command', body)).status, 200);
  assert.equal((await request(remote, 'bootstrap')).body.workspace.sessions.at(-1).title, '远程设计');
  assert.ok(peers.length > 0 && peers.every((peer) => peer === address || peer === `::ffff:${address}`));
});

test('same-origin checks follow a valid request Host, including a different external port', async (context) => {
  const { local } = await setup(context);
  const raw = (host, origin, path = '/api/standalone/bootstrap', body) => new Promise((done, reject) => {
    const outgoing = httpRequest(`${local.origin}${path}`, { method: body ? 'POST' : 'GET', headers: {
      Host: host, authorization: `Bearer ${local.capability}`, ...(origin ? { origin } : {}), ...(body ? { 'content-type': 'application/json' } : {}),
    } }, (incoming) => { let text = ''; incoming.setEncoding('utf8'); incoming.on('data', (chunk) => text += chunk); incoming.on('end', () => done({ status: incoming.statusCode, body: JSON.parse(text) })); });
    outgoing.on('error', reject); outgoing.end(body ? JSON.stringify(body) : undefined);
  });
  const alias = 'panorama.example:43110';
  const bootstrap = await raw(alias, `http://${alias}`);
  assert.equal(bootstrap.status, 200);
  const body = { type: 'create-session', input: { title: '域名与端口', kind: 'architecture' }, expectedRevision: bootstrap.body.workspace.revision };
  assert.equal((await raw(alias, `http://${alias}`, '/api/standalone/command', body)).status, 200);
  assert.equal((await raw(alias, 'http://other.example:43110')).body.error, 'ORIGIN_MISMATCH');
  for (const invalid of ['server/path', 'user@server', 'server:99999', 'server,other', 'server#fragment']) {
    assert.equal((await raw(invalid)).status, 403, invalid);
  }
});

test('an explicit loopback host and port remain configurable', async (context) => {
  const { local, start } = await setup(context);
  const port = local.port;
  await local.close();
  const restricted = await start({ host: '127.0.0.1', port });
  assert.equal(restricted.server.address().address, '127.0.0.1');
  assert.equal(restricted.port, port);
  assert.deepEqual(restricted.launchUrls, [restricted.launchUrl]);
  assert.equal((await request(restricted, 'bootstrap')).status, 200);
});

test('workbench CLI rejects invalid or incomplete network flags before locating a project', () => {
  const cli = fileURLToPath(new URL('../../src/cli.mjs', import.meta.url));
  for (const flags of [['--port', '43110abc'], ['--port', '-1'], ['--port', '65536'], ['--port'], ['--host', 'not-an-ip'], ['--host'], ['--port', '1', '--port', '2'], ['--unknown', '1']]) {
    const child = spawnSync(process.execPath, [cli, 'start', '--project', 'missing-remote-test-project', ...flags], { encoding: 'utf8', windowsHide: true, timeout: 10000 });
    assert.equal(child.status, 2, child.stderr);
    assert.match(child.stderr, /PORT_INVALID|LISTEN_HOST_INVALID|OPTION_INVALID/);
  }
  const help = spawnSync(process.execPath, [cli, 'start', '--help'], { encoding: 'utf8', windowsHide: true, timeout: 10000 });
  assert.equal(help.status, 0); assert.match(help.stdout, /--host 0\.0\.0\.0/);
});

test('external natural language, preview application, review and handoff persist without business writes', async (context) => {
  const { local, start, projectRoot } = await setup(context);
  const before = await readFile(join(projectRoot, 'orders.ts'), 'utf8');
  let { workspace, sessionId, candidate } = await design(local);
  const conversation = await request(local, 'conversation', { sessionId, candidateId: candidate.id, message: '请比较同步和异步通知，先给出设计建议', expectedRevision: workspace.revision });
  assert.equal(conversation.status, 200, JSON.stringify(conversation.body));
  assert.equal(conversation.body.kind, 'external');
  workspace = conversation.body.workspace;
  assert.equal(workspace.sessions[0].messages.length, 2);
  assert.deepEqual(workspace.current, candidate.baseModel);
  const operations = [{ type: 'add-node', node: { id: 'target:notifications', kind: 'service', label: '通知服务', attributes: { deployment: { environment: 'worker', replicas: 1, ports: [] }, responsibilities: ['通知'] } } }];
  const applied = await request(local, 'command', { type: 'apply-operations', input: { candidateId: candidate.id, candidateVersion: candidate.version, baseSnapshotId: candidate.baseSnapshotId, operations }, expectedRevision: workspace.revision });
  assert.equal(applied.status, 200, JSON.stringify(applied.body));
  workspace = applied.body.workspace;
  candidate = workspace.candidates[0];
  assert.ok(candidate.target.nodes.some((node) => node.id === 'target:notifications'));
  assert.ok(!workspace.current.nodes.some((node) => node.id === 'target:notifications'));
  const review = await request(local, 'command', { type: 'import-review', input: { candidateId: candidate.id, source: '外部审阅', markdown: '建议确认重试语义。尚未实施。', candidateVersion: candidate.version, baseSnapshotId: candidate.baseSnapshotId }, expectedRevision: workspace.revision });
  assert.equal(review.status, 200, JSON.stringify(review.body));
  workspace = review.body.workspace;
  assert.equal(workspace.reviews[0].decision, 'pending');
  const handoff = await request(local, 'command', { type: 'export-handoff', input: { candidateId: candidate.id, purpose: 'implementation' }, expectedRevision: workspace.revision });
  assert.equal(handoff.status, 200, JSON.stringify(handoff.body));
  assert.match(handoff.body.output.markdown, /通知服务/);
  assert.match(handoff.body.output.markdown, new RegExp(candidate.baseSnapshotId));
  assert.doesNotMatch(handoff.body.output.markdown, /never-export-this/);
  assert.equal(await readFile(join(projectRoot, 'orders.ts'), 'utf8'), before);
  await local.close();
  const restarted = await start();
  const after = (await request(restarted, 'bootstrap')).body.workspace;
  assert.equal(after.sessions[0].messages.length, 2);
  assert.equal(after.candidates[0].target.nodes.find((node) => node.id === 'target:notifications').label, '通知服务');
});

test('a concurrent edit during model analysis rejects the old answer atomically', async (context) => {
  let release;
  let notifyStarted;
  const started = new Promise((resolveStarted) => { notifyStarted = resolveStarted; });
  const delayed = new Promise((resolveReply) => { release = resolveReply; });
  const { local } = await setup(context, {
    modelConfig: { available: true, name: 'test', label: '协议测试', endpoint: 'http://127.0.0.1/test' },
    fetchImpl: async () => { notifyStarted(); await delayed; return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify({ kind: 'answer', text: '需要评估重试', operations: [] }) } }] })); },
  });
  const { workspace, sessionId, candidate } = await design(local);
  const pending = request(local, 'conversation', { sessionId, candidateId: candidate.id, message: '评估需求', expectedRevision: workspace.revision });
  await started;
  const changed = await request(local, 'command', { type: 'update-session', input: { id: sessionId, constraints: '用户新增限制' }, expectedRevision: workspace.revision });
  assert.equal(changed.status, 200, JSON.stringify(changed.body));
  release();
  const obsolete = await pending;
  assert.equal(obsolete.status, 409, JSON.stringify(obsolete.body));
  const after = (await request(local, 'bootstrap')).body.workspace;
  assert.equal(after.sessions[0].constraints, '用户新增限制');
  assert.equal(after.sessions[0].messages.length, 0);
});

test('source refresh invalidates old proposal bindings and reports modified evidence', async (context) => {
  const { local, projectRoot } = await setup(context);
  let { workspace, candidate } = await design(local);
  await writeFile(join(projectRoot, 'orders.ts'), 'export function order() { return 2; }\n');
  const evidence = await request(local, 'source?path=orders.ts');
  assert.equal(evidence.status, 200);
  assert.equal(evidence.body.stale, true);
  const refreshed = await request(local, 'command', { type: 'refresh', input: {}, expectedRevision: workspace.revision });
  assert.equal(refreshed.status, 200, JSON.stringify(refreshed.body));
  workspace = refreshed.body.workspace;
  assert.notEqual(workspace.current.snapshot.id, candidate.baseSnapshotId);
  const oldProposal = await request(local, 'command', { type: 'apply-operations', input: { candidateId: candidate.id, candidateVersion: candidate.version, baseSnapshotId: candidate.baseSnapshotId, operations: [{ type: 'add-node', node: { id: 'target:old', kind: 'module', label: '旧建议' } }] }, expectedRevision: workspace.revision });
  assert.equal(oldProposal.status, 409, JSON.stringify(oldProposal.body));
  assert.ok(!(await request(local, 'bootstrap')).body.workspace.candidates[0].target.nodes.some((node) => node.id === 'target:old'));
});
