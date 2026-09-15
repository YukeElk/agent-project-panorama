import { randomBytes, randomUUID } from 'node:crypto';
import { join, resolve } from 'node:path';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { VERSION, localPath, configurationDigest, loadReadContext } from './read-context.mjs';
export { VERSION, localPath, configurationDigest } from './read-context.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { validateConfiguration, packReference, uniqueBy } from '../process/validation.mjs';
import { validateDefinition } from '../process/schema.mjs';
import { sha256, sameValue, processValue } from '../process/json.mjs';
import { requireProcess } from '../process/errors.mjs';
import { readJson, writeJson, exists, keys, absolute, exclusiveText, withLock, atomicText } from './io.mjs';
import { openCore, packageRoot } from './core.mjs';
import { configurationLock, preserveRevision } from './configuration-state.mjs';
import { guidanceOwnership } from './guidance.mjs';
import { BOOTSTRAP_V2, normalizeCheckConfiguration } from './check-contracts.mjs';

export const EXCLUDES = ['.git/**', '.structure/**', '.structure-stage-*/**', '.structure-backup-*/**', '.panorama/**', 'node_modules/**', '.venv/**', 'venv/**', '__pycache__/**', '.env', '.env.*', '**/.env', '**/.env.*'];
const machineKinds = ['behavior_test', 'interface_test', 'artifact_integrity'];
function list(values, min = 0, max = 100) { requireProcess(Array.isArray(values) && values.length >= min && values.length <= max, 'LOCAL_CONFIG_INVALID'); return values; }
export function normalizeBootstrap(raw, projectRoot, python) {
  const input = processValue(raw);
  const scoped = input.formatVersion === BOOTSTRAP_V2;
  keys(input, ['profile', 'roots', 'runners', 'publicPaths', 'features', ...(scoped ? ['formatVersion','inputScopes','checkerContracts'] : [])]);
  const profile = input.profile ?? 'solo-light';
  requireProcess(['solo-light', 'team-standard', 'multi-agent', 'regulated'].includes(profile), 'LOCAL_CONFIG_INVALID');
  const roots = list(input.roots ?? [{ id: 'project', kind: 'project', description: 'Declared project sources', include: ['**'], exclude: [] }], 1).map(root => {
    keys(root, ['id', 'kind', 'description', 'path', 'include', 'exclude', 'role', 'observeOnly'], ['id', 'kind', 'description', 'include']);
    validateDefinition(root.id, 'id'); validateDefinition(root.description, 'text');
    requireProcess(['project', 'external', 'artifact'].includes(root.kind) && (root.observeOnly === undefined || typeof root.observeOnly === 'boolean'), 'LOCAL_CONFIG_INVALID');
    requireProcess(!root.observeOnly || root.kind === 'artifact', 'OBSERVE_ONLY_REQUIRES_ARTIFACT_ROOT');
    const path = root.kind === 'project' ? projectRoot : absolute(root.path);
    requireProcess(root.kind !== 'project' || !root.path || resolve(root.path) === projectRoot, 'PROJECT_ROOT_CONFLICT');
    const selector = validateDefinition({ rootId: root.id, include: root.include, exclude: [...new Set([...(root.exclude ?? []), ...(root.kind === 'project' ? EXCLUDES : [])])], role: root.role ?? (root.kind === 'external' ? 'fixture' : root.kind === 'artifact' ? 'artifact' : 'source') }, 'selector');
    return { id: root.id, kind: root.kind, description: root.description, path, allow: root.include, selector, observeOnly: root.observeOnly ?? false };
  });
  uniqueBy(roots, 'id');
  requireProcess(roots.filter(root => root.kind === 'project').length === 1 && roots.some(root => root.id === 'project' && root.kind === 'project' && !root.observeOnly), 'PROJECT_ROOT_REQUIRED');
  const runners = list(input.runners ?? []).map(runner => {
    keys(runner, ['id', 'command', 'args', 'cwd', 'evidenceKinds', 'envKeys', 'timeoutMs', 'maxLogBytes', 'version', ...(scoped ? ['scopeId','checkerContractId'] : [])], ['id', 'command', 'args', 'evidenceKinds']);
    validateDefinition(runner.id, 'id'); absolute(runner.command);
    requireProcess(!/\.(cmd|bat)$/i.test(runner.command), 'RUNNER_EXECUTABLE_REQUIRED');
    list(runner.args).forEach(arg => requireProcess(typeof arg === 'string' && arg.length <= 4000 && !arg.includes('\0'), 'RUNNER_ARGUMENT_INVALID'));
    list(runner.evidenceKinds, 1).forEach(kind => requireProcess(machineKinds.includes(kind), 'RUNNER_EVIDENCE_KIND_INVALID'));
    list(runner.envKeys ?? []).forEach(key => requireProcess(/^[A-Za-z_][A-Za-z0-9_]*$/.test(key), 'RUNNER_ENVIRONMENT_INVALID'));
    if (runner.cwd && runner.cwd !== '.') validateDefinition(runner.cwd, 'relativePath');
    const result = { id: runner.id, command: runner.command, args: runner.args, cwd: runner.cwd ?? '.', evidenceKinds: [...new Set(runner.evidenceKinds)], envKeys: runner.envKeys ?? [], timeoutMs: runner.timeoutMs ?? 120000, maxLogBytes: runner.maxLogBytes ?? 65536, version: runner.version ?? 'content-pinned' };
    requireProcess(Number.isSafeInteger(result.timeoutMs) && result.timeoutMs >= 50 && result.timeoutMs <= 3600000 && Number.isSafeInteger(result.maxLogBytes) && result.maxLogBytes >= 0 && result.maxLogBytes <= 1048576, 'RUNNER_LIMIT_INVALID');
    if (scoped) { validateDefinition(runner.scopeId, 'id'); validateDefinition(runner.checkerContractId, 'id'); Object.assign(result, {scopeId:runner.scopeId,checkerContractId:runner.checkerContractId}); }
    return result;
  }); uniqueBy(runners, 'id');
  const publicPaths = input.publicPaths ?? [];
  list(publicPaths).forEach(path => validateDefinition(path, 'relativePath'));
  return { profile, roots, runners, publicPaths, features: input.features ?? [], python: absolute(python), ...(scoped ? normalizeCheckConfiguration(input, roots, runners) : {}) };
}

