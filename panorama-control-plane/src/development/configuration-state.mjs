// Read-only configuration history and shared command leases. No command runners.
import { AsyncLocalStorage } from 'node:async_hooks';
import { join } from 'node:path';
import { sha256, sameValue } from '../process/json.mjs';
import { validateDefinition } from '../process/schema.mjs';
import { requireProcess } from '../process/errors.mjs';
import { readJson, withLock, exclusiveText, now } from './io.mjs';

export const localPath = dataRoot => join(dataRoot,'process/v1/local.json');
export const configurationDirectory = dataRoot => join(dataRoot,'process/v2/configuration');
export const configurationLock = dataRoot => join(dataRoot,'process/v1/configuration.lock');
export const pendingPath = dataRoot => join(configurationDirectory(dataRoot),'pending.json');
export const configurationDigest = local => sha256(Object.fromEntries(['binding','bootstrap','project','packs'].map(key=>[key,local[key]])));
export const revisionId = local => local.revisionId ?? 'configuration:legacy-' + local.configurationDigest;
export const revisionPath = (dataRoot,id) => join(configurationDirectory(dataRoot),'revisions',sha256(id)+'.json');
export const operationPath = (dataRoot,id) => join(configurationDirectory(dataRoot),'operations',sha256(id)+'.json');
export function workRevisionId(work) {
  requireProcess(['panorama.development-work.v1','panorama.development-work.v2','panorama.development-work.v3'].includes(work.formatVersion),'WORK_FORMAT_UNSUPPORTED');
  if(work.formatVersion!=='panorama.development-work.v1'){validateDefinition(work.configurationRevisionId,'id');return work.configurationRevisionId;}
  requireProcess(!work.configurationRevisionId,'WORK_FORMAT_INVALID');
  return 'configuration:legacy-'+work.configurationDigest;
}
export function verifyLocal(local,projectRoot) {
  requireProcess(['panorama.development-local.v1','panorama.development-local.v2'].includes(local.formatVersion) && local.phase==='committed' && local.projectRoot===projectRoot,'INIT_NOT_COMMITTED');
  requireProcess(configurationDigest(local)===local.configurationDigest,'LOCAL_CONFIGURATION_CHANGED');
  if(local.formatVersion==='panorama.development-local.v2') {
    validateDefinition(local.revisionId,'id');
    requireProcess(Number.isSafeInteger(local.sequence)&&local.sequence>=1&&typeof local.enabled==='boolean','CONFIGURATION_REVISION_INVALID');
  }
  return local;
}
export async function readRevision(locations,id) {
  validateDefinition(id,'id');
  const record=await readJson(revisionPath(locations.dataRoot,id));
  const {recordHash,...body}=record;
  requireProcess(record.formatVersion==='panorama.configuration-revision.v1'&&record.id===id&&sha256(body)===recordHash,'CONFIGURATION_REVISION_CHANGED');
  verifyLocal(record.local,locations.projectRoot);
  requireProcess(revisionId(record.local)===record.id,'CONFIGURATION_REVISION_CHANGED');
  return record;
}
export async function preserveRevision(locations,local,{parentId=null,reason='Initial configuration',origin='configuration_change'}={}) {
  verifyLocal(local,locations.projectRoot);
  const id=revisionId(local), file=revisionPath(locations.dataRoot,id), previous=await readJson(file,{optional:true});
  if(previous) {
    const saved=await readRevision(locations,id);
    requireProcess(sameValue(saved.local,local)&&saved.parentId===parentId&&saved.reason===reason,'CONFIGURATION_REVISION_CONFLICT');
    return saved;
  }
  const record={formatVersion:'panorama.configuration-revision.v1',id,parentId,reason,origin,recordedAt:now(),local};
  record.recordHash=sha256(record);
  await exclusiveText(file,JSON.stringify(record,null,2)+'\n');
  return record;
}
export async function readActiveConfiguration(locations,{optional=false,allowPending=false}={}) {
  if(!allowPending) requireProcess(!await readJson(pendingPath(locations.dataRoot),{optional:true}),'CONFIGURATION_RECOVERY_REQUIRED');
  const local=await readJson(localPath(locations.dataRoot),{optional});
  if(!local)return null;
  verifyLocal(local,locations.projectRoot);
  if(local.formatVersion==='panorama.development-local.v2')requireProcess(sameValue((await readRevision(locations,local.revisionId)).local,local),'ACTIVE_CONFIGURATION_CHANGED');
  return local;
}
export async function selectedConfiguration(locations,active,workId) {
  if(!workId)return active;
  validateDefinition(workId,'id');
  const work=await readJson(join(locations.dataRoot,'process/v1/development/works',sha256(workId)+'.json'));
  requireProcess(work.workItemRef.id===workId&&sameValue(work.binding,active.binding),'WORK_BINDING_MISMATCH');
  const selected=workRevisionId(work);
  const local=selected===revisionId(active) ? active : (await readRevision(locations,selected)).local;
  requireProcess(local.configurationDigest===work.configurationDigest,'WORK_CONFIGURATION_CHANGED');
  return local;
}
export async function assertActiveConfiguration(ctx) {
  const current=await readActiveConfiguration(ctx);
  requireProcess(revisionId(current)===(ctx.configuration?.activeRevisionId??revisionId(ctx.local)),'CONFIGURATION_CHANGED_RETRY');
}
const leases=new AsyncLocalStorage();
export async function withConfigurationLease(ctx,fn) {
  if(leases.getStore()===ctx.dataRoot)return fn();
  return withLock(configurationLock(ctx.dataRoot),async()=>{
    await assertActiveConfiguration(ctx);
    return leases.run(ctx.dataRoot,fn);
  });
}
