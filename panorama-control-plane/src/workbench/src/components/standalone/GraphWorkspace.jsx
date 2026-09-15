import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, Maximize2, Minus, Plus, Search } from 'lucide-react';
import { compileView } from '../../../../standalone/view-compiler.mjs';
import { diffModels } from '../../../../standalone/design-model.mjs';
import { standaloneRequest } from '../../standalone-api.js';
import { DiffSummary, Empty, KIND_LABELS, Tag, VIEW_LABELS } from './Shared.jsx';
import { ProcessEvidencePanel } from './ProcessEvidencePanel.jsx';

function diffProjection(base, target) {
  if (!base) return { model: target, changes: new Map() };
  const changes = new Map();
  const diff = diffModels(base, target);
  for (const group of [diff.nodes, diff.edges]) for (const [kind, items] of Object.entries(group)) for (const item of items) changes.set(item.id, kind);
  const merge = (before, after) => {
    const next = new Map(after.map((item) => [item.id, item]));
    const removed = before.filter((item) => !next.has(item.id));
    return [...after, ...removed];
  };
  return { model: { ...target, nodes: merge(base.nodes, target.nodes), edges: merge(base.edges, target.edges) }, changes };
}

export function GraphWorkspace({ workspace, candidate, selectedId, onSelect, command, busy, mode: outerMode, onMode }) {
  const [kind, setKind] = useState('modules');
  const [search, setSearch] = useState('');
  const query = useDeferredValue(search);
  const [scopeId, setScopeId] = useState(null);
  const [collapsedIds, setCollapsedIds] = useState([]);
  const [neighbors, setNeighbors] = useState(false);
  const [zoom, setZoom] = useState(100);
  const [viewTitle, setViewTitle] = useState('');
  const [limit, setLimit] = useState(120);
  const canvasRef = useRef(null);
  const [canvasSize, setCanvasSize] = useState({ width: 800, height: 500 });
  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0 && entry.contentRect.height > 0) setCanvasSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const mode = candidate ? outerMode : 'current';
  const model = mode === 'current' ? workspace.current : candidate.target;
  const comparison = useMemo(() => mode === 'diff' ? diffProjection(candidate.baseModel, candidate.target) : { model, changes: new Map() }, [mode, candidate, model]);
  const view = useMemo(() => compileView(comparison.model, { kind, search: query, scopeId, collapsedIds, limit }), [comparison.model, kind, query, scopeId, collapsedIds, limit]);
  const adjacency = useMemo(() => {
    const ids = new Set([selectedId]);
    for (const edge of view.edges) if (edge.from === selectedId || edge.to === selectedId) { ids.add(edge.from); ids.add(edge.to); }
    return ids;
  }, [view.edges, selectedId]);
  const nodes = neighbors && selectedId ? view.nodes.filter((node) => adjacency.has(node.id)) : view.nodes;
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const selectedNode = comparison.model.nodes.find((node) => node.id === selectedId);
  const scopeNode = comparison.model.nodes.find((node) => node.id === scopeId);
  const selectedHasChildren = selectedNode && comparison.model.nodes.some((node) => node.parentId === selectedNode.id);
  const stale = candidate && candidate.baseSnapshotId !== workspace.current.snapshot.id;
  const fit = Math.min(canvasSize.width / (view.width || 1000), canvasSize.height / (view.height || 600));
  return <div className="st-graph-layout">
    <section className="st-graph-main" aria-label="项目图谱">
      <div className="st-workspace-title"><div><p className="st-eyebrow">理解项目</p><h1>项目全景</h1></div><div className="st-segment" aria-label="模型范围">
        {[['current', '当前源码'], ['target', '目标方案'], ['diff', '变化对比']].map(([value, label]) => <button type="button" key={value} aria-pressed={mode === value} disabled={!candidate && value !== 'current'} onClick={() => onMode(value)}>{label}</button>)}
      </div></div>
      {mode !== 'current' ? <div className="st-context-strip"><Tag tone="target">目标设计</Tag><span>{candidate.title} · v{candidate.version}</span>{stale ? <Tag tone="attention">源码已变化，候选基线过期</Tag> : null}<DiffSummary base={candidate.baseModel} target={candidate.target} /></div> : null}
      {mode === 'diff' ? <p className="st-note">对比候选基线与目标。绿色为新增，红色为移除，橙色为调整；当前源码刷新后的落实情况请在交接与回读查看。</p> : null}
      <div className="st-graph-toolbar"><div className="st-segment" aria-label="图种">{Object.entries(VIEW_LABELS).map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setScopeId(null); }}>{label}</button>)}</div>
        <label className="st-search"><Search size={16} /><input aria-label="搜索节点或源码路径" placeholder="搜索节点或源码路径" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <div className="st-zoom"><button type="button" aria-label="缩小图谱" onClick={() => setZoom((value) => Math.max(10, value - 20))}><Minus size={16} /></button><output>{zoom}%</output><button type="button" aria-label="放大图谱" onClick={() => setZoom((value) => Math.min(300, value + 20))}><Plus size={16} /></button><button type="button" aria-label="适合画布" onClick={() => setZoom(Math.max(10, Math.min(300, Math.floor(fit * 100))))}><Maximize2 size={16} /></button><button type="button" aria-label="恢复可读尺寸" onClick={() => setZoom(100)}>1:1</button></div>
      </div>
      <div className="st-scope-bar"><button type="button" onClick={() => { setScopeId(null); setNeighbors(false); }}>整个项目</button>{scopeId ? <><ChevronRight size={14} /><span>{scopeNode?.label || '范围已不存在'}</span></> : null}
        <label><input type="checkbox" checked={neighbors} disabled={!selectedNode} onChange={(event) => setNeighbors(event.target.checked)} />仅看选中节点及相邻关系</label>
        {selectedHasChildren ? <><button type="button" onClick={() => { setScopeId(selectedId); setSearch(''); }}>下钻选中分组</button><button type="button" onClick={() => setCollapsedIds((ids) => ids.includes(selectedId) ? ids.filter((id) => id !== selectedId) : [...ids, selectedId])}>{collapsedIds.includes(selectedId) ? '展开分组' : '折叠分组'}</button></> : null}
        {collapsedIds.length ? <button type="button" onClick={() => setCollapsedIds([])}>展开全部</button> : null}
      </div>
      <div className="st-graph-canvas" ref={canvasRef}>
        {nodes.length ? <svg viewBox={`0 0 ${view.width || 1000} ${view.height || 600}`} style={{ width: `${(view.width || 1000) * zoom / 100}px`, minWidth: 0 }} aria-label={`${VIEW_LABELS[kind]}图，${nodes.length} 个节点`} role="group">
          <defs><marker id="st-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 z" fill="currentColor" /></marker></defs>
          {view.edges.map((edge) => {
            const from = nodeMap.get(edge.from); const to = nodeMap.get(edge.to);
            if (!from || !to) return null;
            const startX = from.x + from.width / 2; const startY = from.y + from.height;
            const endX = to.x + to.width / 2; const endY = to.y;
            const midY = (startY + endY) / 2;
            const related = edge.id === selectedId || edge.from === selectedId || edge.to === selectedId;
            return <g key={edge.id} className={`st-edge ${related ? 'related' : ''} ${comparison.changes.get(edge.id) || ''}`} role="button" tabIndex="0" aria-label={`关系：${from.label} → ${to.label}，${edge.label || edge.kind}`} onClick={() => onSelect(edge.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(edge.id); } }}>
              <path d={`M${startX},${startY} C${startX},${midY} ${endX},${midY} ${endX},${endY}`} markerEnd="url(#st-arrow)" /><title>{edge.label || edge.kind}</title>{related ? <text x={(startX + endX) / 2} y={midY - 4}>{(edge.label || edge.kind).slice(0, 30)}</text> : null}
            </g>;
          })}
          {nodes.map((node) => <g key={node.id} role="button" tabIndex="0" aria-label={`${KIND_LABELS[node.kind] || node.kind}：${node.label}`} className={`st-node ${selectedId === node.id ? 'selected' : ''} ${comparison.changes.get(node.id) || ''}`} onClick={() => onSelect(node.id)} onDoubleClick={() => { if (comparison.model.nodes.some((item) => item.parentId === node.id)) setScopeId(node.id); }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.id); } }}>
            <rect x={node.x} y={node.y} width={node.width} height={node.height} rx="7" /><text className="st-node-kind" x={node.x + 12} y={node.y + 19}>{KIND_LABELS[node.kind] || node.kind}{collapsedIds.includes(node.id) ? ' · 已折叠' : ''}</text><text className="st-node-title" x={node.x + 12} y={node.y + 40}>{node.label.length > 24 ? `${node.label.slice(0, 23)}…` : node.label}</text><title>{node.label}{node.sourcePath ? `\n${node.sourcePath}` : ''}</title>
          </g>)}
        </svg> : <Empty title={kind === 'deployment' && !search && !scopeId ? '没有可展示的部署声明' : '当前范围没有节点'}>{kind === 'deployment' ? '支持的部署配置解析后显示在这里。也可以在目标方案中新增服务，先完成部署设计。' : '尝试清除搜索、返回整个项目，或重新解析源码。'}</Empty>}
      </div>
      <div className="st-graph-footer"><span>显示 {nodes.length} / {view.totalNodes ?? comparison.model.nodes.length} 个节点{view.truncated ? ' · 范围已截取' : ''}</span>{view.truncated ? <button type="button" disabled={limit >= 1000} onClick={() => setLimit((value) => Math.min(1000, value + 120))}>{limit >= 1000 ? '已达 1000 节点，请缩小范围' : '再显示 120 个'}</button> : null}<span>单击查看依据 · 双击分组下钻</span></div>
      <div className="st-save-view"><input aria-label="视角名称" placeholder="保存当前筛选与折叠方式" value={viewTitle} onChange={(event) => setViewTitle(event.target.value)} /><button type="button" className="secondary-button" disabled={busy || !viewTitle.trim()} onClick={async () => { const result = await command('save-view', { title: viewTitle, kind, scopeId, search, collapsedIds }); if (result) setViewTitle(''); }}>保存视角</button>
        <select aria-label="打开已保存视角" value="" onChange={(event) => { const saved = workspace.views.find((item) => item.id === event.target.value); if (saved) { setKind(saved.kind); setScopeId(saved.scopeId || null); setSearch(saved.search || ''); setCollapsedIds(saved.collapsedIds || []); setNeighbors(false); } }}><option value="">已保存视角（{workspace.views.length}）</option>{workspace.views.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select>
      </div>
    </section>
    <EvidencePanel model={comparison.model} current={workspace.current} workspace={workspace} candidateId={candidate?.id} selectedId={selectedId} mode={mode} onSelect={onSelect} />
  </div>;
}

