import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile, access, rename, unlink } from 'node:fs/promises';
import { join, resolve, dirname } from 'node:path';
import { fork } from 'node:child_process';
import { fixture, launch, python } from './development-helpers.mjs';
import { temporary } from './temp.mjs';
import { loadReadContext } from '../../src/development/read-context.mjs';
import { readWork, readWorkHeader, processStore } from '../../src/development/journal.mjs';
import { planConfiguration, applyConfiguration } from '../../src/development/configuration.mjs';
import { localPath, revisionId, pendingPath } from '../../src/development/configuration-state.mjs';
import { readJson, writeJson } from '../../src/development/io.mjs';
import { createStandaloneServer } from '../../src/server/standalone-server.mjs';

const locations=f=>({projectRoot:f.project,dataRoot:f.data});
const context=(f,id=null)=>loadReadContext(locations(f),{workId:id});
async function finishBehavior(f,id='work:old') {
  await f.start(id);await writeFile(join(f.project,'source.txt'),'after');
  await f.call(['check','--work',id,'--runner','test'],0);
  return f.call(['finish','--work',id,'--input',await f.input('finish',{outcome:{summary:'Measured old configuration behavior',incomplete:[],resumeNotes:[]}})],0);
}
async function plan(f,request) {return (await f.call(['config-plan','--input',await f.input('change',request)],0)).data.plan;}
async function apply(f,request) {return f.call(['config-apply','--input',await f.input('plan',request)],0);}
async function receipts(f,id) {const ctx=await context(f,id),work=await readWork(ctx,id),store=await processStore(ctx);return Promise.all(work.receipts.map(async row=>[row.id,JSON.stringify(await store.getReceipt(row.id))]));}
async function api(server,path,body) {
  const response=await fetch(server.origin+'/api/standalone/'+path,{method:body?'POST':'GET',headers:{authorization:'Bearer '+server.capability,...(body?{origin:server.origin,'content-type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  assert.equal(response.status,200);return response.json();
}

test('P6 empty preflight only reads metadata and probes the fixed runtime',async t=>{
  const directory=await temporary(t),project=join(directory,'empty'),data=join(directory,'data'),host=join(directory,'host');await mkdir(project);
  await writeFile(join(project,'package.json'),JSON.stringify({scripts:{test:'create-a-marker-with-project-code'}}));
  await writeFile(join(project,'.coverage'),'ignored cache');
  const result=await launch(['preflight','--project',project,'--data',data,'--python',python],{env:{PANORAMA_STATE_HOME:host}}).completed;
  assert.equal(result.code,0,result.stderr);assert.equal(result.data.valid,true);
  assert.deepEqual(result.data.draft.runners,[]);assert.deepEqual(result.data.packageScripts,['test']);
  assert.ok(result.data.observations.some(row=>row.path==='.coverage'));
  assert.ok(result.data.warnings.some(row=>row.code==='NO_RUNNER_REGISTERED'));
  await assert.rejects(access(host));await assert.rejects(access(data));await assert.rejects(access(join(project,'.structure')));
});

test('P6 revisions retain old receipts and baseline; withdrawn runner and scope agree in CLI and API',async t=>{
  const f=await fixture(t);await finishBehavior(f);
  const original=await context(f,'work:old'),oldWork=await readWork(original,'work:old'),oldReceipts=await receipts(f,'work:old');
  const request={reason:'Update checker and exclude generated cache',bootstrap:{...f.bootstrap,roots:f.bootstrap.roots.map(root=>({...root,exclude:[...(root.exclude??[]),'cache/**']})),runners:[{id:'test',command:python,args:['-c','raise SystemExit(0)'],evidenceKinds:['behavior_test']} ]}};
  const first=await plan(f,request),competing=await plan(f,{...request,reason:'Competing stale plan'});
  const updated=await apply(f,first);
  assert.notEqual(updated.data.revisionId,revisionId(original.local));
  await f.start('work:upgraded');await f.call(['check','--work','work:upgraded','--runner','test'],0);
  await f.call(['finish','--work','work:upgraded','--input',await f.input('upgraded-done',{outcome:{summary:'Run the changed Python checker',incomplete:[],resumeNotes:[]}})],0);
  await f.call(['config-apply','--input',await f.input('stale',competing)],1);
  const resumed=await f.call(['resume','--work','work:old'],2);
  assert.equal(resumed.data.configuration.revisionId,revisionId(original.local));assert.equal(resumed.data.configuration.historical,true);
  assert.equal(JSON.stringify(resumed.data.baseline),JSON.stringify(oldWork.context.baseline));
  assert.deepEqual(resumed.data.configuration.withdrawnRunners,['test']);assert.deepEqual(resumed.data.configuration.withdrawnRoots,['project']);
  for(const [id,value]of oldReceipts)assert.equal(JSON.stringify(await (await processStore(await context(f,'work:old'))).getReceipt(id)),value);
  const server=await createStandaloneServer({...locations(f),modelConfig:{available:false,label:'External'}});t.after(()=>server.close());
  const catalog=await api(server,'process');assert.equal(catalog.configuration.activeRevisionId,updated.data.revisionId);
  const detail=await api(server,'process/work?id=work%3Aold');assert.equal(detail.configuration.revisionId,resumed.data.configuration.revisionId);
  const preview=await api(server,'process/preview',{workItemId:'work:old'});
  assert.equal(preview.currentEvidenceReady,false);
  assert.deepEqual(preview.assessment.criteria.map(row=>[row.criterionId,row.satisfaction,row.freshness.status]),resumed.data.assessment.criteria.map(row=>[row.criterionId,row.satisfaction,row.freshness.status]));
  const restored=await apply(f,await plan(f,{reason:'Restore prior checker definitions',restoreRevisionId:revisionId(original.local)}));
  assert.notEqual(restored.data.revisionId,revisionId(original.local));assert.equal(restored.data.configurationDigest,original.local.configurationDigest);
  const retry=await apply(f,first);assert.equal(retry.data.idempotent,true);assert.equal(retry.data.activeRevisionId,restored.data.revisionId);
  await f.start('work:new');assert.equal((await readWork(await context(f),'work:new')).configurationRevisionId,restored.data.revisionId);
});

test('P6 killed switch is recovered after activation, without rewriting its original revision',async t=>{
  const f=await fixture(t),prepared=await plan(f,{reason:'Exercise actual process interruption'});
  const request=await f.input('crash',{...locations(f),plan:prepared,pause:'active_written'});
  const child=fork(new URL('configuration-crash-worker.mjs',import.meta.url),[request],{windowsHide:true,stdio:['ignore','pipe','pipe','ipc']});
  const exited=new Promise(done=>child.once('exit',done));t.after(()=>{if(child.exitCode===null)child.kill();});
  await new Promise((done,reject)=>{const timer=setTimeout(()=>reject(Error('No configuration checkpoint')),30000);child.once('message',()=>{clearTimeout(timer);done();});child.once('error',reject);});
  child.kill();await exited;
  const active=await readJson(localPath(f.data));assert.notEqual(active.revisionId,prepared.expectedRevisionId);
  assert.equal((await f.call(['config-status'],2)).data.activeRevisionId,active.revisionId);
  await f.call(['config-recover'],0);assert.equal((await context(f)).local.revisionId,active.revisionId);
});

test('P6 rescan recovers only the exact upstream pre-swap tree; orphaned intent does not lock new work',async t=>{
  const f=await fixture(t);await mkdir(join(f.project,'src'));await writeFile(join(f.project,'src/new.mjs'),'export const value=1;');
  const prepared=await plan(f,{reason:'Discover new module with recovery',refreshModules:true});
  await assert.rejects(applyConfiguration(locations(f),prepared,{onPhase:phase=>{if(phase==='prepared')throw Error('Cut');}}),/Cut/);
  const before=resolve(f.project,'.structure'),backup=resolve(f.project,'.structure-backup-'+'a'.repeat(32));
  assert.equal(dirname(before),f.project);assert.equal(dirname(backup),f.project); // Bound both rename targets to the isolated fixture.
  await rename(before,backup);
  await f.call(['config-recover'],0);assert.ok((await context(f)).project.moduleBindings.some(row=>row.moduleId==='src'));
  const another=await plan(f,{reason:'Orphaned intent before pending marker'});
  await assert.rejects(applyConfiguration(locations(f),another,{onPhase:phase=>{if(phase==='prepared')throw Error('Cut');}}),/Cut/);
  await unlink(pendingPath(f.data)); // Reproduce saved intent with no published marker.
  await f.start('work:after-intent');
  const refused=await f.call(['config-apply','--input',await f.input('retry',another)],1);assert.equal(refused.data.error,'CONFIGURATION_WORK_CONFLICT');
  assert.equal((await f.call(['config-status'],0)).data.ready,true);
});

test('P6 active work blocks switching and a cut between declaration and activation is recoverable',async t=>{
  const f=await fixture(t);await f.start('work:open');
  const request={reason:'Add a runner',bootstrap:{...f.bootstrap,runners:[...f.bootstrap.runners,{...f.bootstrap.runners[0],id:'another'}]}};
  const blocked=await f.call(['config-plan','--input',await f.input('blocked',request)],2);
  assert.ok(blocked.data.blockers.some(row=>row.workItemId==='work:open'));
  await f.call(['config-apply','--input',await f.input('blocked-plan',blocked.data.plan)],1);
  await writeFile(join(f.project,'source.txt'),'after');await f.call(['check','--work','work:open','--runner','test'],0);
  await f.call(['finish','--work','work:open','--input',await f.input('outcome',{outcome:{summary:'Close before reconfiguration',incomplete:[],resumeNotes:[]}})],0);
  const prepared=await plan(f,request),before=await receipts(f,'work:open');
  await assert.rejects(applyConfiguration(locations(f),prepared,{onPhase:phase=>{if(phase==='declarations_written')throw Error('Injected cut');}}),/Injected cut/);
  const status=await f.call(['config-status'],2);assert.ok(status.data.pending.operationId);
  const rejected=await f.call(['resume','--work','work:open'],1);assert.equal(rejected.data.error,'CONFIGURATION_RECOVERY_REQUIRED');
  await assert.rejects(context(f),{code:'CONFIGURATION_RECOVERY_REQUIRED'});
  const recovered=await f.call(['config-recover'],0);assert.equal(recovered.data.applied,true);
  assert.equal((await f.call(['config-recover'],0)).data.recovered,false);
  const active=await context(f);assert.ok(active.local.bootstrap.runners.some(row=>row.id==='another'));
  for(const [id,value]of before)assert.equal(JSON.stringify(await (await processStore(active)).getReceipt(id)),value);
});

test('P6 empty engineering work closes before first runner and preserving module rescan',async t=>{
  const directory=await temporary(t),project=join(directory,'project'),data=join(directory,'data'),host=join(directory,'host');await mkdir(project);
  const env={PANORAMA_STATE_HOME:host,PANORAMA_PYTHON:python};
  const call=async(args,expected=0)=>{const result=await launch([...args,'--project',project],{env}).completed;assert.equal(result.code,expected,result.stderr||result.stdout);return result.data;};
  const input=async(name,value)=>{const path=join(directory,name+'.json');await writeFile(path,JSON.stringify(value));return path;};
  const bootstrap={roots:[{id:'project',kind:'project',description:'Engineering source',include:['**'],exclude:['.agents/**']}],runners:[]};
  await call(['init','--data',data,'--input',await input('bootstrap',bootstrap)]);
  const began=await call(['begin','--work','work:setup','--input',await input('setup',{goal:'Create the first engineering skeleton',expectedOutcome:'Document the checker and create its source',publicBehavior:false,plannedPaths:['README.md','src/check.mjs'],criteria:[{id:'DOC',version:1,required:true,requirement:'Review the engineering setup',subjectIds:['doc'],allowedEvidenceKinds:['document_review'],acceptedActorKinds:['coding_agent']}],subjects:[{id:'doc',kind:'document',locator:{rootId:'project',path:'README.md'}}]})]);
  await writeFile(join(project,'README.md'),'The first checker is src/check.mjs.');await mkdir(join(project,'src'));await writeFile(join(project,'src/check.mjs'),'process.exit(0);');
  await call(['review','--work','work:setup','--input',await input('review',{kind:'document_review',result:'passed',summary:'Read the new setup',method:'Compare documented checker path with source',findings:['The checker exists at the documented path.']})]);
  await call(['finish','--work','work:setup','--input',await input('finish',{outcome:{summary:'Engineering preparation ready',incomplete:[],resumeNotes:[]}})]);
  const oldCore=await readFile(join(project,'.structure/work-items',(await import('../../src/development/read-context.mjs')).coreKey('work:setup')+'.json'),'utf8');
  const planned=await call(['config-plan','--input',await input('register',{reason:'Register first actual checker and discovered source module',refreshModules:true,bootstrap:{...bootstrap,runners:[{id:'first',command:process.execPath,args:['src/check.mjs'],evidenceKinds:['behavior_test']}]}})]);
  assert.ok(planned.addedModules.includes('src'));
  await call(['config-apply','--input',await input('plan',planned.plan)]);
  assert.equal(await readFile(join(project,'.structure/work-items',(await import('../../src/development/read-context.mjs')).coreKey('work:setup')+'.json'),'utf8'),oldCore);
  const ctx=await loadReadContext({projectRoot:project,dataRoot:data},{workId:'work:setup'});
  assert.equal(JSON.stringify((await readWork(ctx,'work:setup')).context.baseline),JSON.stringify(began.baseline));
  const work={goal:'Run the first business checker',expectedOutcome:'Observed checker execution',moduleIds:['src'],plannedPaths:['src/check.mjs'],publicBehavior:false,subjects:[{id:'source',kind:'source_behavior',locator:null}],criteria:[{id:'RUN',version:1,required:true,requirement:'Run the registered checker',subjectIds:['source'],allowedEvidenceKinds:['behavior_test'],acceptedActorKinds:['system']}]};
  await call(['begin','--work','work:first','--input',await input('first',work)]);await call(['check','--work','work:first','--runner','first']);
  await call(['finish','--work','work:first','--input',await input('done',{outcome:{summary:'First registered execution observed',incomplete:[],resumeNotes:[]}})]);
});

test('P6 legacy v1 configuration and work are archived without rewriting receipts',async t=>{
  const f=await fixture(t),local=await readJson(localPath(f.data));
  local.formatVersion='panorama.development-local.v1';delete local.revisionId;delete local.sequence;delete local.enabled;delete local.guidanceOwnership;
  await writeJson(localPath(f.data),local); // Isolated valid pre-P6 input fixture.
  await finishBehavior(f);const legacy=await readWork(await context(f),'work:old');assert.equal(legacy.formatVersion,'panorama.development-work.v1');
  const before=await receipts(f,'work:old');
  await apply(f,await plan(f,{reason:'Archive legacy configuration on explicit upgrade'}));
  const restored=await context(f,'work:old');assert.equal(restored.configuration.revisionId,'configuration:legacy-'+local.configurationDigest);
  assert.equal((await readWorkHeader(restored,'work:old')).configurationRevisionId,undefined);
  for(const [id,value]of before)assert.equal(JSON.stringify(await (await processStore(restored)).getReceipt(id)),value);
});

test('P6 invalid tools, changed plans and edited guidance are not silently accepted; detach preserves custom content',async t=>{
  const f=await fixture(t);
  const missing=await f.call(['config-plan','--input',await f.input('missing',{reason:'Missing tool',bootstrap:{...f.bootstrap,runners:[{...f.bootstrap.runners[0],command:join(f.directory,'absent.exe')}]}})],2);
  assert.ok(missing.data.errors.some(row=>row.path==='/runners/0/command'));
  const script=await f.call(['config-plan','--input',await f.input('missing-script',{reason:'Missing registered script',bootstrap:{...f.bootstrap,runners:[{...f.bootstrap.runners[0],args:['missing.mjs']}]}})],2);
  assert.ok(script.data.errors.some(row=>row.path==='/runners/0/args/0'));
  const request=await plan(f,{reason:'Prepare exact change'});request.target.enabled=false;
  await f.call(['config-apply','--input',await f.input('tampered',request)],1);
  const custom=join(f.project,'.panorama/DEVELOPMENT.md');await writeFile(custom,'User-authored replacement guidance.');
  await f.call(['config-plan','--input',await f.input('edited',{reason:'Must not overwrite user guidance'})],1);
  const id=(await f.call(['config-status'],0)).data.activeRevisionId;
  const detached=await f.call(['detach','--input',await f.input('detach',{reason:'Pause process onboarding',expectedRevisionId:id})],0);
  assert.equal(detached.data.enabled,false);assert.match(await readFile(custom,'utf8'),/User-authored/);
  assert.match(await readFile(join(f.project,'AGENTS.md'),'utf8'),/preserve this line/);assert.doesNotMatch(await readFile(join(f.project,'AGENTS.md'),'utf8'),/panorama-development-process:v1/);
  await f.call(['begin','--input',await f.input('new-work',f.work)],1);
});
