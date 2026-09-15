// Versioned collector contracts. P0/P1 remain generic evidence evaluators.
import { readFileSync } from 'node:fs';
import { sha256, sameValue } from '../process/json.mjs';
import { validateDefinition } from '../process/schema.mjs';
import { uniqueBy } from '../process/validation.mjs';
import { requireProcess } from '../process/errors.mjs';
import { matchPath } from '../process/applicability.mjs';
import { keys } from './io.mjs';

export const BOOTSTRAP_V2 = 'panorama.development-bootstrap.v2';
export const BINDINGS_V1 = 'panorama.check-bindings.v1';
export const WORK_V3 = 'panorama.development-work.v3';
const protocols = ['panorama.checker-result.v1', 'catcare-tar.v1', 'retained-support.v1'];
const kinds = ['behavior_test', 'interface_test', 'artifact_integrity'];
const array = (value, min = 0, max = 100) => {
  requireProcess(Array.isArray(value) && value.length >= min && value.length <= max, 'CHECK_CONTRACT_INVALID');
  return value;
};
function strings(value, definition = 'text', min = 1) {
  array(value, min).forEach(item => validateDefinition(item, definition));
  requireProcess(new Set(value).size === value.length, 'CHECK_CONTRACT_INVALID');
}
export function checkerCatalog() {
  return JSON.parse(readFileSync(new URL('../../contracts/checkers/catalog.v1.json', import.meta.url), 'utf8'));
}
export function validateCheckerContract(contract) {
  keys(contract, ['formatVersion','id','version','title','subjectKinds','formats','claims','notChecked','dependencyRoles','dependencies','failureProtocol','limits','examples'], ['formatVersion','id','version','title','subjectKinds','formats','claims','notChecked','dependencyRoles','dependencies','failureProtocol','limits','examples']);
  requireProcess(contract.formatVersion === 'panorama.checker-contract.v1', 'CHECK_CONTRACT_VERSION_UNSUPPORTED');
  for (const key of ['id','version']) validateDefinition(contract[key], 'id');
  validateDefinition(contract.title, 'text');
  strings(contract.subjectKinds, 'id'); strings(contract.formats, 'id');
  requireProcess(contract.subjectKinds.every(kind => ['source_behavior','retained_artifact','verification_input'].includes(kind)), 'CHECK_CONTRACT_INVALID');
  array(contract.claims, 1); uniqueBy(contract.claims, 'id');
  for (const claim of contract.claims) {
    keys(claim, ['id','property','evidenceKinds'], ['id','property','evidenceKinds']);
    validateDefinition(claim.id, 'id'); validateDefinition(claim.property, 'text'); strings(claim.evidenceKinds, 'id');
    requireProcess(claim.evidenceKinds.every(kind => kinds.includes(kind)), 'CHECK_CONTRACT_INVALID');
  }
  strings(contract.notChecked); strings(contract.dependencies); strings(contract.dependencyRoles, 'id');
  requireProcess(contract.dependencyRoles.every(role => ['source','config','dependency','tool','artifact','fixture'].includes(role)), 'CHECK_CONTRACT_INVALID');
  requireProcess(protocols.includes(contract.failureProtocol), 'CHECKER_PROTOCOL_UNSUPPORTED');
  keys(contract.limits, ['timeoutMs','maxLogBytes','maxObjectBytes','limitations'], ['timeoutMs','maxLogBytes','maxObjectBytes','limitations']);
  for (const key of ['timeoutMs','maxLogBytes','maxObjectBytes']) requireProcess(Number.isSafeInteger(contract.limits[key]) && contract.limits[key] > 0 && contract.limits[key] <= ({timeoutMs:3600000,maxLogBytes:1048576,maxObjectBytes:67108864})[key], 'CHECKER_LIMIT_INVALID');
  strings(contract.limits.limitations);
  keys(contract.examples, ['positive','negative'], ['positive','negative']); strings(contract.examples.positive); strings(contract.examples.negative);
  // Built-in report protocols attest only their frozen, reviewed properties.
  if (contract.failureProtocol !== 'panorama.checker-result.v1') {
    const builtin = checkerCatalog().contracts.find(item => item.failureProtocol === contract.failureProtocol);
    requireProcess(sameValue(contract, builtin), 'BUILTIN_CHECKER_CONTRACT_CHANGED');
  }
  return contract;
}

