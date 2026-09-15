// Browser-safe design domain: no filesystem, clocks, random IDs, or execution tools.
export const NODE_KINDS = ['module', 'file', 'symbol', 'route', 'service', 'resource', 'external'];
export const EDGE_KINDS = ['contains', 'imports', 'depends-on', 'serves', 'deploys', 'uses', 'calls', 'publishes', 'subscribes'];
export const clone = value => structuredClone(value);
export function domainError(message, statusCode = 400, code = 'INVALID_INPUT') {
  return Object.assign(new Error(message), { statusCode, code });
}
export function assert(condition, message, statusCode = 400, code = 'INVALID_INPUT') {
  if (!condition) throw domainError(message, statusCode, code);
}
export function object(value, name = 'object') {
  assert(value !== null && typeof value === 'object' && !Array.isArray(value), `${name} must be an object`);
  assert(Object.keys(value).every(key => !['__proto__', 'constructor', 'prototype'].includes(key)), `${name} contains a forbidden key`);
  return value;
}
export function text(value, name, { max = 10000, empty = false } = {}) {
  assert(typeof value === 'string' && value.length <= max && (empty || value.trim().length > 0), `${name} must be ${empty ? '' : 'nonempty '}text (max ${max})`);
  return value;
}
export function stringList(value, name, max = 100) {
  assert(Array.isArray(value) && value.length <= max, `${name} must be a bounded array`);
  value.forEach(item => text(item, name, { max: 4000 }));
  return value;
}
const allowedKeys = (value, keys, name) => assert(Object.keys(value).every(key => keys.includes(key)), `${name} contains unsupported fields`);
const idText = (value, name = 'id') => text(value, name, { max: 512 });

function designAttributes(value, isEdge) {
  object(value, 'attributes');
  allowedKeys(value, isEdge ? ['communication', 'channel'] : ['responsibilities', 'interfaces', 'stateOwnership', 'deployment'], 'attributes');
  if (isEdge) {
    if ('communication' in value) assert(['sync', 'async', 'unspecified'].includes(value.communication), 'Invalid communication');
    if ('channel' in value) text(value.channel, 'channel', { max: 1000, empty: true });
  } else {
    for (const key of ['responsibilities', 'stateOwnership']) if (key in value) stringList(value[key], key);
    if ('interfaces' in value) {
      assert(Array.isArray(value.interfaces) && value.interfaces.length <= 100, 'interfaces must be a bounded array');
      for (const entry of value.interfaces) {
        object(entry, 'interface'); allowedKeys(entry, ['name', 'protocol', 'description'], 'interface');
        text(entry.name, 'interface.name', { max: 500 });
        text(entry.protocol, 'interface.protocol', { max: 200 });
        text(entry.description, 'interface.description', { empty: true, max: 4000 });
      }
    }
    if ('deployment' in value) {
      const item = object(value.deployment, 'deployment');
      allowedKeys(item, ['environment', 'replicas', 'ports', 'image'], 'deployment');
      text(item.environment, 'deployment.environment', { max: 500 });
      assert(Number.isSafeInteger(item.replicas) && item.replicas >= 0, 'deployment.replicas must be a nonnegative integer');
      stringList(item.ports, 'deployment.ports');
      if ('image' in item) text(item.image, 'deployment.image', { max: 1000, empty: true });
    }
  }
  return clone(value);
}

export function validateGraph(model) {
  object(model, 'model');
  assert(Array.isArray(model.nodes) && model.nodes.length <= 50000, 'Invalid model.nodes');
  assert(Array.isArray(model.edges) && model.edges.length <= 200000, 'Invalid model.edges');
  const nodes = new Map();
  for (const node of model.nodes) {
    object(node, 'node'); idText(node.id); assert(!nodes.has(node.id), `Duplicate node ${node.id}`);
    assert(NODE_KINDS.includes(node.kind), `Invalid node kind: ${node.kind}`);
    text(node.label, 'node.label', { max: 4000 });
    if (node.parentId != null) idText(node.parentId, 'parentId');
    object(node.attributes ?? {}, 'node.attributes'); nodes.set(node.id, node);
  }
  const complete = new Set();
  for (const node of nodes.values()) {
    const visiting = new Set(); let cursor = node;
    while (cursor && !complete.has(cursor.id)) {
      assert(!visiting.has(cursor.id), `Parent cycle at ${cursor.id}`); visiting.add(cursor.id);
      assert(cursor.parentId == null || nodes.has(cursor.parentId), `Missing parent ${cursor.parentId}`);
      cursor = cursor.parentId == null ? null : nodes.get(cursor.parentId);
    }
    for (const id of visiting) complete.add(id);
  }
  const edgeIds = new Set();
  for (const edge of model.edges) {
    object(edge, 'edge'); idText(edge.id); assert(!edgeIds.has(edge.id), `Duplicate edge ${edge.id}`); edgeIds.add(edge.id);
    assert(nodes.has(edge.from) && nodes.has(edge.to), `Dangling edge ${edge.id}`);
    text(edge.kind, 'edge.kind', { max: 100 });
    if (edge.label != null) text(edge.label, 'edge.label', { max: 4000, empty: true });
    object(edge.attributes ?? {}, 'edge.attributes');
  }
  return model;
}

