import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { canonicalJson, sha256 } from '../../src/domain/canonical.mjs';

const root = new URL('../../contracts/process/', import.meta.url);
const read = path => JSON.parse(readFileSync(new URL(path, root), 'utf8'));
const text = path => readFileSync(new URL(path, root), 'utf8');
const manifest = read('schema-manifest.json');
const pack = read(manifest.rulePack.path);
const catalog = read('examples/catalog.json');
const fixtures = catalog.cases.map(entry => ({ ...entry, projectValue: read(entry.project), receiptValue: read(entry.receipt), assessmentValue: read(entry.assessment), factsValue: read(entry.facts) }));
const schemas = manifest.schemas.map(entry => ({ ...entry, document: read(entry.path) }));
const schemaId = type => `urn:panorama:process:schema:${type}`;
const bodyHash = (value, field) => { const copy = structuredClone(value); delete copy[field]; return sha256(copy); };
const definition = context => Object.fromEntries(['goal','expectedOutcome','moduleIds','plannedPaths','criteria'].map(key => [key,context[key]]));
const byCase = id => fixtures.find(fixture => fixture.id === id);
const names = values => values.map(value => value.id);
const uniqueIds = values => assert.equal(new Set(names(values)).size, values.length, 'duplicate ID');

const positives = [ { id: 'pack', schemaId: schemaId('standard-pack:2'), document: pack } ];
for (const fixture of fixtures) {
  positives.push({ id: fixture.id + ':project', schemaId: schemaId('project:1'), document: fixture.projectValue });
  positives.push({ id: fixture.id + ':receipt', schemaId: schemaId('receipt:1'), document: fixture.receiptValue });
  positives.push({ id: fixture.id + ':assessment', schemaId: schemaId('assessment:1'), document: fixture.assessmentValue });
}
const bad = [];
function negative(id, type, from, mutate) {
  const document = structuredClone(from); mutate(document); bad.push({ id, schemaId: schemaId(type), document });
}
const receipt = byCase('cat-valid').receiptValue;
const assessment = byCase('cat-valid').assessmentValue;
negative('missing-work-binding','receipt:1',receipt,r => delete r.workItemRef);
negative('machine-zero-but-failed','receipt:1',receipt,r => r.checks.find(c => c.kind === 'machine').execution.result = 'failed');
negative('machine-nonzero-but-passed','receipt:1',receipt,r => r.checks.find(c => c.kind === 'machine').execution.exitCode = 1);
negative('not-run-with-execution-evidence','receipt:1',receipt,r => r.checks[0].execution.result = 'not_run');
negative('invalid-time','receipt:1',receipt,r => r.generatedAt = 'yesterday');
negative('future-format','receipt:1',receipt,r => r.formatVersion = 'panorama.process-receipt.v99');
negative('raw-log-body','receipt:1',receipt,r => r.stdout = 'raw body is not a Receipt field');
negative('fixture-promoted-to-real-record','receipt:1',receipt,r => r.purpose = 'record');
negative('parent-path','receipt:1',receipt,r => r.subjects[1].locator.path = '../secret.txt');
negative('drive-path','receipt:1',receipt,r => r.inputSets[0].selectors[0].include = ['C:/outside.txt']);
negative('backslash-path','receipt:1',receipt,r => r.inputSets[0].after.files[0].path = 'dir\\file.txt');
negative('unreadable-file-with-digest','receipt:1',receipt,r => r.inputSets[0].after.files[0].state = 'unreadable');
negative('rule-carries-shell','standard-pack:2',pack,p => p.rules[0].evaluator.command = 'echo unsafe');
negative('unknown-expression','standard-pack:2',pack,p => p.rules[0].when = { javascript: 'return true' });
negative('required-rule-with-condition','standard-pack:2',pack,p => p.rules[0].when = { fact: 'x', equals: true });
negative('whitespace-rule','standard-pack:2',pack,p => p.rules[0].requirement = '   ');
negative('unknown-is-satisfied','assessment:1',assessment,a => a.rules[0].applicability.status = 'unknown');
negative('stale-required-is-ready','assessment:1',assessment,a => a.rules[0].freshness.status = 'stale');
negative('unsupported-rule-is-ready','assessment:1',assessment,a => a.rules[0].satisfaction = 'insufficient');
negative('missing-review-ready','assessment:1',byCase('cat-review-missing').assessmentValue,a => { a.overall.processReady = true; a.overall.status = 'satisfied'; a.overall.blockingRuleIds = []; a.overall.blockingCriterionIds = []; });
negative('not-applicable-with-pass','assessment:1',assessment,a => a.rules.find(r => r.applicability.status === 'not_applicable').satisfaction = 'satisfied');
negative('fixture-assessor-as-production','assessment:1',assessment,a => a.purpose = 'record');
negative('absolute-root-in-portable-config','project:1',byCase('cat-valid').projectValue,p => p.inputRoots[0].absolutePath = 'D:/outside');
negative('required-is-not-applicable','assessment:1',assessment,a => { Object.assign(a.rules[0],{applicability:{status:'not_applicable',reason:'skip',basisRefs:['fixture']},satisfaction:null,freshness:null,checkRefs:[],evidenceRefs:[]}); });
negative('current-but-changed','assessment:1',assessment,a => a.rules[0].freshness.changedIds.push('INPUT-SOURCE'));
negative('unknown-input-with-digest','assessment:1',assessment,a => a.currentInputObservations[0].state = 'unknown');
negative('invalid-calendar-date','receipt:1',receipt,r => r.generatedAt = '2026-02-31T00:00:00Z');
negative('missing-start-baseline','receipt:1',receipt,r => delete r.contextSnapshot.baseline);
negative('unknown-baseline-carries-observation','receipt:1',receipt,r => r.contextSnapshot.baseline.state = 'unknown');
negative('observed-baseline-without-inputs','receipt:1',receipt,r => r.contextSnapshot.baseline.inputSets = []);