export function normalizeCheckConfiguration(input, roots, runners) {
  const contracts = array(input.checkerContracts ?? []).map(validateCheckerContract);
  uniqueBy(contracts, 'id');
  const scopes = array(input.inputScopes ?? [], 0, 32).map(scope => {
    keys(scope, ['id','selectors','completeness','basis'], ['id','selectors','completeness','basis']);
    validateDefinition(scope.id, 'id'); validateDefinition(scope.basis, 'text');
    requireProcess(['declared_complete','incomplete'].includes(scope.completeness), 'INPUT_SCOPE_COMPLETENESS_INVALID');
    const selectors = array(scope.selectors, 1).map(selector => {
      const root = roots.find(root => root.id === selector.rootId);
      requireProcess(root && !root.observeOnly, 'INPUT_SCOPE_ROOT_INVALID');
      validateDefinition(selector, 'selector');
      // Same bounded subset proof as the input registry; never expand root authority.
      requireProcess(selector.include.every(pattern => root.allow.some(allowed => allowed === '**' || allowed === pattern || allowed.endsWith('/**') && !allowed.slice(0,-3).includes('*') && pattern.startsWith(allowed.slice(0,-2)) || !pattern.includes('*') && matchPath(allowed, pattern))), 'INPUT_SCOPE_NOT_AUTHORIZED');
      return {...selector, exclude:[...new Set([...root.selector.exclude, ...selector.exclude])]};
    });
    return {...scope, selectors};
  });
  uniqueBy(scopes, 'id');
  requireProcess(roots.length + scopes.reduce((count,scope) => count + new Set(scope.selectors.map(selector => selector.rootId)).size,0) <= 100, 'INPUT_SCOPE_COUNT_LIMIT');
  for (const runner of runners) {
    const scope = scopes.find(scope => scope.id === runner.scopeId), contract = contracts.find(contract => contract.id === runner.checkerContractId);
    requireProcess(scope && contract, 'RUNNER_CONTRACT_REQUIRED');
    requireProcess(runner.evidenceKinds.every(kind => contract.claims.some(claim => claim.evidenceKinds.includes(kind))), 'RUNNER_CONTRACT_KIND_MISMATCH');
    requireProcess(contract.dependencyRoles.every(role => scope.selectors.some(selector => selector.role === role)) || scope.completeness === 'incomplete', 'CHECKER_DEPENDENCIES_UNDECLARED');
    requireProcess(runner.timeoutMs <= contract.limits.timeoutMs && runner.maxLogBytes > 0 && runner.maxLogBytes <= contract.limits.maxLogBytes, 'RUNNER_CONTRACT_LIMIT_MISMATCH');
    if (contract.failureProtocol === 'panorama.checker-result.v1') requireProcess(runner.args.some(arg => arg.includes('{executionId}')), 'CHECKER_EXECUTION_ARGUMENT_REQUIRED');
  }
  return {formatVersion:BOOTSTRAP_V2, inputScopes:scopes, checkerContracts:contracts};
}

