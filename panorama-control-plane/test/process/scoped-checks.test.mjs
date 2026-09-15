import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture, launch } from './development-helpers.mjs';
import { BOOTSTRAP_V2, BINDINGS_V1, checkerCatalog, validateCheckerContract, verifyCheckerReport, validateCheckCoverage, checkBindingDigest } from '../../src/development/check-contracts.mjs';
import { normalizeBootstrap } from '../../src/development/config.mjs';
import { loadReadContext } from '../../src/development/read-context.mjs';
import { readWork, processStore } from '../../src/development/journal.mjs';
import { bootstrapInput } from '../../src/development/preflight.mjs';
import { draftRequest } from '../../src/development/drafts.mjs';
import { createStandaloneServer } from '../../src/server/standalone-server.mjs';
import { bodyHash } from '../../src/process/json.mjs';

const contract = {formatVersion:'panorama.checker-contract.v1',id:'fixture.behavior',version:'1',title:'Fixture source behavior',subjectKinds:['source_behavior'],formats:['fixture.text'],
  claims:[{id:'source.expected-content',property:'source.txt contains after',evidenceKinds:['behavior_test','interface_test']}],notChecked:['Visual quality or production correctness.'],dependencyRoles:['source','tool'],dependencies:['source.txt and related/**/*.txt; check.mjs; package locks and declared environment.'],
  failureProtocol:'panorama.checker-result.v1',limits:{timeoutMs:20000,maxLogBytes:65536,maxObjectBytes:67108864,limitations:['No OS memory sandbox.']},examples:{positive:['after'],negative:['before; missing report; wrong binding']}};
const scope = completeness => ({id:'behavior',completeness,basis:'The fixture reads source.txt, related/**/*.txt and its registered script only; no service or undeclared input.',selectors:[
  {rootId:'project',include:['source.txt','related/**/*.txt'],exclude:[],role:'source'},
  {rootId:'project',include:['check.mjs'],exclude:[],role:'tool'}]});
function bootstrap(completeness='declared_complete') {return {formatVersion:BOOTSTRAP_V2,inputScopes:[scope(completeness)],checkerContracts:[structuredClone(contract)],runners:[
  {id:'test',command:process.execPath,args:['check.mjs','{executionId}','{subjectDigest:source}'],evidenceKinds:['behavior_test','interface_test'],envKeys:['PANORAMA_P7_CHECK_MODE'],timeoutMs:10000,scopeId:'behavior',checkerContractId:contract.id}]};}
const bindings = {formatVersion:BINDINGS_V1,subjects:[{subjectId:'source',format:'fixture.text',scopeId:'behavior'}],criteria:[{criterionId:'AC',claims:['source.expected-content']}]};
const script = `import {readFileSync} from 'node:fs';
if(readFileSync('source.txt','utf8')!=='after')process.exit(7);
console.log(JSON.stringify({formatVersion:'panorama.checker-result.v1',executionId:process.argv[2],contractId:'fixture.behavior',contractVersion:'1',status:'passed',subjects:[{id:'source',identityDigest:process.argv[3],format:'fixture.text'}],claims:['source.expected-content']}));
`;
async function setup(t,options={}) {
  const f=await fixture(t,{script,bootstrap:bootstrap(options.completeness),work:{checkBindings:structuredClone(bindings),...options.work}});
  await mkdir(join(f.project,'related'));await writeFile(join(f.project,'related/known.txt'),'known');await writeFile(join(f.project,'unrelated.txt'),'unrelated');
  await f.start();await writeFile(join(f.project,'source.txt'),'after');return f;
}
const context=f=>loadReadContext({projectRoot:f.project,dataRoot:f.data},{workId:'work:test'});
async function checked(f) { const r=await f.call(['check','--work','work:test','--runner','test'],0);const ctx=await context(f),store=await processStore(ctx);return {result:r.data,receipt:await store.getReceipt(r.data.receiptId)}; }
async function assessment(f) { const result=(await f.call(['assess','--work','work:test'])).data;assert.ok(result.assessment,JSON.stringify(result));return result; }
const ac=result=>result.assessment.criteria.find(row=>row.criterionId==='AC');

