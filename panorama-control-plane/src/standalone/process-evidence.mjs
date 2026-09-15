import { join } from 'node:path';
import { loadReadContext, readCoreWork } from '../development/read-context.mjs';
import { readJson, keys, now } from '../development/io.mjs';
import { listWorks, readWork, workTransaction, processStore, commitReceipt, reconcileReceipts } from '../development/journal.mjs';
import { captureInputs, captureSubjects, captureEnvironment, makeReceipt, currentFacts } from '../development/capture.mjs';
import { prepareAssessment, assessCollected } from '../development/assessment.mjs';
import { assessProcess } from '../process/evaluate.mjs';
import { validateReceipt } from '../process/validation.mjs';
import { validateDefinition, validateDocument } from '../process/schema.mjs';
import { sameValue, processValue, parseProcessJson } from '../process/json.mjs';
import { requireProcess } from '../process/errors.mjs';
import { exportWorkHandoff, readHandoff } from './handoff.mjs';
import { withConfigurationLease } from '../development/configuration-state.mjs';
import { standardMatrix } from '../development/standards.mjs';
import { historicalEvolution, currentEvolution } from './process-evolution.mjs';

export function candidateStatus(work, workspace) {
  const binding = work.candidateBinding;
  if (!binding) return { status: 'unbound', reason: '普通开发工作项，无设计候选绑定。', binding: null };
  const candidate = workspace.candidates.find(item => item.id === binding.candidateId);
  const reasons = [];
  if (!candidate) reasons.push('候选已不存在');
  else {
    if (candidate.version !== binding.candidateVersion) reasons.push('候选版本已变化');
    if (candidate.baseSnapshotId !== binding.baselineSnapshotId) reasons.push('候选基线已变化');
    if (workspace.current.snapshot.id !== binding.baselineSnapshotId) reasons.push('当前源码快照已变化');
  }
  return { status: reasons.length ? 'stale' : 'current', reason: reasons.join('；') || '候选版本与基线匹配。', binding };
}

export function compatibilityPreview(raw) {
  const document = processValue(raw);
  if (['panorama.handoff.v1', 'panorama.handoff.v2'].includes(document.formatVersion)) return readHandoff(document);
  if (document.formatVersion === 'standard-pack.v0.2') {
    validateDocument(document, 'standard-pack.v0.2');
    return { formatVersion: document.formatVersion, status: 'supported', gaps: [], document };
  }
  requireProcess(document.formatVersion === 'standard-pack.v0.1' && Array.isArray(document.rules), 'COMPATIBILITY_FORMAT_UNSUPPORTED');
  return { formatVersion: document.formatVersion, status: 'legacy_unknown', document, gaps: [
    '旧规则包保留原文；需另建 v0.2 包并明确规则强度、适用条件与证据要求后才能计算。',
    ...document.rules.flatMap((rule, index) => ['strength', 'when', 'evidenceRequirements'].filter(key => !Object.hasOwn(rule, key)).map(key => `${rule.id || index}: 缺少 ${key}`)),
    'manual 检查器及缺少的自动评估定义保持 unknown，不从严重性推断强度或通过。',
  ] };
}

export function importDocument(body, key) {
  const jsonKey = key + 'Json';
  requireProcess(Object.hasOwn(body, key) !== Object.hasOwn(body, jsonKey), 'DOCUMENT_INPUT_REQUIRED');
  return Object.hasOwn(body, jsonKey) ? parseProcessJson(body[jsonKey]) : body[key];
}

async function observationScan(ctx, work) {
  const before = await captureInputs(ctx, work), after = await captureInputs(ctx, work);
  const subjects = await captureSubjects(ctx, work, after), environment = await captureEnvironment(ctx, null, after);
  return { before, after, subjects, receipt: makeReceipt(ctx, work, { before, after, subjects, environment }), facts: currentFacts(ctx, work, after),
    scopeComplete: after.every(item => item.complete) && work.context.baseline.state === 'observed',
    policy: { decision: 'unknown', reasons: ['工作台只观察过程证据；Structure policy 与完成迁移请使用本地开发命令。'] } };
}