export function validateBindings(ctx, bindings, work) {
  if (ctx.local.bootstrap.formatVersion !== BOOTSTRAP_V2) {
    requireProcess(bindings === undefined, 'CHECK_BINDINGS_REQUIRE_BOOTSTRAP_V2'); return;
  }
  requireProcess(bindings, 'CHECK_BINDINGS_REQUIRED');
  keys(bindings, ['formatVersion','subjects','criteria'], ['formatVersion','subjects','criteria']);
  requireProcess(bindings.formatVersion === BINDINGS_V1, 'CHECK_BINDINGS_VERSION_UNSUPPORTED');
  array(bindings.subjects, 1); array(bindings.criteria, 1);
  uniqueBy(bindings.subjects, 'subjectId'); uniqueBy(bindings.criteria, 'criterionId');
  requireProcess(sameValue(bindings.subjects.map(row => row.subjectId).sort(), work.subjects.map(row => row.id).sort()) && sameValue(bindings.criteria.map(row => row.criterionId).sort(), work.context.criteria.map(row => row.id).sort()), 'CHECK_BINDINGS_COVERAGE_INVALID');
  for (const row of bindings.subjects) {
    keys(row, ['subjectId','format','scopeId'], ['subjectId','format','scopeId']);
    validateDefinition(row.format, 'id');
    requireProcess(row.scopeId === null || ctx.local.bootstrap.inputScopes.some(scope => scope.id === row.scopeId), 'SUBJECT_SCOPE_NOT_REGISTERED');
  }
  for (const row of bindings.criteria) { keys(row, ['criterionId','claims'], ['criterionId','claims']); strings(row.claims, 'id', 0); }
}
export function checkBindingDigest(work) { return sha256({workItemRef:work.workItemRef, bindings:work.checkBindings}); }
export function validateWorkBindings(ctx, work) {
  if (work.formatVersion !== WORK_V3) return;
  validateBindings(ctx, work.checkBindings, work);
  requireProcess(work.checkBindingsDigest === checkBindingDigest(work), 'WORK_CHECK_BINDINGS_CHANGED');
}
export function scopeDefinition(ctx, scopeId) { return ctx.local.bootstrap.inputScopes?.find(scope => scope.id === scopeId); }
export function scopedDefinitions(ctx, work) {
  if (!work?.checkBindings) return [];
  return ctx.local.bootstrap.inputScopes.flatMap(scope => [...new Set(scope.selectors.map(selector => selector.rootId))].map(rootId => {
    const selectors = scope.selectors.filter(selector => selector.rootId === rootId);
    return {id:'scope:' + sha256({scopeId:scope.id, selectors}), scopeId:scope.id, selectors};
  }));
}
export function selectedInputs(ctx, work, inputs, scopeId) {
  const scope = work?.checkBindings && scopeDefinition(ctx, scopeId);
  if (!scope || scope.completeness !== 'declared_complete') return inputs.filter(set => set.id.startsWith('input:'));
  const ids = new Set(scopedDefinitions(ctx, work).filter(set => set.scopeId === scopeId).map(set => set.id));
  return inputs.filter(set => ids.has(set.id));
}
export function checkerFor(ctx, runner) { return ctx.local.bootstrap.checkerContracts?.find(contract => contract.id === runner?.checkerContractId); }
export function supportsCriterionKind(ctx, work, runner, criterion, kind) {
  if (!work.checkBindings) return true;
  const contract=checkerFor(ctx,runner),binding=work.checkBindings.criteria.find(row=>row.criterionId===criterion.id);
  return Boolean(binding?.claims.length && binding.claims.every(id=>contract?.claims.some(claim=>claim.id===id && claim.evidenceKinds.includes(kind))));
}
export function validateCheckCoverage(ctx, work, runner, selection) {
  if (!work.checkBindings) return null;
  validateWorkBindings(ctx, work);
  const contract = checkerFor(ctx, runner), scope = scopeDefinition(ctx, runner.scopeId);
  requireProcess(contract && scope, 'RUNNER_CONTRACT_REQUIRED');
  const criterionIds = selection.criterionIds ?? work.context.criteria.filter(criterion => criterion.allowedEvidenceKinds.some(kind => runner.evidenceKinds.includes(kind))).map(row => row.id);
  const subjectIds = selection.subjectIds ?? [...new Set(criterionIds.flatMap(id => work.context.criteria.find(row => row.id === id)?.subjectIds ?? []))];
  for (const id of criterionIds) {
    const criterion = work.context.criteria.find(row => row.id === id), binding = work.checkBindings.criteria.find(row => row.criterionId === id);
    requireProcess(criterion && binding?.claims.length, 'CHECKER_CLAIMS_REQUIRED', {criterionId:id});
    requireProcess(criterion.allowedEvidenceKinds.some(kind => runner.evidenceKinds.includes(kind) && supportsCriterionKind(ctx,work,runner,criterion,kind)), 'CHECKER_CLAIM_NOT_COVERED', {criterionId:id});
  }
  for (const id of subjectIds) {
    const subject = work.subjects.find(row => row.id === id), binding = work.checkBindings.subjects.find(row => row.subjectId === id);
    requireProcess(subject && binding && contract.subjectKinds.includes(subject.kind) && contract.formats.includes(binding.format), 'CHECKER_SUBJECT_FORMAT_UNSUPPORTED', {subjectId:id});
    if (subject.kind === 'source_behavior') requireProcess(binding.scopeId === runner.scopeId, 'SOURCE_SUBJECT_SCOPE_MISMATCH', {subjectId:id});
    if (subject.locator && scope.completeness === 'declared_complete') requireProcess(scope.selectors.some(selector => selector.rootId === subject.locator.rootId && selector.include.some(pattern => matchPath(pattern, subject.locator.path)) && !selector.exclude.some(pattern => matchPath(pattern, subject.locator.path))), 'CHECKER_OBJECT_OUTSIDE_SCOPE', {subjectId:id});
  }
  return {contractId:contract.id, contractVersion:contract.version, contractDigest:sha256(contract), scopeId:scope.id,
    scopeMode:scope.completeness === 'declared_complete' ? 'declared_scope' : 'whole_declared_inputs', basis:scope.basis,
    limitations:scope.completeness === 'incomplete' ? ['DEPENDENCY_SCOPE_INCOMPLETE: fallback to all declared inputs; undeclared dependencies remain outside observation.'] : ['Dependency closure is explicitly declared by the project; no sandbox proves arbitrary code reads.']};
}

