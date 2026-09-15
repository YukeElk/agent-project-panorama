import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, readdir, stat, access } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { fixture } from './development-helpers.mjs';
import { draftRequest } from '../../src/development/drafts.mjs';
import { validateRequest } from '../../src/development/request-validation.mjs';
import { summarize, exitStatus } from '../../src/development/presentation.mjs';
import { withLock } from '../../src/development/io.mjs';
import { startTiming, timingResult } from '../../src/development/timing.mjs';
import { temporary } from './temp.mjs';

const context = {
  project:{moduleBindings:[{moduleId:'root'}]},
  local:{bootstrap:{publicPaths:['api/**'],roots:[{id:'project',kind:'project',allow:['**'],selector:{exclude:['secrets/**']}}],runners:[{id:'unit',evidenceKinds:['behavior_test'],args:['check.mjs']}]}}
};
const intent = {goal:'修复行为',expectedOutcome:'按给定样本处理',plannedPaths:['source.txt'],publicBehavior:false,
  subjects:[{kind:'source_behavior'}],criteria:[{requirement:'保留输入语义并通过样本',runnerId:'unit'}]};

test('P5 compact work preserves explicit semantics and defaults only registered metadata', () => {
  const source=structuredClone(intent), result=draftRequest(context,'begin',source);
  assert.equal(result.valid,true,JSON.stringify(result)); assert.deepEqual(source,intent);
  assert.equal(result.request.criteria[0].requirement,intent.criteria[0].requirement);
  assert.deepEqual(result.request.criteria[0].subjectIds,[result.request.subjects[0].id]);
  assert.equal(result.request.publicBehavior,false);
  assert.equal(result.defaults.some(item => item.path === '/goal'),false);
  const human=draftRequest(context,'begin',{...intent,criteria:[{requirement:'人工核对',reviewKind:'visual_review',acceptedActorKinds:['human'],required:true}]});
  assert.deepEqual(human.request.criteria[0].acceptedActorKinds,['human']);
  assert.ok(human.warnings.some(row => row.code === 'HUMAN_SOURCE_NOT_AVAILABLE'));
});

test('P5 drafts retain unknowns, missing review, remaining outcome and public-path implications', () => {
  const missing=draftRequest(context,'begin',{});
  assert.equal(missing.valid,false); assert.equal(missing.request.publicBehavior,null);
  assert.ok(missing.errors.some(row => row.path === '/goal'));
  const publicDoc=draftRequest(context,'begin',{...intent,plannedPaths:['api/README.md'],criteria:[{requirement:'修改公开接口承诺',reviewKind:'document_review'}]});
  assert.ok(publicDoc.warnings.some(row => row.code === 'PUBLIC_BEHAVIOR_EXPECTED'));
  const work={context:{criteria:publicDoc.request.criteria},subjects:publicDoc.request.subjects,receipts:[]};
  const review=draftRequest(context,'review',{kind:'document_review'},{work});
  assert.equal(review.request.result,null); assert.equal(review.valid,false);
  const finish=draftRequest(context,'finish',{summary:'还需要样本'},{work});
  assert.equal(finish.request.outcome.incomplete,null); assert.equal(finish.valid,false);
  const incomplete=draftRequest(context,'finish',{summary:'尚缺样本',incomplete:['补充样本'],resumeNotes:[]},{work});
  assert.equal(incomplete.valid,true); assert.deepEqual(incomplete.request.outcome.incomplete,['补充样本']);
  assert.ok(incomplete.warnings.some(row => row.code === 'INCOMPLETE_REMAINS'));
  assert.equal(validateRequest(context,'finish',{outcome:{summary:'已记录',incomplete:[],resumeNotes:[]},selection:null},{work}).valid,true);
});