export class ProcessEvidenceService {
  constructor(workspaceStore) { this.workspaceStore = workspaceStore; }
  async context(workId = null) {
    return loadReadContext({ projectRoot: this.workspaceStore.projectRoot, dataRoot: this.workspaceStore.dataRoot }, { optional: true, workId });
  }
  async requiredContext(workId = null) { const ctx = await this.context(workId); requireProcess(ctx, 'PROCESS_NOT_INITIALIZED'); return ctx; }
  async matrix(id) { const ctx=await this.requiredContext(id);return withConfigurationLease(ctx,async()=>{const work=await readWork(ctx,id);await readCoreWork(ctx,work);return standardMatrix(ctx,work);}); }
  async catalog(nodeId = null, candidateId = null) {
    const ctx = await this.context();
    if (!ctx) return { status: 'not_initialized', works: [], modules: [], reason: '尚未用 panorama-process init 登记开发过程；页面读取不会自动初始化。' };
    return withConfigurationLease(ctx,async()=>{
    const workspace = this.workspaceStore.snapshot();
    const manifest = await readJson(join(ctx.projectRoot, '.structure/manifest.json'));
    const node = nodeId ? workspace.current.nodes.find(item => item.id === nodeId) : null;
    const modules = ctx.project.moduleBindings.map(binding => {
      const path = manifest.modules[binding.moduleId]?.path;
      const explicit = binding.sourceSnapshotId === workspace.current.snapshot.id && binding.panoramaNodeIds.includes(nodeId);
      const contains = node?.sourcePath != null && path != null && (path === '.' || node.sourcePath === path || node.sourcePath.startsWith(path.replace(/\/$/, '') + '/'));
      return { moduleId: binding.moduleId, path: path ?? null, related: Boolean(explicit || contains), basis: explicit ? 'explicit_snapshot_binding' : contains ? 'current_source_path' : 'unmapped' };
    });
    const works = [];
    for (const entry of await listWorks(ctx)) {
      const selected=await this.requiredContext(entry.workItemId);
      const work = await readWork(selected, entry.workItemId);
      const moduleIds = work.context?.moduleIds ?? work.legacy?.modules ?? [];
      if (nodeId && !modules.some(item => item.related && moduleIds.includes(item.moduleId))) continue;
      if (candidateId && work.candidateBinding?.candidateId !== candidateId) continue;
      for (const moduleId of moduleIds) if (!modules.some(item => item.moduleId === moduleId)) modules.push({ moduleId, path: null, related: false, basis: 'historical_registration' });
      works.push({ ...entry, moduleIds, configuration:selected.configuration, candidate: candidateStatus(work, workspace), receiptCount: work.receipts?.length ?? 0,
        historicalAssessment: work.lastAssessment ? { id: work.lastAssessment.id, recordedAt: work.lastAssessment.recordedAt } : null });
    }
    return { status: 'registered', observedAt: now(), revision: (await (await processStore(ctx)).list()).revision,
      binding: ctx.project.binding, rulePacks: ctx.project.rulePacks, configuration:ctx.configuration, modules, works,
      reason: candidateId ? '仅按工作项显式候选 ID 关联；这是候选级关联，不证明所选目标节点的职责或实现。' : nodeId ? '关联由已登记模块及当前源码路径确定；路径包含不证明架构职责。未映射实体不会按名称猜测。' : '按登记模块筛选已有工作项；历史结论需要重新观察当前输入。' };
    });
  }
  async detail(id, ctx = null, capturedWork = null) {
    ctx ??= await this.requiredContext(id);
    return withConfigurationLease(ctx,async()=>{
    const work = capturedWork ?? await readWork(ctx, id), store = await processStore(ctx), workspace = this.workspaceStore.snapshot();
    const core = await readCoreWork(ctx, work);
    if (work.phase === 'legacy_read_only') return { status: 'legacy_unknown', workItemRef: work.workItemRef, goal: work.legacy.title, criteria: [], receipts: [], candidate: candidateStatus(work, workspace), coreState: core.state, configuration:ctx.configuration };
    const receipts = await Promise.all(work.receipts.map(async meta => {
      const receipt = await store.getReceipt(meta.id);
      requireProcess(meta.hash === receipt.receiptHash, 'WORK_RECEIPT_HASH_MISMATCH');
      return { receipt, origin: meta.origin, locallyAttested: ['local_scan', 'local_execution', 'local_agent_review'].includes(meta.origin) };
    }));
    const lastAssessment = work.lastAssessment ? await store.getAssessment(work.lastAssessment.id) : null;
    if (lastAssessment) requireProcess(lastAssessment.assessmentHash === work.lastAssessment.hash, 'WORK_ASSESSMENT_HASH_MISMATCH');
    const assessments = lastAssessment ? [lastAssessment] : [];
    const completion = work.operations.finish;
    if (completion?.assessmentId && completion.assessmentId !== lastAssessment?.assessmentId) {
      const saved = await store.getAssessment(completion.assessmentId);
      requireProcess(saved.assessmentHash === completion.assessmentHash && sameValue(saved.workItemRef, work.workItemRef), 'WORK_ASSESSMENT_HASH_MISMATCH');
      assessments.push(saved);
    }
    return { status: 'registered', revision: (await store.list()).revision, workItemRef: work.workItemRef, phase: work.phase, context: work.context,
      candidate: candidateStatus(work, workspace), coreState: core.state, rules: ctx.config.rules, rulePacks: ctx.project.rulePacks,configuration:ctx.configuration,
      receipts, lastAssessment, checkBindings:work.checkBindings ?? null, checkBindingsDigest:work.checkBindingsDigest ?? null, lastSelection: work.lastAssessment ?? null, completionSelection: work.operations.finish?.selection ?? null,
      evolution: historicalEvolution(ctx, work, receipts, assessments, workspace),
      pending: { receipts: work.pendingReceipts.length, runs: work.runs.filter(run => run.status === 'pending').map(run => ({ executionId: run.executionId, runnerId: run.runnerId })) },
      limits: ['保存的 Assessment 是历史记录；当前文件、依赖和候选状态以重新观察为准。', '页面不会恢复执行中的检查，不签发正式验收或迁移工作项状态。'] };
    });
  }
  async receipt(id) { validateDefinition(id, 'id'); return (await processStore(await this.requiredContext())).getReceipt(id); }
  async assessment(id) { validateDefinition(id, 'id'); return (await processStore(await this.requiredContext())).getAssessment(id); }
  async evaluate(body, persist = false) {
    keys(body, persist ? ['workItemId', 'expectedRevision'] : ['workItemId', 'receipt', 'receiptJson'], ['workItemId', ...(persist ? ['expectedRevision'] : [])]);
    const ctx = await this.requiredContext(body.workItemId);
    return workTransaction(ctx, body.workItemId, async work => {
      requireProcess(work.phase !== 'legacy_read_only' && work.operations.begin.status === 'committed', 'WORK_NOT_READY_FOR_OBSERVATION');
      await readCoreWork(ctx, work);
      if (persist) {
        requireProcess((await (await processStore(ctx)).list()).revision === body.expectedRevision, 'STORE_REVISION_CONFLICT');
        await reconcileReceipts(ctx, work);
      }
      const extra = [];
      if (Object.hasOwn(body, 'receipt') || Object.hasOwn(body, 'receiptJson')) {
        const receipt = validateReceipt(importDocument(body, 'receipt'), ctx.config, { workItemRef: work.workItemRef, candidateBinding: work.candidateBinding });
        requireProcess(sameValue(receipt.contextSnapshot.baseline, work.context.baseline), 'WORK_BASELINE_CONFLICT');
        const known = work.receipts.find(item => item.id === receipt.receiptId);
        if (known) requireProcess(known.hash === receipt.receiptHash, 'RECEIPT_ID_CONFLICT');
        else extra.push(receipt);
      }
      const scan = await observationScan(ctx, work);
      let assessment;
      if (persist) await commitReceipt(ctx, work, scan.receipt, 'local_scan');
      const prepared = await prepareAssessment(ctx, work, scan, null, extra);
      if (persist) {
        assessment = (await assessCollected(ctx, work, scan, null, prepared)).assessment;
      } else {
        assessment = assessProcess({ project: ctx.project, packs: ctx.packs, receipts: prepared.receipts, ...prepared.evaluation });
      }
      const candidate = candidateStatus(work, this.workspaceStore.snapshot());
      return { status: persist ? 'recorded' : 'preview', assessment, candidate, observationOnly: true,configuration:ctx.configuration,
        evolution: currentEvolution(ctx, work, prepared, scan, assessment),
        currentEvidenceReady: assessment.overall.processReady && candidate.status !== 'stale' && !work.runs.some(run => run.status === 'pending') && !work.pendingReceipts.length,
        evidenceSelection: { mode: 'all_registered', receiptIds: assessment.receiptRefs.map(ref => ref.receiptId),
          reason: '本次复查使用全部已登记检查和当前观察；导入预览还包括待导入声明。',
          completionSelection: work.operations.finish?.selection ?? null },
        pendingRuns: work.runs.filter(run => run.status === 'pending').length, assessedAt: assessment.assessedAt,
        revision: (await (await processStore(ctx)).list()).revision, policy: scan.policy,
        note: '此结论覆盖本次观察的过程证据，不表示 Structure 完成、正式 Acceptance、测试重跑或部署。' };
    });
  }
  async import(body) {
    keys(body, ['workItemId', 'receipt', 'receiptJson', 'expectedRevision'], ['workItemId', 'expectedRevision']);
    const ctx = await this.requiredContext(body.workItemId);
    await workTransaction(ctx, body.workItemId, async work => {
      requireProcess(work.phase !== 'legacy_read_only', 'HARNESS_WORK_READ_ONLY');
      await readCoreWork(ctx, work);
      requireProcess((await (await processStore(ctx)).list()).revision === body.expectedRevision, 'STORE_REVISION_CONFLICT');
      const receipt = validateReceipt(importDocument(body, 'receipt'), ctx.config, { workItemRef: work.workItemRef, candidateBinding: work.candidateBinding });
      requireProcess(sameValue(receipt.contextSnapshot.baseline, work.context.baseline), 'WORK_BASELINE_CONFLICT');
      await commitReceipt(ctx, work, receipt, 'external_import');
    });
    return this.detail(body.workItemId, ctx);
  }
  async handoff(body) {
    keys(body, ['workItemId', 'expectedWorkspaceRevision'], ['workItemId', 'expectedWorkspaceRevision']);
    const ctx = await this.requiredContext(body.workItemId);
    return workTransaction(ctx, body.workItemId, async work => {
      const workspace = this.workspaceStore.snapshot();
      requireProcess(workspace.revision === body.expectedWorkspaceRevision, 'REVISION_CONFLICT');
      requireProcess(work.phase !== 'legacy_read_only', 'HARNESS_WORK_READ_ONLY');
      const detail = await this.detail(body.workItemId, ctx, work);
      requireProcess(this.workspaceStore.snapshot().revision === workspace.revision, 'REVISION_CONFLICT');
      return exportWorkHandoff(workspace, work, { rulePacks: ctx.project.rulePacks, rules: ctx.config.rules, detail });
    });
  }
}