async function guidance(projectRoot) {
  const guide = '.panorama/DEVELOPMENT.md';
  const text = await readFile(join(packageRoot, 'skills/panorama-development-process/references/project-guide.md'), 'utf8');
  const target = join(projectRoot, guide);
  await assertOrdinaryPath(target, { allowMissing: true });
  if (await exists(target)) requireProcess(await readFile(target, 'utf8') === text, 'PROJECT_GUIDANCE_CONFLICT');
  else await exclusiveText(target, text);
  const agents = join(projectRoot, 'AGENTS.md');
  await assertOrdinaryPath(agents, { allowMissing: true });
  const block = '<!-- panorama-development-process:v1 -->\n本项目已启用开发过程记录。处理开发任务时先阅读 [.panorama/DEVELOPMENT.md](.panorama/DEVELOPMENT.md)，使用本地 `panorama-process` CLI。\n<!-- /panorama-development-process:v1 -->';
  const previous = await exists(agents) ? await readFile(agents, 'utf8') : '';
  if (previous.includes('<!-- panorama-development-process:v1 -->')) requireProcess(previous.includes(block), 'AGENTS_GUIDANCE_CONFLICT');
  else {
    // A bounded pointer is appended without replacing existing instructions.
    await assertOrdinaryPath(agents, { allowMissing: true });
    await atomicText(agents, previous + (previous ? '\n\n' : '') + block + '\n');
  }
  for (const relative of ['SKILL.md', 'agents/openai.yaml', 'references/project-guide.md']) {
    const content = await readFile(join(packageRoot, 'skills/panorama-development-process', relative), 'utf8');
    const target = join(projectRoot, '.agents/skills/panorama-development-process', relative);
    await assertOrdinaryPath(target, { allowMissing: true });
    if (await exists(target)) requireProcess(await readFile(target, 'utf8') === content, 'PROJECT_SKILL_CONFLICT');
    else await exclusiveText(target, content);
  }
  return guide;
}

