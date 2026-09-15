import { validateDocument } from './schema.mjs';
import { bodyHash, sameValue, sha256, workDefinition } from './json.mjs';
import { requireProcess } from './errors.mjs';
import { matchPath } from './applicability.mjs';

export function uniqueBy(rows, key, code = 'DUPLICATE_ID') {
  const map = new Map();
  for (const row of rows) { const id = typeof key === 'function' ? key(row) : row[key]; requireProcess(!map.has(id), code); map.set(id, row); }
  return map;
}
const subset = (ids, map, code = 'REFERENCE_MISSING') => ids.forEach(id => requireProcess(map.has(id), code));
const ordered = (a, b) => a.rootId < b.rootId ? -1 : a.rootId > b.rootId ? 1 : a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
export const packReference = pack => ({ id: pack.id, version: pack.version, digest: sha256(pack) });

export function checkRedaction(value) {
  const forbidden = new Set(['stdout', 'stderr', 'rawlog', 'rawlogs', 'sourcebody', 'sourcecode', 'environmentvalues', 'authorization', 'cookie', 'password', 'apikey', 'accesstoken', 'secret']);
  function inspect(item) {
    if (typeof item === 'string') {
      requireProcess(!/-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/-]{16,}|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})|https?:\/\/[^\s/]+:[^\s/]+@/i.test(item), 'REDACTION_REQUIRED');
    } else if (item && typeof item === 'object') {
      for (const [key, child] of Object.entries(item)) {
        requireProcess(!forbidden.has(key.replace(/[-_]/g, '').toLowerCase()), 'REDACTION_REQUIRED');
        inspect(child);
      }
    }
  }
  inspect(value);
}

export function validateConfiguration(projectInput, packInputs, { mode = 'record' } = {}) {
  requireProcess(['record', 'contract_example'].includes(mode), 'MODE_INVALID');
  const project = validateDocument(projectInput, 'panorama.process-project.v1');
  requireProcess(project.purpose === mode, 'PURPOSE_MISMATCH');
  requireProcess(Array.isArray(packInputs) && packInputs.length > 0 && packInputs.length <= 20, 'PACKS_REQUIRED');
  const packs = packInputs.map(pack => validateDocument(pack, 'standard-pack.v0.2'));
  const byPack = uniqueBy(packs, 'id');
  uniqueBy(project.rulePacks, 'id');
  requireProcess(project.rulePacks.length === packs.length, 'PACK_BINDING_MISMATCH');
  for (const ref of project.rulePacks) requireProcess(byPack.has(ref.id) && sameValue(ref, packReference(byPack.get(ref.id))), 'PACK_BINDING_MISMATCH');
  const rules = packs.flatMap(pack => pack.rules);
  requireProcess(rules.length <= 1000, 'RULE_LIMIT');
  uniqueBy(rules, 'id', 'RULE_ID_CONFLICT');
  for (const rule of rules) uniqueBy(rule.evidenceRequirements, 'id');
  const roots = uniqueBy(project.inputRoots, 'id');
  requireProcess(project.inputRoots.filter(root => root.kind === 'project').length === 1, 'PROJECT_ROOT_AMBIGUOUS');
  uniqueBy(project.moduleBindings, 'moduleId'); uniqueBy(project.runnerRefs, 'id');
  checkRedaction(project); packs.forEach(checkRedaction);
  return { project, packs, rules, roots, mode };
}

function validateSnapshot(set, snapshot, roots) {
  requireProcess(set.selectionDigest === sha256(set.selectors), 'SELECTION_DIGEST_MISMATCH');
  subset(set.selectors.map(selector => selector.rootId), roots, 'INPUT_ROOT_UNREGISTERED');
  uniqueBy(snapshot.files, file => `${file.rootId}/${file.path}`, 'FILE_ID_CONFLICT');
  requireProcess(sameValue(snapshot.files, [...snapshot.files].sort(ordered)), 'FILE_ORDER_INVALID');
  requireProcess(snapshot.digest === sha256({ selectionDigest: set.selectionDigest, files: snapshot.files }), 'INPUT_DIGEST_MISMATCH');
  for (const file of snapshot.files) {
    requireProcess(roots.has(file.rootId), 'INPUT_ROOT_UNREGISTERED');
    const selectors = set.selectors.filter(selector => selector.rootId === file.rootId && selector.role === file.role);
    requireProcess(selectors.some(selector => selector.include.some(pattern => matchPath(pattern, file.path)) && !selector.exclude.some(pattern => matchPath(pattern, file.path))), 'FILE_OUTSIDE_SELECTION');
  }
}