test('P5 validation reports nested fields, unknown properties and incompatible bindings', () => {
  const request=draftRequest(context,'begin',intent).request;
  const invalid=structuredClone(request); invalid.criteria[0].required='yes'; invalid.subjects[0].locator={rootId:'project',path:'../outside'};
  const fields=validateRequest(context,'begin',invalid);
  assert.equal(fields.valid,false); assert.ok(fields.errors.some(row => row.path === '/criteria/0/required'));
  assert.match(fields.errors.find(row => row.path === '/criteria/0/required').message,/boolean/);
  assert.ok(fields.errors.some(row => row.path === '/subjects/0/locator/path'));
  assert.equal(validateRequest(context,'begin',{...request,unknown:true}).valid,false);
  const duplicate=structuredClone(request); duplicate.criteria.push(duplicate.criteria[0]);
  assert.ok(validateRequest(context,'begin',duplicate).errors.some(row => row.code === 'CRITERION_ID_DUPLICATE'));
  const outside=structuredClone(request); outside.subjects[0].locator={rootId:'project',path:'secrets/key.txt'};
  assert.ok(validateRequest(context,'begin',outside).errors.some(row => row.code === 'SUBJECT_OUTSIDE_INPUT_SCOPE'));
  const wrong=structuredClone(request); wrong.criteria[0].subjectIds=['absent'];
  assert.ok(validateRequest(context,'begin',wrong).errors.some(row => row.code === 'SUBJECT_NOT_DEFINED'));
  const work={context:{criteria:request.criteria},subjects:request.subjects,receipts:[]};
  assert.equal(validateRequest(context,'check',{criterionIds:['missing']},{work,runnerId:'unit'}).valid,false);
});

test('P5 ambiguous drafts require a selection, while legacy implicit selection stays visible', () => {
  const request=draftRequest(context,'begin',{...intent,criteria:[{id:'one',requirement:'行为一',runnerId:'unit'},{id:'two',requirement:'行为二',runnerId:'unit'}]}).request;
  const work={context:{criteria:request.criteria},subjects:request.subjects,receipts:[]};
  const draft=draftRequest(context,'check',{}, {work,runnerId:'unit'});
  assert.equal(draft.valid,false); assert.equal(draft.request.criterionIds,null); assert.equal(draft.suggestions.length,2);
  const legacy=validateRequest(context,'check',{}, {work,runnerId:'unit'});
  assert.equal(legacy.valid,true); assert.ok(legacy.warnings.some(row => row.code === 'IMPLICIT_MULTIPLE_CRITERIA'));
  const explicit=draftRequest(context,'check',{criterionIds:['two']},{work,runnerId:'unit'});
  assert.equal(explicit.valid,true); assert.deepEqual(explicit.request.criterionIds,['two']);
});

async function fingerprint(roots) {
  const result=[];
  async function walk(path) {
    for (const entry of await readdir(path,{withFileTypes:true})) {
      const next=join(path,entry.name);
      if(entry.isDirectory()) await walk(next);
      else {const meta=await stat(next);result.push({path:next,mtimeMs:meta.mtimeMs,sha:createHash('sha256').update(await readFile(next)).digest('hex')});}
    }
  }
  for(const root of roots)await walk(root);
  return result.sort((a,b)=>a.path.localeCompare(b.path));
}

test('P5 draft/validate are read-only, create usable exclusive request files, and do not start core', async t => {
  const f=await fixture(t);
  const before=await fingerprint([f.project,f.data,f.home]);
  const seed={...intent,criteria:[{requirement:'source.txt contains after',runnerId:'test'}]};
  const requestFile=join(f.directory,'prepared-work.json');
  const drafted=await f.call(['draft','--for','begin','--input',await f.input('intent',seed),'--output',requestFile,'--format','summary','--timings','on'],0);
  assert.equal(drafted.data.valid,true); assert.equal(drafted.data.processEvidenceReady,null);
  assert.equal(drafted.data.request,undefined); assert.ok(drafted.data.nextActions.some(item=>item.action==='validate'));
  assert.equal(drafted.data.timing.phases.some(item=>item.phase==='core'),false);
  const checked=await f.call(['validate','--for','begin','--input',requestFile],0);
  assert.equal(checked.data.valid,true);
  await f.call(['draft','--for','begin','--output',requestFile],1);
  assert.deepEqual(await fingerprint([f.project,f.data,f.home]),before);
  await f.call(['begin','--work','work:prepared','--input',requestFile],0);
  const review=await f.call(['draft','--for','review','--work','work:prepared','--input',await f.input('incomplete-review',{kind:'document_review'})],2);
  assert.equal(review.data.request.result,null);
});

test('P5 preparation of uninitialized project does not create location or process storage', async t => {
  const f=await fixture(t,{init:false});
  await f.call(['draft','--for','begin'],1);
  await assert.rejects(access(f.home)); await assert.rejects(access(f.data));
  await assert.rejects(access(join(f.project,'.structure')));
});

