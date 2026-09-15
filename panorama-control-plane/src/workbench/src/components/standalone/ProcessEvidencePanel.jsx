import { useEffect, useRef, useState } from 'react';
import { standaloneRequest } from '../../standalone-api.js';
import { CopyButton, Empty, Field, Tag, downloadText } from './Shared.jsx';
import { ProcessEvolution } from './ProcessEvolution.jsx';

const LABELS = { satisfied: '满足', unsatisfied: '未满足', insufficient: '证据不足', conflict: '冲突', current: '当前', stale: '过期', unknown: '未知', applicable: '适用', not_applicable: '不适用', required: '必备', conditional: '条件必备', recommended: '建议', optional: '可选', blocked: '有阻塞', local_scan: '本地观察', local_execution: '本地命令采集', local_agent_review: 'Coding Agent 评审', external_import: '外部声明', active: '开发中', finished: '已记录完成', finishing: '完成处理中', starting: '开始处理中', legacy_read_only: '旧工作项，只读', legacy_unknown: '旧格式，未知', external_context: '外部上下文', supported: '格式可读取' };
const label = value => LABELS[value] || value;
function Status({ value }) { return <Tag tone={['stale', 'unknown', 'conflict', 'insufficient', 'unsatisfied', 'blocked'].includes(value) ? 'attention' : ''}>{label(value)}</Tag>; }

function ConfigurationContext({ value, work = false }) {
  if (!value) return null;
  return <div className="st-process-context" aria-label={work ? '工作配置版本' : '活动配置版本'}>
    <p className="st-path">{work ? '工作绑定配置' : '当前活动配置'}：{value.revisionId}（修订 {value.sequence}）</p>
    {work && value.historical ? <p className="st-path">当前活动配置：{value.activeRevisionId}。此工作按原配置与规范解释，原始基线和历史证据保留。</p> : null}
    {!value.enabled ? <p className="st-warning">项目接入已停用，历史记录可继续查看。</p> : null}
    {value.withdrawnRoots.length ? <p className="st-warning">读取范围已调整或撤销：{value.withdrawnRoots.join('、')}。当前复查将这些范围记为未知。</p> : null}
    {value.withdrawnRunners.length ? <p className="st-warning">检查器已调整或撤销：{value.withdrawnRunners.join('、')}。历史检查保留，当前工具身份无法确认。</p> : null}
    {value.mappingGaps.length ? <p className="st-warning">输入位置不可读取：{value.mappingGaps.map(item => item.rootId).join('、')}。</p> : null}
  </div>;
}

function EvidenceRow({ row, title, receipts }) {
  return <article className="st-process-row">
    <div className="st-process-row-title"><strong>{title}</strong><Status value={row.satisfaction || row.applicability?.status} />{row.freshness ? <Status value={row.freshness.status} /> : null}</div>
    {row.strength ? <p>{label(row.strength)} · {label(row.applicability.status)}：{row.applicability.reason}</p> : null}
    <p>{row.reason}</p>
    {row.missing.length ? <ul className="st-process-gaps">{row.missing.map(item => <li key={item}>{item}</li>)}</ul> : null}
    {row.freshness?.changedIds.length ? <p className="st-warning">发生变化：{row.freshness.changedIds.join('、')}</p> : null}
    {row.freshness?.unknowns.length ? <p className="st-note">尚未确认：{row.freshness.unknowns.join('；')}</p> : null}
    {row.evidenceRefs.length ? <details><summary>证据来源（{row.evidenceRefs.length}）</summary>{row.evidenceRefs.map(ref => {
      const entry = receipts.find(item => item.receipt.receiptHash === ref.receiptHash);
      const evidence = entry?.receipt.evidence.find(item => item.id === ref.evidenceId);
      const check = entry?.receipt.checks.find(item => item.id === evidence?.checkId);
      return <div className="st-process-reference" key={`${ref.receiptHash}/${ref.evidenceId}`}><strong>{entry ? label(entry.origin) : '本次观察或待导入证据'}</strong><p>{evidence?.summary || ref.evidenceId}</p>{evidence ? <p>作者：{evidence.author.id} · {evidence.author.kind} · {evidence.author.authority}</p> : null}{check ? <p>检查结果：{check.execution.result} · 退出码 {check.execution.exitCode ?? '不适用'} · {check.execution.finishedAt || '未执行'}</p> : null}<p className="st-path">{ref.receiptId}<br />{ref.receiptHash}<br />{ref.evidenceId}</p></div>;
    })}</details> : null}
  </article>;
}

