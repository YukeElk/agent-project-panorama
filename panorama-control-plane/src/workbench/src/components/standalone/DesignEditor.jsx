import { useState } from 'react';
import { Field, KIND_LABELS, lines } from './Shared.jsx';

const makeId = (kind) => `target:${kind}:${crypto.randomUUID()}`;
const unique = (items) => [...new Set(items)];
const fields = (node) => ({
  label: node?.label || '', description: node?.description || '',
  responsibilities: (node?.attributes?.responsibilities || []).join('\n'),
  state: (node?.attributes?.stateOwnership || []).join('\n'),
  interfaces: (node?.attributes?.interfaces || []).map((item) => `${item.name}|${item.protocol}|${item.description || ''}`).join('\n'),
  environment: node?.attributes?.deployment?.environment || '',
  replicas: node?.attributes?.deployment?.replicas ?? 1,
  ports: (node?.attributes?.deployment?.ports || []).join('\n'),
  image: node?.attributes?.deployment?.image || '',
});

function attributes(form, deployment = false) {
  const result = {
    responsibilities: lines(form.responsibilities), stateOwnership: lines(form.state),
    interfaces: form.interfaces.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => { const [name, protocol = '待确定', description = ''] = line.split('|').map((value) => value.trim()); return { name, protocol, description }; }),
  };
  if (deployment || form.environment.trim()) result.deployment = { environment: form.environment.trim() || '待确定', replicas: Number(form.replicas), ports: lines(form.ports), ...(form.image.trim() ? { image: form.image.trim() } : {}) };
  return result;
}

function NodeSelect({ label, value, onChange, nodes, empty = '请选择节点' }) {
  return <Field label={label}><select value={value} onChange={(event) => onChange(event.target.value)}><option value="">{empty}</option>{nodes.map((node) => <option key={node.id} value={node.id}>{node.label} · {KIND_LABELS[node.kind] || node.kind}</option>)}</select></Field>;
}

function NodeFields({ form, setForm, deployment }) {
  const set = (name) => (event) => setForm((current) => ({ ...current, [name]: event.target.value }));
  return <>
    <Field label="节点名称"><input required value={form.label} onChange={set('label')} /></Field>
    <Field label="说明"><textarea rows="2" value={form.description} onChange={set('description')} /></Field>
    <div className="st-form-grid"><Field label="职责（每行一项）"><textarea rows="3" value={form.responsibilities} onChange={set('responsibilities')} /></Field><Field label="状态所有权（每行一项）"><textarea rows="3" value={form.state} onChange={set('state')} /></Field></div>
    <Field label="接口（每行：名称 | 协议 | 说明）"><textarea rows="2" placeholder="通知请求 | queue | 接受订单事件" value={form.interfaces} onChange={set('interfaces')} /></Field>
    <details open={deployment || Boolean(form.environment)}><summary>部署意图</summary><div className="st-form-grid"><Field label="部署环境"><input placeholder="例如 worker / production" value={form.environment} onChange={set('environment')} /></Field><Field label="副本数"><input type="number" min="0" step="1" value={form.replicas} onChange={set('replicas')} /></Field></div><Field label="端口（每行一项）"><textarea rows="2" placeholder="8080:80" value={form.ports} onChange={set('ports')} /></Field><Field label="镜像（可选）"><input value={form.image} onChange={set('image')} /></Field></details>
  </>;
}

export function DesignEditor({ candidate, selectedId, prepare, disabled }) {
  const [action, setAction] = useState('edit');
  const nodes = candidate.target.nodes;
  return <section className="st-editor"><div className="st-section-heading"><h3>图上调整</h3><span>生成变更后先预览</span></div>
    <div className="st-action-tabs">{[['edit', '修改节点'], ['add', '新增模块 / 服务'], ['relation', '调整关系'], ['split', '拆分模块'], ['merge', '合并节点'], ['state', '迁移状态'], ['remove', '移除节点']].map(([id, label]) => <button type="button" key={id} aria-pressed={action === id} onClick={() => setAction(id)}>{label}</button>)}</div>
    <fieldset disabled={disabled} className="st-form-fieldset"><EditorForm key={`${action}:${candidate.id}:${candidate.version}:${selectedId}`} action={action} nodes={nodes} edges={candidate.target.edges} selectedId={selectedId} prepare={prepare} /></fieldset>
  </section>;
}

