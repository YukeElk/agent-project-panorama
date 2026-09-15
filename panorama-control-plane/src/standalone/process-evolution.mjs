// Read-only projections of existing work/receipt identities. No execution or new ledger.
import { sha256 } from '../process/json.mjs';
import { assessProcess } from '../process/evaluate.mjs';
import { diffModels } from './design-model.mjs';

const unique = values => [...new Set(values)];
const ref = (kind, id, hash, path = '') => ({ kind, id, hash: hash ?? null, pointer: path });
const receiptRef = (receipt, path = '') => ref('receipt', receipt.receiptId, receipt.receiptHash, path);
function logReferences(receipt) {
  const values = receipt.extensions?.['panorama.development.capture']?.logReferences;
  if (!Array.isArray(values)) return [];
  return values.filter(row => row && ['stdout', 'stderr'].includes(row.stream) && typeof row.digest === 'string' && /^[a-f0-9]{64}$/.test(row.digest)
    && Number.isSafeInteger(row.retainedBytes) && row.retainedBytes >= 0 && Number.isSafeInteger(row.totalBytes) && row.totalBytes >= row.retainedBytes && typeof row.truncated === 'boolean')
    .map(({ stream, digest, retainedBytes, totalBytes, truncated }) => ({ stream, digest, retainedBytes, totalBytes, truncated }));
}
const snapshotSets = receipt => receipt.inputSets.map(set => ({ ...set, snapshot: set.after,
  complete: !receipt.contextSnapshot.unknowns.includes('INPUT_INCOMPLETE:' + set.id) && !set.after.files.some(file => file.state === 'unreadable') }));

export function inputDelta(before, after) {
  const changes = new Map(), unknowns = [];
  for (const left of before) {
    const right = after.find(set => set.id === left.id);
    if (!right || right.complete === false || left.complete === false || left.selectionDigest !== right.selectionDigest) {
      unknowns.push(left.id); continue; // A revoked/unreadable root is not a mass deletion.
    }
    const a = new Map(left.snapshot.files.map(file => [file.rootId + '/' + file.path, file]));
    const b = new Map(right.snapshot.files.map(file => [file.rootId + '/' + file.path, file]));
    for (const key of new Set([...a.keys(), ...b.keys()])) {
      const old = a.get(key), next = b.get(key);
      if (old?.state === next?.state && old?.digest === next?.digest) continue;
      const presentBefore = old?.state === 'present', presentAfter = next?.state === 'present';
      const kind = old?.state === 'unreadable' || next?.state === 'unreadable' ? 'unknown' : !presentBefore && presentAfter ? 'added' : presentBefore && !presentAfter ? 'removed' : 'modified';
      const row = changes.get(key) ?? { rootId: (next ?? old).rootId, path: (next ?? old).path, kind,
        beforeDigest: old?.digest ?? null, afterDigest: next?.digest ?? null, inputSetIds: [] };
      row.inputSetIds.push(left.id); changes.set(key, row);
    }
  }
  return { files: [...changes.values()].sort((a, b) => (a.rootId + a.path).localeCompare(b.rootId + b.path)), unknownInputSetIds: unique(unknowns) };
}

function designContext(work, workspace) {
  const binding = work.candidateBinding, candidate = workspace.candidates.find(item => item.id === binding?.candidateId);
  const exact = candidate && candidate.version === binding.candidateVersion && candidate.baseSnapshotId === binding.baselineSnapshotId;
  const source = { snapshot: workspace.current.snapshot, files: workspace.current.files.length, nodes: workspace.current.nodes.length,
    status: 'recorded_source_observation', ref: ref('workspace', String(workspace.revision), null, '/current/snapshot'),
    limitation: '最近一次源码解析的结构；当前文件需显式刷新。静态导入不代表运行调用。' };
  if (!binding) return { binding: null, target: null, source, references: [], gaps: ['未绑定设计候选；按工作项目标与验收项追踪。没有正式 Requirement / Decision 引用。'] };
  if (!exact) return { binding, target: null, source, references: [], gaps: ['绑定候选已缺失或改版；仅保留原绑定，不将当前候选替换为历史目标。'] };
  const session = workspace.sessions.find(item => item.id === candidate.sessionId);
  const index = workspace.candidates.indexOf(candidate), sessionIndex = workspace.sessions.indexOf(session);
  const base = new Map(candidate.baseModel.files.map(file => [file.path, file]));
  const current = new Map(workspace.current.files.map(file => [file.path, file]));
  const files = [...new Set([...base.keys(), ...current.keys()])].filter(path => base.get(path)?.digest !== current.get(path)?.digest)
    .map(path => ({ path, kind: !base.has(path) ? 'added' : !current.has(path) ? 'removed' : 'modified', beforeDigest: base.get(path)?.digest ?? null, afterDigest: current.get(path)?.digest ?? null }));
  return { binding, source, target: { id: candidate.id, title: candidate.title, version: candidate.version, status: 'declared_design',
    baselineSnapshotId: candidate.baseSnapshotId, sourceChanged: candidate.baseSnapshotId !== source.snapshot.id,
    diff: diffModels(candidate.baseModel, candidate.target).summary, ref: ref('workspace', String(workspace.revision), null, '/candidates/' + index),
    bindings: candidate.bindings.map(row => ({ ...row, status: row.sourceSnapshotId === source.snapshot.id ? 'explicit_current_identity' : 'stale_identity' })) },
    sourceChanges: { fromSnapshotId: candidate.baseSnapshotId, toSnapshotId: source.snapshot.id, files,
      limitation: '比较两个已解析快照的文件清单；解析范围变化也可能改变清单，不能据此证明业务实现。' },
    references: session ? [{ kind: 'design_question', text: session.question, ref: ref('workspace', String(workspace.revision), null, '/sessions/' + sessionIndex + '/question') },
      { kind: 'design_decision', text: session.selectedCandidateId === candidate.id ? session.decisionReason : '', ref: ref('workspace', String(workspace.revision), null, '/sessions/' + sessionIndex + '/decisionReason') }] : [],
    gaps: ['设计问题和选择理由是当前设计工作区的显式上下文，不是正式 Requirement / Decision，也不证明目标已实现。'] };
}