const python = process.env.PANORAMA_CONTRACT_PYTHON || process.env.PANORAMA_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const validation = spawnSync(python, ['-I','-B',fileURLToPath(new URL('./validate_schemas.py',import.meta.url))], {
  input: JSON.stringify([...positives,...bad]), encoding: 'utf8', windowsHide: true, shell: false, maxBuffer: 4 * 1024 * 1024,
});
if (validation.error || validation.status !== 0) throw new Error(`Contract validator unavailable. Set PANORAMA_CONTRACT_PYTHON to Python with test/process/requirements.txt installed. ${validation.error?.message || validation.stderr}`);
const validated = JSON.parse(validation.stdout);
const result = id => validated.results.find(entry => entry.id === id);

test('four document schemas and shared definitions compile with an offline Draft 2020-12 validator', () => {
  assert.equal(validated.schemasChecked,5); assert.equal(validated.networkRetrieval,false); assert.equal(validated.version,'4.25.1');
  assert.equal(schemas.filter(s => s.role === 'document').length,4);
  assert.equal(new Set(schemas.map(s => s.id)).size,5);
  for (const schema of schemas) {
    assert.equal(schema.id,schema.document.$id);
    const inspect = value => { if (!value || typeof value !== 'object') return; if (value.$ref) assert.ok(schemas.some(s => value.$ref.split('#')[0] === s.id), `unregistered reference ${value.$ref}`); Object.values(value).forEach(inspect); };
    inspect(schema.document);
  }
});

test('22 validation requests across 17 normative/sample documents pass, including blocked and unknown results', () => {
  assert.equal(positives.length,22);
  for (const request of positives) assert.equal(result(request.id).valid,true,`${request.id}: ${JSON.stringify(result(request.id).errors)}`);
  assert.ok(fixtures.some(f => f.expectedStatus === 'blocked'));
  assert.ok(fixtures.some(f => f.expectedStatus === 'unknown'));
});

test('30 malformed or internally contradictory documents are rejected by JSON Schema', () => {
  assert.equal(bad.length,30);
  for (const request of bad) assert.equal(result(request.id).valid,false,request.id);
});

test('one shared pack contains six mandatory and four conditionally mandatory rules', () => {
  uniqueIds(pack.rules); assert.equal(pack.rules.length,10);
  assert.deepEqual(pack.rules.filter(r => r.strength === 'required').map(r => r.id),Array.from({length:6},(_,i) => `BASE-0${i+1}`));
  assert.equal(pack.rules.filter(r => r.strength === 'conditional').length,4);
  assert.ok(pack.rules.every(r => r.evidenceRequirements.length && r.sourceRefs.length));
  assert.deepEqual(pack.rules.find(r => r.id === 'COND-REVIEW').evidenceRequirements[0].acceptedActorKinds,['human']);
  assert.equal(pack.rules.find(r => r.id === 'COND-INTERFACE').evidenceRequirements.length,2);
  assert.equal(manifest.rulePack.digest,sha256(pack));
});

