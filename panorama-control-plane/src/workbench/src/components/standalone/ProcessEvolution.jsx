import { useState } from 'react';
import { CopyButton, Tag, downloadText } from './Shared.jsx';

const names = { begin: '开始', scan: '观察', receipt: '检查 / 评审', assessment: '历史评估', completion: '完成记录',
  added: '新增', removed: '移除', modified: '修改', unknown: '未知', passed: '通过', failed: '失败', errored: '执行有误',
  satisfied: '满足', unsatisfied: '未满足', insufficient: '证据不足', conflict: '冲突', current: '当前', stale: '过期',
  local_scan: '本地观察', local_execution: '本地执行', local_agent_review: 'Coding Agent 评审', external_import: '外部声明' };
const label = value => names[value] ?? value;
export function EvolutionReference({ value }) {
  if (!value) return null;
  return <details className="st-evolution-ref"><summary>原始引用</summary><p className="st-path">{value.kind} · {value.id}<br />{value.hash}<br />{value.pointer || '/'}</p><CopyButton text={JSON.stringify(value, null, 2)} label="复制引用" /></details>;
}

function Changes({ value, title = '文件变化' }) {
  const [limit, setLimit] = useState(20);
  if (!value) return null;
  return <details className="st-evolution-changes"><summary>{title}（{value.files.length}）</summary>
    {value.unknownInputSetIds?.length ? <p className="st-warning">无法完整比较：{value.unknownInputSetIds.join('、')}。不据此推断删除。</p> : null}
    <ul>{value.files.slice(0, limit).map(file => <li key={`${file.rootId}/${file.path}`}><Tag>{label(file.kind)}</Tag><span className="st-path">{file.rootId ? file.rootId + '/' : ''}{file.path}</span><details><summary>前后身份</summary><p className="st-path">前：{file.beforeDigest ?? '未记录存在的身份'}<br />后：{file.afterDigest ?? '未记录存在的身份'}</p>{file.inputSetIds ? <p className="st-path">输入集合：{file.inputSetIds.join('、')}</p> : null}</details></li>)}</ul>
    {value.files.length > limit ? <button type="button" className="st-text-button" onClick={() => setLimit(n => n + 20)}>再显示 20 个文件（剩余 {value.files.length - limit}）</button> : null}
    {value.limitation ? <p className="st-note">{value.limitation}</p> : null}
  </details>;
}

function Observation({ check }) {
  if (!check) return <p className="st-note">当前有效性未复查。</p>;
  return <div className="st-evolution-observation">
    {check.criteria.map(row => <p key={row.criterionId}><strong>{row.criterionId}</strong> · {label(row.satisfaction)} · {label(row.freshness.status)}</p>)}
    {check.explanations.map(item => <div key={item.id}><p className="st-warning">{item.reason}</p><p className="st-path">{item.id}{item.locator ? ` · ${item.locator.rootId}/${item.locator.path}` : ''}</p>
      <Changes value={item.sinceCheck} title="检查后发生的文件变化" /><Changes value={item.duringCheck} title="执行期间发生的文件变化" />
      {item.kind === 'subject' ? <details><summary>对象前后身份</summary><p className="st-path">{item.beforeDigest ?? '未知'} → {item.afterDigest ?? '未知'}</p></details> : null}<EvolutionReference value={item.ref} />
    </div>)}
    {check.unknowns.length ? <p className="st-warning">尚未确认：{check.unknowns.join('；')}</p> : null}
  </div>;
}

