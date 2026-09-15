import test from 'node:test';
import assert from 'node:assert/strict';
import { assessProcess } from '../../src/process/evaluate.mjs';
import { validateDocument } from '../../src/process/schema.mjs';
import { validateConfiguration, validateReceipt, validateAssessment } from '../../src/process/validation.mjs';
import { parseProcessJson, processValue, sha256, bodyHash } from '../../src/process/json.mjs';
import { evaluateApplicability, matchPath } from '../../src/process/applicability.mjs';
import { fixture, catalog, read, seal, production, attest } from './helpers.mjs';

const rejects = (fn, code) => assert.throws(fn, error => error.code === code, code);
const row = (assessment, id) => assessment.rules.find(rule => rule.ruleId === id);

test('P1 computes the seven P0 scenario outcomes from files and facts, without reading expected assessments as input', async () => {
  for (const entry of catalog.cases) {
    const request = await fixture(entry.id), assessment = assessProcess(request);
    assert.equal(assessment.overall.status, entry.expectedStatus, entry.id);
    const expected = await read(entry.assessment);
    for (const rule of expected.rules) {
      assert.equal(row(assessment, rule.ruleId).applicability.status, rule.applicability.status, `${entry.id}/${rule.ruleId} applicability`);
      assert.equal(row(assessment, rule.ruleId).satisfaction, rule.satisfaction, `${entry.id}/${rule.ruleId} satisfaction`);
    }
    for (const criterion of expected.criteria) assert.equal(assessment.criteria.find(item => item.criterionId === criterion.criterionId).satisfaction, criterion.satisfaction, entry.id);
    assert.deepEqual(assessProcess(request), assessment);
    assert.equal(assessment.assessor.version, 'panorama-process-1.0.0');
  }
});

test('strict JSON rejects duplicate keys, invalid bytes, unsafe values, limits and executable object accessors', () => {
  rejects(() => parseProcessJson('{"a":1,"\\u0061":2}'), 'JSON_DUPLICATE_KEY');
  rejects(() => parseProcessJson(Buffer.from([0xff])), 'JSON_INVALID_UTF8');
  for (const number of ['9007199254740992','1e999']) rejects(() => parseProcessJson(number), 'JSON_UNSAFE_NUMBER');
  rejects(() => parseProcessJson('['.repeat(22) + '0' + ']'.repeat(22)), 'JSON_TOO_DEEP');
  rejects(() => parseProcessJson(' '.repeat(1048577)), 'JSON_TOO_LARGE');
  for (const text of ['{"a":1,}', '[1,]', '1 2', '"bad\nstring"']) rejects(() => parseProcessJson(text), 'JSON_SYNTAX');
  let invoked = false;
  rejects(() => processValue({get value() {invoked = true; return 1;}}), 'JSON_NON_DATA_VALUE');
  assert.equal(invoked, false);
  const data = parseProcessJson('{"__proto__":{"x":true},"x":1.5}');
  assert.equal({}.x, undefined); assert.equal(data.x, 1.5);
  assert.equal(processValue({extensions:{unknown:{kept:true}}}).extensions.unknown.kept,true);
});

test('declarative conditions retain unknown/conflict, use typed facts and only prove path absence with coverage', () => {
  const fact = (value, status = 'declared') => ({key:'impact',value,status,basisRefs:['test:source']});
  const condition = {fact:'impact',equals:true};
  assert.equal(evaluateApplicability(condition).status,'unknown');
  assert.equal(evaluateApplicability(condition,{facts:[fact('true')]}).status,'not_applicable');
  assert.equal(evaluateApplicability(condition,{facts:[fact(true),fact(false)]}).status,'conflict');
  assert.equal(evaluateApplicability({any:[{always:true},condition]}).status,'applicable');
  assert.equal(evaluateApplicability({all:[{not:{always:true}},condition]}).status,'not_applicable');
  assert.equal(evaluateApplicability({any:[{always:true},condition]},{facts:[fact(null,'conflict')]}).status,'conflict');
  const pathCondition = {paths:{rootId:'project',include:['src/**/*.ts']}};
  assert.equal(evaluateApplicability(pathCondition,{paths:[{rootId:'project',paths:['README.md'],complete:false,basisRefs:['test:scan']}]}).status,'unknown');
  assert.equal(evaluateApplicability(pathCondition,{paths:[{rootId:'project',paths:['README.md'],complete:true,basisRefs:['test:scan']}]}).status,'not_applicable');
  assert.equal(matchPath('src/**/*.ts','src/a.ts'),true); assert.equal(matchPath('src/*.ts','src/nested/a.ts'),false);
  assert.equal(matchPath('**/?.ts','a.ts'),true); assert.equal(matchPath('a'.repeat(100)+'*','b'.repeat(100)),false);
  rejects(() => evaluateApplicability({command:'execute'}),'SCHEMA_INVALID');
});

