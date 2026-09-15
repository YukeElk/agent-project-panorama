import { useState } from 'react';
import { diffModels } from '../../../../standalone/design-model.mjs';
import { CopyButton, Empty, Field, STATUS_LABELS, Tag, downloadText, lines, shortId } from './Shared.jsx';
import { ProcessEvidencePanel } from './ProcessEvidencePanel.jsx';

export function HandoffWorkspace({ workspace, candidate, command, busy }) {
  const [exported, setExported] = useState(null); const [comparison, setComparison] = useState(null);
  const [source, setSource] = useState(''); const [summary, setSummary] = useState(''); const [references, setReferences] = useState('');
  const [targetNodeId, setTargetNodeId] = useState(''); const [sourceNodeId, setSourceNodeId] = useState('');
  if (!candidate) return <main className="st-document-workspace"><div className="st-workspace-title"><div><p className="st-eyebrow">交接与核对</p><h1>交接与回读</h1></div></div><ProcessEvidencePanel workspace={workspace} /><Empty title="设计交接可选">有设计候选时，这里也可以生成目标方案说明并核对源码结构。</Empty></main>;
  const session = workspace.sessions.find((item) => item.id === candidate.sessionId);
  const selected = session?.selectedCandidateId === candidate.id;
  const stale = candidate.baseSnapshotId !== workspace.current.snapshot.id;
  const additions = diffModels(candidate.baseModel, candidate.target).nodes.added;
  const chosenTarget = additions.find((item) => item.id === targetNodeId);
  const candidates = workspace.current.nodes.filter((node) => node.kind === chosenTarget?.kind);
  const results = workspace.results.filter((item) => item.candidateId === candidate.id);
  const exportCurrent = exported?.candidateId === candidate.id;
  const exportStale = exportCurrent && (exported.version !== candidate.version || exported.snapshotId !== workspace.current.snapshot.id);
  const comparisonCurrent = comparison?.candidateId === candidate.id;
  const comparisonStale = comparisonCurrent && (comparison.candidateVersion !== candidate.version || comparison.sourceSnapshotId !== workspace.current.snapshot.id);
  const exportPrompt = async (purpose) => {
    const result = await command('export-handoff', { candidateId: candidate.id, purpose });
    if (result?.output) setExported({ ...result.output, candidateId: candidate.id, version: candidate.version, snapshotId: workspace.current.snapshot.id, purpose });
  };
  return <main className="st-document-workspace"><div className="st-workspace-title"><div><p className="st-eyebrow">交接与核对</p><h1>交接与回读</h1></div><Tag tone="target">{candidate.title} · v{candidate.version}</Tag></div>
    <p className="st-lead">把设计交给外部工具实现，再读取源码核对结构。全景保存交接内容和结果引用，帮助你区分设计、外部结论与源码事实。</p>
    <ProcessEvidencePanel workspace={workspace} />
    {stale ? <p className="st-warning">该候选基于较早源码。导出将保留历史基线；回读使用当前源码快照。</p> : null}
    <section className="st-handoff-section"><h2>1. 生成交接说明</h2><div className="st-export-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => exportPrompt('design')}>导出设计分析说明</button><button type="button" className="secondary-button" disabled={busy} onClick={() => exportPrompt('review')}>导出评审说明</button><button type="button" className="primary-button" disabled={busy || !selected} onClick={() => exportPrompt('implementation')}>导出实施说明</button></div>{!selected ? <p className="st-note">实施说明需要先在设计工作区选定候选并记录取舍理由。</p> : null}
      {exportCurrent ? <div className="st-exported"><div className="st-section-heading"><h3>交接内容预览</h3><Tag tone={exportStale ? 'attention' : ''}>{exportStale ? '内容已过期，请重新导出' : '已生成，等待外部交接'}</Tag></div><div className="st-form-actions"><CopyButton text={exported.markdown} label="复制 Prompt" /><button type="button" className="secondary-button" onClick={() => downloadText(exported.markdown, `panorama-${exported.purpose}.md`, 'text/markdown;charset=utf-8')}>下载 Markdown</button><button type="button" className="secondary-button" onClick={() => downloadText(JSON.stringify(exported.manifest, null, 2), `panorama-${exported.purpose}-manifest.json`, 'application/json')}>下载变更清单</button></div><pre className="st-markdown">{exported.markdown}</pre><p className="st-note">复制与下载只表示交接准备，未启动外部执行，也不表示代码完成。</p></div> : null}
    </section>
    <section className="st-handoff-section"><h2>2. 保存外部结果引用</h2><form className="st-form" onSubmit={async (event) => { event.preventDefault(); const result = await command('import-result', { candidateId: candidate.id, source, summary, references: lines(references) }); if (result) { setSummary(''); setReferences(''); } }}><Field label="外部结果来源"><input required placeholder="例如外部开发会话 / 人工核查" value={source} onChange={(event) => setSource(event.target.value)} /></Field><Field label="外部结果摘要"><textarea required rows="3" value={summary} onChange={(event) => setSummary(event.target.value)} /></Field><Field label="结果引用（每行一项）"><textarea rows="2" placeholder="提交号、报告路径或评审链接" value={references} onChange={(event) => setReferences(event.target.value)} /></Field><button type="submit" className="secondary-button" disabled={busy}>保存结果引用</button></form>
      {results.map((item) => <article className="st-result" key={item.id}><strong>{item.source}</strong><Tag>外部声明，未由全景验证</Tag><p>{item.summary}</p>{item.references.map((reference, index) => <p className="st-path" key={index}>{typeof reference === 'string' ? reference : JSON.stringify(reference)}</p>)}</article>)}
    </section>
    <section className="st-handoff-section"><h2>3. 刷新源码并核对</h2><p className="st-note">当前快照 {shortId(workspace.current.snapshot.id)}。结构核对不会运行项目测试；职责、接口实现与运行性能可能仍然证据不足。</p><div className="st-form-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => command('refresh', {})}>重新读取源码</button><button type="button" className="primary-button" disabled={busy} onClick={async () => { const result = await command('reconcile', { candidateId: candidate.id }); if (result?.output) setComparison({ ...result.output, candidateId: candidate.id }); }}>核对目标与当前源码</button></div>
      {additions.length ? <details className="st-bindings"><summary>为新增目标实体确认源码对应关系（{additions.length} 项）</summary><p className="st-note">新增实体不会按名称自动匹配。请确认同类型实体的对应关系；映射只确认身份，无法证明职责已实现。</p><div className="st-form-grid"><Field label="新增目标实体"><select value={targetNodeId} onChange={(event) => { setTargetNodeId(event.target.value); setSourceNodeId(''); }}><option value="">请选择目标</option>{additions.map((node) => <option key={node.id} value={node.id}>{node.label} · {node.kind}</option>)}</select></Field><Field label="当前源码对应实体"><select value={sourceNodeId} disabled={!chosenTarget} onChange={(event) => setSourceNodeId(event.target.value)}><option value="">请选择已观察实体</option>{candidates.map((node) => <option key={node.id} value={node.id}>{node.label} · {node.sourcePath || node.kind}</option>)}</select></Field></div><button type="button" className="secondary-button" disabled={busy || !targetNodeId || !sourceNodeId} onClick={() => command('bind-target', { candidateId: candidate.id, targetNodeId, sourceNodeId, sourceSnapshotId: workspace.current.snapshot.id })}>确认对应关系</button>{(candidate.bindings || []).map((binding) => <p className="st-note" key={binding.targetNodeId}>{candidate.target.nodes.find((node) => node.id === binding.targetNodeId)?.label || binding.targetNodeId} → {workspace.current.nodes.find((node) => node.id === binding.sourceNodeId)?.label || binding.sourceNodeId} · {binding.sourceSnapshotId === workspace.current.snapshot.id ? '当前快照已绑定' : '源码已刷新，需重新确认'}</p>)}</details> : null}
      {comparisonCurrent ? <div className="st-reconciliation"><div className="st-section-heading"><h3>结构核对结果</h3><Tag tone={comparisonStale ? 'attention' : ''}>{comparisonStale ? '结果已过期，请重新核对' : STATUS_LABELS[comparison.status] || comparison.status}</Tag></div><ul>{comparison.items.map((item, index) => <li key={`${item.id}-${index}`}><Tag>{STATUS_LABELS[item.status] || item.status}</Tag><div><strong>{candidate.target.nodes.find((node) => node.id === item.id)?.label || candidate.baseModel.nodes.find((node) => node.id === item.id)?.label || item.id}</strong><p>{item.detail}</p></div></li>)}</ul></div> : null}
    </section>
  </main>;
}