export async function initialize({ projectRoot, dataRoot, python, input = {} }) {
  return withLock(configurationLock(dataRoot), async () => {
    let local = await readJson(localPath(dataRoot), { optional: true });
    const bootstrap = normalizeBootstrap(input, projectRoot, python ?? local?.bootstrap.python ?? process.env.PANORAMA_PYTHON);
    requireProcess(!local || local.projectRoot === projectRoot && sameValue(local.bootstrap, bootstrap), 'INIT_CONFIGURATION_CONFLICT');
    if(local?.phase==='committed') {
      const ctx=await loadReadContext({projectRoot,dataRoot});
      await openCore({projectRoot,python:bootstrap.python});
      requireProcess((await readJson(join(projectRoot,'.structure/manifest.json'))).policy.profile===bootstrap.profile,'CORE_PROFILE_CONFLICT');
      return {initialized:true,idempotent:true,binding:ctx.project.binding,projectRoot,dataRoot,configuration:ctx.configuration};
    }
    const core = await openCore({ projectRoot, python: bootstrap.python });
    if (!local) {
      local = { formatVersion: 'panorama.development-local.v2', revisionId:'configuration:'+randomUUID(),sequence:1,enabled:true, phase: 'pending', projectRoot, bootstrap, environmentKey: randomBytes(32).toString('hex') };
      await writeJson(localPath(dataRoot), local);
    }
    if (!await exists(join(projectRoot, '.structure'))) await core.call('init', { mode: 'auto', profile: bootstrap.profile, adapters: [] });
    const identity = await core.call('identity');
    requireProcess(identity.project_root === projectRoot && identity.project_id && identity.identity_source === 'explicit', 'CORE_IDENTITY_REQUIRES_MIGRATION');
    const context = await core.call('context');
    const manifest = await readJson(join(projectRoot, '.structure/manifest.json'));
    requireProcess(manifest.policy.profile === bootstrap.profile, 'CORE_PROFILE_CONFLICT');
    const binding = { authority: 'structure-core', projectId: identity.project_id, checkoutId: identity.checkout_id, panoramaProjectId: null };
    const packs = [await readJson(join(packageRoot, 'contracts/process/rules/development-process.v0.1.json'))];
    const project = {
      formatVersion: 'panorama.process-project.v1', purpose: 'record', binding,
      corePin: { commit: '9cab2b1345cca68177708fc30f2cac6bbe5792b5', version: '2.0.0-preview.2' }, rulePacks: packs.map(packReference),
      inputRoots: bootstrap.roots.map(({ id, kind, description }) => ({ id, kind, description })),
      moduleBindings: Object.keys(manifest.modules).sort().map(moduleId => ({ moduleId, panoramaNodeIds: [], sourceSnapshotId: null, status: 'observed', reason: 'Structure core module identity; discovery knowledge may still be draft.' })),
      runnerRefs: bootstrap.runners.map(runner => ({ id: runner.id, definitionDigest: sha256(runner), evidenceKinds: runner.evidenceKinds })), features: bootstrap.features, extensions: {},
    };
    validateConfiguration(project, packs);
    requireProcess(!local.binding || sameValue(local.binding, binding), 'CORE_IDENTITY_CHANGED');
    const configFile = join(projectRoot, '.panorama/process.json'), rulesFile = join(projectRoot, '.panorama/rules/development-process.v0.1.json');
    for (const [path, content] of [[configFile, project], [rulesFile, packs[0]]]) {
      const previous = await readJson(path, { optional: true });
      if (previous) requireProcess(sameValue(previous, content), 'PROJECT_CONFIG_CONFLICT');
      else await exclusiveText(path, JSON.stringify(content, null, 2) + '\n');
    }
    await guidance(projectRoot);
    Object.assign(local, { phase: 'committed', binding, project, packs, guidanceOwnership:await guidanceOwnership(projectRoot) });
    local.configurationDigest = configurationDigest(local);
    if(local.revisionId)await preserveRevision({projectRoot,dataRoot},local,{reason:'Initial configuration',origin:'initialize'});
    await writeJson(localPath(dataRoot), local);
    return { initialized: true, binding, projectRoot, dataRoot, modules: project.moduleBindings.map(module => module.moduleId), coreVersion: core.version, nativeWrites: 'post_write_scan', guidance: '.panorama/DEVELOPMENT.md',configuration:(await loadReadContext({projectRoot,dataRoot})).configuration };
  });
}

export async function loadDevelopment(locations, options = {}) {
  const context = await loadReadContext(locations,options);
  const core = await openCore({ ...locations, python: context.activeLocal.bootstrap.python });
  const identity = await core.call('identity');
  requireProcess(identity.project_id === context.project.binding.projectId && identity.checkout_id === context.project.binding.checkoutId && identity.project_root === locations.projectRoot, 'CORE_IDENTITY_CHANGED');
  return { ...context, core };
}