test('P5 real behavior failure, repair, finish and stale resume retain facts in summaries and timings', async t => {
  const f=await fixture(t);
  const seed={...intent,criteria:[{id:'AC',requirement:'source.txt contains after',runnerId:'test'}]};
  const draft=await f.call(['draft','--for','begin','--input',await f.input('intent',seed)],0);
  await f.call(['begin','--work','work:test','--input',await f.input('generated',draft.data.request),'--format','summary'],0);
  const failed=await f.call(['check','--work','work:test','--runner','test','--format','summary','--timings','on'],2);
  assert.equal(failed.data.execution.exitCode,7); assert.equal(failed.data.result,'failed');
  assert.ok(failed.data.execution.workerTiming.phases.some(row=>row.phase==='business'&&row.inclusiveMs>0));
  assert.ok(failed.data.timing.phases.some(row=>row.phase==='executor_wait'));
  assert.equal(failed.stdout.includes('example-secret-marker'),false);
  assert.match(await readFile(join(failed.data.runDirectory,'stdout.log'),'utf8'),/example-secret-marker/);
  await writeFile(join(f.project,'source.txt'),'after');
  const scope=await f.call(['draft','--for','check','--work','work:test','--runner','test'],0);
  await f.call(['validate','--for','check','--work','work:test','--runner','test','--input',await f.input('scope',scope.data.request)],0);
  await f.call(['check','--work','work:test','--runner','test','--input',await f.input('scope',scope.data.request)],0);
  const done=await f.call(['finish','--work','work:test','--input',await f.input('outcome',{outcome:{summary:'修复并核对',incomplete:[],resumeNotes:['沿用原工作']}}),'--format','summary','--timings','on'],0);
  assert.equal(done.data.finished,true); assert.equal(done.data.processEvidenceReady,true); assert.equal(done.data.baseline.state,'observed');
  assert.equal(done.data.criterionDefinitions[0].requirement,seed.criteria[0].requirement);
  assert.ok(done.data.timing.phases.some(row=>row.phase==='assessment')); assert.ok(done.data.criteria.every(row=>row.satisfaction==='satisfied'));
  await writeFile(join(f.project,'source.txt'),'changed later');
  const resumed=await f.call(['resume','--work','work:test','--format','summary'],2);
  assert.equal(resumed.data.coreState,'done'); assert.equal(resumed.data.currentProcessReady,false);
  assert.equal(resumed.data.baseline.id,done.data.baseline.id); assert.ok(resumed.data.criteria.some(row=>row.freshness.status==='stale'));
});

test('P5 document-only review cannot hide changed public API requirements', async t => {
  const f=await fixture(t,{bootstrap:{publicPaths:['README.md']}});
  const seed={goal:'更新 API 文档',expectedOutcome:'准确描述公开行为',plannedPaths:['README.md'],publicBehavior:false,subjects:[{kind:'document',locator:{rootId:'project',path:'README.md'}}],criteria:[{requirement:'回读新承诺',reviewKind:'document_review'}]};
  const draft=await f.call(['draft','--for','begin','--input',await f.input('intent',seed)],0);
  assert.ok(draft.data.warnings.some(row=>row.code==='PUBLIC_BEHAVIOR_EXPECTED'));
  await f.call(['begin','--work','work:doc','--input',await f.input('work',draft.data.request)],0);
  await writeFile(join(f.project,'README.md'),'The public API accepts the new form.');
  const review=await f.call(['draft','--for','review','--work','work:doc','--input',await f.input('review-intent',{kind:'document_review',result:'passed',summary:'回读公开承诺',method:'逐项对照文档',findings:['文档写明新输入形式，行为检查尚缺。']})],0);
  await f.call(['review','--work','work:doc','--input',await f.input('review',review.data.request)],0);
  const result=await f.call(['finish','--work','work:doc','--input',await f.input('finish',{outcome:{summary:'只有文档回读',incomplete:[],resumeNotes:[]}}),'--format','summary'],2);
  assert.equal(result.data.finished,false); assert.equal(result.data.processEvidenceReady,false);
  const publicRule=result.data.rules.find(row=>row.ruleId==='COND-INTERFACE');
  assert.equal(publicRule.applicability.status,'applicable'); assert.notEqual(publicRule.satisfaction,'satisfied');
});

