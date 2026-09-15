import test from 'node:test';
import assert from 'node:assert/strict';
import { applyOperations, diffModels, stateOwnershipQuestions } from '../../src/standalone/design-model.mjs';

const node = (id, attributes = {}, extra = {}) => ({ id, kind: 'module', label: id, parentId: null, attributes, description: '', sourcePath: `${id}.ts`, line: 1, evidence: [{ path: `${id}.ts`, line: 1, kind: 'source', detail: 'Module' }], ...extra });
const baseline = () => ({ nodes: [node('orders', { responsibilities: ['订单', '通知'], stateOwnership: ['orders'], parserAttribute: 'preserve', deployment: { environment: 'api', replicas: 1, ports: ['8080'] } }), node('payments', { responsibilities: ['支付'], stateOwnership: ['payments'] })], edges: [{ id: 'dependency', from: 'orders', to: 'payments', kind: 'imports', label: '', evidence: [] }] });

test('split responsibilities, interfaces, async communication and deployment are one immutable semantic transaction', () => {
  const base = baseline(); const original = structuredClone(base);
  const target = applyOperations(base, [
    { type: 'update-node', id: 'orders', changes: { attributes: { responsibilities: ['订单'] } } },
    { type: 'add-node', node: { id: 'notifications', kind: 'service', label: '通知服务', attributes: { responsibilities: ['通知'], interfaces: [{ name: 'notify', protocol: 'queue', description: '接受订单通知' }], deployment: { environment: 'worker', replicas: 2, ports: [] } } } },
    { type: 'add-edge', edge: { id: 'events', from: 'orders', to: 'notifications', kind: 'publishes', attributes: { communication: 'async', channel: 'order-events' } } },
  ]);
  assert.deepEqual(base, original); assert.deepEqual(target.nodes[0].attributes.responsibilities, ['订单']);
  assert.equal(target.nodes[0].attributes.parserAttribute, 'preserve'); assert.equal(target.nodes[0].attributes.deployment.environment, 'api');
  assert.equal(target.nodes[2].origin, 'target'); assert.equal(target.nodes[2].sourcePath, null); assert.deepEqual(target.nodes[2].evidence, []);
  assert.equal(target.edges[1].attributes.communication, 'async');
  const diff = diffModels(base, target); assert.deepEqual(diff.summary, { added: 2, removed: 0, changed: 1 }); assert.deepEqual(diff.nodes.changed[0].fields, ['attributes']);
});

test('merge redirects relationships explicitly and does not auto-remove unselected dependencies or children', () => {
  const base = baseline(); base.nodes.push(node('child', {}, { parentId: 'payments' }));
  assert.throws(() => applyOperations(base, [{ type: 'remove-node', id: 'payments' }]), /Missing parent|Dangling edge/);
  assert.throws(() => applyOperations(base, [{ type: 'remove-edge', id: 'dependency' }, { type: 'remove-node', id: 'payments' }]), /Missing parent/);
  const merged = applyOperations(base, [
    { type: 'update-node', id: 'orders', changes: { attributes: { responsibilities: ['订单', '通知', '支付'] } } },
    { type: 'update-node', id: 'child', changes: { parentId: 'orders' } },
    { type: 'remove-edge', id: 'dependency' }, { type: 'remove-node', id: 'payments' },
    { type: 'add-edge', edge: { id: 'new', from: 'orders', to: 'child', kind: 'uses' } },
  ]);
  assert.equal(merged.nodes.length, 2); assert.equal(merged.nodes[1].parentId, 'orders'); assert.equal(merged.edges[0].to, 'child');
});

test('state migration changes both owners and unresolved multi-ownership produces a question', () => {
  const base = baseline();
  const shared = applyOperations(base, [{ type: 'update-node', id: 'payments', changes: { attributes: { stateOwnership: ['payments', 'orders'] } } }]);
  assert.equal(stateOwnershipQuestions(shared).length, 1);
  const migrated = applyOperations(shared, [{ type: 'update-node', id: 'orders', changes: { attributes: { stateOwnership: [] } } }]);
  assert.equal(stateOwnershipQuestions(migrated).length, 0); assert.equal(diffModels(base, migrated).nodes.changed.length, 2);
});

test('malformed, invented source, unsupported attribute, cyclic and duplicate changes reject the full batch', () => {
  const base = baseline(); const original = structuredClone(base);
  const invalid = [
    [{ type: 'update-node', id: 'orders', changes: { sourcePath: 'fake.ts' } }],
    [{ type: 'add-node', node: { kind: 'service', label: 'fake', evidence: [{ path: 'fake.ts' }] } }],
    [{ type: 'update-node', id: 'orders', changes: { attributes: { command: 'run something' } } }],
    [{ type: 'update-node', id: 'orders', changes: { attributes: { deployment: { environment: 'api', replicas: -1, ports: [] } } } }],
    [{ type: 'update-node', id: 'orders', changes: { parentId: 'payments' } }, { type: 'update-node', id: 'payments', changes: { parentId: 'orders' } }],
    [{ type: 'add-node', node: { id: 'orders', kind: 'module', label: 'duplicate' } }],
    [{ type: 'update-edge', id: 'dependency', changes: { from: 'payments' } }],
    [{ type: 'exec', command: 'anything' }],
  ];
  for (const operations of invalid) assert.throws(() => applyOperations(base, operations));
  assert.deepEqual(base, original);
});

test('generated IDs are deterministic, layout is not semantic, and changes return before/after', () => {
  const base = baseline(); const op = [{ type: 'add-node', node: { kind: 'service', label: 'new' } }];
  assert.deepEqual(applyOperations(base, op), applyOperations(base, op));
  const layout = structuredClone(base); layout.nodes[0].x = 500; layout.nodes[0].y = 20; layout.snapshot = { id: 'other' }; layout.nodes[0].origin = 'target';
  assert.equal(diffModels(base, layout).hasChanges, false);
  const target = applyOperations(base, [{ type: 'update-edge', id: 'dependency', changes: { kind: 'calls', attributes: { communication: 'sync' } } }]);
  const change = diffModels(base, target).edges.changed[0]; assert.equal(change.before.kind, 'imports'); assert.equal(change.after.kind, 'calls');
});