test('external completion and self-reported observed provenance cannot establish trusted success', async () => {
  const request = production(await fixture());
  const untrusted = assessProcess(request);
  assert.equal(untrusted.overall.processReady,false);
  assert.ok(untrusted.criteria.every(item => item.satisfaction === 'insufficient'));
  assert.equal(assessProcess(attest(request)).overall.processReady,true);
  request.verifiedEvidence[0].actorId = 'someone-else';
  rejects(() => assessProcess(request),'ATTESTATION_BINDING_MISMATCH');
  const example = await fixture(); delete example.mode;
  rejects(() => assessProcess(example),'PURPOSE_MISMATCH');
});

test('wrong hashes, bindings, versions, evidence ownership and payload secrets fail before evaluation', async () => {
  const request = await fixture();
  const config = validateConfiguration(request.project,request.packs,{mode:request.mode});
  const probes = [
    [r => r.receiptHash = '0'.repeat(64),'RECEIPT_HASH_MISMATCH',false],
    [r => r.binding.checkoutId = 'different','PROJECT_BINDING_MISMATCH',true],
    [r => r.rulePacks[0].version = 'new','PACK_BINDING_MISMATCH',true],
    [r => r.checks[0].evidenceIds = [],'CHECK_EVIDENCE_MISMATCH',true],
    [r => r.evidence[0].author.id = 'other','EVIDENCE_AUTHOR_MISMATCH',true],
    [r => r.subjects.push({...r.subjects[0]}),'DUPLICATE_ID',true],
    [r => r.checks[0].execution.finishedAt = '2026-09-13T00:00:00Z','EXECUTION_TIME_INVALID',true],
    [r => r.extensions.credentials = {api_key:'fake-test-key'},'REDACTION_REQUIRED',true],
    [r => r.extensions.note = 'Bearer ' + 'a'.repeat(30),'REDACTION_REQUIRED',true],
  ];
  for (const [mutate,code,reseal] of probes) { const receipt = structuredClone(request.receipts[0]); mutate(receipt); if (reseal) seal(receipt); rejects(() => validateReceipt(receipt,config),code); }
  const receipt = structuredClone(request.receipts[0]); receipt.generatedAt = '2026-02-31T00:00:00Z';
  rejects(() => validateDocument(receipt),'SCHEMA_INVALID');
  rejects(() => validateReceipt(request.receipts[0],config,{workItemRef:{...request.workItemRef,definitionDigest:'0'.repeat(64)}}),'WORK_BINDING_MISMATCH');
  const candidate = await fixture('hogwarts-valid'); candidate.candidateBinding = {...candidate.candidateBinding,candidateVersion:9};
  rejects(() => assessProcess(candidate),'CANDIDATE_BINDING_MISMATCH');
});

test('input changes during execution, missing current observations and unknown environment do not become current success', async () => {
  const request = await fixture();
  request.receipts[0].inputSets[0].before.files[0].digest = 'e'.repeat(64);
  const input = request.receipts[0].inputSets[0]; input.before.digest = sha256({selectionDigest:input.selectionDigest,files:input.before.files});
  seal(request.receipts[0]);
  const changed = assessProcess(request);
  assert.equal(changed.overall.processReady,false); assert.equal(changed.criteria[0].freshness.status,'stale');
  const missing = await fixture(); missing.currentInputObservations = []; missing.currentSubjectObservations = [];
  assert.equal(assessProcess(missing).criteria[0].freshness.status,'unknown');
  const runtime = attest(production(await fixture())); runtime.environmentObservations = [];
  assert.equal(assessProcess(runtime).criteria[0].satisfaction,'insufficient');
});

test('a failed execution stays unsatisfied and conflicting current evidence stays conflict', async () => {
  const request = await fixture(), receipt = request.receipts[0];
  const check = receipt.checks.find(item => item.id === 'CHK-BEHAVIOR');
  check.execution.result = 'failed'; check.execution.exitCode = 7; seal(receipt);
  assert.equal(assessProcess(request).criteria[0].satisfaction,'unsatisfied');
  const previous = structuredClone(receipt); previous.receiptId += ':second'; previous.executionId += ':second';
  previous.checks.find(item => item.id === check.id).execution = {...check.execution,result:'passed',exitCode:0}; seal(previous);
  request.receipts.push(previous);
  assert.equal(assessProcess(request).overall.status,'conflict');
});