function checkerContext(ctx, receipt, check) {
  const runner = ctx.local.bootstrap.runners.find(row => row.id === check.runnerRef?.id);
  const matches = runner && sha256(runner) === check.runnerRef.definitionDigest;
  const contract = matches && runner.checkerContractId ? ctx.local.bootstrap.checkerContracts.find(row => row.id === runner.checkerContractId) : null;
  return { id: check.checkerId, definitionDigest: check.runnerRef?.definitionDigest ?? null, version: matches ? runner.version : null,
    versionStatus: matches ? 'registered_original_configuration' : 'unknown',
    contract: contract ? { id: contract.id, version: contract.version, digest: sha256(contract), claims: contract.claims, notChecked: contract.notChecked } : null,
    binding: receipt.extensions?.['panorama.development.check-binding'] ?? null };
}

export function historicalEvolution(ctx, work, receipts, assessments, workspace) {
  const workRef = ref('work', work.workItemRef.id, work.workItemRef.definitionDigest);
  const events = [{ id: 'begin:' + work.workItemRef.id, kind: 'begin', at: work.context.baseline.capturedAt,
    title: work.context.goal, ref: { ...workRef, pointer: '/context/baseline' } }];
  for (const { receipt, origin } of receipts) {
    events.push({ id: receipt.receiptId, kind: origin === 'local_scan' ? 'scan' : 'receipt', at: receipt.generatedAt, origin,
      title: origin === 'local_scan' ? '输入观察' : '采集检查或评审', ref: receiptRef(receipt),
      changes: inputDelta(work.context.baseline.inputSets, snapshotSets(receipt)),
      checks: receipt.checks.filter(check => check.kind !== 'record').map(check => ({ id: check.id, kind: check.kind, execution: check.execution,
        subjectIds: check.subjectIds, criterionIds: check.criterionIds, inputSetIds: check.inputSetIds, checker: checkerContext(ctx, receipt, check),
        evidence: receipt.evidence.filter(e => e.checkId === check.id).map(e => ({ id: e.id, kind: e.kind, summary: e.summary, author: e.author,
          ref: receiptRef(receipt, '/evidence/' + receipt.evidence.indexOf(e)) })),
        ref: receiptRef(receipt, '/checks/' + receipt.checks.indexOf(check)) })),
      logReferences: logReferences(receipt),
      executionId: receipt.executionId, configurationDigest: typeof receipt.extensions?.['panorama.development.capture']?.configurationDigest === 'string' ? receipt.extensions['panorama.development.capture'].configurationDigest : null });
  }
  for (const assessment of assessments) events.push({ id: assessment.assessmentId, kind: 'assessment', at: assessment.assessedAt,
    title: '历史过程评估', overall: assessment.overall, criteria: assessment.criteria, rules: assessment.rules, receiptRefs: assessment.receiptRefs,
    ref: ref('assessment', assessment.assessmentId, assessment.assessmentHash),
    selectionReason: work.lastAssessment?.id === assessment.assessmentId ? work.lastAssessment.selectionReason : work.operations.finish?.assessmentId === assessment.assessmentId ? work.operations.finish.selection?.reason ?? '当次使用全部已登记检查。' : null });
  if (work.operations.finish) events.push({ id: work.operations.finish.id, kind: 'completion', at: null, title: '本地完成操作记录',
    status: work.operations.finish.status, phase: work.phase, selection: work.operations.finish.selection,
    assessmentId: work.operations.finish.assessmentId ?? null, ref: { ...workRef, pointer: '/operations/finish' },
    limitation: '完成操作没有独立时间戳，单列于时间记录后；完成状态不代表证据永久有效。' });
  events.sort((a, b) => a.at && b.at ? a.at.localeCompare(b.at) : a.at ? -1 : b.at ? 1 : 0);
  return { formatVersion: 'panorama.process-evolution.v1', scope: 'historical', workItemRef: work.workItemRef,
    baseline: { id: work.context.baseline.id, capturedAt: work.context.baseline.capturedAt, ref: { ...workRef, pointer: '/context/baseline' } },
    why: { goal: work.context.goal, expectedOutcome: work.context.expectedOutcome, status: 'declared_work_goal', ref: { ...workRef, pointer: '/context/goal' } },
    design: designContext(work, workspace), events, current: null,
    limitations: ['事件顺序来自已有记录的时间戳，不推断因果或运行调用。', '展示全部已登记 Receipt，以及最近评估和完成操作引用的评估；其他历史评估仍保留在原过程存储。', '日志仅显示原 Receipt 的摘要哈希引用；不读取或公开业务日志正文。'] };
}

