import { validateDefinition } from './schema.mjs';
import { processValue, sha256, bodyHash, sameValue } from './json.mjs';
import { validateConfiguration, validateReceipt, validateAssessment, aggregateAssessment, uniqueBy } from './validation.mjs';
import { evaluateApplicability } from './applicability.mjs';
import { requireProcess } from './errors.mjs';

export const EVALUATOR_VERSION = 'panorama-process-1.0.0';
const supported = new Set(['process.identity', 'process.scope', 'process.applicability', 'process.execution-binding', 'process.criteria-evidence', 'process.outcome', 'process.retained-artifact', 'process.external-input', 'process.interface', 'process.review']);
const distinct = values => [...new Set(values)].sort();
const mandatory = criterion => criterion.required;
const unknownFreshness = reason => ({ status: 'unknown', scope: 'declared_inputs_and_subjects', changedIds: [], unknowns: [reason] });
function mergeFreshness(values) {
  if (!values.length) return unknownFreshness('NO_MATCHING_EVIDENCE');
  const changedIds = distinct(values.flatMap(value => value.changedIds)), unknowns = distinct(values.flatMap(value => value.unknowns));
  return { status: changedIds.length ? 'stale' : unknowns.length ? 'unknown' : 'current', scope: 'declared_inputs_and_subjects', changedIds, unknowns };
}
const evidenceRef = entry => ({ receiptId: entry.receipt.receiptId, receiptHash: entry.receipt.receiptHash, evidenceId: entry.evidence.id });
const checkRef = entry => ({ receiptId: entry.receipt.receiptId, receiptHash: entry.receipt.receiptHash, checkId: entry.check.id });
const dedupe = (values, key) => [...new Map(values.map(value => [key(value), value])).values()];
const referenceKey = ref => ref.receiptId + '/' + (ref.evidenceId ?? ref.checkId);

function scopeGaps(receipt, project) {
  const baseline = receipt.contextSnapshot.baseline;
  if (baseline.state !== 'observed') return ['WORK_BASELINE_UNKNOWN'];
  const projectRoots = new Set(project.inputRoots.filter(root => root.kind === 'project').map(root => root.id));
  const changes = new Set(), gaps = [];
  const baselineSets = new Map(baseline.inputSets.map(set => [set.id, set]));
  for (const set of baseline.inputSets) if (!receipt.inputSets.some(input => input.id === set.id)) gaps.push('BASELINE_CURRENT_SCOPE_UNOBSERVED:' + set.id);
  for (const input of receipt.inputSets.filter(set => set.selectors.some(selector => projectRoots.has(selector.rootId)))) {
    const before = baselineSets.get(input.id);
    if (!before || before.selectionDigest !== input.selectionDigest) { gaps.push('BASELINE_SELECTION_UNCOVERED:' + input.id); continue; }
    const initial = new Map(before.snapshot.files.map(file => [file.rootId + '/' + file.path, file]));
    const current = new Map(input.after.files.map(file => [file.rootId + '/' + file.path, file]));
    for (const key of new Set([...initial.keys(), ...current.keys()])) {
      const left = initial.get(key), right = current.get(key), file = left ?? right;
      if (!projectRoots.has(file.rootId)) continue;
      if ([left, right].some(value => value?.state === 'unreadable')) gaps.push('BASELINE_INPUT_UNREADABLE:' + input.id);
      if (!left || !right || left.state !== right.state || left.digest !== right.digest) changes.add(file.path);
    }
  }
  if (!sameValue([...changes].sort(), [...receipt.contextSnapshot.changedPaths].sort())) gaps.push('CHANGED_PATHS_NOT_RECONCILED');
  return distinct(gaps);
}