test('all examples are explicitly synthetic and cannot masquerade as new runtime results', () => {
  assert.equal(catalog.fixturesAreNotRuntimeEvidence,true);
  for (const f of fixtures) {
    assert.equal(f.receiptValue.purpose,'contract_example'); assert.equal(f.projectValue.purpose,'contract_example');
    assert.equal(f.assessmentValue.assessor.mode,'contract_example'); assert.equal(f.receiptValue.producer.kind,'contract_fixture');
    assert.equal(f.assessmentValue.overall.status,f.expectedStatus);
  }
  assert.equal(manifest.runtimeEvaluatorImplemented,false);
});

test('portable identities and hashes bind project, work definition, pack, receipt and expected assessment', () => {
  for (const {receiptValue:r,assessmentValue:a,projectValue:p} of fixtures) {
    assert.deepEqual(a.binding,r.binding); assert.deepEqual(p.binding,r.binding); assert.deepEqual(a.workItemRef,r.workItemRef);
    assert.equal(r.workItemRef.definitionDigest,sha256(definition(r.contextSnapshot)));
    assert.equal(r.receiptHash,bodyHash(r,'receiptHash')); assert.equal(a.assessmentHash,bodyHash(a,'assessmentHash'));
    for (const owner of [p,r,a]) assert.deepEqual(owner.rulePacks,[{id:pack.id,version:pack.version,digest:sha256(pack)}]);
    assert.deepEqual(a.receiptRefs,[{receiptId:r.receiptId,receiptHash:r.receiptHash}]);
  }
});

function inspectBundle(f) {
  const r = f.receiptValue, a = f.assessmentValue, p = f.projectValue;
  for (const values of [r.subjects,r.checks,r.evidence,r.inputSets,r.contextSnapshot.criteria,p.inputRoots]) uniqueIds(values);
  assert.equal(new Set(a.rules.map(x => x.ruleId)).size,a.rules.length,'duplicate assessment rule');
  assert.deepEqual(a.rules.map(x => x.ruleId).sort(),pack.rules.map(x => x.id).sort(),'missing or foreign rule');
  assert.deepEqual(a.criteria.map(x => x.criterionId).sort(),r.contextSnapshot.criteria.map(x => x.id).sort(),'missing or foreign criterion');
  const assertSubset = (ids,allowed,label) => ids.forEach(value => assert.ok(allowed.includes(value),`${label}: ${value}`));
  for (const check of r.checks) {
    assertSubset(check.subjectIds,names(r.subjects),'subject'); assertSubset(check.inputSetIds,names(r.inputSets),'input');
    assertSubset(check.criterionIds,names(r.contextSnapshot.criteria),'criterion'); assertSubset(check.evidenceIds,names(r.evidence),'evidence');
    if (check.runnerRef) assert.ok(p.runnerRefs.some(value => value.id === check.runnerRef.id && value.definitionDigest === check.runnerRef.definitionDigest),'runner definition mismatch');
    for (const eid of check.evidenceIds) assert.equal(r.evidence.find(e => e.id === eid).checkId,check.id,'reciprocal check/evidence link');
    if (check.execution.startedAt !== null && check.execution.finishedAt !== null) assert.ok(Date.parse(check.execution.finishedAt) >= Date.parse(check.execution.startedAt),'negative check duration');
  }
  for (const e of r.evidence) {
    assertSubset(e.subjectIds,names(r.subjects),'evidence subject'); assertSubset(e.inputSetIds,names(r.inputSets),'evidence input'); assertSubset(e.criterionIds,names(r.contextSnapshot.criteria),'evidence criterion'); assertSubset([e.checkId],names(r.checks),'evidence check');
    const owner = r.checks.find(check => check.id === e.checkId);
    assertSubset([e.id],owner.evidenceIds,'reciprocal evidence/check link');
    assertSubset(e.subjectIds,owner.subjectIds,'evidence exceeds check subject scope');
    assertSubset(e.inputSetIds,owner.inputSetIds,'evidence exceeds check input scope');
    assertSubset(e.criterionIds,owner.criterionIds,'evidence exceeds check criterion scope');
  }
  for (const criterion of r.contextSnapshot.criteria) assertSubset(criterion.subjectIds,names(r.subjects),'criterion subject');
  for (const set of [...r.inputSets,...r.contextSnapshot.baseline.inputSets]) {
    for (const selector of set.selectors) assertSubset([selector.rootId],names(p.inputRoots),'registered selector root');
    for (const snapshot of set.snapshot ? [set.snapshot] : [set.before,set.after]) {
      const locations = snapshot.files.map(file => `${file.rootId}/${file.path}`);
      assert.equal(new Set(locations).size,locations.length,'duplicate file location');
      assert.deepEqual(locations,[...locations].sort(),'unstable file ordering');
      for (const file of snapshot.files) assertSubset([file.rootId],names(p.inputRoots),'registered file root');
    }
  }
  for (const subject of r.subjects) if (subject.locator) assertSubset([subject.locator.rootId],names(p.inputRoots),'registered root');
  for (const row of [...a.rules,...a.criteria]) {
    for (const ref of row.evidenceRefs) { assert.equal(ref.receiptHash,r.receiptHash); assert.equal(ref.receiptId,r.receiptId); assertSubset([ref.evidenceId],names(r.evidence),'assessment evidence'); }
    for (const ref of row.checkRefs) { assert.equal(ref.receiptHash,r.receiptHash); assert.equal(ref.receiptId,r.receiptId); assertSubset([ref.checkId],names(r.checks),'assessment check'); }
    if (row.ruleId) { const rule = pack.rules.find(x => x.id === row.ruleId); assert.equal(row.ruleVersion,rule.version); assert.equal(row.strength,rule.strength); }
    else { const c = r.contextSnapshot.criteria.find(x => x.id === row.criterionId); assert.equal(row.required,c.required); assert.equal(row.criterionVersion,c.version); }
  }
  assert.equal(a.overall.totalRules,pack.rules.length);
  for (const [state,field] of [['applicable','applicableRules'],['not_applicable','notApplicableRules'],['unknown','unknownRules'],['conflict','conflictingRules']]) assert.equal(a.overall[field],a.rules.filter(row => row.applicability.status === state).length);
}