export function ProcessEvidencePanel({ workspace, nodeId = null, candidateId = null, compact = false }) {
  const [catalog, setCatalog] = useState(null), [error, setError] = useState(''), [selected, setSelected] = useState(''), [revision, setRevision] = useState(0);
  const [moduleId, setModuleId] = useState('');
  useEffect(() => {
    const abort = new AbortController(); setCatalog(null); setError('');
    const query = new URLSearchParams({ ...(nodeId ? { nodeId } : {}), ...(candidateId ? { candidateId } : {}) });
    standaloneRequest(`process?${query}`, { signal: abort.signal }).then(setCatalog, failure => { if (failure.name !== 'AbortError') setError(failure.message); });
    return () => abort.abort();
  }, [nodeId, candidateId, workspace.revision, revision]);
  const works = catalog?.works.filter(item => !moduleId || item.moduleIds.includes(moduleId)) ?? [];
  const workId = works.some(item => item.workItemId === selected) ? selected : works[0]?.workItemId;
  return <section className={`st-process-panel${compact ? ' compact' : ''}`} aria-label={compact ? '模块开发过程' : '开发过程证据'}>
    <div className="st-section-heading"><h2>{compact ? '模块开发过程' : '开发过程证据'}</h2><button type="button" className="st-text-button" onClick={() => setRevision(value => value + 1)}>刷新工作项</button></div>
    <p className="st-note">{compact ? '查看当前实体所属模块的关联工作项。' : '普通开发任务可直接交接。每项要求分别保留证据、来源和当前可用性。'}</p>
    {error ? <p className="st-inline-error" role="alert">{error}</p> : !catalog ? <p role="status">正在读取工作项…</p> : catalog.status === 'not_initialized' ? <Empty title="尚未登记开发过程">{catalog.reason}</Empty> : <>
      <p className="st-note">{catalog.reason}</p>
      <ConfigurationContext value={catalog.configuration} />
      {!compact ? <Field label="登记模块"><select aria-label="登记模块" value={moduleId} onChange={event => setModuleId(event.target.value)}><option value="">所有模块</option>{catalog.modules.map(item => <option key={item.moduleId} value={item.moduleId}>{item.moduleId}{item.path ? ` · ${item.path}` : ' · 路径未映射'}</option>)}</select></Field> : null}
      {!works.length ? <Empty title="没有关联的工作项">使用项目内的 panorama-process begin 登记开发任务及模块范围。</Empty> : <><Field label="开发工作项"><select aria-label="开发工作项" value={workId} onChange={event => setSelected(event.target.value)}>{works.map(item => <option key={item.workItemId} value={item.workItemId}>{item.goal} · {label(item.phase)}</option>)}</select></Field>
        <ProcessWorkDetail key={`${workId}:${workspace.revision}:${revision}`} workId={workId} workspace={workspace} compact={compact} />
      </>}
    </>}
  </section>;
}