test('P5 artifact validation requires actual argv binding and stale summaries preserve object failure', async t => {
  const f=await fixture(t,{init:false,script:"import {readFileSync} from 'node:fs'; process.exit(readFileSync(process.argv[2],'utf8') === 'valid retained object' ? 0 : 9);"});
  f.bootstrap.roots.push({id:'delivery',kind:'artifact',description:'Retained object',path:f.external,include:['bundle.txt']});
  f.bootstrap.runners[0].args=['check.mjs','{subject:bundle}']; f.bootstrap.runners[0].evidenceKinds=['artifact_integrity'];
  await writeFile(join(f.external,'bundle.txt'),'valid retained object');
  await f.call(['init','--data',f.data,'--input',await f.input('bootstrap',f.bootstrap)],0);
  const seed={goal:'检查留存对象',expectedOutcome:'实际留存文件内容符合约定',plannedPaths:['source.txt'],publicBehavior:false,
    subjects:[{id:'bundle',kind:'retained_artifact',locator:{rootId:'delivery',path:'bundle.txt'}}],criteria:[{requirement:'检查实际留存文件',runnerId:'test'}]};
  const drafted=await f.call(['draft','--for','begin','--input',await f.input('intent',seed)],0);
  await f.call(['begin','--work','work:artifact','--input',await f.input('work',drafted.data.request)],0);
  const scope=await f.call(['draft','--for','check','--work','work:artifact','--runner','test'],0);
  await f.call(['validate','--for','check','--work','work:artifact','--runner','test','--input',await f.input('scope',scope.data.request)],0);
  await f.call(['check','--work','work:artifact','--runner','test','--input',await f.input('scope',scope.data.request)],0);
  await f.call(['finish','--work','work:artifact','--input',await f.input('finish',{outcome:{summary:'留存文件检查完成',incomplete:[],resumeNotes:[]}})],0);
  await writeFile(join(f.external,'bundle.txt'),'corrupt');
  const result=await f.call(['resume','--work','work:artifact','--format','summary'],2);
  assert.equal(result.data.currentProcessReady,false); assert.ok(result.data.criteria.some(row=>row.freshness.status==='stale'));
  const mockWork={context:{criteria:drafted.data.request.criteria},subjects:drafted.data.request.subjects,receipts:[]};
  const mockCtx=structuredClone(context); mockCtx.local.bootstrap.runners[0].evidenceKinds=['artifact_integrity'];
  assert.ok(validateRequest(mockCtx,'check',scope.data.request,{work:mockWork,runnerId:'unit'}).errors.some(row=>row.code==='RUNNER_OBJECT_NOT_BOUND'));
});

test('P5 summaries retain conflicts, source selection and unavailable readiness', () => {
  const original={workItemId:'work:test',scopeComplete:true,policy:{decision:'allow'},criteria:[{id:'one',requirement:'实际要求'}],assessment:{overall:{processReady:false},criteria:[{criterionId:'one',satisfaction:'conflict',freshness:{status:'current'},reasonCodes:['CONFLICT']}],rules:[{ruleId:'required',satisfaction:'insufficient'}]},selectedReceiptIds:['receipt:failed','receipt:passed'],selectionReason:'Keep both'};
  const result=summarize('assess',original);
  assert.equal(exitStatus(original),exitStatus({...original})); assert.equal(result.operationExitCode,2);
  assert.deepEqual(result.criteria,original.assessment.criteria); assert.deepEqual(result.selection.receiptIds,original.selectedReceiptIds);
  assert.deepEqual(result.criterionDefinitions,original.criteria);
  assert.equal(summarize('scan',{scopeComplete:true}).processEvidenceReady,null);
  const reviewed=summarize('review',{receiptId:'receipt:review',result:'passed'},{projectRoot:'D:/demo',workId:'work:original'});
  assert.ok(reviewed.nextActions.some(row=>row.action==='resume'&&row.args.includes('work:original')));
  assert.deepEqual(summarize('draft',{for:'begin',valid:false},{workId:'work:not-created'}).nextActions,[]);
});

test('P5 lock timing includes the real retry wait, not just acquisition attempts', async t => {
  const file=join(await temporary(t),'held.lock');
  let owned,release;
  const ownership=new Promise(resolve=>{owned=resolve;}), hold=new Promise(resolve=>{release=resolve;});
  const holding=withLock(file,async()=>{owned();await hold;});
  await ownership;
  const clock=startTiming(), waiting=withLock(file,async()=>true);
  try {
    await new Promise(resolve=>setTimeout(resolve,80)); release();
    assert.equal(await waiting,true); await holding;
    const measured=timingResult(clock).phases.find(row=>row.phase==='lock_wait');
    assert.equal(measured.calls,1); assert.ok(measured.inclusiveMs >= 60,JSON.stringify(measured));
  } finally {release();await holding;}
});