test('scope gaps, unknown evaluators and all required targets block while optional failures remain visible', async () => {
  const request = await fixture();
  request.receipts[0].contextSnapshot.changedPaths = []; seal(request.receipts[0]);
  assert.equal(row(assessProcess(request),'BASE-02').satisfaction,'insufficient');
  const optional = await fixture(); const extra = structuredClone(optional.packs[0].rules[0]);
  extra.id = 'OPTIONAL-CUSTOM'; extra.strength = 'optional'; extra.evaluator.id = 'custom.unsupported';
  optional.packs[0].rules.push(extra);
  const packRef = {id:optional.packs[0].id,version:optional.packs[0].version,digest:sha256(optional.packs[0])};
  optional.project.rulePacks = [packRef]; optional.receipts[0].rulePacks = [packRef]; seal(optional.receipts[0]);
  assert.equal(assessProcess(optional).overall.processReady,true);
  assert.equal(row(assessProcess(optional),extra.id).satisfaction,'insufficient');
  const multi = await fixture(); multi.receipts[0].contextSnapshot.criteria[0].subjectIds.push('SUB-RETAINED'); seal(multi.receipts[0]); multi.workItemRef = multi.receipts[0].workItemRef;
  assert.equal(assessProcess(multi).criteria[0].satisfaction,'insufficient');
});

test('missing human review and conflicting feature declarations cannot be replaced with completion text', async () => {
  const request = await fixture('cat-review-missing');
  assert.equal(row(assessProcess(request),'COND-REVIEW').satisfaction,'insufficient');
  request.facts.find(fact => fact.key === 'acceptance.human-review').value = false;
  assert.equal(assessProcess(request).overall.status,'conflict');
  assert.equal(request.receipts[0].upstreamCompletionClaim.status,'completed');
});

test('assessment import verifies references, exact coverage and aggregation, without certifying external reasoning', async () => {
  const request = await fixture(), config = validateConfiguration(request.project,request.packs,{mode:request.mode});
  const assessment = assessProcess(request);
  assessment.rules[0].strength = 'optional'; assessment.assessmentHash = bodyHash(assessment,'assessmentHash');
  rejects(() => validateAssessment(assessment,config,request.receipts),'RULE_BINDING_MISMATCH');
});

test('evidence cannot erase its check external-input dependency and stale retained-object bytes block artifact acceptance', async () => {
  const request = await fixture('cat-external-stale'), receipt = request.receipts[0];
  receipt.checks.find(check => check.id === 'CHK-BEHAVIOR').inputSetIds.push('INPUT-EXTERNAL');
  seal(receipt);
  const assessment = assessProcess(request);
  assert.equal(assessment.criteria.find(item => item.criterionId === 'AC-BEHAVIOR').freshness.status,'stale');
  assert.equal(assessment.criteria.find(item => item.criterionId === 'AC-BEHAVIOR').satisfaction,'insufficient');
  const artifact = await fixture(); artifact.currentSubjectObservations.find(subject => subject.subjectId === 'SUB-RETAINED').identityDigest = 'c'.repeat(64);
  const changed = assessProcess(artifact);
  assert.equal(row(changed,'COND-ARTIFACT').freshness.status,'stale');
  assert.equal(changed.overall.processReady,false);
});

test('unknown start baseline stays insufficient, and an unknown conditional differs from an applied failure', async () => {
  const request = await fixture();
  request.receipts[0].contextSnapshot.baseline = {...request.receipts[0].contextSnapshot.baseline,state:'unknown',capturedAt:null,inputSets:[],unknowns:['No original observation']};
  seal(request.receipts[0]);
  assert.equal(row(assessProcess(request),'BASE-02').satisfaction,'insufficient');
  assert.equal(assessProcess(await fixture('hogwarts-impact-unknown')).overall.status,'unknown');
});

test('continued work cannot replace its original baseline under a new receipt ID', async () => {
  const request = await fixture(), changed = structuredClone(request.receipts[0]);
  changed.receiptId += ':continued'; changed.contextSnapshot.baseline.id += ':replacement'; seal(changed);
  request.receipts.push(changed);
  rejects(() => assessProcess(request),'WORK_BASELINE_CONFLICT');
});