function EvidencePanel({ model, current, workspace, candidateId, selectedId, mode, onSelect }) {
  const [source, setSource] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  useEffect(() => { requestId.current += 1; setSource(null); setError(''); setLoading(false); }, [selectedId, current.snapshot.id]);
  const node = model.nodes.find((item) => item.id === selectedId);
  const edge = model.edges.find((item) => item.id === selectedId);
  const entity = node || edge;
  const links = node ? model.edges.filter((item) => item.from === node.id || item.to === node.id) : [];
  const evidence = entity?.evidence || [];
  const names = new Map(model.nodes.map((item) => [item.id, item.label]));
  const read = async (item) => {
    const id = ++requestId.current; setLoading(true); setError('');
    try { const value = await standaloneRequest(`source?path=${encodeURIComponent(item.path)}&line=${item.line || 1}`); if (id === requestId.current) setSource(value); }
    catch (failure) { if (id === requestId.current) { setError(failure.message); setSource(null); } }
    finally { if (id === requestId.current) setLoading(false); }
  };
  return <aside className="st-inspector"><div className="st-inspector-heading"><p className="st-eyebrow">依据与范围</p><h2>证据检查器</h2></div>
    {!entity ? <Empty title="选择一个节点或关系">查看来源、语义信息和相邻关系。当前筛选外的选择不会被自动替换为别的实体。</Empty> : <>
      <section><Tag tone={mode === 'current' ? '' : 'target'}>{mode === 'current' ? '源码观察' : '目标 / 基线参考'}</Tag><h3>{entity.label || entity.kind}</h3><p className="st-note">{entity.description || (node ? KIND_LABELS[node.kind] : `${names.get(edge.from)} → ${names.get(edge.to)}`)}</p>{node?.sourcePath ? <p className="st-path">{node.sourcePath}{node.line ? `:${node.line}` : ''}</p> : null}
        <dl className="st-detail-list">{node?.attributes?.responsibilities?.length ? <div><dt>职责</dt><dd>{node.attributes.responsibilities.join('、')}</dd></div> : null}{node?.attributes?.stateOwnership?.length ? <div><dt>状态所有权</dt><dd>{node.attributes.stateOwnership.join('、')}</dd></div> : null}{node?.attributes?.interfaces?.length ? <div><dt>接口</dt><dd>{node.attributes.interfaces.map((item) => `${item.name} (${item.protocol})`).join('、')}</dd></div> : null}{node?.attributes?.deployment ? <div><dt>部署意图</dt><dd>{JSON.stringify(node.attributes.deployment)}</dd></div> : null}{edge?.attributes?.communication ? <div><dt>通信方式</dt><dd>{edge.attributes.communication === 'async' ? '异步' : edge.attributes.communication === 'sync' ? '同步' : '待确定'}</dd></div> : null}{edge?.resolution ? <div><dt>解析结果</dt><dd>{edge.resolution}</dd></div> : null}</dl>
      </section><section><h3>来源依据（{evidence.length}）</h3>{evidence.length ? evidence.map((item, index) => <div className="st-source-item" key={`${item.path}-${item.line}-${index}`}><span>{item.kind || 'source'}</span>{item.path ? <button type="button" onClick={() => read(item)}>{item.path}:{item.line || 1}</button> : <span>目标设计</span>}<small>{item.detail}</small></div>) : <p className="st-note">此项没有当前源码依据。目标设计需要外部实现与证据回读。</p>}
        {loading ? <p role="status">正在读取源码…</p> : null}{error ? <p className="st-inline-error" role="alert">{error}</p> : null}{source ? <div className="st-source-preview"><strong>{source.path}:{source.line}</strong>{source.stale ? <Tag tone="attention">源码已变化，请刷新</Tag> : null}<pre>{source.text}</pre></div> : null}
      </section>{links.length ? <section><h3>相关关系</h3>{links.map((item) => <button className="st-relation-link" type="button" key={item.id} onClick={() => onSelect(item.id)}>{names.get(item.from)} → {names.get(item.to)}<small>{item.label || item.kind}</small></button>)}</section> : null}
    </>}
    {node && mode === 'current' ? <ProcessEvidencePanel key={node.id} workspace={workspace} nodeId={node.id} compact /> : null}
    {node && mode !== 'current' && candidateId ? <ProcessEvidencePanel key={`${node.id}:${candidateId}`} workspace={workspace} candidateId={candidateId} compact /> : null}
    <details className="st-coverage"><summary>解析能力与缺口（{current.gaps.length}）</summary>{current.capabilities.map((item, index) => <div key={index}><strong>{item.language} · {item.status}</strong><p>{item.detail}</p></div>)}{current.gaps.slice(0, 50).map((item, index) => <p key={index}>{item.path ? `${item.path}：` : ''}{item.message}</p>)}{current.gaps.length > 50 ? <p>其余 {current.gaps.length - 50} 项可缩小范围后查看。</p> : null}</details>
  </aside>;
}
