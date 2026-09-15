// Filesystem observation only. Keep this dependency tree free of command runners.
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { realpath } from 'node:fs/promises';
import { createInputRegistry } from '../process/inputs.mjs';
import { validateConfiguration } from '../process/validation.mjs';
import { sha256, sameValue } from '../process/json.mjs';
import { requireProcess } from '../process/errors.mjs';
import { readJson } from './io.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { localPath, configurationDigest, readActiveConfiguration, selectedConfiguration, revisionId } from './configuration-state.mjs';
export { localPath, configurationDigest } from './configuration-state.mjs';

export const VERSION = 'panorama-development-2.0.0';
export const coreKey = value => createHash('sha256').update(value, 'utf8').digest('hex');

export async function loadReadContext(locations, { optional = false, workId = null } = {}) {
  const active = await readActiveConfiguration(locations,{optional});
  if (!active) return null;
  const declared = await readJson(join(locations.projectRoot, '.panorama/process.json'));
  const declaredPacks = [await readJson(join(locations.projectRoot, '.panorama/rules/development-process.v0.1.json'))];
  requireProcess(sameValue(active.project, declared) && sameValue(active.packs, declaredPacks), 'PROJECT_CONFIGURATION_CHANGED');
  const local=await selectedConfiguration(locations,active,workId), project=local.project, packs=local.packs;
  const config = validateConfiguration(project, packs);
  const root = await realpath(locations.projectRoot);
  const identity = await readJson(join(root, '.structure/identity.json'));
  const checkoutId = coreKey(process.platform === 'win32' ? root.toLowerCase() : root);
  requireProcess(identity.schema_version === 1 && identity.kind === 'project' && identity.project_id === project.binding.projectId && checkoutId === project.binding.checkoutId && root === locations.projectRoot, 'CORE_IDENTITY_CHANGED');
  const mappings = {}, withdrawnRoots=[], mappingGaps=[];
  for(const item of local.bootstrap.roots) {
    const allowed=active.bootstrap.roots.find(root=>root.id===item.id);
    if(active.enabled===false || !allowed || !sameValue([item.path,item.selector,item.observeOnly],[allowed.path,allowed.selector,allowed.observeOnly])) {withdrawnRoots.push(item.id);continue;}
    try {await assertOrdinaryPath(item.path);mappings[item.id]={path:item.path,allow:item.allow};}
    catch(error) {mappingGaps.push({rootId:item.id,code:error.code??'INPUT_ROOT_UNAVAILABLE'});}
  }
  const registry = await createInputRegistry(project, mappings);
  const after=await readActiveConfiguration(locations);
  requireProcess(revisionId(after)===revisionId(active),'CONFIGURATION_CHANGED_RETRY');
  const runnerBinding = (bootstrap,runner) => runner.scopeId ? [runner,bootstrap.inputScopes?.find(scope=>scope.id===runner.scopeId),bootstrap.checkerContracts?.find(contract=>contract.id===runner.checkerContractId)] : runner;
  const configuration={revisionId:revisionId(local),sequence:local.sequence??0,configurationDigest:local.configurationDigest,
    activeRevisionId:revisionId(active),activeConfigurationDigest:active.configurationDigest,historical:revisionId(local)!==revisionId(active),enabled:active.enabled!==false,
    withdrawnRoots,mappingGaps,withdrawnRunners:local.bootstrap.runners.filter(runner=>active.enabled===false || !active.bootstrap.runners.some(current=>sameValue(runnerBinding(local.bootstrap,runner),runnerBinding(active.bootstrap,current)))).map(runner=>runner.id)};
  return { ...locations, local, activeLocal:active, project, packs, config, registry, configuration };
}

export async function readCoreWork(ctx, work) {
  const core = await readJson(join(ctx.projectRoot, '.structure/work-items', coreKey(work.workItemRef.id) + '.json'));
  const definition = Object.fromEntries(['id', 'title', 'kind', 'impact', 'modules', 'scope', 'acceptance', 'project_id', 'managed_by'].filter(key => Object.hasOwn(core, key)).map(key => [key, core[key]]));
  requireProcess(core.id === work.workItemRef.id && core.project_id === ctx.project.binding.projectId && (!work.coreDefinition || sameValue(work.coreDefinition, definition)), 'CORE_WORK_DEFINITION_CHANGED');
  return core;
}
