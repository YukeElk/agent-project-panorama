import { randomUUID } from 'node:crypto';
import { readdir, unlink, rename, readFile } from 'node:fs/promises';
import { join, resolve, dirname } from 'node:path';
import { loadReadContext, readCoreWork } from './read-context.mjs';
import { normalizeBootstrap } from './config.mjs';
import { openCore } from './core.mjs';
import { bootstrapInput, inspectBootstrap } from './preflight.mjs';
import { planGuidance, applyGuidance, guidanceOwnership } from './guidance.mjs';
import { listWorks, readWorkHeader } from './journal.mjs';
import { localPath, configurationDigest, configurationLock, pendingPath, operationPath, revisionId, readActiveConfiguration, readRevision, preserveRevision } from './configuration-state.mjs';
import { sha256, sameValue } from '../process/json.mjs';
import { validateDefinition } from '../process/schema.mjs';
import { validateConfiguration } from '../process/validation.mjs';
import { requireProcess } from '../process/errors.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { readJson, writeJson, withLock, exclusiveText, now, keys, exists } from './io.mjs';

const planBody=plan=>Object.fromEntries(Object.entries(plan).filter(([key])=>key!=='planHash'));
const coreManifest=locations=>join(locations.projectRoot,'.structure/manifest.json');
const metadata=record=>({id:record.id,parentId:record.parentId,sequence:record.local.sequence??0,configurationDigest:record.local.configurationDigest,enabled:record.local.enabled!==false,reason:record.reason,recordedAt:record.recordedAt});
async function blockers(ctx) {
  const result=[];
  for(const entry of await listWorks(ctx)) {
    const work=await readWorkHeader(ctx,entry.workItemId), core=await readCoreWork(ctx,work);
    if(work.pendingReceipts?.length||work.runs?.some(run=>run.status==='pending')||work.operations?.begin?.status==='pending'||work.operations?.finish?.status==='pending')result.push({workItemId:entry.workItemId,code:'WORK_RECOVERY_REQUIRED'});
    if(work.phase!=='legacy_read_only'&&(work.phase!=='finished'||core.state!=='done'))result.push({workItemId:entry.workItemId,code:'WORK_NOT_CLOSED'});
  }
  const directory=join(ctx.projectRoot,'.structure/work-items');
  const files=await exists(directory)?(await readdir(directory)).filter(name=>/^[a-f0-9]{64}\.json$/.test(name)):[];
  requireProcess(files.length<=1000,'WORK_INDEX_LIMIT');
  for(const file of files) {
    const work=await readJson(join(directory,file));
    if(!['done','cancelled','rejected'].includes(work.state)&&!result.some(row=>row.workItemId===work.id))result.push({workItemId:work.id,code:'CORE_WORK_NOT_CLOSED'});
  }
  return result;
}
function differences(before,after,path='') {
  if(sameValue(before,after))return [];
  if(before&&after&&!Array.isArray(before)&&!Array.isArray(after)&&typeof before==='object'&&typeof after==='object')return [...new Set([...Object.keys(before),...Object.keys(after)])].flatMap(key=>differences(before[key]??null,after[key]??null,path+'/'+key));
  return [{path,before:before??null,after:after??null}];
}
export async function configurationStatus(locations) {
  const pending=await readJson(pendingPath(locations.dataRoot),{optional:true});
  const active=await readActiveConfiguration(locations,{allowPending:true}),history=[];
  let id=revisionId(active);
  if(!active.revisionId)history.push({id,sequence:0,configurationDigest:active.configurationDigest,enabled:true,reason:'Legacy configuration; archived only on explicit upgrade.'});
  else while(id) {
    requireProcess(history.length<1000&&!history.some(row=>row.id===id),'CONFIGURATION_HISTORY_INVALID');
    const record=await readRevision(locations,id);history.push(metadata(record));id=record.parentId;
  }
  return {formatVersion:'panorama.configuration-status.v1',activeRevisionId:revisionId(active),configurationDigest:active.configurationDigest,enabled:active.enabled!==false,history,
    pending:pending?{operationId:pending.operationId,planHash:pending.planHash}:null,ready:!pending};
}
export async function planConfiguration(locations,request) {
  keys(request,['reason','bootstrap','python','restoreRevisionId','refreshModules','enabled'],['reason']);validateDefinition(request.reason,'text');
  requireProcess(!(request.restoreRevisionId&&(request.bootstrap||request.python)),'CONFIGURATION_REQUEST_AMBIGUOUS');
  requireProcess(request.refreshModules===undefined||typeof request.refreshModules==='boolean','CONFIGURATION_REQUEST_INVALID');
  requireProcess(request.enabled===undefined||typeof request.enabled==='boolean','CONFIGURATION_REQUEST_INVALID');
  const ctx=await loadReadContext(locations),active=ctx.activeLocal;
  const source=request.restoreRevisionId?(request.restoreRevisionId===revisionId(active)?active:(await readRevision(locations,request.restoreRevisionId)).local):active;
  const bootstrap=normalizeBootstrap(request.bootstrap??bootstrapInput(source.bootstrap),ctx.projectRoot,request.python??source.bootstrap.python);
  requireProcess(bootstrap.profile===active.bootstrap.profile,'CONFIGURATION_PROFILE_UNSUPPORTED');
  requireProcess(sameValue(bootstrap.features,active.bootstrap.features),'CONFIGURATION_FEATURE_CHANGE_UNSUPPORTED');
  const enabled=request.enabled??true, validation=enabled?await inspectBootstrap(ctx.projectRoot,bootstrap):{valid:true,errors:[],checks:[]};
  const manifest=await readJson(coreManifest(locations));let modules=manifest.modules;
  if(request.refreshModules) {
    requireProcess(enabled&&validation.valid,'CONFIGURATION_PREFLIGHT_FAILED');
    const core=await openCore({projectRoot:ctx.projectRoot,python:bootstrap.python});
    modules=(await core.call('init',{mode:'auto',profile:bootstrap.profile,force:true,dry_run:true,adapters:[]})).plan.modules;
  }
  const project={...active.project,inputRoots:bootstrap.roots.map(({id,kind,description})=>({id,kind,description})),runnerRefs:bootstrap.runners.map(runner=>({id:runner.id,definitionDigest:sha256(runner),evidenceKinds:runner.evidenceKinds})),
    moduleBindings:Object.keys(modules).sort().map(moduleId=>active.project.moduleBindings.find(row=>row.moduleId===moduleId)??{moduleId,panoramaNodeIds:[],sourceSnapshotId:null,status:'observed',reason:'Structure preserving rescan identity; discovered knowledge remains draft.'})};
  validateConfiguration(project,active.packs);
  const guidance=await planGuidance(ctx.projectRoot,active,enabled);
  const plan={formatVersion:'panorama.configuration-plan.v1',createdAt:now(),projectRoot:ctx.projectRoot,dataRoot:ctx.dataRoot,binding:ctx.project.binding,
    expectedRevisionId:revisionId(active),expectedConfigurationDigest:active.configurationDigest,expectedCoreManifestDigest:sha256(manifest),request,
    target:{bootstrap,project,packs:active.packs,enabled,modules},guidance};
  plan.planHash=sha256(plan);
  const blocked=await blockers(ctx);
  const after=await readActiveConfiguration(locations);
  requireProcess(revisionId(after)===plan.expectedRevisionId,'CONFIGURATION_CHANGED_RETRY');
  return {valid:validation.valid&&!blocked.length,errors:validation.errors,checks:validation.checks,blockers:blocked,plan,
    diff:differences({bootstrap:active.bootstrap,project:active.project,enabled:active.enabled!==false},{bootstrap,project,enabled}),
    addedModules:project.moduleBindings.filter(row=>!active.project.moduleBindings.some(old=>old.moduleId===row.moduleId)).map(row=>row.moduleId),
    historyImpact:'Existing work keeps its original configuration, rules and baseline; changed/revoked input scopes are conservatively unavailable to historical observation.'};
}
async function coreInventory(root) {
  const files=[];
  async function walk(relative='') {
    await assertOrdinaryPath(join(root,relative));
    for(const entry of await readdir(join(root,relative),{withFileTypes:true})) {
      requireProcess(files.length<10000&&!entry.isSymbolicLink(),'CORE_STATE_UNSAFE');
      const path=relative?relative+'/'+entry.name:entry.name;
      if(entry.isDirectory())await walk(path);else files.push({path,sha256:sha256(await readFile(join(root,path)))});
    }
  }
  await walk();return files.sort((a,b)=>a.path.localeCompare(b.path));
}
async function recoverCoreDirectory(locations,operation) {
  const target=resolve(locations.projectRoot,'.structure');
  if(await exists(target))return;
  const matches=[];
  for(const name of await readdir(locations.projectRoot))if(/^\.structure-backup-[a-f0-9]{32}$/.test(name)) {
    const path=resolve(locations.projectRoot,name);
    requireProcess(dirname(path)===locations.projectRoot&&dirname(target)===locations.projectRoot,'CORE_RECOVERY_PATH_INVALID');
    if(sameValue(await coreInventory(path),operation.coreBefore))matches.push(path);
  }
  requireProcess(matches.length===1,'CORE_RECOVERY_BACKUP_REQUIRED');
  // Restore only the exact pre-rescan tree produced by the pinned core's swap.
  await assertOrdinaryPath(matches[0]);await assertOrdinaryPath(target,{allowMissing:true});
  await rename(matches[0],target);
}
async function continueChange(locations,operation,onPhase) {
  requireProcess(operation.plan.planHash===sha256(planBody(operation.plan))&&revisionId(operation.previous)===operation.plan.expectedRevisionId&&configurationDigest(operation.next)===operation.next.configurationDigest,'CONFIGURATION_OPERATION_CHANGED');
  requireProcess(sameValue([operation.next.bootstrap,operation.next.project,operation.next.packs,operation.next.enabled],[operation.plan.target.bootstrap,operation.plan.target.project,operation.plan.target.packs,operation.plan.target.enabled])&&operation.next.environmentKey===operation.previous.environmentKey,'CONFIGURATION_OPERATION_CHANGED');
  const checkpoint=async phase=>{operation.phase=phase;await writeJson(operationPath(locations.dataRoot,operation.id),operation);if(onPhase)await onPhase(phase);};
  let active=await readActiveConfiguration(locations,{allowPending:true});
  requireProcess([operation.plan.expectedRevisionId,operation.next.revisionId].includes(revisionId(active)),'CONFIGURATION_CHANGED_RETRY');
  const pending=await readJson(pendingPath(locations.dataRoot),{optional:true});
  requireProcess(!pending||pending.operationId===operation.id&&pending.planHash===operation.plan.planHash,'CONFIGURATION_OPERATION_CONFLICT');
  if(!pending) {
    // A process may die after saving intent but before publishing the pending marker.
    // Do not block newly started work when retrying such an orphaned intent.
    requireProcess(!(await blockers({...locations,project:operation.previous.project})).length,'CONFIGURATION_WORK_CONFLICT');
    await exclusiveText(pendingPath(locations.dataRoot),JSON.stringify({operationId:operation.id,planHash:operation.plan.planHash})+'\n');
  }
  await checkpoint('prepared');
  if(operation.plan.request.refreshModules) {
    await recoverCoreDirectory(locations,operation);
    const manifest=await readJson(coreManifest(locations));
    if(!sameValue(manifest.modules,operation.plan.target.modules)) {
      requireProcess(sha256(manifest)===operation.plan.expectedCoreManifestDigest,'CORE_MANIFEST_CHANGED');
      const core=await openCore({projectRoot:locations.projectRoot,python:operation.next.bootstrap.python});
      const args={mode:'auto',profile:operation.next.bootstrap.profile,force:true,adapters:[]};
      requireProcess(sameValue((await core.call('init',{...args,dry_run:true})).plan.modules,operation.plan.target.modules),'MODULE_DISCOVERY_CHANGED');
      await core.call('init',args);
    }
    requireProcess(sameValue((await readJson(coreManifest(locations))).modules,operation.plan.target.modules),'CORE_MANIFEST_CHANGED');
  } else requireProcess(sha256(await readJson(coreManifest(locations)))===operation.plan.expectedCoreManifestDigest,'CORE_MANIFEST_CHANGED');
  await checkpoint('core_ready');
  const pseudo={...locations,project:operation.next.project};
  requireProcess(!(await blockers(pseudo)).length,'CONFIGURATION_WORK_CONFLICT');
  await applyGuidance(locations.projectRoot,operation.plan.guidance,checkpoint);
  await checkpoint('guidance_ready');
  await preserveRevision(locations,operation.next,{parentId:operation.plan.expectedRevisionId,reason:operation.plan.request.reason,origin:operation.plan.request.restoreRevisionId?'restore':'configuration_change'});
  await checkpoint('revision_saved');
  for(const [path,expected,after] of [
    [join(locations.projectRoot,'.panorama/process.json'),operation.previous.project,operation.next.project],
    [join(locations.projectRoot,'.panorama/rules/development-process.v0.1.json'),operation.previous.packs[0],operation.next.packs[0]],
  ]) {
    const current=await readJson(path);requireProcess(sameValue(current,expected)||sameValue(current,after),'PROJECT_CONFIGURATION_CHANGED');
    if(!sameValue(current,after))await writeJson(path,after);
  }
  await checkpoint('declarations_written');
  await writeJson(localPath(locations.dataRoot),operation.next);
  await checkpoint('active_written');
  operation.status='committed';await checkpoint('committed');
  await unlink(pendingPath(locations.dataRoot));
  return {applied:true,operationId:operation.id,revisionId:operation.next.revisionId,configurationDigest:operation.next.configurationDigest,enabled:operation.next.enabled,retainedGuidance:operation.plan.guidance.retained};
}
export async function applyConfiguration(locations,plan,{onPhase}={}) {
  requireProcess(plan?.formatVersion==='panorama.configuration-plan.v1'&&sha256(planBody(plan))===plan.planHash,'CONFIGURATION_PLAN_CHANGED');
  requireProcess(plan.projectRoot===locations.projectRoot&&plan.dataRoot===locations.dataRoot,'CONFIGURATION_PLAN_BINDING_MISMATCH');
  return withLock(configurationLock(locations.dataRoot),async()=>{
    const id='configuration-change:'+plan.planHash.slice(0,40),path=operationPath(locations.dataRoot,id);
    let operation=await readJson(path,{optional:true});
    if(operation) {
      requireProcess(sameValue(operation.plan,plan),'CONFIGURATION_OPERATION_CONFLICT');
      if(operation.status==='committed') {
        const pending=await readJson(pendingPath(locations.dataRoot),{optional:true});
        if(pending?.operationId===id)await unlink(pendingPath(locations.dataRoot));
        return {applied:true,idempotent:true,operationId:id,revisionId:operation.next.revisionId,activeRevisionId:revisionId(await readActiveConfiguration(locations))};
      }
      return continueChange(locations,operation,onPhase);
    }
    const active=await readActiveConfiguration(locations);
    requireProcess(revisionId(active)===plan.expectedRevisionId&&active.configurationDigest===plan.expectedConfigurationDigest&&sameValue(active.binding,plan.binding),'CONFIGURATION_REVISION_CONFLICT');
    const fresh=await planConfiguration(locations,plan.request);
    requireProcess(fresh.valid,'CONFIGURATION_PREFLIGHT_FAILED',{errors:fresh.errors,blockers:fresh.blockers});
    requireProcess(fresh.plan.expectedCoreManifestDigest===plan.expectedCoreManifestDigest&&sameValue(fresh.plan.target,plan.target)&&sameValue(fresh.plan.guidance,plan.guidance),'CONFIGURATION_PLAN_STALE');
    if(!active.revisionId)await preserveRevision(locations,active,{reason:'Legacy configuration before first P6 upgrade',origin:'legacy_archive'});
    const ownership=await guidanceOwnership(locations.projectRoot);
    for(const change of plan.guidance.changes)if(change.path!=='AGENTS.md') {if(change.afterHash)ownership[change.path]=change.afterHash;else delete ownership[change.path];}
    const next={...active,formatVersion:'panorama.development-local.v2',phase:'committed',revisionId:'configuration:'+randomUUID(),sequence:(active.sequence??0)+1,enabled:plan.target.enabled,
      bootstrap:plan.target.bootstrap,project:plan.target.project,packs:plan.target.packs,guidanceOwnership:plan.target.enabled?ownership:{}};
    next.configurationDigest=configurationDigest(next);
    operation={formatVersion:'panorama.configuration-change.v1',id,status:'pending',phase:'intent_saved',createdAt:now(),plan,previous:active,next,coreBefore:plan.request.refreshModules?await coreInventory(join(locations.projectRoot,'.structure')):null};
    await exclusiveText(path,JSON.stringify(operation,null,2)+'\n');
    return continueChange(locations,operation,onPhase);
  });
}
export async function recoverConfiguration(locations,operationId=null,{onPhase}={}) {
  const pending=await readJson(pendingPath(locations.dataRoot),{optional:true}),id=operationId??pending?.operationId;
  if(!id)return {...await configurationStatus(locations),recovered:false};
  validateDefinition(id,'id');const operation=await readJson(operationPath(locations.dataRoot,id));
  requireProcess(operation.id===id,'CONFIGURATION_OPERATION_CONFLICT');
  return applyConfiguration(locations,operation.plan,{onPhase});
}
export async function detachConfiguration(locations,request) {
  keys(request,['reason','expectedRevisionId'],['reason','expectedRevisionId']);
  const planned=await planConfiguration(locations,{reason:request.reason,enabled:false});
  requireProcess(planned.plan.expectedRevisionId===request.expectedRevisionId,'CONFIGURATION_REVISION_CONFLICT');
  return applyConfiguration(locations,planned.plan);
}