export function assessProcess(request) {
  request = processValue(request, { maxBytes: 16 * 1024 * 1024, maxDepth: 24 });
  requireProcess(request && typeof request === 'object' && !Array.isArray(request), 'ASSESSMENT_REQUEST_INVALID');
  const mode = request.mode ?? 'record';
  const config = validateConfiguration(request.project, request.packs, { mode });
  const workItemRef = validateDefinition(request.workItemRef, 'workRef');
  const candidateBinding = request.candidateBinding ?? null;
  const assessedAt = validateDefinition(request.assessedAt, 'timestamp');
  requireProcess(Array.isArray(request.receipts) && request.receipts.length > 0 && request.receipts.length <= 100, 'RECEIPTS_REQUIRED');
  const receipts = request.receipts.map(input => validateReceipt(input, config, { workItemRef, candidateBinding })).sort((a, b) => a.receiptId < b.receiptId ? -1 : a.receiptId > b.receiptId ? 1 : 0);
  uniqueBy(receipts, 'receiptId', 'RECEIPT_ID_CONFLICT');
  const startBaseline = receipts[0].contextSnapshot.baseline;
  requireProcess(receipts.every(receipt => sameValue(receipt.contextSnapshot.baseline, startBaseline)), 'WORK_BASELINE_CONFLICT');
  requireProcess(receipts.every(receipt => Date.parse(receipt.generatedAt) <= Date.parse(assessedAt)), 'ASSESSMENT_TIME_INVALID');
  const current = [...receipts].sort((a, b) => Date.parse(b.generatedAt) - Date.parse(a.generatedAt))[0];
  // Trusted caller context is separate from imported receipt content. In production
  // P2/other collectors must verify these attestations before supplying them here.
  const context = processValue({ facts: request.facts ?? [], paths: request.paths ?? [],
    inputObservations: request.currentInputObservations ?? [], subjectObservations: request.currentSubjectObservations ?? [],
    environmentObservations: request.environmentObservations ?? [], verifiedEvidence: request.verifiedEvidence ?? [], verifiedObjectDigests: request.verifiedObjectDigests ?? [] });
  for (const values of Object.values(context)) requireProcess(Array.isArray(values) && values.length <= 1000, 'EVALUATION_CONTEXT_INVALID');
  const inputs = uniqueBy(context.inputObservations, 'inputSetId'), subjects = uniqueBy(context.subjectObservations, 'subjectId');
  const environment = uniqueBy(context.environmentObservations, 'executionId');
  const inputDefinitions = new Map(), subjectDefinitions = new Map();
  for (const receipt of receipts) {
    requireProcess(sameValue(receipt.contextSnapshot.criteria, current.contextSnapshot.criteria), 'CRITERIA_CONFLICT');
    for (const input of receipt.inputSets) inputDefinitions.set(input.id, input);
    for (const subject of receipt.subjects) {
      if (subjectDefinitions.has(subject.id)) {
        const previous = subjectDefinitions.get(subject.id);
        requireProcess(previous.kind === subject.kind && sameValue(previous.locator, subject.locator), 'SUBJECT_ID_CONFLICT');
      }
      subjectDefinitions.set(subject.id, subject);
    }
  }
  for (const id of inputs.keys()) requireProcess(inputDefinitions.has(id), 'OBSERVATION_REFERENCE_MISSING');
  for (const id of subjects.keys()) requireProcess(subjectDefinitions.has(id), 'OBSERVATION_REFERENCE_MISSING');
  for (const [id, input] of inputDefinitions) if (!inputs.has(id)) inputs.set(id, { inputSetId: id, selectionDigest: input.selectionDigest, digest: null, state: 'unknown', observedAt: assessedAt });
  for (const id of subjectDefinitions.keys()) if (!subjects.has(id)) subjects.set(id, { subjectId: id, identityDigest: null, state: 'unknown', observedAt: assessedAt });
  for (const observation of [...inputs.values(), ...subjects.values(), ...environment.values()]) {
    validateDefinition(observation.observedAt, 'timestamp');
    requireProcess(Date.parse(observation.observedAt) <= Date.parse(assessedAt), 'OBSERVATION_FROM_FUTURE');
  }
  for (const observation of environment.values()) requireProcess(['observed', 'unknown'].includes(observation.state) && (observation.state === 'observed' ? /^[a-f0-9]{64}$/.test(observation.identityDigest) : observation.identityDigest === null), 'ENVIRONMENT_OBSERVATION_INVALID');
  requireProcess(context.verifiedObjectDigests.every(digest => typeof digest === 'string' && /^[a-f0-9]{64}$/.test(digest)), 'OBJECT_DIGEST_INVALID');
  const allEvidence = receipts.flatMap(receipt => receipt.evidence.map(evidence => ({ receipt, evidence, check: receipt.checks.find(check => check.id === evidence.checkId) })));
  uniqueBy(context.verifiedEvidence, entry => `${entry.receiptHash}/${entry.evidenceId}`, 'ATTESTATION_CONFLICT');
  for (const attestation of context.verifiedEvidence) {
    const entry = allEvidence.find(value => value.receipt.receiptId === attestation.receiptId && value.receipt.receiptHash === attestation.receiptHash && value.evidence.id === attestation.evidenceId);
    requireProcess(entry && attestation.actorId === entry.evidence.author.id && attestation.actorKind === entry.evidence.author.kind, 'ATTESTATION_BINDING_MISMATCH');
  }
  function freshness(entry) {
    const changedIds = [], unknowns = [], { receipt, check, evidence } = entry;
    // A narrower evidence label cannot erase dependencies declared by its check.
    for (const id of distinct([...evidence.inputSetIds, ...check.inputSetIds])) {
      const input = receipt.inputSets.find(set => set.id === id), now = inputs.get(id);
      if (input.before.digest !== input.after.digest) changedIds.push(id);
      if ([...input.before.files, ...input.after.files].some(file => file.state === 'unreadable')) unknowns.push('INPUT_UNREADABLE:' + id);
      if (!now || now.state !== 'readable' || now.digest === null || Date.parse(now.observedAt) < Date.parse(check.execution.finishedAt)) unknowns.push('INPUT_CURRENT_UNKNOWN:' + id);
      else if (now.selectionDigest !== input.selectionDigest || now.digest !== input.after.digest) changedIds.push(id);
    }
    for (const id of evidence.subjectIds) {
      const subject = receipt.subjects.find(value => value.id === id), now = subjects.get(id);
      if (subject.identityDigest === null || !now || now.state !== 'observed' || now.identityDigest === null || Date.parse(now.observedAt) < Date.parse(check.execution.finishedAt)) unknowns.push('SUBJECT_CURRENT_UNKNOWN:' + id);
      else if (now.identityDigest !== subject.identityDigest) changedIds.push(id);
    }
    if (check.kind === 'machine' && mode !== 'contract_example') {
      const now = environment.get(receipt.executionId);
      if (!check.runnerRef) unknowns.push('RUNNER_UNBOUND');
      if (!receipt.environment.identityDigest || !now || now.state !== 'observed' || !now.identityDigest || Date.parse(now.observedAt) < Date.parse(check.execution.finishedAt)) unknowns.push('ENVIRONMENT_CURRENT_UNKNOWN');
      else if (now.identityDigest !== receipt.environment.identityDigest) changedIds.push(receipt.executionId);
    }
    return { status: changedIds.length ? 'stale' : unknowns.length ? 'unknown' : 'current', scope: 'declared_inputs_and_subjects', changedIds: distinct(changedIds), unknowns: distinct(unknowns) };
  }
  const entries = allEvidence.map(entry => ({ ...entry, freshness: freshness(entry), trusted: mode === 'contract_example' || context.verifiedEvidence.some(attestation => attestation.receiptHash === entry.receipt.receiptHash && attestation.evidenceId === entry.evidence.id) }));

  function proof(requirements, extraFilter = () => true) {
    const selected = [], missing = [], states = [], coverageGaps = [];
    for (const requirement of requirements) {
      if (!requirement.targets.length) { missing.push('REQUIRED_SUBJECTS_UNDEFINED'); coverageGaps.push('REQUIRED_SUBJECTS_UNDEFINED'); states.push('insufficient'); continue; }
      for (const subjectId of requirement.targets) {
        const candidates = entries.filter(entry => extraFilter(entry) && entry.evidence.subjectIds.includes(subjectId) && requirement.allowedKinds.includes(entry.evidence.kind) &&
          requirement.acceptedActorKinds.includes(entry.evidence.author.kind) && (!requirement.criterionId || entry.evidence.criterionIds.includes(requirement.criterionId)));
        const usable = candidates.filter(entry => entry.trusted && entry.freshness.status === 'current' && (!entry.evidence.objectDigest || context.verifiedObjectDigests.includes(entry.evidence.objectDigest)));
        const passed = usable.filter(entry => entry.check.execution.result === 'passed'), failed = usable.filter(entry => entry.check.execution.result === 'failed');
        if (passed.length && failed.length) { states.push('conflict'); selected.push(...passed, ...failed); missing.push('CONFLICTING_CURRENT_EVIDENCE:' + subjectId); }
        else if (failed.length) { states.push('unsatisfied'); selected.push(...failed); missing.push('CHECK_FAILED:' + subjectId); }
        else if (passed.length >= requirement.minCount) { states.push('satisfied'); selected.push(...passed); }
        else {
          states.push('insufficient'); selected.push(...candidates);
          if (!candidates.length) coverageGaps.push('EVIDENCE_MISSING:' + subjectId);
          missing.push(candidates.some(entry => !entry.trusted) ? 'EVIDENCE_SOURCE_UNVERIFIED:' + subjectId : candidates.length ? 'EVIDENCE_NOT_USABLE:' + subjectId : 'EVIDENCE_MISSING:' + subjectId);
        }
      }
    }
    const unique = dedupe(selected, entry => `${entry.receipt.receiptHash}/${entry.evidence.id}`);
    const satisfaction = states.includes('conflict') ? 'conflict' : states.includes('unsatisfied') ? 'unsatisfied' : states.includes('insufficient') || !states.length ? 'insufficient' : 'satisfied';
    const fresh = mergeFreshness([...unique.map(entry => entry.freshness), ...coverageGaps.map(unknownFreshness)]);
    return { checkRefs: dedupe(unique.map(checkRef), referenceKey), evidenceRefs: unique.map(evidenceRef), freshness: fresh, satisfaction,
      reason: ({ satisfied: '指定对象、证据类型和来源的当前证据足够。', unsatisfied: '当前有效的检查证据明确未满足要求。', insufficient: '缺少可使用且覆盖要求的证据。', conflict: '当前证据存在未解决的相反结论。' })[satisfaction], missing: distinct(missing) };
  }
  const criteria = current.contextSnapshot.criteria.map(criterion => ({ criterionId: criterion.id, criterionVersion: criterion.version, required: criterion.required,
    ...proof([{ targets: criterion.subjectIds, allowedKinds: criterion.allowedEvidenceKinds, acceptedActorKinds: criterion.acceptedActorKinds, minCount: 1, criterionId: criterion.id }]) }));
  const definitions = current.contextSnapshot.criteria;
  const idsOf = kind => [...subjectDefinitions.values()].filter(subject => subject.kind === kind).map(subject => subject.id);
  const requiredSubjectIds = new Set(definitions.filter(mandatory).flatMap(criterion => criterion.subjectIds));
  const targetGroups = {
    process: idsOf('process'), acceptance_subjects: distinct(definitions.flatMap(criterion => criterion.subjectIds)),
    retained_artifacts: idsOf('retained_artifact').filter(id => requiredSubjectIds.has(id)),
    external_inputs: [...subjectDefinitions.values()].filter(subject => subject.kind === 'verification_input' && subject.locator && config.roots.get(subject.locator.rootId).kind === 'external').map(subject => subject.id),
    interface_subjects: [...subjectDefinitions.values()].filter(subject => ['source_behavior', 'live_endpoint'].includes(subject.kind)).map(subject => subject.id),
    review_subjects: distinct(definitions.filter(criterion => criterion.allowedEvidenceKinds.includes('visual_review')).flatMap(criterion => criterion.subjectIds)),
  };
  const derived = [];
  const deriveTrue = (key, condition) => { if (condition) derived.push({ key, value: true, status: 'observed', basisRefs: ['receipt:' + current.receiptHash] }); };
  deriveTrue('delivery.retained-artifact', targetGroups.retained_artifacts.length > 0);
  deriveTrue('inputs.external', receipts.some(receipt => receipt.inputSets.some(set => set.selectors.some(selector => config.roots.get(selector.rootId).kind === 'external'))));
  deriveTrue('acceptance.human-review', definitions.some(criterion => criterion.required && criterion.acceptedActorKinds.length === 1 && criterion.acceptedActorKinds[0] === 'human'));
  const facts = [...config.project.features, ...context.facts, ...derived];
  function supplement(result, gaps, status = 'insufficient') {
    if (!gaps.length) return result;
    return { ...result, satisfaction: result.satisfaction === 'conflict' || status === 'conflict' ? 'conflict' : status,
      missing: distinct([...result.missing, ...gaps]), reason: '证据或过程记录仍有必须处理的缺口。' };
  }
  const rules = config.rules.map(rule => {
    const applicability = evaluateApplicability(rule.when, { facts, paths: context.paths });
    const base = { ruleId: rule.id, ruleVersion: rule.version, strength: rule.strength, applicability };
    if (applicability.status === 'not_applicable') return { ...base, checkRefs: [], evidenceRefs: [], freshness: null, satisfaction: null, reason: applicability.reason, missing: [] };
    if (applicability.status !== 'applicable') return { ...base, checkRefs: [], evidenceRefs: [], freshness: unknownFreshness('APPLICABILITY_UNRESOLVED'), satisfaction: applicability.status === 'conflict' ? 'conflict' : 'insufficient', reason: applicability.reason, missing: ['APPLICABILITY_UNRESOLVED'] };
    if (!supported.has(rule.evaluator.id) || rule.evaluator.version !== '1') return { ...base, checkRefs: [], evidenceRefs: [], freshness: unknownFreshness('EVALUATOR_UNSUPPORTED'), satisfaction: 'insufficient', reason: '该声明式检查器版本未实现。', missing: ['EVALUATOR_UNSUPPORTED'] };
    let result = proof(rule.evidenceRequirements.map(requirement => ({ ...requirement, targets: targetGroups[requirement.subjects] })), entry => rule.evaluator.id !== 'process.execution-binding' || entry.evidence.kind === 'execution_record');
    if (rule.evaluator.id === 'process.scope') result = supplement(result, scopeGaps(current, config.project));
    if (rule.evaluator.id === 'process.execution-binding') {
      const gaps = [];
      for (const receipt of receipts) {
        const used = distinct(receipt.checks.flatMap(check => check.inputSetIds));
        const recorded = new Set(receipt.evidence.filter(item => item.kind === 'execution_record').flatMap(item => item.inputSetIds));
        if (used.some(id => !recorded.has(id))) gaps.push('EXECUTION_INPUT_COVERAGE_INCOMPLETE');
      }
      result = supplement(result, gaps);
    }
    if (rule.evaluator.id === 'process.criteria-evidence') {
      const failures = criteria.filter(criterion => criterion.required && criterion.satisfaction !== 'satisfied');
      if (failures.length) {
        const status = failures.some(criterion => criterion.satisfaction === 'conflict') ? 'conflict' : failures.some(criterion => criterion.satisfaction === 'unsatisfied') ? 'unsatisfied' : 'insufficient';
        result = supplement(result, failures.map(criterion => 'CRITERION_NOT_SATISFIED:' + criterion.criterionId), status);
        result.checkRefs = dedupe([...result.checkRefs, ...failures.flatMap(criterion => criterion.checkRefs)], referenceKey);
        result.evidenceRefs = dedupe([...result.evidenceRefs, ...failures.flatMap(criterion => criterion.evidenceRefs)], referenceKey);
        result.freshness = mergeFreshness([result.freshness, ...failures.map(criterion => criterion.freshness)]);
      }
    }
    return { ...base, ...result };
  });
  const receiptRefs = receipts.map(receipt => ({ receiptId: receipt.receiptId, receiptHash: receipt.receiptHash }));
  const inputDigest = sha256({ evaluator: EVALUATOR_VERSION, project: config.project, receiptRefs, workItemRef, candidateBinding, assessedAt, context });
  const assessment = { formatVersion: 'panorama.process-assessment.v1', purpose: mode, assessmentId: 'assessment:' + inputDigest, assessmentHash: '', binding: config.project.binding, workItemRef, rulePacks: config.project.rulePacks,
    assessor: { id: 'panorama-process', version: EVALUATOR_VERSION, mode: mode === 'contract_example' ? 'contract_example' : 'deterministic' }, assessedAt, receiptRefs,
    currentInputObservations: [...inputs.values()].sort((a, b) => a.inputSetId < b.inputSetId ? -1 : 1), currentSubjectObservations: [...subjects.values()].sort((a, b) => a.subjectId < b.subjectId ? -1 : 1),
    rules, criteria, overall: aggregateAssessment(rules, criteria),
    limitations: ['结果仅覆盖声明的输入、对象及调用方提供的当前观察，不表示正式验收、部署或宿主拦截。', '外部生产者自报不提供来源信任；真实模式需独立核验的证据凭据。', ...(mode === 'contract_example' ? ['本次计算使用合成合同样本，未执行业务检查；样本不声明运行环境已冻结。'] : [])],
    extensions: { 'panorama.process.evaluation': { inputDigest, facts, evidenceAttestationDigest: sha256(context.verifiedEvidence) } } };
  assessment.assessmentHash = bodyHash(assessment, 'assessmentHash');
  return validateAssessment(assessment, config, receipts);
}
