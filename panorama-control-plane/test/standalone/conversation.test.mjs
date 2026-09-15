import assert from 'node:assert/strict';
import test from 'node:test';
import { analyzeConversation, buildDesignContext, modelConfiguration } from '../../src/standalone/conversation.mjs';

function fixture() {
  const model = {
    schemaVersion: 'panorama.source.v1', project: { id: 'project:example', name: '示例', root: '/not-sent' },
    snapshot: { id: 'snapshot:one' },
    nodes: [{ id: 'module:orders', kind: 'module', label: '订单', parentId: null, sourcePath: 'src/orders', attributes: {}, evidence: [] }],
    edges: [], files: [], capabilities: [], gaps: [],
  };
  const workspace = { revision: 3, current: model,
    sessions: [{ id: 'session:one', title: '通知需求', kind: 'requirements', question: '如何处理通知？', constraints: '不阻塞下单', unknowns: [], messages: [{ role: 'user', content: '先考虑邮件通知' }, { role: 'assistant', content: '失败时需要重试吗？' }] }],
    candidates: [{ id: 'candidate:one', sessionId: 'session:one', title: '异步通知', version: 2, baseSnapshotId: 'snapshot:one', target: model }],
  };
  const input = { sessionId: 'session:one', candidateId: 'candidate:one', expectedRevision: 3, message: '需要重试，先帮我分析方案', selectedId: 'module:orders' };
  return { workspace, input };
}
const config = { available: true, name: 'test-model', label: '协议测试服务', endpoint: 'http://127.0.0.1/v1/chat/completions', apiKey: 'test-secret' };
const response = (value) => new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(value) } }] }), { headers: { 'content-type': 'application/json' } });

test('no model provides a complete external design brief, not a fabricated model response', async () => {
  const { workspace, input } = fixture();
  const result = await analyzeConversation(workspace, input, { config: { available: false } });
  assert.equal(result.kind, 'external');
  assert.equal(result.candidateVersion, 2);
  assert.match(result.prompt, /snapshot:one/);
  assert.match(result.prompt, /不阻塞下单/);
  assert.match(result.prompt, /先考虑邮件通知/);
  assert.doesNotMatch(result.prompt, /not-sent|test-secret/);
  assert.deepEqual(result.operations, []);
});

test('model request preserves bounded history and proposal remains an unapplied target', async () => {
  const { workspace, input } = fixture();
  const before = structuredClone(workspace);
  const operations = [{ type: 'add-node', node: { id: 'target:notify', kind: 'service', label: '通知服务', attributes: { responsibilities: ['发送通知'], deployment: { environment: 'worker', replicas: 1, ports: [] } } } }];
  let request;
  const result = await analyzeConversation(workspace, input, { config, fetchImpl: async (url, init) => {
    request = { url, init, body: JSON.parse(init.body) };
    return response({ kind: 'proposal', text: '新增通知服务，仍需确认重试策略。', operations });
  } });
  assert.equal(request.url, config.endpoint);
  assert.equal(request.init.redirect, 'error');
  assert.equal(request.init.headers.authorization, 'Bearer test-secret');
  assert.match(request.body.messages[1].content, /失败时需要重试吗/);
  assert.equal(result.kind, 'proposal');
  assert.equal(result.operations.length, 1);
  assert.deepEqual(workspace, before);
});

test('model output cannot forge source evidence, dangling references or command execution', async () => {
  const { workspace, input } = fixture();
  for (const operations of [
    [{ type: 'update-node', id: 'module:orders', changes: { sourcePath: '../../credentials' } }],
    [{ type: 'add-edge', edge: { id: 'target:bad', from: 'module:orders', to: 'missing', kind: 'calls' } }],
    [{ type: 'execute', command: 'write business source' }],
  ]) {
    await assert.rejects(analyzeConversation(workspace, input, { config, fetchImpl: async () => response({ kind: 'proposal', text: 'bad', operations }) }), /MODEL_OPERATIONS_INVALID/);
  }
});

test('query replies cannot carry hidden operations and stale input never calls a model', async () => {
  const { workspace, input } = fixture();
  await assert.rejects(analyzeConversation(workspace, input, { config, fetchImpl: async () => response({ kind: 'answer', text: '回答', operations: [{ type: 'remove-node', id: 'module:orders' }] }) }), /MODEL_UNEXPECTED_OPERATIONS/);
  let called = false;
  await assert.rejects(analyzeConversation(workspace, { ...input, expectedRevision: 2 }, { config, fetchImpl: async () => { called = true; } }), /REVISION_CONFLICT/);
  assert.equal(called, false);
  workspace.candidates[0].baseSnapshotId = 'old';
  assert.throws(() => buildDesignContext(workspace, input), /STALE_BASELINE/);
});

test('provider failures and invalid responses remain explicit without echoing secrets', async () => {
  const { workspace, input } = fixture();
  await assert.rejects(analyzeConversation(workspace, input, { config, fetchImpl: async () => new Response('test-secret', { status: 401 }) }), /MODEL_HTTP_401/);
  await assert.rejects(analyzeConversation(workspace, input, { config, fetchImpl: async () => { throw new Error('private provider failure test-secret'); } }), /^Error: MODEL_REQUEST_FAILED$/);
  await assert.rejects(analyzeConversation(workspace, input, { config, fetchImpl: async () => new Response(JSON.stringify({ choices: [{ message: { content: 'not json' } }] })) }), /MODEL_JSON_INVALID/);
  await assert.rejects(analyzeConversation(workspace, input, { config, fetchImpl: async () => { throw new DOMException('timeout', 'TimeoutError'); } }), /MODEL_TIMEOUT/);
});

test('model endpoint is explicit configuration with no redirects or URL credentials', () => {
  assert.equal(modelConfiguration({}).available, false);
  assert.equal(modelConfiguration({ PANORAMA_MODEL_BASE_URL: 'http://127.0.0.1:9000/v1/', PANORAMA_MODEL_NAME: 'local' }).endpoint, 'http://127.0.0.1:9000/v1/chat/completions');
  for (const url of ['http://remote.example/v1', 'https://user:secret@example.com/v1', 'file:///tmp/model', 'https://example.com/v1?key=secret']) {
    assert.throws(() => modelConfiguration({ PANORAMA_MODEL_BASE_URL: url, PANORAMA_MODEL_NAME: 'model' }), /MODEL_BASE_URL_INVALID/);
  }
});
