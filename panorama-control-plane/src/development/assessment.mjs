// Shared observation and deterministic assessment; no execution or core transitions.
import { randomUUID } from 'node:crypto';
import { validateDefinition } from '../process/schema.mjs';
import { requireProcess } from '../process/errors.mjs';
import { now, keys } from './io.mjs';
import { captureInputs, captureSubjects, captureEnvironment } from './capture.mjs';
import { processStore, saveWork } from './journal.mjs';
import { timed } from './timing.mjs';
import { validateReceiptBinding } from './check-contracts.mjs';

export async function prepareAssessment(ctx, work, scan, selection, extraReceipts = []) {
  const store = await processStore(ctx);
  const known = work.receipts.filter(entry => entry.origin !== 'local_scan');
  let selected = known;
  if (selection) {
    keys(selection, ['receiptIds', 'reason'], ['receiptIds', 'reason']); validateDefinition(selection.reason, 'text');
    requireProcess(Array.isArray(selection.receiptIds) && new Set(selection.receiptIds).size === selection.receiptIds.length && selection.receiptIds.every(id => known.some(entry => entry.id === id)), 'RECEIPT_SELECTION_INVALID');
    selected = known.filter(entry => selection.receiptIds.includes(entry.id));
  }
  const receipts = [...await Promise.all(selected.map(entry => store.getReceipt(entry.id))), ...extraReceipts, scan.receipt];
  const receiptIds = receipts.map(receipt => receipt.receiptId);
  const inputObservations = new Map(), subjectObservations = new Map(), environmentObservations = [], verifiedEvidence = [];
  for (const receipt of receipts) {
    validateReceiptBinding(work, receipt);
    const observed = await timed('input_observation', () => ctx.registry.observe(receipt, now()));
    for (const row of observed.currentInputObservations) inputObservations.set(row.inputSetId, row);
    for (const row of observed.currentSubjectObservations) subjectObservations.set(row.subjectId, row);
    const meta = receipt.receiptHash === scan.receipt.receiptHash ? { hash: receipt.receiptHash, origin: 'local_scan' } : work.receipts.find(entry => entry.id === receipt.receiptId);
    const external = extraReceipts.some(item => item.receiptHash === receipt.receiptHash);
    if (!external && meta && meta.hash === receipt.receiptHash && ['local_scan', 'local_execution', 'local_agent_review'].includes(meta.origin)) {
      for (const evidence of receipt.evidence) verifiedEvidence.push({ receiptId: receipt.receiptId, receiptHash: receipt.receiptHash, evidenceId: evidence.id, actorId: evidence.author.id, actorKind: evidence.author.kind });
    }
    const runnerRef = receipt.checks.find(check => check.kind === 'machine')?.runnerRef;
    if (runnerRef) {
      const runner = ctx.local.bootstrap.runners.find(runner => runner.id === runnerRef.id);
      const environment = ctx.configuration?.withdrawnRunners.includes(runnerRef.id) ? {identityDigest:null} : await captureEnvironment(ctx, runner, scan.after, work);
      environmentObservations.push({ executionId: receipt.executionId, identityDigest: environment.identityDigest, state: environment.identityDigest ? 'observed' : 'unknown', observedAt: now() });
    }
  }
  // Independently recomputed non-file identities. Never echo imported digests.
  const currentSubjects = await captureSubjects(ctx, work, await captureInputs(ctx, work));
  for (const subject of currentSubjects) if (subjectObservations.has(subject.id) && !subject.locator) subjectObservations.set(subject.id, { subjectId: subject.id, identityDigest: subject.identityDigest, state: subject.identityDigest ? 'observed' : 'unknown', observedAt: now() });
  const evaluation = { workItemRef: work.workItemRef, candidateBinding: work.candidateBinding, assessedAt: now(), ...scan.facts, currentInputObservations: [...inputObservations.values()], currentSubjectObservations: [...subjectObservations.values()], environmentObservations, verifiedEvidence, verifiedObjectDigests: [] };
  return { store, receipts, receiptIds, evaluation };
}

export async function assessCollected(ctx, work, scan, selection, prepared = null) {
  const { store, receiptIds, evaluation } = prepared ?? await prepareAssessment(ctx, work, scan, selection);
  const operationId = 'assess:' + randomUUID();
  let stored;
  for (let attempt = 0; attempt < 4; attempt++) {
    try { stored = await timed('assessment', async () => store.assess({ receiptIds, evaluation, operationId, expectedRevision: (await store.list()).revision })); break; }
    catch (error) { if (error.code !== 'STORE_REVISION_CONFLICT' || attempt === 3) throw error; }
  }
  const assessment = await store.getAssessment(stored.id);
  const result = { assessment, policy: scan.policy, scopeComplete: scan.scopeComplete, selectedReceiptIds: receiptIds, selectionReason: selection?.reason ?? 'All collected/imported checks plus the current process scan; no contradictory check was dropped.' };
  work.lastAssessment = { id: assessment.assessmentId, hash: assessment.assessmentHash, selectedReceiptIds: receiptIds, selectionReason: result.selectionReason, recordedAt: assessment.assessedAt };
  await saveWork(ctx, work);
  return result;
}