function EditorForm({ action, nodes, edges, selectedId, prepare }) {
  const [id, setId] = useState(nodes.some((node) => node.id === selectedId) ? selectedId : '');
  const selected = nodes.find((node) => node.id === id);
  const [otherId, setOtherId] = useState(action === 'edit' ? selected?.parentId || '' : '');
  const [form, setForm] = useState(fields(action === 'edit' ? selected : null));
  const [kind, setKind] = useState('module');
  const [relationKind, setRelationKind] = useState('depends-on');
  const [communication, setCommunication] = useState('unspecified');
  const [channel, setChannel] = useState('');
  const [edgeId, setEdgeId] = useState('');
  const [removeChildren, setRemoveChildren] = useState(false);
  const [error, setError] = useState('');
  const other = nodes.find((node) => node.id === otherId);
  const selectNode = (value) => { setId(value); if (action === 'edit') { const node = nodes.find((item) => item.id === value); setForm(fields(node)); setOtherId(node?.parentId || ''); } };
  const submit = (event) => {
    event.preventDefault(); setError('');
    try {
      let operations = [];
      if (action === 'add') {
        operations = [{ type: 'add-node', node: { id: makeId(kind), kind, label: form.label.trim(), description: form.description, parentId: id || null, attributes: attributes(form, kind === 'service') } }];
      } else if (action === 'edit') {
        if (!selected) throw new Error('请选择要修改的节点。');
        operations = [{ type: 'update-node', id, changes: { label: form.label.trim(), description: form.description, parentId: otherId || null, attributes: attributes(form, selected.kind === 'service') } }];
      } else if (action === 'relation') {
        if (edgeId) operations = [{ type: 'update-edge', id: edgeId, changes: { label: form.label, kind: relationKind, attributes: { communication, channel } } }];
        else {
          if (!selected || !other) throw new Error('请选择关系的起点和终点。');
          operations = [{ type: 'add-edge', edge: { id: makeId('edge'), from: id, to: otherId, kind: relationKind, label: form.label || relationKind, attributes: { communication, channel } } }];
        }
      } else if (action === 'split') {
        if (!selected) throw new Error('请选择要拆分的原节点。');
        const moved = lines(form.responsibilities);
        if (!moved.length) throw new Error('请填写拆分出的职责。');
        const existing = selected.attributes?.responsibilities || [];
        if (moved.some((value) => !existing.includes(value))) throw new Error('待拆分职责必须已在原节点中明确记录，请先修改原节点职责。');
        const newId = makeId('service');
        operations = [
          { type: 'update-node', id, changes: { attributes: { responsibilities: existing.filter((value) => !moved.includes(value)) } } },
          { type: 'add-node', node: { id: newId, kind: 'service', label: form.label, description: form.description, attributes: attributes(form, true) } },
          { type: 'add-edge', edge: { id: makeId('edge'), from: id, to: newId, kind: communication === 'async' ? 'publishes' : 'depends-on', label: channel || '拆分后的协作关系', attributes: { communication, channel } } },
        ];
      } else if (action === 'merge') {
        if (!selected || !other || id === otherId) throw new Error('请选择两个不同的节点。');
        let ancestor = other; const visited = new Set();
        while (ancestor?.parentId && !visited.has(ancestor.id)) { visited.add(ancestor.id); if (ancestor.parentId === id) throw new Error('不能把父节点合并到其子孙节点，请先调整包含关系。'); ancestor = nodes.find((item) => item.id === ancestor.parentId); }
        const related = edges.filter((edge) => edge.from === id || edge.to === id);
        operations = [
          ...related.map((edge) => ({ type: 'remove-edge', id: edge.id })),
          ...nodes.filter((node) => node.parentId === id).map((node) => ({ type: 'update-node', id: node.id, changes: { parentId: otherId } })),
          { type: 'update-node', id: otherId, changes: { attributes: { responsibilities: unique([...(other.attributes?.responsibilities || []), ...(selected.attributes?.responsibilities || [])]), stateOwnership: unique([...(other.attributes?.stateOwnership || []), ...(selected.attributes?.stateOwnership || [])]), interfaces: [...(other.attributes?.interfaces || []), ...(selected.attributes?.interfaces || [])] } } },
          { type: 'remove-node', id },
          ...related.filter((edge) => (edge.from === id ? otherId : edge.from) !== (edge.to === id ? otherId : edge.to)).map((edge) => ({ type: 'add-edge', edge: { id: makeId('edge'), from: edge.from === id ? otherId : edge.from, to: edge.to === id ? otherId : edge.to, kind: edge.kind, label: edge.label, attributes: { communication: edge.attributes?.communication || 'unspecified', ...(edge.attributes?.channel ? { channel: edge.attributes.channel } : {}) } } })),
        ];
      } else if (action === 'state') {
        if (!selected || !other || id === otherId) throw new Error('请选择两个不同的节点。');
        const moved = lines(form.state); const existing = selected.attributes?.stateOwnership || [];
        if (!moved.length || moved.some((value) => !existing.includes(value))) throw new Error('状态项必须已在原节点中记录，才能明确迁移。');
        operations = [{ type: 'update-node', id, changes: { attributes: { stateOwnership: existing.filter((value) => !moved.includes(value)) } } }, { type: 'update-node', id: otherId, changes: { attributes: { stateOwnership: unique([...(other.attributes?.stateOwnership || []), ...moved]) } } }];
      } else if (action === 'remove') {
        if (!selected) throw new Error('请选择节点。');
        const ids = new Set([id]);
        if (removeChildren) { let count; do { count = ids.size; for (const node of nodes) if (ids.has(node.parentId)) ids.add(node.id); } while (count !== ids.size); }
        else if (nodes.some((node) => node.parentId === id)) throw new Error('该节点有子节点。请明确选择连同子节点移除，或先调整包含关系。');
        operations = [...edges.filter((edge) => ids.has(edge.from) || ids.has(edge.to)).map((edge) => ({ type: 'remove-edge', id: edge.id })), ...[...ids].reverse().map((nodeId) => ({ type: 'remove-node', id: nodeId }))];
      }
      prepare(operations, `手动设计：${action}`);
    } catch (failure) { setError(failure.message); }
  };
  return <form className="st-form" onSubmit={submit}>
    {action === 'add' ? <Field label="新增类型"><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="module">模块</option><option value="service">服务</option><option value="resource">资源</option></select></Field> : null}
    <NodeSelect label={action === 'add' ? '所属分组（可选）' : action === 'merge' ? '合并后移除的节点' : action === 'state' ? '原状态所有者' : action === 'relation' ? '关系起点' : '要调整的节点'} value={id} onChange={selectNode} nodes={nodes} empty={action === 'add' ? '项目根层级' : '请选择节点'} />
    {action === 'edit' ? <NodeSelect label="调整所属分组" value={otherId} onChange={setOtherId} nodes={nodes.filter((node) => node.id !== id)} empty="项目根层级" /> : null}
    {['merge', 'state', 'relation'].includes(action) ? <NodeSelect label={action === 'merge' ? '保留并接收职责的节点' : action === 'state' ? '新状态所有者' : '关系终点'} value={otherId} onChange={setOtherId} nodes={nodes.filter((node) => node.id !== id)} /> : null}
    {['add', 'edit', 'split'].includes(action) ? <NodeFields form={form} setForm={setForm} deployment={kind === 'service' || action === 'split'} /> : null}
    {action === 'split' ? <p className="st-note">原节点已记录的职责：{selected?.attributes?.responsibilities?.join('、') || '尚未记录，请先修改节点职责'}。新服务会接收表单中的职责，原节点会移除这些职责。</p> : null}
    {action === 'state' ? <Field label="迁移的状态项（每行一项）" hint={`原节点状态：${selected?.attributes?.stateOwnership?.join('、') || '尚未记录'}`}><textarea rows="3" value={form.state} onChange={(event) => setForm((current) => ({ ...current, state: event.target.value }))} /></Field> : null}
    {action === 'relation' ? <><Field label="修改已有关系（可选）"><select value={edgeId} onChange={(event) => { const value = edges.find((edge) => edge.id === event.target.value); setEdgeId(event.target.value); if (value) { setRelationKind(value.kind); setCommunication(value.attributes?.communication || 'unspecified'); setChannel(value.attributes?.channel || ''); setForm((current) => ({ ...current, label: value.label || '' })); } }}><option value="">新增关系</option>{edges.map((edge) => <option key={edge.id} value={edge.id}>{nodes.find((node) => node.id === edge.from)?.label} → {nodes.find((node) => node.id === edge.to)?.label} · {edge.label}</option>)}</select></Field><Field label="关系名称"><input value={form.label} onChange={(event) => setForm((current) => ({ ...current, label: event.target.value }))} /></Field><Field label="关系类型"><select value={relationKind} onChange={(event) => setRelationKind(event.target.value)}>{['contains', 'imports', 'depends-on', 'serves', 'deploys', 'uses', 'calls', 'publishes', 'subscribes'].map((value) => <option key={value}>{value}</option>)}</select></Field></> : null}
    {['relation', 'split'].includes(action) ? <div className="st-form-grid"><Field label="通信方式"><select value={communication} onChange={(event) => setCommunication(event.target.value)}><option value="unspecified">待确定</option><option value="sync">同步</option><option value="async">异步</option></select></Field><Field label="通道或主题"><input value={channel} onChange={(event) => setChannel(event.target.value)} /></Field></div> : null}
    {action === 'remove' ? <label className="st-checkbox"><input type="checkbox" checked={removeChildren} onChange={(event) => setRemoveChildren(event.target.checked)} />连同子节点和全部关联关系移除（仅目标设计）</label> : null}
    {action === 'merge' ? <p className="st-note">预览会列出职责和状态合并、子节点归属变化、关系重定向及旧节点移除。重复接口或职责冲突请继续评审。</p> : null}
    {error ? <p role="alert" className="st-inline-error">{error}</p> : null}
    <div className="st-form-actions"><button type="submit" className="primary-button">预览目标变化</button>{action === 'relation' && edgeId ? <button type="button" className="secondary-button" onClick={() => prepare([{ type: 'remove-edge', id: edgeId }], '移除目标关系')}>预览移除关系</button> : null}</div>
  </form>;
}