export function receiptBinding(work, coverage = null, rawExecution = null) {
  return {formatVersion:'panorama.check-binding-receipt.v1', bindingsDigest:work.checkBindingsDigest, coverage, rawExecution};
}
export function validateReceiptBinding(work, receipt) {
  if (!work.checkBindings) return;
  const binding = receipt.extensions?.['panorama.development.check-binding'];
  requireProcess(binding?.formatVersion === 'panorama.check-binding-receipt.v1' && binding.bindingsDigest === work.checkBindingsDigest, 'RECEIPT_CHECK_BINDING_MISMATCH');
}

// Structured success is required in addition to the actual process exit status.
// It detects missing/swallowed reports, not a deliberately dishonest local writer.
export function verifyCheckerReport(contract, {stdout, truncated, executionId, subjects, claims, formats}) {
  try {
    requireProcess(!truncated, 'CHECKER_REPORT_TRUNCATED');
    const prefix = contract.failureProtocol === 'retained-support.v1' ? 'RETAINED_SUPPORT_RESULT ' : '';
    const rows = stdout.split(/\r?\n/).filter(line => prefix ? line.startsWith(prefix) : line.trim().startsWith('{')).map(line => {
      try { return JSON.parse(prefix ? line.slice(prefix.length) : line); } catch { return null; }
    });
    requireProcess(rows.length === 1 && rows[0], 'CHECKER_REPORT_MISSING_OR_AMBIGUOUS');
    const report = rows[0];
    if (contract.failureProtocol === 'catcare-tar.v1') {
      requireProcess(subjects.length === 1 && report.status === 'passed' && Number.isSafeInteger(report.original_files) && report.original_files >= 0 && Number.isSafeInteger(report.original_bytes) && report.original_bytes >= 0, 'CHECKER_REPORT_FAILED');
    } else if (contract.failureProtocol === 'retained-support.v1') {
      requireProcess(subjects.length === 1 && report.result === 'passed' && report.sha256 === subjects[0].identityDigest && report.storedPositionVertices > 0 && report.nonemptyMeshObjects > 0 && /^[a-f0-9]{64}$/.test(report.sourceSha256) && report.sourceBytes > 0, 'CHECKER_REPORT_FAILED');
    } else {
      requireProcess(report.formatVersion === 'panorama.checker-result.v1' && report.executionId === executionId && report.contractId === contract.id && report.contractVersion === contract.version && report.status === 'passed', 'CHECKER_REPORT_FAILED');
      requireProcess(Array.isArray(report.subjects) && report.subjects.length === subjects.length && new Set(report.subjects.map(row => row.id)).size === subjects.length && subjects.every(subject => report.subjects.some(row => row.id === subject.id && row.identityDigest === subject.identityDigest && row.format === formats[subject.id])), 'CHECKER_REPORT_OBJECT_MISMATCH');
      requireProcess(Array.isArray(report.claims) && claims.every(id => report.claims.includes(id)) && report.claims.every(id => contract.claims.some(claim => claim.id === id)), 'CHECKER_REPORT_CLAIMS_INCOMPLETE');
    }
    return {status:'verified', protocol:contract.failureProtocol, reportDigest:sha256(report), reason:null};
  } catch (error) { return {status:'unknown', protocol:contract.failureProtocol, reportDigest:null, reason:error.code ?? 'CHECKER_REPORT_INVALID'}; }
}