test('P7 versioned declarations preserve legacy bootstrap and reject unsupported scopes/contracts',()=>{
  const root='D:/P7-declaration-only',python=process.execPath;
  const old=normalizeBootstrap({},root,python);assert.equal(old.formatVersion,undefined);assert.equal(old.inputScopes,undefined);
  for(const item of checkerCatalog().contracts)assert.equal(validateCheckerContract(item),item);
  const raw=bootstrap(),normalized=normalizeBootstrap(raw,root,python);
  assert.deepEqual(normalizeBootstrap(bootstrapInput(normalized),root,python),normalized);
  assert.ok(normalized.inputScopes[0].selectors.every(row=>row.exclude.includes('**/.env.*')));
  const invalid=bootstrap();invalid.inputScopes[0].selectors[0].rootId='outside';assert.throws(()=>normalizeBootstrap(invalid,root,python),error=>error.code==='INPUT_SCOPE_ROOT_INVALID');
  const expanded=bootstrap();expanded.roots=[{id:'project',kind:'project',description:'Narrow',include:['source.txt']}];assert.throws(()=>normalizeBootstrap(expanded,root,python),error=>error.code==='INPUT_SCOPE_NOT_AUTHORIZED');
  const noDependency=bootstrap();noDependency.inputScopes[0].selectors.pop();assert.throws(()=>normalizeBootstrap(noDependency,root,python),error=>error.code==='CHECKER_DEPENDENCIES_UNDECLARED');
  const bad=checkerCatalog().contracts[0];bad.claims[0].id='visual.quality';assert.throws(()=>validateCheckerContract(bad),error=>error.code==='BUILTIN_CHECKER_CONTRACT_CHANGED');
});

test('P7 non-file subject, machine inputs and environment stay current for unrelated files; broad scan still denies outside work scope',async t=>{
  const f=await setup(t),{receipt}=await checked(f);
  const machine=receipt.checks.find(row=>row.kind==='machine');assert.ok(machine.inputSetIds.every(id=>id.startsWith('scope:')));
  assert.ok(receipt.checks[0].inputSetIds.includes('input:project'));
  const original=await assessment(f);assert.equal(ac(original).satisfaction,'satisfied',JSON.stringify(original));
  await writeFile(join(f.project,'unrelated.txt'),'changed');
  const after=await assessment(f);assert.equal(ac(after).satisfaction,'satisfied');assert.equal(ac(after).freshness.status,'current');assert.equal(after.policy.decision,'deny');assert.ok(after.changedPaths.includes('unrelated.txt'));
  assert.equal(after.assessment.overall.processReady,true); // Evidence readiness and scope permission stay separate.
  const ctx=await context(f),work=await readWork(ctx,'work:test');assert.equal(work.formatVersion,'panorama.development-work.v3');assert.equal(receipt.extensions['panorama.development.check-binding'].bindingsDigest,work.checkBindingsDigest);
  assert.ok(receipt.contextSnapshot.baseline.inputSets.some(row=>row.id===machine.inputSetIds[0]));
  const server=await createStandaloneServer({projectRoot:f.project,dataRoot:f.data,modelConfig:{available:false,label:'External'}});t.after(()=>server.close());
  const response=await fetch(server.origin+'/api/standalone/process/preview',{method:'POST',headers:{authorization:'Bearer '+server.capability,origin:server.origin,'content-type':'application/json'},body:JSON.stringify({workItemId:'work:test'})});assert.equal(response.status,200);
  const api=await response.json();assert.deepEqual(ac(api),ac(after));
});

test('P7 related source, matching additions/deletions, locks, checker and environment each invalidate prior evidence',async t=>{
  const f=await setup(t);await checked(f);
  const scenarios=[
    ['related source',async()=>writeFile(join(f.project,'related/known.txt'),'changed'),async()=>writeFile(join(f.project,'related/known.txt'),'known')],
    ['matching addition',async()=>writeFile(join(f.project,'related/new.txt'),'new'),async()=>unlink(join(f.project,'related/new.txt'))],
    ['matching deletion',async()=>unlink(join(f.project,'related/known.txt')),async()=>writeFile(join(f.project,'related/known.txt'),'known')],
    ['lock addition',async()=>writeFile(join(f.project,'pnpm-lock.yaml'),'lockfileVersion: 9'),async()=>unlink(join(f.project,'pnpm-lock.yaml'))],
    ['checker',async()=>writeFile(join(f.project,'check.mjs'),script+'\n// changed checker'),async()=>writeFile(join(f.project,'check.mjs'),script)],
  ];
  for(const [label,change,restore]of scenarios) {await change();assert.equal(ac(await assessment(f)).freshness.status,'stale',label);await restore();assert.equal(ac(await assessment(f)).freshness.status,'current',label+' restored');}
  const env=await launch(['assess','--work','work:test','--project',f.project],{cwd:f.project,env:{...f.env,PANORAMA_P7_CHECK_MODE:'changed'}}).completed;
  assert.equal(ac(env.data).freshness.status,'stale');assert.ok(ac(env.data).freshness.changedIds.some(id=>id.startsWith('execution:')));
  assert.equal(JSON.stringify(env.data).includes('"changed"'),false);
});