test('sample bundles contain complete, unique, reciprocal references and complete rule accounting', () => {
  for (const f of fixtures) inspectBundle(f);
});

test('cross-document orphan, duplicate and version-drift probes fail the fixture integrity checks', () => {
  for (const mutate of [
    f => f.receiptValue.checks[0].evidenceIds.push('EV-MISSING'),
    f => f.assessmentValue.rules.pop(),
    f => f.receiptValue.evidence.push({...f.receiptValue.evidence[0],summary:'same ID, different content'}),
    f => f.assessmentValue.rules[0].strength = 'optional',
    f => f.assessmentValue.criteria[0].criterionVersion++,
    f => f.receiptValue.checks[0].evidenceIds = [],
    f => f.receiptValue.contextSnapshot.criteria[0].subjectIds = ['SUB-MISSING'],
    f => f.receiptValue.inputSets[0].selectors[0].rootId = 'unregistered',
  ]) { const f = structuredClone(byCase('cat-valid')); mutate(f); assert.throws(() => inspectBundle(f)); }
});

test('work-start baselines independently reproduce pre-development bytes and are distinct from check inputs', () => {
  for (const f of fixtures) {
    const baseline = f.receiptValue.contextSnapshot.baseline;
    assert.equal(baseline.state,'observed');
    uniqueIds(baseline.inputSets);
    for (const input of baseline.inputSets) {
      assert.equal(input.selectionDigest,sha256(input.selectors));
      const files = input.snapshot.files.map(entry => {
        const body = readFileSync(new URL(`${f.baselineInputRoot}/${entry.rootId}/${entry.path}`,root));
        return {...entry,digest:sha256(body),bytes:body.length};
      });
      assert.equal(input.snapshot.digest,sha256({selectionDigest:input.selectionDigest,files}));
      const checked = f.receiptValue.inputSets.find(value => value.id === input.id);
      assert.notEqual(input.snapshot.digest,checked.before.digest);
      assert.deepEqual(files.filter(file => checked.before.files.some(current => current.rootId === file.rootId && current.path === file.path && current.digest !== file.digest)).map(file => file.path),f.receiptValue.contextSnapshot.changedPaths);
    }
    assert.ok(f.receiptValue.checks.filter(check => check.execution.startedAt !== null).every(check => Date.parse(baseline.capturedAt) <= Date.parse(check.execution.startedAt)));
  }
});