export function validateOperations(operations) {
  assert(Array.isArray(operations) && operations.length <= 500, 'operations must be an array of at most 500 items');
  for (const operation of operations) {
    const op = object(operation, 'operation');
    assert(['add-node', 'update-node', 'remove-node', 'add-edge', 'update-edge', 'remove-edge'].includes(op.type), `Unsupported operation ${op.type}`);
    const isEdge = op.type.endsWith('edge');
    if (op.type.startsWith('add-')) {
      allowedKeys(op, ['type', isEdge ? 'edge' : 'node'], 'operation');
      const item = object(op[isEdge ? 'edge' : 'node'], 'new entity');
      allowedKeys(item, isEdge ? ['id', 'from', 'to', 'kind', 'label', 'attributes'] : ['id', 'kind', 'label', 'description', 'parentId', 'attributes'], 'new entity');
      if (item.id !== undefined) idText(item.id);
      assert((isEdge ? EDGE_KINDS : NODE_KINDS).includes(item.kind), 'Invalid target entity kind');
      if (isEdge) { idText(item.from, 'edge.from'); idText(item.to, 'edge.to'); if (item.label !== undefined) text(item.label, 'label', { empty: true, max: 4000 }); }
      else { text(item.label, 'label', { max: 4000 }); if (item.parentId != null) idText(item.parentId, 'parentId'); if (item.description !== undefined) text(item.description, 'description', { empty: true, max: 10000 }); }
      if (item.attributes !== undefined) designAttributes(item.attributes, isEdge);
    } else {
      allowedKeys(op, op.type.startsWith('update-') ? ['type', 'id', 'changes'] : ['type', 'id'], 'operation'); idText(op.id);
      if (op.type.startsWith('update-')) {
        const changes = object(op.changes, 'changes');
        allowedKeys(changes, isEdge ? ['label', 'kind', 'attributes'] : ['label', 'description', 'attributes', 'parentId'], 'changes');
        if ('label' in changes) text(changes.label, 'label', { max: 4000, empty: isEdge });
        if ('description' in changes) text(changes.description, 'description', { max: 10000, empty: true });
        if ('parentId' in changes && changes.parentId != null) idText(changes.parentId, 'parentId');
        if ('kind' in changes) assert(EDGE_KINDS.includes(changes.kind), 'Invalid target edge kind');
        if ('attributes' in changes) designAttributes(changes.attributes, isEdge);
      }
    }
  }
  return operations;
}