function ProcessWorkDetail({ workId, workspace, compact }) {
  const [detail, setDetail] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null), [raw, setRaw] = useState(''), [importPreview, setImportPreview] = useState(null), [compatibility, setCompatibility] = useState(null), [handoff, setHandoff] = useState(null);
  const [historical, setHistorical] = useState(null), [matrix, setMatrix] = useState(null);
  const pending = useRef(false), controller = useRef(null);
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort;
    standaloneRequest(`process/work?id=${encodeURIComponent(workId)}`, { signal: abort.signal }).then(setDetail, failure => { if (failure.name !== 'AbortError') setError(failure.message); });
    return () => abort.abort();
  }, [workId]);
  const request = (path, body) => standaloneRequest(path, { body, signal: controller.current.signal });
  const action = async fn => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try { await fn(); } catch (failure) { if (failure.name !== 'AbortError') { setError(failure.status === 409 ? `记录已变化，请刷新工作项后重试。${failure.message}` : failure.message); setResult(null); setImportPreview(null); } }
    finally { pending.current = false; if (!controller.current.signal.aborted) setBusy(false); }
  };
  if (!detail) return error ? <p role="alert" className="st-inline-error">{error}</p> : <p role="status">正在读取过程记录…</p>;
  if (detail.status === 'legacy_unknown') return <><ConfigurationContext value={detail.configuration} work /><Empty title="旧工作项保持只读">{detail.goal}。没有结构化过程证据，不能从旧完成状态推断当前通过。</Empty></>;
  const assessment = result?.assessment || detail.lastAssessment;
  const freshness = result ? `${result.status === 'recorded' ? '已保存本次评估' : '本次观察预览'} · ${result.assessedAt}` : detail.lastAssessment ? `历史评估 · ${detail.lastAssessment.assessedAt}，当前有效性未复查` : '尚未评估，当前状态未知';
  return <div className="st-process-detail">
    <p className="st-path">{workId}</p><p>{detail.context.expectedOutcome}</p>
    <p className={detail.candidate.status === 'stale' ? 'st-warning' : 'st-note'}>{detail.candidate.reason}{detail.candidate.binding ? `（v${detail.candidate.binding.candidateVersion}）` : ''}</p>
    <p className="st-note">模块：{detail.context.moduleIds.join('、')} · 工作项阶段：{label(detail.phase)} · Structure：{detail.coreState}</p>
    <p className="st-note">规范版本：{detail.rulePacks.map(pack => `${pack.id} @ ${pack.version}`).join('；')}</p>
    <ConfigurationContext value={result?.configuration || detail.configuration} work />
    <details className="st-process-context"><summary>工作范围与交接上下文</summary><p>目标：{detail.context.goal}</p><p>计划范围：{detail.context.plannedPaths.join('、')}</p><p className="st-path">原始基线：{detail.context.baseline.id} · {detail.context.baseline.capturedAt} · {label(detail.context.baseline.state)}</p><p>结果记录：{detail.context.outcome.summary}</p><p>未完成：{detail.context.outcome.incomplete.join('；') || '未记录未完成项'}</p><p>接续说明：{detail.context.outcome.resumeNotes.join('；')}</p>{detail.context.unknowns.length ? <p>待确认：{detail.context.unknowns.join('；')}</p> : null}{detail.lastSelection ? <p>历史证据选择理由：{detail.lastSelection.selectionReason}</p> : null}</details>
    {detail.pending.runs.length || detail.pending.receipts ? <p className="st-warning">有 {detail.pending.runs.length} 个检查和 {detail.pending.receipts} 份 Receipt 待本地恢复，页面不会重新启动检查。</p> : null}
    <div className="st-form-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => action(async () => { setResult(await request('process/preview', { workItemId: workId })); })}>复查当前证据</button><button type="button" className="primary-button" disabled={busy} onClick={() => action(async () => { const value = await request('process/assess', { workItemId: workId, expectedRevision: detail.revision }); setDetail(await request(`process/work?id=${encodeURIComponent(workId)}`)); setResult(value); })}>保存过程评估</button>{!compact ? <button type="button" className="secondary-button" disabled={busy} onClick={() => action(async () => { setHandoff(await request('process/handoff', { workItemId: workId, expectedWorkspaceRevision: workspace.revision })); })}>生成开发交接 v2</button> : null}</div>
    {busy ? <p role="status">正在读取已登记输入并处理过程记录…</p> : null}
    {error ? <p className="st-inline-error" role="alert">{error}</p> : null}
    <div className="st-process-conclusion" role="status"><strong>{freshness}</strong>{assessment ? <p><Status value={assessment.overall.status} />{result ? ` ${result.currentEvidenceReady ? '本次过程证据就绪' : '本次仍有缺口、待恢复记录或过期绑定'}` : ' 历史结论不代表当前通过'}</p> : null}<p className="st-note">复查只读取证据与输入，不运行业务检查。过程证据就绪不等于人工验收、部署或工作项完成。</p></div>
    {result?.evidenceSelection ? <p className="st-note">证据范围：{result.evidenceSelection.reason}</p> : null}
    {detail.completionSelection ? <p className="st-warning">本地完成记录曾明确选择 {detail.completionSelection.receiptIds.length} 份检查证据：{detail.completionSelection.reason}。工作台复查包含全部检查，因此结论可能与该次选择不同；失败历史仍保留。</p> : null}
    <ProcessEvolution value={detail.evolution} current={result?.evolution} receipts={detail.receipts} busy={busy} onAssessment={id => action(async () => setHistorical(await request(`process/assessment?id=${encodeURIComponent(id)}`)))} />
    {historical ? <details className="st-process-history" open><summary>历史评估原文 · {historical.assessedAt}</summary><p className="st-note">保存时的结论，不代表当前有效。</p><pre>{JSON.stringify(historical, null, 2)}</pre><button type="button" className="st-text-button" onClick={() => setHistorical(null)}>关闭历史原文</button></details> : null}
    <button type="button" className="st-text-button" disabled={busy} onClick={() => action(async () => setMatrix(await request(`process/matrix?id=${encodeURIComponent(workId)}`)))}>查看规范覆盖</button>
    {matrix ? <details className="st-process-rules" open><summary>声明的规范覆盖（不表示检查通过）</summary>{matrix.coverage.map(row => <article className="st-process-row" key={row.criterionId}><strong>{row.criterionId} · {row.requirement}</strong><p>属性：{row.claims?.join('、') || '旧版未声明属性绑定'}</p>{row.runners.map(runner => <p key={runner.runnerId}>{runner.runnerId} · {runner.status === 'declared_coverage' ? '已声明覆盖' : runner.status === 'uncovered' ? `未覆盖：${runner.error}` : '旧版仅关联证据类型'}{runner.contract ? ` · ${runner.contract.id} @ ${runner.contract.version}` : ''}</p>)}</article>)}{matrix.rules.map(rule => <p key={rule.ruleId}>{rule.ruleId} @ {rule.ruleVersion} · {label(rule.strength)} · {label(rule.applicability.status)}：{rule.applicability.reason}</p>)}</details> : null}
    {assessment ? <>
      <h3>逐项要求（{assessment.criteria.length}）</h3>{assessment.criteria.map(row => <EvidenceRow key={row.criterionId} row={row} title={`${row.required ? '必备' : '可选'} · ${row.criterionId}：${detail.context.criteria.find(item => item.id === row.criterionId)?.requirement || ''}`} receipts={detail.receipts} />)}
      <details className="st-process-rules" open={!compact}><summary>适用规范与理由（{assessment.rules.length}）</summary>{assessment.rules.map(row => <EvidenceRow key={row.ruleId} row={row} title={`${row.ruleId} @ ${row.ruleVersion} · ${detail.rules.find(rule => rule.id === row.ruleId)?.title || row.ruleId}`} receipts={detail.receipts} />)}</details>
    </> : <ul>{detail.context.criteria.map(item => <li key={item.id}>{item.id}：{item.requirement} · 未评估</li>)}</ul>}
    <details className="st-process-history"><summary>Receipt 来源与历史（{detail.receipts.length}）</summary>{detail.receipts.map(({ receipt, origin, locallyAttested }) => <article className="st-process-reference" key={receipt.receiptId}><strong>{label(origin)}</strong><p>{locallyAttested ? '来源由本地采集记录关联；有效性仍需当前复查。' : '外部声明，未获得本地采集凭据。'}</p><p className="st-path">{receipt.receiptId} · {receipt.generatedAt}</p><p>生产者：{receipt.producer.id} · {receipt.producer.kind}</p><button type="button" className="st-text-button" onClick={() => downloadText(JSON.stringify(receipt, null, 2), 'process-receipt.json', 'application/json')}>下载 Receipt</button></article>)}</details>
    {!compact ? <>
      <details className="st-process-import"><summary>导入结构化 Receipt 或查看旧格式</summary><p className="st-note">Receipt 先验证项目、工作项、原始基线和候选绑定；导入来源保留为外部声明。旧交接包和旧规则包只做兼容预览。</p><Field label="结构化 JSON"><textarea rows="7" value={raw} onChange={event => { setRaw(event.target.value); setImportPreview(null); setCompatibility(null); }} /></Field>
        <div className="st-form-actions"><button type="button" className="secondary-button" disabled={busy || !raw.trim()} onClick={() => action(async () => { const document = JSON.parse(raw); if (document.formatVersion === 'panorama.process-receipt.v1') { const preview = await request('process/preview', { workItemId: workId, receiptJson: raw }); setImportPreview({ raw, result: preview }); setResult(preview); setCompatibility(null); } else { setCompatibility(await request('process/compatibility', { documentJson: raw })); setImportPreview(null); } })}>预览导入</button><button type="button" className="secondary-button" disabled={busy || !importPreview || importPreview.raw !== raw} onClick={() => action(async () => { setDetail(await request('process/import', { workItemId: workId, receiptJson: importPreview.raw, expectedRevision: detail.revision })); setResult(null); setRaw(''); setImportPreview(null); })}>保存外部 Receipt</button></div>
        {importPreview ? <p role="status">Receipt 结构与绑定可读取，预览按外部声明计算。保存后仍需复查当前证据。</p> : null}
        {compatibility ? <div role="status"><strong>{label(compatibility.status)}</strong><ul>{compatibility.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></div> : null}
      </details>
      {handoff ? <div className="st-exported"><h3>开发交接预览 · v2</h3><div className="st-form-actions"><CopyButton text={handoff.markdown} /><button type="button" className="secondary-button" onClick={() => downloadText(handoff.markdown, 'panorama-process-handoff.md', 'text/markdown;charset=utf-8')}>下载开发交接</button><button type="button" className="secondary-button" onClick={() => downloadText(JSON.stringify(handoff.manifest, null, 2), 'panorama-handoff-v2.json', 'application/json')}>下载交接 JSON</button></div><pre className="st-markdown">{handoff.markdown}</pre></div> : null}
    </> : null}
  </div>;
}