test('P7 incomplete dependency declarations fall back wide without claiming narrow evidence',async t=>{
  const f=await setup(t,{completeness:'incomplete'}),{receipt}=await checked(f);
  assert.deepEqual(receipt.checks.find(row=>row.kind==='machine').inputSetIds,['input:project']);
  assert.equal(receipt.extensions['panorama.development.check-binding'].coverage.scopeMode,'whole_declared_inputs');
  await writeFile(join(f.project,'unrelated.txt'),'changed');assert.equal(ac(await assessment(f)).freshness.status,'stale');
});

test('P7 swallowed failures retain the real zero exit and cannot produce a passing criterion',async t=>{
  const f=await setup(t);await writeFile(join(f.project,'check.mjs'),"try{throw Error('swallowed')}catch{}\n");
  const run=await f.call(['check','--work','work:test','--runner','test'],2);
  assert.equal(run.data.result,'errored');assert.equal(run.data.recovery[0].exitCode,0);
  const ctx=await context(f),receipt=await (await processStore(ctx)).getReceipt(run.data.receiptId);
  assert.equal(receipt.extensions['panorama.development.check-binding'].rawExecution.result,'passed');
  assert.equal(receipt.checks.find(row=>row.kind==='machine').execution.result,'errored');
  assert.equal(ac(await assessment(f)).satisfaction,'insufficient');
});

test('P7 unsupported claims and formats are rejected before executing a runner',async t=>{
  const f=await setup(t,{work:{checkBindings:{...bindings,criteria:[{criterionId:'AC',claims:['visual.quality']}]}}});
  const validation=await f.call(['validate','--for','check','--work','work:test','--runner','test','--input',await f.input('selection',{})],2);
  assert.ok(validation.data.errors.some(row=>row.code==='CHECKER_CLAIM_NOT_COVERED'));
  const refused=await f.call(['check','--work','work:test','--runner','test'],1);assert.equal(refused.data.error,'CHECKER_CLAIM_NOT_COVERED');
  assert.deepEqual((await readWork(await context(f),'work:test')).runs,[]);
  const matrix=(await f.call(['matrix','--work','work:test'],0)).data;assert.equal(matrix.coverage[0].runners[0].status,'uncovered');
});

test('P7 receipt extension cannot be rebound to a different claim declaration',async t=>{
  const f=await setup(t),{receipt}=await checked(f),changed=structuredClone(receipt);delete changed.extensions['panorama.development.check-binding'];changed.receiptHash=bodyHash(changed,'receiptHash');
  const result=await f.call(['import','--work','work:test','--input',await f.input('changed',changed)],1);assert.equal(result.data.error,'RECEIPT_CHECK_BINDING_MISMATCH');
});

test('P7 matrix uses actual public document changes and matches the read-only API without journal writes',async t=>{
  const f=await fixture(t,{bootstrap:{...bootstrap(),publicPaths:['api/**']}});
  await mkdir(join(f.project,'api'));await writeFile(join(f.project,'api/README.md'),'old promise');
  const seed={goal:'Update API description',expectedOutcome:'Accurate description',plannedPaths:['api/**']};
  const draft=(await f.call(['draft','--for','begin','--recipe','document-maintenance','--input',await f.input('seed',seed)],0)).data;
  assert.equal(draft.request.publicBehavior,null);assert.equal(draft.recipe.id,'document-maintenance');
  await f.call(['begin','--work','work:docs','--input',await f.input('docs',draft.request)],0);
  await writeFile(join(f.project,'api/README.md'),'new public promise');
  const ctx=await loadReadContext({projectRoot:f.project,dataRoot:f.data},{workId:'work:docs'}),before=JSON.stringify(await readWork(ctx,'work:docs'));
  const matrix=(await f.call(['matrix','--work','work:docs'],0)).data;
  assert.equal(matrix.rules.length,10);assert.equal(matrix.rules.filter(row=>row.strength==='required').length,6);
  assert.equal(matrix.rules.find(row=>row.evaluator.id==='process.interface').applicability.status,'applicable');
  const server=await createStandaloneServer({projectRoot:f.project,dataRoot:f.data,modelConfig:{available:false,label:'External'}});t.after(()=>server.close());
  const response=await fetch(server.origin+'/api/standalone/process/matrix?id=work%3Adocs',{headers:{authorization:'Bearer '+server.capability}});assert.equal(response.status,200);
  const api=await response.json();assert.deepEqual(api.rules,matrix.rules);assert.deepEqual(api.coverage,matrix.coverage);
  assert.equal(JSON.stringify(await readWork(ctx,'work:docs')),before);
  const assessed=(await f.call(['assess','--work','work:docs'],2)).data;assert.notEqual(assessed.assessment.rules.find(row=>row.ruleId===matrix.rules.find(row=>row.evaluator.id==='process.interface').ruleId).satisfaction,'satisfied');
});