export function ProcessEvolution({ value, current, receipts, onAssessment, busy }) {
  const [filter, setFilter] = useState('all'), [limit, setLimit] = useState(12);
  if (!value) return null;
  const events = [...value.events].reverse().filter(event => filter === 'all' || (filter === 'checks' ? event.kind === 'receipt' : event.kind === filter));
  const design = value.design;
  return <section className="st-evolution" aria-label="工作演进记录">
    <h3>工作演进记录</h3><p>{value.why.goal}</p><p className="st-note">工作目标为开发声明，验收项见下方逐项要求。</p>
    <details><summary>目标设计与源码观察</summary><p className="st-path">源码快照：{design.source.snapshot.id} · {design.source.snapshot.generatedAt}</p><p>已解析 {design.source.files} 个文件、{design.source.nodes} 个节点。{design.source.limitation}</p>
      {design.target ? <><p><Tag tone="target">声明的目标设计</Tag> {design.target.title} · v{design.target.version}</p><p className="st-path">目标原基线：{design.target.baselineSnapshotId}</p><p>{design.target.sourceChanged ? '源码快照已变化，保留历史目标绑定。' : '目标基线与已解析快照一致。'}身份对应不证明业务职责实现。</p>
        {design.target.bindings.map(binding => <p key={binding.targetNodeId} className="st-path">{binding.targetNodeId} → {binding.sourceNodeId} · {binding.status === 'explicit_current_identity' ? '本次快照的显式身份对应' : '身份对应已过期'}</p>)}<EvolutionReference value={design.target.ref} /><Changes value={design.sourceChanges} title="设计基线至源码快照的文件清单变化" /></> : <p className="st-note">{design.binding ? '原目标绑定存在，但对应版本内容不可用。' : '此工作没有设计候选绑定。'}</p>}
      {design.references.filter(item => item.text).map(item => <div key={item.kind}><strong>{item.kind === 'design_question' ? '设计问题' : '当前记录的方案选择理由'}</strong><p>{item.text}</p><EvolutionReference value={item.ref} /></div>)}{design.gaps.map(gap => <p className="st-note" key={gap}>{gap}</p>)}
    </details>
    {current ? <div className="st-evolution-current"><strong>本次观察 · {current.observedAt}</strong><p className="st-note">{current.note}</p><Changes value={current.baselineChanges} title="原始工作基线至本次观察" /></div> : <p className="st-note">下方是历史记录。点击“复查当前证据”后，每次检查将显示本次有效性与失效原因。</p>}
    <div className="st-form-actions"><label>记录类型 <select aria-label="演进记录类型" value={filter} onChange={event => { setFilter(event.target.value); setLimit(12); }}><option value="all">全部记录</option><option value="checks">检查与评审</option><option value="scan">输入观察</option><option value="assessment">历史评估</option><option value="completion">完成记录</option></select></label><span className="st-note">{events.length} 条 · 最近记录在前；未记时间的完成操作单列</span></div>
    <ol className="st-evolution-events">{events.slice(0, limit).map(event => <li key={event.id}>
      <details><summary><Tag>{label(event.kind)}</Tag> {event.at ? new Date(event.at).toLocaleString() : '未记录独立时间'} · {event.title}{event.checks?.map(check => ` · ${label(check.execution.result)}`)}</summary>
        <p className="st-path">{event.id}</p>{event.origin ? <p>{label(event.origin)} · {event.origin === 'external_import' ? '外部声明不获得本地采集凭据。' : '本地登记来源；当前有效性仍需复查。'}</p> : null}
        <Changes value={event.changes} title="原始工作基线至该次记录" />
        {event.checks?.map(check => <article key={check.id} className="st-process-row"><strong>{check.kind === 'review' ? '评审声明' : '检查执行'}：{label(check.execution.result)} · 退出码 {check.execution.exitCode ?? '不适用'}</strong>
          <p>关联验收项：{check.criterionIds.join('、') || '无'} · 对象：{check.subjectIds.join('、')}</p><p>检查器：{check.checker.id} · 版本 {check.checker.version ?? '未登记 / 不适用'}</p>
          {check.checker.contract ? <details><summary>检查契约 {check.checker.contract.id} @ {check.checker.contract.version}</summary><ul>{check.checker.contract.claims.map(claim => <li key={claim.id}>{claim.id}：{claim.property}</li>)}</ul><p>未检查：{check.checker.contract.notChecked.join('；')}</p><p className="st-path">{check.checker.contract.digest}</p></details> : null}
          {check.checker.binding ? <details><summary>输入范围与报告绑定</summary><pre>{JSON.stringify(check.checker.binding, null, 2)}</pre></details> : null}
          {check.evidence.map(evidence => <div key={evidence.id}><p>{evidence.kind} · {evidence.summary}</p><p className="st-note">作者：{evidence.author.kind} · {evidence.author.authority}</p><EvolutionReference value={evidence.ref} /></div>)}
          <Observation check={current?.checks.find(item => item.receiptId === event.id && item.checkId === check.id)} /><EvolutionReference value={check.ref} />
        </article>)}
        {event.logReferences?.length ? <details><summary>检查日志引用（{event.logReferences.length}）</summary><p className="st-note">日志正文保存在本地采集目录。页面只提供原 Receipt 记录的引用，未重新校验日志文件。</p><p className="st-path">执行：{event.executionId}</p>{event.logReferences.map((log, i) => <p className="st-path" key={i}>{log.stream} · 保留 {log.retainedBytes} / {log.totalBytes} 字节{log.truncated ? ' · 已截断' : ''}<br />SHA-256：{log.digest}</p>)}</details> : null}
        {event.configurationDigest ? <p className="st-path">原配置摘要：{event.configurationDigest}</p> : null}
        {event.kind === 'assessment' ? <><p>历史结论：{label(event.overall.status)}。当前有效性需复查。</p><p>{event.selectionReason || '未记录选择理由。'}</p><p>当次证据：{event.receiptRefs.length} 份</p><button type="button" className="st-text-button" disabled={busy} onClick={() => onAssessment(event.id)}>查看历史评估原文</button></> : null}
        {event.kind === 'completion' ? <><p>{event.phase} · {event.status}</p><p>{event.selection?.reason || '未指定证据子集。'}</p><p className="st-path">关联评估：{event.assessmentId || '无'}</p><p className="st-note">{event.limitation}</p></> : null}
        <EvolutionReference value={event.ref} />{['receipt', 'scan'].includes(event.kind) ? <button type="button" className="st-text-button" onClick={() => downloadText(JSON.stringify(receipts.find(item => item.receipt.receiptId === event.id)?.receipt, null, 2), 'process-receipt.json', 'application/json')}>下载原始 Receipt</button> : null}
      </details>
    </li>)}</ol>{events.length > limit ? <button type="button" className="secondary-button" onClick={() => setLimit(n => n + 12)}>再显示 12 条（剩余 {events.length - limit}）</button> : null}
    <details><summary>追踪范围与限制</summary>{value.limitations.map(item => <p key={item} className="st-note">{item}</p>)}<EvolutionReference value={value.baseline.ref} /></details>
  </section>;
}