test('file-list snapshots and independently read fixture bytes reproduce declared current observations', () => {
  for (const f of fixtures) {
    for (const input of f.receiptValue.inputSets) {
      assert.equal(input.selectionDigest,sha256(input.selectors));
      for (const snapshot of [input.before,input.after]) assert.equal(snapshot.digest,sha256({selectionDigest:input.selectionDigest,files:snapshot.files}));
      const files = input.after.files.map(entry => { const body = readFileSync(new URL(`${f.inputRoot}/${entry.rootId}/${entry.path}`,root)); return {...entry,digest:sha256(body),bytes:body.length}; });
      const observed = f.assessmentValue.currentInputObservations.find(x => x.inputSetId === input.id);
      assert.equal(observed.digest,sha256({selectionDigest:input.selectionDigest,files}));
      if (f.mode === 'external-stale' && input.id === 'INPUT-EXTERNAL') assert.notEqual(observed.digest,input.after.digest);
      else assert.equal(observed.digest,input.after.digest);
    }
    assert.deepEqual(f.assessmentValue.currentSubjectObservations.map(x => x.subjectId).sort(),names(f.receiptValue.subjects).sort());
    for (const subject of f.receiptValue.subjects) {
      const observed = f.assessmentValue.currentSubjectObservations.find(x => x.subjectId === subject.id);
      const expected = subject.locator ? sha256(readFileSync(new URL(`${f.inputRoot}/${subject.locator.rootId}/${subject.locator.path}`,root))) : subject.identityDigest;
      assert.equal(observed.identityDigest,expected);
      assert.equal(observed.state,expected === null ? 'unknown':'observed');
    }
  }
});

test('external staleness preserves historical execution success without a ready assessment', () => {
  const f = byCase('cat-external-stale');
  assert.equal(f.receiptValue.checks.find(x => x.id === 'CHK-EXTERNAL').execution.exitCode,0);
  assert.equal(f.receiptValue.upstreamCompletionClaim.status,'completed');
  assert.equal(f.assessmentValue.rules.find(x => x.ruleId === 'COND-EXTERNAL').freshness.status,'stale');
  assert.equal(f.assessmentValue.overall.processReady,false);
});

test('missing review and the wrong artifact target remain schema-valid representations of insufficient evidence', () => {
  const review = byCase('cat-review-missing');
  assert.equal(review.receiptValue.checks.find(x => x.id === 'CHK-VISUAL').execution.result,'not_run');
  assert.equal(review.assessmentValue.criteria.find(x => x.criterionId === 'AC-VISUAL').satisfaction,'insufficient');
  const artifact = byCase('hogwarts-retained-uncovered');
  assert.deepEqual(artifact.receiptValue.evidence.find(e => e.kind === 'artifact_integrity').subjectIds,['SUB-FRESH']);
  assert.deepEqual(artifact.receiptValue.contextSnapshot.criteria.find(c => c.id === 'AC-ARTIFACT').subjectIds,['SUB-RETAINED']);
  assert.equal(artifact.assessmentValue.rules.find(x => x.ruleId === 'COND-ARTIFACT').satisfaction,'insufficient');
});

test('docs-only and unknown impact samples exercise applicability independently of execution', () => {
  const docs = byCase('hogwarts-docs-only');
  assert.equal(docs.receiptValue.checks.filter(c => c.kind === 'machine').length,0);
  assert.equal(docs.assessmentValue.overall.notApplicableRules,4);
  assert.equal(docs.assessmentValue.overall.processReady,true);
  const unknown = byCase('hogwarts-impact-unknown');
  assert.equal(unknown.factsValue.facts['change.public-behavior'],null);
  assert.equal(unknown.assessmentValue.overall.unknownRules,1);
  assert.equal(unknown.assessmentValue.overall.status,'unknown');
});

test('upstream lock pins the reviewed source and retains its license without installing a runtime', () => {
  const lock = read('upstream.lock.json');
  assert.equal(lock.commit,'9cab2b1345cca68177708fc30f2cac6bbe5792b5'); assert.equal(lock.archivedFileCount,116);
  assert.equal(lock.harnessVersion,'0.2.2'); assert.equal(lock.coreVersion,'2.0.0-preview.2');
  for (const file of lock.interfaceFiles) { assert.match(file.sha256,/^[0-9a-f]{64}$/); assert.ok(file.sourceUrl.includes(lock.commit + '/' + file.path)); }
  assert.equal(sha256(readFileSync(new URL('UPSTREAM-LICENSE.txt',root))),lock.interfaceFiles.find(f => f.path === 'packages/structure-core/LICENSE').sha256);
  assert.match(text('UPSTREAM-LICENSE.txt'),/MIT License/);
});
