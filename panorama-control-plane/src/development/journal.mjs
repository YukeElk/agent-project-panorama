import { randomUUID } from 'node:crypto';
import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { sha256, sameValue, workDefinition } from '../process/json.mjs';
import { validateDefinition } from '../process/schema.mjs';
import { validateReceipt, uniqueBy } from '../process/validation.mjs';
import { openProcessStore } from '../process/storage.mjs';
import { requireProcess } from '../process/errors.mjs';
import { readJson, writeJson, withLock, now, exists, keys } from './io.mjs';
import { captureInputs, currentFacts } from './capture.mjs';
import { evaluateApplicability } from '../process/applicability.mjs';
import { timed } from './timing.mjs';
import { withConfigurationLease, workRevisionId } from './configuration-state.mjs';
import { WORK_V3, validateBindings, validateWorkBindings, checkBindingDigest, validateReceiptBinding } from './check-contracts.mjs';

export const workPath = (ctx, id) => join(ctx.dataRoot, 'process/v1/development/works', sha256(id) + '.json');
export const runDirectory = (ctx, executionId) => join(ctx.dataRoot, 'process/v1/development/runs', sha256(executionId));
export async function saveWork(ctx, work) { await writeJson(workPath(ctx, work.workItemRef.id), work); }
export async function readWorkHeader(ctx, id) {
  validateDefinition(id, 'id');
  const work = await readJson(workPath(ctx, id));
  workRevisionId(work);
  requireProcess(work.workItemRef.id === id && sameValue(work.binding, ctx.project.binding), 'WORK_BINDING_MISMATCH');
  return work;
}
export async function readWork(ctx, id) {
  const work=await readWorkHeader(ctx,id);
  requireProcess(work.configurationDigest === ctx.local.configurationDigest, 'WORK_CONFIGURATION_CHANGED');
  if(work.configurationRevisionId) requireProcess(work.configurationRevisionId===ctx.local.revisionId,'WORK_CONFIGURATION_CHANGED');
  validateWorkBindings(ctx, work);
  return work;
}
export async function workTransaction(ctx, id, fn) {
  validateDefinition(id, 'id');
  return withConfigurationLease(ctx,()=>withLock(workPath(ctx, id) + '.lock', async () => fn(await readWork(ctx, id))));
}
export async function listWorks(ctx) {
  const directory = join(ctx.dataRoot, 'process/v1/development/works');
  if (!await exists(directory)) return [];
  const paths = (await readdir(directory)).filter(name => /^[a-f0-9]{64}\.json$/.test(name));
  requireProcess(paths.length <= 1000, 'WORK_INDEX_LIMIT');
  const result = [];
  for (const path of paths) { const work = await readJson(join(directory, path)); result.push({ workItemId: work.workItemRef.id, phase: work.phase, goal: work.context?.goal ?? work.legacy.title }); }
  return result;
}
const coreDefinition = work => Object.fromEntries(['id', 'title', 'kind', 'impact', 'modules', 'scope', 'acceptance', 'project_id', 'managed_by'].filter(key => Object.hasOwn(work, key)).map(key => [key, work[key]]));
export async function currentCore(ctx, work) {
  const response = await ctx.core.call('work', { action: 'get', id: work.workItemRef.id });
  requireProcess(!work.coreDefinition || sameValue(work.coreDefinition, coreDefinition(response.work_item)), 'CORE_WORK_DEFINITION_CHANGED');
  return response.work_item;
}
export async function reconcileBegin(ctx, work) {
  if (work.operations.begin.status === 'committed') return currentCore(ctx, work);
  const existing = (await ctx.core.call('work')).work_items.find(item => item.id === work.workItemRef.id);
  let actual = existing ?? (await ctx.core.call('work', { action: 'create', ...work.operations.begin.request })).work_item;
  if (!existing && ctx.onCoreMutation) await ctx.onCoreMutation('begin.core_created');
  requireProcess(!actual.managed_by && sameValue(actual.modules, work.context.moduleIds) && sameValue(actual.scope, work.context.plannedPaths) && sameValue(actual.acceptance, work.context.criteria.map(criterion => criterion.requirement)) && actual.title === work.context.goal && actual.kind === work.kind && actual.impact === work.impact, 'CORE_WORK_LINK_CONFLICT');
  work.coreDefinition ??= coreDefinition(actual); await saveWork(ctx, work);
  requireProcess(sameValue(work.coreDefinition, coreDefinition(actual)), 'CORE_WORK_DEFINITION_CHANGED');
  for (const [from, to] of [['proposed', 'approved'], ['approved', 'implementing']]) {
    if (actual.state === from) actual = (await ctx.core.call('work', { action: 'transition', id: actual.id, to })).work_item;
  }
  requireProcess(['implementing', 'verifying'].includes(actual.state), 'CORE_WORK_NOT_ACTIVE');
  work.operations.begin.status = 'committed'; work.phase = 'active'; await saveWork(ctx, work);
  return actual;
}
export async function begin(ctx, raw, id = 'work:' + randomUUID()) {
  return withConfigurationLease(ctx,async()=>{
  requireProcess(ctx.configuration?.enabled!==false&&!ctx.configuration?.historical,'CONFIGURATION_NOT_ACTIVE');
  keys(raw, ['goal', 'expectedOutcome', 'moduleIds', 'plannedPaths', 'criteria', 'subjects', 'kind', 'impact', 'publicBehavior', 'unknowns', 'decisionRefs', 'candidateBinding', 'checkBindings'], ['goal', 'expectedOutcome', 'plannedPaths', 'criteria', 'subjects']);
  validateDefinition(id, 'id');
  return withLock(workPath(ctx, id) + '.lock', async () => {
    let work = await readJson(workPath(ctx, id), { optional: true });
    const requestDigest = sha256(raw);
    if (work) {
      requireProcess(work.requestDigest === requestDigest && sameValue(work.binding, ctx.project.binding) && work.configurationDigest === ctx.local.configurationDigest, 'BEGIN_OPERATION_CONFLICT');
      if(ctx.local.revisionId)requireProcess(work.configurationRevisionId===ctx.local.revisionId,'BEGIN_OPERATION_CONFLICT');
      if (work.phase !== 'legacy_read_only') await reconcileBegin(ctx, work);
      return { workItemId: id, phase: work.phase, baseline: work.context?.baseline ?? null, applicability: work.initialApplicability ?? [], idempotent: true };
    }
    const actual = (await ctx.core.call('work')).work_items.find(item => item.id === id);
    if (actual?.managed_by === 'pi-development-harness') {
      work = { formatVersion: ctx.local.revisionId ? 'panorama.development-work.v2' : 'panorama.development-work.v1', ...(ctx.local.revisionId ? {configurationRevisionId:ctx.local.revisionId} : {}), binding: ctx.project.binding, configurationDigest: ctx.local.configurationDigest, workItemRef: { id, definitionDigest: sha256(coreDefinition(actual)) }, phase: 'legacy_read_only', requestDigest, legacy: actual };
      await saveWork(ctx, work);
      return { workItemId: id, phase: work.phase, reason: 'Harness owns this work item; no second active task or state transition was created.' };
    }
    validateBindings(ctx, raw.checkBindings, {subjects:raw.subjects, context:{criteria:raw.criteria}});
    const inputs = await captureInputs(ctx, {checkBindings:raw.checkBindings}), projectInputs = inputs.filter(set => set.selectors.every(selector => ctx.config.roots.get(selector.rootId).kind === 'project'));
    const observed = projectInputs.length > 0 && projectInputs.every(set => set.complete);
    const baseline = { id: 'baseline:' + randomUUID(), state: observed ? 'observed' : 'unknown', capturedAt: observed ? now() : null, scope: 'declared_project_scope', inputSets: projectInputs.map(({ id, selectors, selectionDigest, snapshot }) => ({ id, selectors, selectionDigest, snapshot })), unknowns: observed ? [] : ['Start scope could not be completely captured; it must not be replaced by a later snapshot.'] };
    const context = validateDefinition({ goal: raw.goal, expectedOutcome: raw.expectedOutcome, moduleIds: raw.moduleIds ?? ctx.project.moduleBindings.map(module => module.moduleId), plannedPaths: raw.plannedPaths, changedPaths: [], criteria: raw.criteria, unknowns: raw.unknowns ?? [], decisionRefs: raw.decisionRefs ?? [], outcome: { summary: 'Development started; outcome has not been recorded.', incomplete: ['Outcome pending'], resumeNotes: ['Resume from this original baseline.'] }, baseline }, 'context');
    requireProcess(context.plannedPaths.length > 0 && context.moduleIds.every(id => ctx.project.moduleBindings.some(module => module.moduleId === id)), 'WORK_SCOPE_INVALID');
    requireProcess(Array.isArray(raw.subjects) && raw.subjects.length > 0 && raw.subjects.length <= 100, 'WORK_SUBJECTS_REQUIRED');
    const subjects = raw.subjects.map(subject => {
      keys(subject, ['id', 'kind', 'locator'], ['id', 'kind', 'locator']);
      validateDefinition({ ...subject, identityDigest: null, producedByExecutionId: null }, 'subject');
      requireProcess(subject.id !== 'process' && subject.kind !== 'process' && (!subject.locator || ctx.config.roots.has(subject.locator.rootId)), 'WORK_SUBJECT_INVALID');
      return subject;
    }); uniqueBy(subjects, 'id');
    requireProcess(context.criteria.every(criterion => criterion.subjectIds.every(id => subjects.some(subject => subject.id === id))), 'CRITERION_SUBJECT_MISSING');
    const kind = raw.kind ?? 'feature', impact = raw.impact ?? 'internal';
    requireProcess(['feature', 'bugfix', 'spike', 'refactor', 'migration', 'incident'].includes(kind) && ['mechanical', 'internal', 'behavior', 'interface', 'boundary', 'architecture'].includes(impact) && (raw.publicBehavior === undefined || raw.publicBehavior === null || typeof raw.publicBehavior === 'boolean'), 'WORK_IMPACT_INVALID');
    if (raw.candidateBinding !== undefined && raw.candidateBinding !== null) {
      keys(raw.candidateBinding, ['candidateId', 'candidateVersion', 'baselineSnapshotId'], ['candidateId', 'candidateVersion', 'baselineSnapshotId']);
      validateDefinition(raw.candidateBinding.candidateId, 'id'); validateDefinition(raw.candidateBinding.baselineSnapshotId, 'id');
      requireProcess(Number.isSafeInteger(raw.candidateBinding.candidateVersion) && raw.candidateBinding.candidateVersion >= 0, 'CANDIDATE_BINDING_INVALID');
    }
    work = { formatVersion: 'panorama.development-work.v1', binding: ctx.project.binding, configurationDigest: ctx.local.configurationDigest, workItemRef: { id, definitionDigest: sha256(workDefinition(context)) }, context, subjects, kind, impact, publicBehavior: raw.publicBehavior ?? null, candidateBinding: raw.candidateBinding ?? null, requestDigest, phase: 'starting', receipts: [], pendingReceipts: [], runs: [], operations: { begin: { id: 'begin:' + sha256(id), status: 'pending', request: { id, title: context.goal, kind, impact, modules: context.moduleIds, scope: context.plannedPaths, acceptance: context.criteria.map(criterion => criterion.requirement) } } } };
    if(ctx.local.revisionId)Object.assign(work,{formatVersion:'panorama.development-work.v2',configurationRevisionId:ctx.local.revisionId});
    if(raw.checkBindings) { requireProcess(ctx.local.revisionId, 'CHECK_BINDINGS_REQUIRE_REVISION'); Object.assign(work,{formatVersion:WORK_V3,checkBindings:raw.checkBindings}); work.checkBindingsDigest=checkBindingDigest(work); }
    // Durable intent and original baseline precede every core mutation.
    const facts = currentFacts(ctx, work, inputs);
    work.initialApplicability = ctx.config.rules.map(rule => ({ ruleId: rule.id, ...evaluateApplicability(rule.when, { facts: [...ctx.project.features, ...facts.facts], paths: facts.paths }) }));
    await saveWork(ctx, work); await reconcileBegin(ctx, work);
    return { workItemId: id, phase: work.phase, baseline, applicability: work.initialApplicability, inputGaps: inputs.flatMap(set => set.unknowns) };
  });
  });
}
export async function processStore(ctx) { return timed('storage', () => openProcessStore({ projectRoot: ctx.projectRoot, dataRoot: ctx.dataRoot, project: ctx.project, packs: ctx.packs })); }
export async function commitReceipt(ctx, work, receipt, origin) {
  validateReceipt(receipt, ctx.config, { workItemRef: work.workItemRef, candidateBinding: work.candidateBinding });
  validateReceiptBinding(work, receipt);
  const file = join(ctx.dataRoot, 'process/v1/development/pending', receipt.receiptHash + '.json');
  const existing = work.receipts.find(entry => entry.id === receipt.receiptId);
  if (existing) { requireProcess(existing.hash === receipt.receiptHash, 'RECEIPT_ID_CONFLICT'); return existing; }
  if (!work.pendingReceipts.some(entry => entry.hash === receipt.receiptHash)) {
    await writeJson(file, receipt);
    work.pendingReceipts.push({ id: receipt.receiptId, hash: receipt.receiptHash, origin, executionId: receipt.executionId });
    await saveWork(ctx, work);
  }
  const store = await processStore(ctx);
  let result;
  for (let attempt = 0; attempt < 4; attempt++) {
    try { result = await timed('storage', async () => store.importReceipt({ receipt, operationId: 'receipt:' + receipt.receiptHash, expectedRevision: (await store.list()).revision })); break; }
    catch (error) { if (error.code !== 'STORE_REVISION_CONFLICT' || attempt === 3) throw error; }
  }
  const pending = work.pendingReceipts.find(entry => entry.hash === receipt.receiptHash);
  if (ctx.onReceiptCommit) await ctx.onReceiptCommit('receipt.store_committed');
  const record = { ...pending };
  work.receipts.push(record); work.pendingReceipts = work.pendingReceipts.filter(entry => entry.hash !== receipt.receiptHash);
  await saveWork(ctx, work); return record;
}
export async function reconcileReceipts(ctx, work) {
  for (const entry of [...work.pendingReceipts]) {
    const receipt = await readJson(join(ctx.dataRoot, 'process/v1/development/pending', entry.hash + '.json'));
    requireProcess(receipt.receiptHash === entry.hash, 'PENDING_RECEIPT_CHANGED');
    await commitReceipt(ctx, work, receipt, entry.origin);
  }
}