export function applyOperations(model, operations) {
  validateGraph(model);
  validateOperations(operations);
  const result = clone(model);
  const freshId = (prefix, entities) => { const ids = new Set(entities.map(item => item.id)); let i = 1; while (ids.has(`${prefix}:${i}`)) i++; return `${prefix}:${i}`; };
  for (const op of operations) {
    object(op, 'operation');
    assert(['add-node', 'update-node', 'remove-node', 'add-edge', 'update-edge', 'remove-edge'].includes(op.type), `Unsupported operation ${op.type}`);
    const edge = op.type.endsWith('edge'); const entities = edge ? result.edges : result.nodes;
    if (op.type.startsWith('add-')) {
      allowedKeys(op, ['type', edge ? 'edge' : 'node'], 'operation');
      const item = clone(object(op[edge ? 'edge' : 'node'], 'new entity'));
      allowedKeys(item, edge ? ['id', 'from', 'to', 'kind', 'label', 'attributes'] : ['id', 'kind', 'label', 'description', 'parentId', 'attributes'], 'new entity');
      item.id ??= freshId(edge ? 'target:edge' : 'target:node', entities); idText(item.id);
      assert(!entities.some(existing => existing.id === item.id), `Duplicate id ${item.id}`);
      item.attributes = designAttributes(item.attributes ?? {}, edge); item.origin = 'target'; item.evidence = [];
      if (edge) {
        assert(EDGE_KINDS.includes(item.kind), `Invalid target edge kind ${item.kind}`);
        idText(item.from, 'edge.from'); idText(item.to, 'edge.to'); item.label ??= '';
        text(item.label, 'edge.label', { max: 4000, empty: true }); item.confidence = 'design'; item.resolution = 'target';
      } else {
        assert(NODE_KINDS.includes(item.kind), `Invalid target node kind ${item.kind}`);
        text(item.label, 'node.label', { max: 4000 }); item.parentId ??= null;
        item.description ??= ''; text(item.description, 'description', { max: 10000, empty: true });
        item.sourcePath = null; item.line = null;
      }
      entities.push(item);
    } else {
      allowedKeys(op, op.type.startsWith('update-') ? ['type', 'id', 'changes'] : ['type', 'id'], 'operation');
      idText(op.id); const index = entities.findIndex(item => item.id === op.id);
      assert(index !== -1, `Unknown entity ${op.id}`);
      if (op.type.startsWith('remove-')) { entities.splice(index, 1); continue; }
      const changes = object(op.changes, 'changes');
      allowedKeys(changes, edge ? ['label', 'kind', 'attributes'] : ['label', 'description', 'attributes', 'parentId'], 'changes');
      if ('label' in changes) text(changes.label, 'label', { max: 4000, empty: edge });
      if ('description' in changes) text(changes.description, 'description', { max: 10000, empty: true });
      if ('parentId' in changes && changes.parentId != null) idText(changes.parentId, 'parentId');
      if ('kind' in changes) assert(EDGE_KINDS.includes(changes.kind), `Invalid target edge kind ${changes.kind}`);
      const updated = { ...entities[index], ...clone(changes), origin: 'target' };
      if ('attributes' in changes) updated.attributes = { ...(entities[index].attributes ?? {}), ...designAttributes(changes.attributes, edge) };
      entities[index] = updated;
    }
  }
  // Whole-batch validation permits split/merge operations in either meaningful order,
  // but never silently removes unselected relationships or children.
  validateGraph(result);
  return result;
}

export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().filter(key => value[key] !== undefined).map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
export const equal = (a, b) => canonical(a) === canonical(b);
function entityDiff(beforeItems, afterItems, isEdge) {
  const before = new Map(beforeItems.map(item => [item.id, item]));
  const after = new Map(afterItems.map(item => [item.id, item]));
  const keys = isEdge ? ['from', 'to', 'kind', 'label', 'attributes'] : ['kind', 'label', 'parentId', 'description', 'attributes'];
  const added = afterItems.filter(item => !before.has(item.id)).map(clone);
  const removed = beforeItems.filter(item => !after.has(item.id)).map(clone);
  const changed = [];
  for (const item of afterItems) {
    const old = before.get(item.id); if (!old) continue;
    const fields = keys.filter(key => !equal(old[key] ?? (key === 'attributes' ? {} : key === 'description' ? '' : null), item[key] ?? (key === 'attributes' ? {} : key === 'description' ? '' : null)));
    if (fields.length) changed.push({ id: item.id, before: clone(old), after: clone(item), fields });
  }
  return { added, removed, changed };
}
export function diffModels(base, target) {
  validateGraph(base); validateGraph(target);
  const nodes = entityDiff(base.nodes, target.nodes, false); const edges = entityDiff(base.edges, target.edges, true);
  const summary = Object.fromEntries(['added', 'removed', 'changed'].map(key => [key, nodes[key].length + edges[key].length]));
  return { nodes, edges, summary, hasChanges: Object.values(summary).some(Boolean) };
}

export function stateOwnershipQuestions(model) {
  const owners = new Map();
  for (const node of model.nodes) for (const state of node.attributes?.stateOwnership ?? []) {
    const list = owners.get(state) ?? []; list.push(node); owners.set(state, list);
  }
  return [...owners].filter(([, nodes]) => nodes.length > 1).map(([state, nodes]) => `状态“${state}”存在多个拥有者：${nodes.map(node => node.label).join('、')}。请确认共享或迁移边界。`);
}
