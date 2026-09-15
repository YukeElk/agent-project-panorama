// A pure projection: the same source or target identities appear in every view.
export function compileView(model, { kind = 'modules', scopeId = null, search = '', limit = 120, collapsedIds = [] } = {}) {
  if (!['modules', 'dependencies', 'deployment'].includes(kind)) throw new Error('VIEW_KIND_INVALID');
  if (!Number.isInteger(limit) || limit < 1 || limit > 1000) throw new Error('VIEW_LIMIT_INVALID');
  const nodeMap = new Map(model.nodes.map((node) => [node.id, node]));
  const children = new Map();
  for (const node of model.nodes) if (node.parentId) {
    if (!children.has(node.parentId)) children.set(node.parentId, []);
    children.get(node.parentId).push(node.id);
  }
  const descendants = (id) => {
    const result = new Set([id]);
    const queue = [id];
    for (let i = 0; i < queue.length; i += 1) for (const child of children.get(queue[i]) ?? []) if (!result.has(child)) { result.add(child); queue.push(child); }
    return result;
  };
  const scope = scopeId ? (nodeMap.has(scopeId) ? descendants(scopeId) : new Set()) : null;
  const hidden = new Set();
  for (const id of collapsedIds) for (const child of descendants(id)) if (child !== id) hidden.add(child);
  let candidates = model.nodes.filter((node) => !hidden.has(node.id) && (!scope || scope.has(node.id)));
  if (kind === 'deployment') candidates = candidates.filter((node) => ['service', 'resource'].includes(node.kind) || node.attributes?.deployment);
  else if (kind === 'modules') {
    // At the project root expose actual top-level groups/files, plus target services.
    // Drilling into a group exposes its contents rather than inventing architecture layers.
    candidates = candidates.filter((node) => scopeId ? (node.id === scopeId || node.parentId === scopeId || ['service', 'resource'].includes(node.kind)) : (!node.parentId && node.kind !== 'external'));
  }
  const needle = String(search).trim().toLocaleLowerCase();
  if (needle) {
    const searchable = model.nodes.filter((node) => !hidden.has(node.id) && (!scope || scope.has(node.id)) && `${node.label} ${node.sourcePath ?? ''}`.toLocaleLowerCase().includes(needle));
    candidates = kind === 'modules' ? searchable.filter((node) => node.kind !== 'external') : candidates.filter((node) => `${node.label} ${node.sourcePath ?? ''}`.toLocaleLowerCase().includes(needle));
  }
  const totalNodes = candidates.length;
  const selected = candidates.slice(0, limit);
  const visibleIds = new Set(selected.map((node) => node.id));
  const nearestVisible = (id) => {
    const visited = new Set();
    let current = id;
    while (current && !visited.has(current)) {
      if (visibleIds.has(current)) return current;
      visited.add(current); current = nodeMap.get(current)?.parentId;
    }
    return null;
  };
  const edgeMap = new Map();
  for (const edge of model.edges) {
    if (kind === 'deployment' && ['contains', 'declares'].includes(edge.kind)) continue;
    const from = kind === 'modules' ? nearestVisible(edge.from) : visibleIds.has(edge.from) ? edge.from : null;
    const to = kind === 'modules' ? nearestVisible(edge.to) : visibleIds.has(edge.to) ? edge.to : null;
    if (!from || !to || from === to) continue;
    const key = `${from}:${to}:${edge.kind}`;
    const projected = edgeMap.get(key);
    if (projected) { projected.sourceEdgeIds.push(edge.id); projected.evidence.push(...edge.evidence); }
    else edgeMap.set(key, { ...edge, from, to, sourceEdgeIds: [edge.id], evidence: [...edge.evidence] });
  }
  const count = selected.length;
  const columns = Math.max(1, Math.min(5, Math.ceil(Math.sqrt(count || 1))));
  const nodes = selected.map((node, index) => ({ ...node, attributes: { ...node.attributes }, evidence: [...node.evidence], childCount: (children.get(node.id) ?? []).length, x: 32 + (index % columns) * 260, y: 36 + Math.floor(index / columns) * 132, width: 218, height: 76 }));
  return { kind, sourceSnapshotId: model.snapshot.id, nodes, edges: [...edgeMap.values()], width: Math.max(340, columns * 260 + 32), height: Math.max(220, Math.ceil(count / columns) * 132 + 36), totalNodes, truncated: totalNodes > limit, gaps: [...model.gaps, ...(totalNodes > limit ? [{ code: 'VIEW_TRUNCATED', path: '', message: `当前仅显示 ${limit}/${totalNodes} 个节点，请缩小范围或搜索。` }] : [])] };
}