export function validateReceipt(input, configuration, { workItemRef, candidateBinding } = {}) {
  const receipt = validateDocument(input, 'panorama.process-receipt.v1');
  const { project, roots, mode } = configuration;
  requireProcess(receipt.purpose === mode, 'PURPOSE_MISMATCH');
  requireProcess(sameValue(receipt.binding, project.binding), 'PROJECT_BINDING_MISMATCH');
  requireProcess(sameValue(receipt.rulePacks, project.rulePacks), 'PACK_BINDING_MISMATCH');
  requireProcess(receipt.receiptHash === bodyHash(receipt, 'receiptHash'), 'RECEIPT_HASH_MISMATCH');
  requireProcess(receipt.workItemRef.definitionDigest === sha256(workDefinition(receipt.contextSnapshot)), 'WORK_DEFINITION_HASH_MISMATCH');
  if (workItemRef) requireProcess(sameValue(receipt.workItemRef, workItemRef), 'WORK_BINDING_MISMATCH');
  if (candidateBinding !== undefined) requireProcess(sameValue(receipt.candidateBinding, candidateBinding), 'CANDIDATE_BINDING_MISMATCH');
  checkRedaction(receipt);
  const subjects = uniqueBy(receipt.subjects, 'id'), checks = uniqueBy(receipt.checks, 'id'), evidence = uniqueBy(receipt.evidence, 'id');
  const inputs = uniqueBy(receipt.inputSets, 'id'), criteria = uniqueBy(receipt.contextSnapshot.criteria, 'id');
  const modules = uniqueBy(project.moduleBindings, 'moduleId'), runners = uniqueBy(project.runnerRefs, 'id');
  subset(receipt.contextSnapshot.moduleIds, modules, 'MODULE_UNREGISTERED');
  for (const inputSet of inputs.values()) for (const snapshot of [inputSet.before, inputSet.after]) validateSnapshot(inputSet, snapshot, roots);
  const baseline = receipt.contextSnapshot.baseline;
  uniqueBy(baseline.inputSets, 'id');
  for (const set of baseline.inputSets) {
    validateSnapshot(set, set.snapshot, roots);
    requireProcess(set.selectors.every(selector => roots.get(selector.rootId).kind === 'project'), 'BASELINE_SCOPE_INVALID');
  }
  for (const subject of subjects.values()) if (subject.locator) requireProcess(roots.has(subject.locator.rootId), 'INPUT_ROOT_UNREGISTERED');
  for (const criterion of criteria.values()) subset(criterion.subjectIds, subjects);
  const generated = Date.parse(receipt.generatedAt);
  for (const check of checks.values()) {
    subset(check.subjectIds, subjects); subset(check.inputSetIds, inputs); subset(check.criterionIds, criteria); subset(check.evidenceIds, evidence);
    if (check.kind === 'machine') requireProcess(check.inputSetIds.length > 0 && check.subjectIds.length > 0, 'CHECK_SCOPE_MISSING');
    if (check.runnerRef) requireProcess(runners.has(check.runnerRef.id) && runners.get(check.runnerRef.id).definitionDigest === check.runnerRef.definitionDigest, 'RUNNER_BINDING_MISMATCH');
    if (check.execution.result !== 'not_run') {
      const start = Date.parse(check.execution.startedAt), finish = Date.parse(check.execution.finishedAt);
      requireProcess(start <= finish && finish <= generated, 'EXECUTION_TIME_INVALID');
      if (baseline.state === 'observed') requireProcess(Date.parse(baseline.capturedAt) <= start, 'BASELINE_TIME_INVALID');
    }
    for (const id of check.evidenceIds) requireProcess(evidence.get(id).checkId === check.id, 'CHECK_EVIDENCE_MISMATCH');
  }
  for (const item of evidence.values()) {
    requireProcess(checks.has(item.checkId), 'REFERENCE_MISSING');
    const check = checks.get(item.checkId);
    requireProcess(check.evidenceIds.includes(item.id), 'CHECK_EVIDENCE_MISMATCH');
    requireProcess(item.subjectIds.every(id => check.subjectIds.includes(id)) && item.inputSetIds.every(id => check.inputSetIds.includes(id)) && item.criterionIds.every(id => check.criterionIds.includes(id)), 'EVIDENCE_SCOPE_MISMATCH');
    requireProcess(sameValue(item.author, check.author), 'EVIDENCE_AUTHOR_MISMATCH');
    if (check.runnerRef) requireProcess(runners.get(check.runnerRef.id).evidenceKinds.includes(item.kind), 'RUNNER_EVIDENCE_KIND_MISMATCH');
    requireProcess(Date.parse(item.generatedAt) <= generated, 'EVIDENCE_TIME_INVALID');
  }
  for (const enforcement of receipt.enforcement) subset(enforcement.evidenceIds, evidence);
  return receipt;
}

export function aggregateAssessment(rules, criteria) {
  const mandatory = rules.filter(rule => ['required', 'conditional'].includes(rule.strength));
  const required = criteria.filter(criterion => criterion.required);
  const blockingRules = mandatory.filter(rule => rule.applicability.status !== 'not_applicable' && (rule.satisfaction !== 'satisfied' || rule.freshness?.status !== 'current'));
  const blockingCriteria = required.filter(criterion => criterion.satisfaction !== 'satisfied' || criterion.freshness.status !== 'current');
  const status = blockingRules.some(rule => rule.satisfaction === 'conflict') || blockingCriteria.some(criterion => criterion.satisfaction === 'conflict') ? 'conflict' :
    blockingCriteria.length || blockingRules.some(rule => rule.applicability.status === 'applicable') ? 'blocked' : blockingRules.length ? 'unknown' : 'satisfied';
  return { status, processReady: status === 'satisfied', blockingRuleIds: blockingRules.map(rule => rule.ruleId), blockingCriterionIds: blockingCriteria.map(criterion => criterion.criterionId), totalRules: rules.length,
    applicableRules: rules.filter(rule => rule.applicability.status === 'applicable').length, notApplicableRules: rules.filter(rule => rule.applicability.status === 'not_applicable').length,
    unknownRules: rules.filter(rule => rule.applicability.status === 'unknown').length, conflictingRules: rules.filter(rule => rule.applicability.status === 'conflict').length };
}

