import { assessCollected } from './assessment.mjs';
import { fork } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { sha256, sameValue } from '../process/json.mjs';
import { validateDefinition } from '../process/schema.mjs';
import { validateReceipt } from '../process/validation.mjs';
import { requireProcess } from '../process/errors.mjs';
import { matchPath, evaluateApplicability } from '../process/applicability.mjs';
import { readJson, writeJson, now, alive, keys, withLock } from './io.mjs';
import { captureInputs, captureSubjects, captureEnvironment, makeReceipt, currentFacts, changes } from './capture.mjs';
import { workTransaction, saveWork, readWork, readWorkHeader, currentCore, reconcileBegin, reconcileReceipts, commitReceipt, processStore, runDirectory, listWorks } from './journal.mjs';
import { timed, timingEnabled } from './timing.mjs';
import { withConfigurationLease } from './configuration-state.mjs';
import { validateCheckCoverage } from './check-contracts.mjs';

async function ensureActive(ctx, work) {
  requireProcess(work.phase !== 'legacy_read_only', 'HARNESS_WORK_READ_ONLY');
  await reconcileBegin(ctx, work); await reconcileReceipts(ctx, work);
}
async function collectScan(ctx, work) {
  const core = await currentCore(ctx, work), before = await captureInputs(ctx, work), after = await captureInputs(ctx, work);
  const subjects = await captureSubjects(ctx, work, after), environment = await captureEnvironment(ctx, null, after);
  const actualPaths = changes(work, after), facts = currentFacts(ctx, work, after);
  const policy = await ctx.core.call('policy', { paths: actualPaths, ...(core.state === 'done' ? { modules: core.modules, impact: core.impact } : { work_item: core.id }) });
  const outOfScope = actualPaths.filter(path => !core.scope.some(pattern => matchPath(pattern, path)));
  if (outOfScope.length) { policy.decision = 'deny'; policy.reasons.push(...outOfScope.map(path => 'Outside work scope: ' + path)); }
  const receipt = makeReceipt(ctx, work, { before, after, subjects, environment });
  await commitReceipt(ctx, work, receipt, 'local_scan');
  const scopeComplete = after.every(set => set.complete) && work.context.baseline.state === 'observed';
  const applicability = ctx.config.rules.map(rule => ({ ruleId: rule.id, ...evaluateApplicability(rule.when, { facts: [...ctx.project.features, ...facts.facts], paths: facts.paths }) }));
  return { core, before, after, subjects, receipt, facts, policy, scopeComplete, applicability };
}
async function recoverRuns(ctx, work) {
  const outcomes = [];
  for (const run of work.runs.filter(run => run.status === 'pending')) {
    const directory = runDirectory(ctx, run.executionId);
    let result = await readJson(join(directory, 'result.json'), { optional: true });
    if (!result) {
      const status = await readJson(join(directory, 'status.json'), { optional: true });
      if (alive(status?.workerPid ?? run.workerPid) || alive(status?.businessPid) || alive(run.ownerPid) && run.ownerPid !== process.pid) { outcomes.push({ executionId: run.executionId, status: 'running', workerPid: status?.workerPid ?? run.workerPid, businessPid: status?.businessPid ?? null }); continue; }
      const prepared = await readJson(join(directory, 'prepared.json'), { optional: true });
      const error = await readJson(join(directory, 'error.json'), { optional: true });
      const after = await captureInputs(ctx, work);
      result = { executionId: run.executionId, workItemRef: work.workItemRef, before: prepared?.before ?? after, after, subjects: await captureSubjects(ctx, work, after), environment: prepared?.environmentBefore ?? await captureEnvironment(ctx, null, after), execution: { result: prepared ? 'interrupted' : 'not_run', exitCode: null, startedAt: prepared?.preparedAt ?? null, finishedAt: prepared ? now() : null }, limitations: [error?.code ?? 'COLLECTOR_TERMINATED_WITHOUT_RESULT', 'No command was restarted; original exit status is unavailable.'], logs: [] };
      await writeJson(join(directory, 'result.json'), result);
    }
    requireProcess(result.executionId === run.executionId && sameValue(result.workItemRef, work.workItemRef), 'RUN_RESULT_BINDING_MISMATCH');
    const runner = ctx.local.bootstrap.runners.find(runner => runner.id === run.runnerId);
    const receiptFile = join(directory, 'receipt.json');
    let receipt = await readJson(receiptFile, { optional: true });
    if (!receipt) {
      receipt = makeReceipt(ctx, work, { ...result, runner, subjectIds: run.subjectIds, criterionIds: run.criterionIds });
      // Freeze timestamps and content before either store commits. A retry must
      // not rebuild the same receipt ID with a different generatedAt/hash.
      await writeJson(receiptFile, receipt);
    }
    requireProcess(receipt.executionId === run.executionId && receipt.checks.some(check => check.kind === 'machine' && check.runnerRef?.id === run.runnerId && sameValue(check.subjectIds, run.subjectIds) && sameValue(check.criterionIds, run.criterionIds)), 'RUN_RECEIPT_BINDING_MISMATCH');
    await commitReceipt(ctx, work, receipt, 'local_execution');
    run.status = 'committed'; run.receiptId = receipt.receiptId; run.result = receipt.checks.find(check => check.kind === 'machine').execution.result;
    await saveWork(ctx, work);
    outcomes.push({ executionId: run.executionId, status: 'committed', result: run.result, receiptId: run.receiptId, exitCode: result.execution.exitCode, limitations: result.limitations, ...(result.timing ? {workerTiming:result.timing} : {}) });
  }
  return outcomes;
}
function targets(work, kinds, selection = {}) {
  const criterionIds = selection.criterionIds ?? work.context.criteria.filter(criterion => criterion.allowedEvidenceKinds.some(kind => kinds.includes(kind))).map(criterion => criterion.id);
  const subjectIds = selection.subjectIds ?? [...new Set(criterionIds.flatMap(id => work.context.criteria.find(criterion => criterion.id === id)?.subjectIds ?? []))];
  requireProcess(Array.isArray(criterionIds) && criterionIds.length > 0 && criterionIds.every(id => work.context.criteria.some(criterion => criterion.id === id && criterion.allowedEvidenceKinds.some(kind => kinds.includes(kind)))) && new Set(criterionIds).size === criterionIds.length, 'CHECK_CRITERIA_INVALID');
  requireProcess(Array.isArray(subjectIds) && subjectIds.length > 0 && subjectIds.every(id => work.subjects.some(subject => subject.id === id)) && new Set(subjectIds).size === subjectIds.length, 'CHECK_SUBJECTS_INVALID');
  requireProcess(criterionIds.every(id => work.context.criteria.find(criterion => criterion.id === id).subjectIds.every(id => subjectIds.includes(id))), 'CHECK_CRITERION_SCOPE_INCOMPLETE');
  return { criterionIds, subjectIds };
}