test('P7 report protocols reject wrong execution/object, unsupported claims, truncated or ambiguous success',()=>{
  const params={stdout:JSON.stringify({formatVersion:'panorama.checker-result.v1',executionId:'execution:a',contractId:contract.id,contractVersion:'1',status:'passed',subjects:[{id:'source',identityDigest:'a'.repeat(64),format:'fixture.text'}],claims:['source.expected-content']}),truncated:false,executionId:'execution:a',subjects:[{id:'source',identityDigest:'a'.repeat(64)}],claims:['source.expected-content'],formats:{source:'fixture.text'}};
  assert.equal(verifyCheckerReport(contract,params).status,'verified');
  for(const variant of [{executionId:'execution:b'},{subjects:[{id:'source',identityDigest:'b'.repeat(64)}]},{claims:['unknown']},{truncated:true},{stdout:params.stdout+'\n'+params.stdout}])assert.equal(verifyCheckerReport(contract,{...params,...variant}).status,'unknown');
});

test('P7 unknown object format, mismatched source scope and integrity-to-visual claims are rejected',()=>{
  const bootstrapConfig=normalizeBootstrap(bootstrap(),'D:/fixture',process.execPath),ctx={local:{bootstrap:bootstrapConfig}};
  const work={formatVersion:'panorama.development-work.v3',workItemRef:{id:'work:x',definitionDigest:'a'.repeat(64)},subjects:[{id:'source',kind:'source_behavior',locator:null}],context:{criteria:[{id:'AC',subjectIds:['source'],allowedEvidenceKinds:['behavior_test']}]},checkBindings:structuredClone(bindings)};
  const validate=()=>{work.checkBindingsDigest=checkBindingDigest(work);return validateCheckCoverage(ctx,work,bootstrapConfig.runners[0],{criterionIds:['AC'],subjectIds:['source']});};
  work.checkBindings.subjects[0].format='unsupported';assert.throws(validate,error=>error.code==='CHECKER_SUBJECT_FORMAT_UNSUPPORTED');
  work.checkBindings.subjects[0].format='fixture.text';work.checkBindings.subjects[0].scopeId=null;assert.throws(validate,error=>error.code==='SOURCE_SUBJECT_SCOPE_MISMATCH');
  work.checkBindings.subjects[0].scopeId='behavior';work.checkBindings.criteria[0].claims=['visual.quality'];assert.throws(validate,error=>error.code==='CHECKER_CLAIM_NOT_COVERED');
});

test('P7 all three recipes are editable and human requirements remain human',async t=>{
  const f=await fixture(t,{bootstrap:bootstrap()}),ctx=await loadReadContext({projectRoot:f.project,dataRoot:f.data});
  const input={goal:'Fix fixture',expectedOutcome:'after',plannedPaths:['source.txt']};
  const fix=draftRequest(ctx,'begin',input,{recipeId:'public-behavior-fix',runnerId:'test'});assert.equal(fix.valid,true,JSON.stringify(fix));assert.equal(fix.request.impact,'interface');assert.equal(fix.request.publicBehavior,null);
  const human=draftRequest(ctx,'begin',{...input,criteria:[{id:'visual',requirement:'Human verifies the design',reviewKind:'visual_review',acceptedActorKinds:['human'],claims:[]}]},{recipeId:'retained-artifact-delivery'});
  assert.equal(human.valid,false);assert.equal(human.request.subjects[0].locator,null);assert.deepEqual(human.request.criteria[0].acceptedActorKinds,['human']);
});