// Structural import validation does not certify an external assessment's reasoning.
export function validateAssessment(input, configuration, receipts) {
  const assessment = validateDocument(input, 'panorama.process-assessment.v1');
  requireProcess(Array.isArray(receipts) && receipts.length > 0 && receipts.length <= 100, 'RECEIPTS_REQUIRED');
  receipts = receipts.map(receipt => validateReceipt(receipt, configuration, { workItemRef: assessment.workItemRef }));
  requireProcess(assessment.purpose === configuration.mode, 'PURPOSE_MISMATCH');
  requireProcess(sameValue(assessment.binding, configuration.project.binding), 'PROJECT_BINDING_MISMATCH');
  requireProcess(sameValue(assessment.rulePacks, configuration.project.rulePacks), 'PACK_BINDING_MISMATCH');
  requireProcess(assessment.assessmentHash === bodyHash(assessment, 'assessmentHash'), 'ASSESSMENT_HASH_MISMATCH');
  checkRedaction(assessment);
  const byReceipt = uniqueBy(receipts, 'receiptId'); uniqueBy(assessment.receiptRefs, 'receiptId');
  requireProcess(receipts.length === assessment.receiptRefs.length, 'RECEIPT_COVERAGE_MISMATCH');
  for (const ref of assessment.receiptRefs) {
    const receipt = byReceipt.get(ref.receiptId);
    requireProcess(receipt?.receiptHash === ref.receiptHash && sameValue(receipt.workItemRef, assessment.workItemRef), 'RECEIPT_BINDING_MISMATCH');
  }
  const rules = uniqueBy(configuration.rules, 'id'), criteria = uniqueBy(receipts[0].contextSnapshot.criteria, 'id');
  uniqueBy(assessment.rules, 'ruleId'); uniqueBy(assessment.criteria, 'criterionId');
  requireProcess(assessment.rules.length === rules.size && assessment.criteria.length === criteria.size, 'ASSESSMENT_COVERAGE_MISMATCH');
  uniqueBy(assessment.currentInputObservations, 'inputSetId'); uniqueBy(assessment.currentSubjectObservations, 'subjectId');
  const inputIds = new Set(receipts.flatMap(receipt => receipt.inputSets.map(set => set.id))), subjectIds = new Set(receipts.flatMap(receipt => receipt.subjects.map(subject => subject.id)));
  subset(assessment.currentInputObservations.map(row => row.inputSetId), inputIds);
  subset(assessment.currentSubjectObservations.map(row => row.subjectId), subjectIds);
  requireProcess(assessment.currentInputObservations.length === inputIds.size && assessment.currentSubjectObservations.length === subjectIds.size, 'OBSERVATION_COVERAGE_MISMATCH');
  for (const row of [...assessment.rules, ...assessment.criteria]) {
    if (row.ruleId) {
      const rule = rules.get(row.ruleId);
      requireProcess(rule && row.ruleVersion === rule.version && row.strength === rule.strength, 'RULE_BINDING_MISMATCH');
    } else {
      const criterion = criteria.get(row.criterionId);
      requireProcess(criterion && row.criterionVersion === criterion.version && row.required === criterion.required, 'CRITERION_BINDING_MISMATCH');
    }
    uniqueBy(row.checkRefs, ref => ref.receiptId + '/' + ref.checkId);
    uniqueBy(row.evidenceRefs, ref => ref.receiptId + '/' + ref.evidenceId);
    for (const ref of row.checkRefs) {
      const receipt = byReceipt.get(ref.receiptId);
      requireProcess(receipt?.receiptHash === ref.receiptHash && receipt.checks.some(check => check.id === ref.checkId), 'CHECK_REFERENCE_MISMATCH');
    }
    for (const ref of row.evidenceRefs) {
      const receipt = byReceipt.get(ref.receiptId), evidence = receipt?.evidence.find(item => item.id === ref.evidenceId);
      requireProcess(receipt?.receiptHash === ref.receiptHash && evidence, 'EVIDENCE_REFERENCE_MISMATCH');
      requireProcess(row.checkRefs.some(check => check.receiptId === ref.receiptId && check.checkId === evidence.checkId), 'ASSESSMENT_CHECK_MISSING');
    }
  }
  requireProcess(sameValue(assessment.overall, aggregateAssessment(assessment.rules, assessment.criteria)), 'ASSESSMENT_AGGREGATE_MISMATCH');
  return assessment;
}