export async function check(ctx, id, runnerId, selection = {}) {
  requireProcess(ctx.configuration?.enabled!==false&&!ctx.configuration?.historical,'CONFIGURATION_NOT_ACTIVE');
  keys(selection, ['subjectIds', 'criterionIds']);
  let collector;
  const prepared = await withConfigurationLease(ctx,()=>withLock(join(ctx.dataRoot, 'process/v1/development/execution.lock'), () => workTransaction(ctx, id, async work => {
    await ensureActive(ctx, work); await recoverRuns(ctx, work);
    requireProcess(work.phase === 'active' && !work.runs.some(run => run.status === 'pending'), 'WORK_EXECUTION_BUSY');
    for (const item of await listWorks(ctx)) {
      if (item.workItemId === id || item.phase === 'legacy_read_only') continue;
      const other = await readWorkHeader(ctx, item.workItemId);
      requireProcess(!other.runs.some(run => run.status === 'pending'), 'CHECKOUT_EXECUTION_BUSY', { workItemId: item.workItemId });
    }
    const runner = ctx.local.bootstrap.runners.find(runner => runner.id === runnerId);
    requireProcess(runner, 'RUNNER_NOT_REGISTERED');
    const scope = targets(work, runner.evidenceKinds, selection), executionId = 'execution:' + randomUUID(), directory = runDirectory(ctx, executionId);
    validateCheckCoverage(ctx,work,runner,scope);
    const run = { executionId, runnerId, ...scope, status: 'pending', ownerPid: process.pid, workerPid: null, requestedAt: now() };
    work.runs.push(run); await saveWork(ctx, work);
    const requestFile = join(directory, 'request.json');
    await writeJson(requestFile, { projectRoot: ctx.projectRoot, dataRoot: ctx.dataRoot, directory, workItemId: id, workItemRef: work.workItemRef, ...run, ...(timingEnabled() ? {timings:true} : {}) });
    collector = fork(fileURLToPath(new URL('./check-worker.mjs', import.meta.url)), [requestFile], { execPath: process.execPath, windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'] });
    const completed = new Promise(resolve => { collector.once('exit', resolve); collector.once('error', resolve); });
    run.workerPid = collector.pid ?? null; await saveWork(ctx, work);
    return { executionId, completed };
  })));
  // The committed pending run and open work prevent configuration switches.
  // Release short leases while user code runs so status and recovery stay readable.
  const forward = signal => () => { if (collector.connected) collector.send({ interrupt: signal }); };
  const sigint = forward('SIGINT'), sigterm = forward('SIGTERM');
  process.once('SIGINT', sigint); process.once('SIGTERM', sigterm);
  try { await timed('executor_wait', () => prepared.completed); }
  finally { process.removeListener('SIGINT', sigint); process.removeListener('SIGTERM', sigterm); }
  return workTransaction(ctx, id, async work => {
    const recovered = await recoverRuns(ctx, work);
    const run = work.runs.find(run => run.executionId === prepared.executionId);
    return { workItemId: id, executionId: run.executionId, status: run.status, result: run.result ?? 'running', receiptId: run.receiptId ?? null,
      runDirectory: runDirectory(ctx, run.executionId), recovery: recovered };
  });
}

export async function recordReview(ctx, id, review) {
  keys(review, ['kind', 'result', 'subjectIds', 'criterionIds', 'summary', 'method', 'findings', 'limitations'], ['kind', 'result', 'summary', 'method', 'findings']);
  requireProcess(['document_review', 'visual_review'].includes(review.kind) && ['passed', 'failed'].includes(review.result), 'REVIEW_INVALID');
  validateDefinition(review.summary, 'text'); validateDefinition(review.method, 'text');
  requireProcess(Array.isArray(review.findings) && review.findings.length > 0, 'REVIEW_FINDINGS_REQUIRED');
  review.findings.forEach(finding => validateDefinition(finding, 'text'));
  return workTransaction(ctx, id, async work => {
    await ensureActive(ctx, work); await recoverRuns(ctx, work);
    requireProcess(work.phase === 'active' && !work.runs.some(run => run.status === 'pending'), 'WORK_EXECUTION_BUSY');
    const scope = targets(work, [review.kind], review), startedAt = now(), before = await captureInputs(ctx, work), after = await captureInputs(ctx, work);
    const receipt = makeReceipt(ctx, work, { before, after, subjects: await captureSubjects(ctx, work, after), environment: await captureEnvironment(ctx, null, after), execution: { result: review.result, exitCode: null, startedAt, finishedAt: now() }, ...scope, review: { ...review, summary: [review.summary, review.method, ...review.findings].join('\n') }, limitations: [...(review.limitations ?? []), 'Coding-agent declaration through a local review channel; not human authentication.'] });
    await commitReceipt(ctx, work, receipt, 'local_agent_review');
    return { receiptId: receipt.receiptId, actorKind: 'coding_agent', result: review.result };
  });
}
export async function importReceipt(ctx, id, input) {
  return workTransaction(ctx, id, async work => {
    requireProcess(work.phase !== 'legacy_read_only', 'HARNESS_WORK_READ_ONLY');
    const receipt = validateReceipt(input, ctx.config, { workItemRef: work.workItemRef, candidateBinding: work.candidateBinding });
    requireProcess(sameValue(receipt.contextSnapshot.baseline, work.context.baseline), 'WORK_BASELINE_CONFLICT');
    // Never promote producer/author fields or extensions into local attestations.
    const record = await commitReceipt(ctx, work, receipt, 'external_import');
    return { receiptId: record.id, source: receipt.producer, locallyAttested: record.origin !== 'external_import' };
  });
}


function ready(result, work) { return result.assessment.overall.processReady && result.policy.decision === 'allow' && result.scopeComplete && work.context.outcome.incomplete.length === 0 && !work.runs.some(run => run.status === 'pending'); }
async function finishCollected(ctx, work, result) {
  if (!ready(result, work)) return { ...result, finished: false, coreState: (await currentCore(ctx, work)).state };
  const operation = work.operations.finish;
  operation.status = 'pending'; operation.assessmentId = result.assessment.assessmentId; operation.assessmentHash = result.assessment.assessmentHash;
  work.phase = 'finishing'; await saveWork(ctx, work);
  let core = await currentCore(ctx, work);
  if (core.state === 'implementing') core = (await ctx.core.call('work', { action: 'transition', id: core.id, to: 'verifying' })).work_item;
  requireProcess(['verifying', 'done'].includes(core.state), 'CORE_WORK_NOT_FINISHABLE');
  // Fresh evaluation immediately before transition; a restart follows this same
  // path and never treats an old assessment or core done as current evidence.
  const refreshed = await assessCollected(ctx, work, await collectScan(ctx, work), operation.selection);
  if (!ready(refreshed, work)) { work.phase = 'active'; operation.status = 'blocked'; await saveWork(ctx, work); return { ...refreshed, finished: false, coreState: core.state }; }
  if (core.state !== 'done') core = (await ctx.core.call('work', { action: 'transition', id: core.id, to: 'done', validation: ['panorama-process-assessment:' + refreshed.assessment.assessmentHash] })).work_item;
  if (ctx.onCoreMutation) await ctx.onCoreMutation('finish.core_done');
  const confirmed = await assessCollected(ctx, work, await collectScan(ctx, work), operation.selection);
  const valid = ready(confirmed, work);
  operation.status = valid ? 'committed' : 'blocked'; operation.assessmentId = confirmed.assessment.assessmentId; operation.assessmentHash = confirmed.assessment.assessmentHash;
  work.phase = valid ? 'finished' : 'finishing'; await saveWork(ctx, work);
  return { ...confirmed, finished: valid, coreState: core.state, completionOperation: operation.id };
}
export async function inspect(ctx, id, command, input = {}) {
  if (!id && command === 'resume') return { works: await listWorks(ctx), coreWorks: (await ctx.core.call('work')).work_items.map(work => ({ id: work.id, title: work.title, state: work.state, managedBy: work.managed_by ?? 'structure-core' })) };
  return workTransaction(ctx, id, async work => {
    if (work.phase === 'legacy_read_only') return { workItemId: id, phase: work.phase, legacy: (await ctx.core.call('work', { action: 'get', id })).work_item, processReady: false };
    await ensureActive(ctx, work);
    const recovery = await recoverRuns(ctx, work);
    if (command === 'finish') {
      keys(input, ['outcome', 'selection', 'decisionRefs'], ['outcome']);
      work.context = validateDefinition({ ...work.context, outcome: input.outcome, decisionRefs: [...new Set([...work.context.decisionRefs, ...(input.decisionRefs ?? [])])] }, 'context');
      requireProcess(!work.runs.some(run => run.status === 'pending'), 'WORK_EXECUTION_BUSY');
      work.operations.finish = { id: work.operations.finish?.id ?? 'finish:' + randomUUID(), status: 'pending', selection: input.selection ?? null };
      await saveWork(ctx, work);
    } else keys(input, ['selection']);
    const scan = await collectScan(ctx, work);
    const summary = { workItemId: id, phase: work.phase, coreState: scan.core.state, goal: work.context.goal, expectedOutcome: work.context.expectedOutcome, baseline: work.context.baseline, changedPaths: scan.receipt.contextSnapshot.changedPaths, plannedPaths: work.context.plannedPaths, criteria: work.context.criteria, outcome: work.context.outcome, policy: scan.policy, scopeComplete: scan.scopeComplete, applicability: scan.applicability, recovery, receiptId: scan.receipt.receiptId };
    if (command === 'scan') return summary;
    const selection = command === 'resume' && !input.selection && work.operations.finish ? work.operations.finish.selection : input.selection;
    const result = await assessCollected(ctx, work, scan, selection);
    if (command === 'finish' || command === 'resume' && work.operations.finish?.status === 'pending') return { ...summary, ...await finishCollected(ctx, work, result), phase: work.phase };
    return { ...summary, ...result, currentProcessReady: ready(result, work), next: work.runs.some(run => run.status === 'pending') ? 'An owned collector is still running; do not start a duplicate command.' : result.assessment.overall.processReady ? 'Record outcome and finish, or continue with the saved scope.' : 'Address the reported missing/stale evidence; check commands are never restarted by resume.' };
  });
}
