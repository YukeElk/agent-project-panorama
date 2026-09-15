import { diffModels, equal } from './design-model.mjs';

const observedKinds = new Set(['contains', 'imports', 'type-import', 'dynamic-import', 'declares', 'depends-on', 'serves', 'deploys', 'uses']);
const evidencePresent = entity => Boolean(entity?.sourcePath || entity?.evidence?.length);
const coverageComparable = (base, current) => {
  const blockingGaps = model => (model.gaps ?? []).some(gap => gap.severity !== 'informational' && gap.code !== 'STATIC_ANALYSIS_BOUNDARY');
  if (blockingGaps(base) || blockingGaps(current)) return false;
  if (base.snapshot.parserVersion !== current.snapshot.parserVersion) return false;
  if (!equal(base.analysisConfig ?? null, current.analysisConfig ?? null)) return false;
  const currentCapabilities = new Map((current.capabilities ?? []).map(item => [item.language, item.status]));
  // Removing the last file of a language is a normal source change. A language
  // still present with changed parser availability is a coverage change.
  return (base.capabilities ?? []).every(item => !currentCapabilities.has(item.language) || currentCapabilities.get(item.language) === item.status);
};

export function reconcileCandidate(candidate, current) {
  const diff = diffModels(candidate.baseModel, candidate.target);
  const sourceNodes = new Map(current.nodes.map(node => [node.id, node]));
  const baseNodes = new Map(candidate.baseModel.nodes.map(node => [node.id, node]));
  const items = [];
  const complete = coverageComparable(candidate.baseModel, current);
  function sourceId(targetId) {
    if (targetId == null) return null;
    if (baseNodes.has(targetId)) return targetId;
    const binding = (candidate.bindings ?? []).find(item => item.targetNodeId === targetId && item.sourceSnapshotId === current.snapshot.id);
    return binding?.sourceNodeId;
  }
  function add(id, field, status, detail, entityKind = 'node') {
    items.push({ id: `${id}:${field}`, entityId: id, entityKind, field, status, detail });
  }
  function compareNodeField(target, actual, field, expected, isNew) {
    if (field === 'description' || field === 'responsibilities' || field === 'interfaces' || field === 'stateOwnership') {
      add(target.id, field, 'insufficient_evidence', `静态源码模型不能证明 ${field} 的业务语义或实现。`); return;
    }
    if (!actual || !evidencePresent(actual)) {
      add(target.id, field, 'insufficient_evidence', '没有可追溯且仍有效的源码实体与字段证据。'); return;
    }
    let observed;
    if (field.startsWith('deployment.')) {
      const key = field.slice('deployment.'.length);
      observed = actual.attributes?.deployment?.[key];
      if (observed === undefined) { add(target.id, field, 'insufficient_evidence', `解析器未提供声明部署字段 ${key}；不能推断运行部署。`); return; }
    } else if (field === 'parentId') {
      const mappedParent = sourceId(expected);
      if (expected != null && mappedParent == null) { add(target.id, field, 'insufficient_evidence', '目标父节点尚未绑定当前来源。'); return; }
      expected = mappedParent; observed = actual.parentId ?? null;
    } else if (['label', 'kind'].includes(field)) observed = actual[field];
    else { add(target.id, field, 'insufficient_evidence', `解析属性 ${field} 没有可验证的设计实现契约。`); return; }
    const matches = equal(observed, expected);
    add(target.id, field, matches ? 'reflected' : 'not_reflected', matches ? `当前${field.startsWith('deployment.') ? '声明配置' : '源码结构'}体现目标 ${field}。` : `当前可观察的 ${field} 与目标不同。`);
  }
  function compareNode(target, fields, original = null) {
    const mapped = sourceId(target.id); const actual = mapped == null ? null : sourceNodes.get(mapped);
    if (mapped == null) add(target.id, 'identity', 'insufficient_evidence', '新增目标尚未由用户绑定到本次源码快照；不按同名推断。');
    else if (!actual) add(target.id, 'identity', complete && original ? 'not_reflected' : 'insufficient_evidence', '对应源码实体缺失；结构或扫描覆盖不足以证明目标新增或修改。');
    else if (actual.kind !== target.kind) add(target.id, 'identity', 'insufficient_evidence', '实体类型已变化，需要重新确认来源对应。');
    const usable = actual?.kind === target.kind ? actual : null;
    for (const field of fields) {
      if (field !== 'attributes') { compareNodeField(target, usable, field, target[field] ?? null, !original); continue; }
      const attributes = target.attributes ?? {}; const old = original?.attributes ?? {};
      for (const key of new Set([...Object.keys(old), ...Object.keys(attributes)])) {
        if (original && equal(old[key], attributes[key])) continue;
        if (key === 'deployment') {
          const targetDeploy = attributes.deployment ?? {}; const oldDeploy = old.deployment ?? {};
          for (const item of new Set([...Object.keys(oldDeploy), ...Object.keys(targetDeploy)])) {
            if (original && equal(oldDeploy[item], targetDeploy[item])) continue;
            compareNodeField(target, usable, `deployment.${item}`, targetDeploy[item], !original);
          }
        } else compareNodeField(target, usable, key, attributes[key], !original);
      }
    }
  }
  for (const target of diff.nodes.added) compareNode(target, ['kind', 'label', 'parentId', ...(target.description ? ['description'] : []), 'attributes']);
  for (const item of diff.nodes.changed) compareNode(item.after, item.fields, item.before);
  for (const removed of diff.nodes.removed) {
    const present = sourceNodes.has(removed.id);
    add(removed.id, 'removed', present ? 'not_reflected' : complete && evidencePresent(removed) ? 'reflected' : 'insufficient_evidence', present ? '原有源码实体仍然存在。' : complete && evidencePresent(removed) ? '在覆盖可比较的源码模型中，原有实体已移除。' : '扫描存在缺口或覆盖发生变化，缺失不能证明删除完成。');
  }
  function compareEdge(edge, action, before = null) {
    const from = sourceId(edge.from); const to = sourceId(edge.to);
    if (from === undefined || to === undefined || !sourceNodes.has(from) || !sourceNodes.has(to)) {
      add(edge.id, action, 'insufficient_evidence', '关系端点没有有效的源码对应。', 'edge'); return;
    }
    if (!observedKinds.has(edge.kind)) {
      add(edge.id, action, 'insufficient_evidence', '目标调用／事件关系需要额外实现或运行证据，静态导入不证明该关系。', 'edge'); return;
    }
    const same = current.edges.find(item => item.from === from && item.to === to && item.kind === edge.kind && evidencePresent(item));
    if (action === 'removed') {
      add(edge.id, action, same ? 'not_reflected' : complete && evidencePresent(edge) ? 'reflected' : 'insufficient_evidence', same ? '原关系仍由源码支持。' : complete && evidencePresent(edge) ? '覆盖可比较的源码中已不存在该静态关系。' : '缺失关系没有足够的解析覆盖证明。', 'edge'); return;
    }
    if (!same) add(edge.id, action, complete ? 'not_reflected' : 'insufficient_evidence', complete ? '当前解析范围未体现目标静态关系。' : '未找到关系且解析存在缺口。', 'edge');
    else {
      add(edge.id, action, 'reflected', '当前源码证据体现目标端点及静态关系类型。', 'edge');
      if ((!before || !equal(before.label, edge.label)) && edge.label) add(edge.id, 'label', equal(same.label, edge.label) ? 'reflected' : 'not_reflected', '对照关系在源码模型中的可观察名称。', 'edge');
    }
    for (const key of Object.keys(edge.attributes ?? {})) if (!before || !equal(before.attributes?.[key], edge.attributes[key])) add(edge.id, key, 'insufficient_evidence', `静态关系不足以证明 ${key} 的通信语义。`, 'edge');
  }
  for (const edge of diff.edges.added) compareEdge(edge, 'added');
  for (const change of diff.edges.changed) compareEdge(change.after, 'changed', change.before);
  for (const edge of diff.edges.removed) {
    // Removing both endpoints can prove an edge disappeared only with comparable coverage.
    if ((!sourceNodes.has(sourceId(edge.from)) || !sourceNodes.has(sourceId(edge.to))) && complete && evidencePresent(edge)) add(edge.id, 'removed', 'reflected', '完整可比较的来源中，原关系端点已移除。', 'edge');
    else compareEdge(edge, 'removed');
  }
  const states = new Set(items.map(item => item.status));
  const status = states.size === 0 ? 'insufficient_evidence' : states.size === 1 ? [...states][0] : 'partial';
  return { status, items, sourceSnapshotId: current.snapshot.id, candidateVersion: candidate.version };
}