function explainId(id, receipt, evaluation, currentInputs) {
  const input = receipt.inputSets.find(set => set.id === id);
  if (input) {
    const observed = evaluation.currentInputObservations.find(row => row.inputSetId === id);
    const captured = currentInputs.find(set => set.id === id);
    const comparable = observed?.state === 'readable' && captured?.snapshot.digest === observed.digest;
    return { id, kind: 'input', reason: comparable ? '检查所绑定输入与本次观察不同，或在执行期间发生变化。' : '输入无法完整观察或两次观察不一致，不能判断具体文件增删。',
      sinceCheck: inputDelta(snapshotSets(receipt).filter(set => set.id === id), comparable ? [captured] : []),
      duringCheck: inputDelta([{ ...input, snapshot: input.before }], snapshotSets(receipt).filter(set => set.id === id)),
      ref: receiptRef(receipt, '/inputSets/' + receipt.inputSets.indexOf(input)) };
  }
  const subject = receipt.subjects.find(row => row.id === id);
  if (subject) return { id, kind: 'subject', reason: '验收对象身份已变化；没有当前对象身份时保持未知。', locator: subject.locator,
    beforeDigest: subject.identityDigest, afterDigest: evaluation.currentSubjectObservations.find(row => row.subjectId === id)?.identityDigest ?? null,
    ref: receiptRef(receipt, '/subjects/' + receipt.subjects.indexOf(subject)) };
  if (id === receipt.executionId) return { id, kind: 'environment', reason: '执行环境指纹已变化，包含工具、配置、声明依赖和选定环境变量；不能从聚合指纹判定单一原因。', ref: receiptRef(receipt, '/environment') };
  return { id, kind: 'unknown', reason: '评估核心返回的未解析身份。', ref: receiptRef(receipt) };
}

export function currentEvolution(ctx, work, prepared, scan, assessment) {
  const checks = [];
  for (const receipt of prepared.receipts) {
    if (receipt.receiptId === scan.receipt.receiptId || !receipt.checks.some(check => check.kind !== 'record')) continue;
    // Use the same evaluator and exact observation context as the all-evidence verdict.
    const subset = [receipt, scan.receipt];
    const inputIds = new Set(subset.flatMap(row => row.inputSets.map(input => input.id)));
    const subjectIds = new Set(subset.flatMap(row => row.subjects.map(subject => subject.id)));
    const single = assessProcess({ project: ctx.project, packs: ctx.packs, ...prepared.evaluation, receipts: subset,
      currentInputObservations: prepared.evaluation.currentInputObservations.filter(row => inputIds.has(row.inputSetId)),
      currentSubjectObservations: prepared.evaluation.currentSubjectObservations.filter(row => subjectIds.has(row.subjectId)),
      verifiedEvidence: prepared.evaluation.verifiedEvidence.filter(row => subset.some(item => item.receiptHash === row.receiptHash)) });
    for (const check of receipt.checks.filter(row => row.kind !== 'record')) {
      const criteria = single.criteria.filter(row => check.criterionIds.includes(row.criterionId));
      const changedIds = unique(criteria.flatMap(row => row.freshness.changedIds));
      checks.push({ receiptId: receipt.receiptId, receiptHash: receipt.receiptHash, checkId: check.id, executionId: receipt.executionId,
        execution: check.execution, criteria, assessmentScope: 'receipt_all_checks', explanations: changedIds.map(id => explainId(id, receipt, prepared.evaluation, scan.after)),
        unknowns: unique(criteria.flatMap(row => row.freshness.unknowns)), ref: receiptRef(receipt, '/checks/' + receipt.checks.indexOf(check)) });
    }
  }
  return { scope: 'observed_at', observedAt: assessment.assessedAt, configuration: ctx.configuration, rulePacks: ctx.project.rulePacks,
    baselineChanges: inputDelta(work.context.baseline.inputSets, scan.after), checks,
    note: '逐份 Receipt 使用同一本次观察和 P1 评估器解释，含该份 Receipt 的全部检查；最终结论仍使用全部检查，包括当前相反证据。继续编辑后需再次复查。' };
}
