import { homedir } from 'node:os';
import { join, resolve, dirname, isAbsolute } from 'node:path';
import { realpath } from 'node:fs/promises';
import { sha256 } from './domain/canonical.mjs';
import { prepareStorage, canonicalFuturePath } from './standalone/design-storage.mjs';
import { assertOrdinaryPath } from './process/inputs.mjs';
import { requireProcess } from './process/errors.mjs';
import { exists, readJson, writeJson, withLock } from './development/io.mjs';

export const pathIdentity = path => process.platform === 'win32' ? path.toLowerCase() : path;
export async function findProject(start = process.cwd()) {
  const origin = await realpath(resolve(start));
  await assertOrdinaryPath(origin);
  let moduleContext = null;
  async function owner(root, identity) {
    if (moduleContext) {
      const marker = moduleContext.identity;
      const manifest = await readJson(join(root, '.structure/manifest.json'));
      requireProcess(marker.schema_version === 1 && marker.project_id === identity?.project_id && typeof marker.project_root === 'string' && !isAbsolute(marker.project_root) && pathIdentity(resolve(moduleContext.path, marker.project_root)) === pathIdentity(root) && typeof manifest.modules?.[marker.module_id]?.path === 'string' && pathIdentity(resolve(root, manifest.modules[marker.module_id].path)) === pathIdentity(moduleContext.path), 'MODULE_OWNER_MISMATCH');
    }
    return root;
  }
  for (let current = origin; ; current = dirname(current)) {
    const identity = await readJson(join(current, '.structure', 'identity.json'), { optional: true });
    if (identity?.kind === 'module' && !moduleContext) moduleContext = { path: current, identity };
    if (identity?.kind === 'project' || (!identity && await exists(join(current, '.structure', 'state.json')))) return owner(current, identity);
    if (!identity && await exists(join(current, '.panorama', 'process.json'))) return owner(current, identity);
    if (dirname(current) === current) { requireProcess(!moduleContext, 'MODULE_OWNER_MISMATCH'); return origin; }
  }
}

// Both local launchers use this registry. It detects roots registered by them and
// the historical default; it does not claim to enumerate arbitrary disk folders.
export async function locateProject({ project, data, stateHome, readOnly = false, allowUninitialized = false } = {}) {
  const projectRoot = await findProject(project);
  const home = resolve(stateHome ?? process.env.PANORAMA_STATE_HOME ?? join(process.env.LOCALAPPDATA || join(homedir(), '.local', 'share'), 'Panorama'));
  await assertOrdinaryPath(home, { allowMissing: true });
  const key = sha256(pathIdentity(projectRoot));
  const registry = join(home, 'locations-v1', key + '.json');
  const defaultRoot = join(home, 'standalone', sha256(projectRoot).slice(0, 20));
  const locate = async () => {
    const registered = await readJson(registry, { optional: true });
    requireProcess(!registered || registered.projectRoot === pathIdentity(projectRoot) && Array.isArray(registered.dataRoots), 'LOCATION_BINDING_MISMATCH');
    const requested = data ? await canonicalFuturePath(resolve(data)) : null;
    // Reserve one destination even before init has created local.json, so two
    // simultaneous first-time launchers cannot both establish a different root.
    requireProcess(!requested || !registered?.dataRoots?.length || registered.dataRoots.some(root => pathIdentity(root) === pathIdentity(requested)), 'MULTIPLE_DATA_ROOTS', { registered: registered?.dataRoots ?? [], requested });
    const candidates = [...new Set([...(registered?.dataRoots ?? []), defaultRoot, ...(requested ? [requested] : [])])];
    const active = [];
    for (const root of candidates) {
      await assertOrdinaryPath(root, { allowMissing: true });
      if (await exists(join(root, 'workspace.json')) || await exists(join(root, 'process', 'v1', 'local.json')) || await exists(join(root, 'process', 'v1', 'index.json'))) active.push(root);
    }
    const activeIds = [...new Set(active.map(pathIdentity))];
    requireProcess(activeIds.length <= 1 && (!requested || !activeIds.length || activeIds[0] === pathIdentity(requested)), 'MULTIPLE_DATA_ROOTS', { active, requested });
    const dataRoot = requested ?? active[0] ?? registered?.dataRoots?.[0] ?? defaultRoot;
    if (readOnly) {
      requireProcess(allowUninitialized || await exists(join(dataRoot, 'process/v1/local.json')), 'INIT_REQUIRED');
      const canonical = allowUninitialized ? await canonicalFuturePath(dataRoot) : await realpath(dataRoot);
      requireProcess(pathIdentity(canonical) !== pathIdentity(projectRoot) && !pathIdentity(canonical).startsWith(pathIdentity(projectRoot) + (process.platform === 'win32' ? '\\' : '/')), 'DATA_ROOT_INSIDE_PROJECT');
      return { projectRoot, dataRoot: canonical };
    }
    const locations = await prepareStorage(projectRoot, dataRoot);
    await writeJson(registry, { formatVersion: 'panorama.local-locations.v1', projectRoot: pathIdentity(projectRoot), dataRoots: [...new Set([...active, locations.dataRoot])] });
    return locations;
  };
  return readOnly ? locate() : withLock(registry + '.lock', locate);
}
