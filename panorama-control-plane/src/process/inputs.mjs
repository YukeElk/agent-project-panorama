import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, realpath, open, readdir } from 'node:fs/promises';
import { resolve, parse, join, relative, isAbsolute, sep } from 'node:path';
import { sha256, sameValue } from './json.mjs';
import { validateDefinition } from './schema.mjs';
import { matchPath } from './applicability.mjs';
import { ProcessError, requireProcess } from './errors.mjs';

export const INPUT_LIMITS = Object.freeze({ maxFiles: 50000, maxEntries: 100000, maxDepth: 32, maxFileBytes: 64 * 1024 * 1024, maxTotalBytes: 512 * 1024 * 1024 });
const inside = (root, path) => { const suffix = relative(root, path); return suffix === '' || (!isAbsolute(suffix) && suffix !== '..' && !suffix.startsWith('..' + sep)); };
const secretPath = path => /(?:^|\/)(?:\.git|\.ssh|\.gnupg|secrets|credentials)(?:\/|$)|(?:^|\/)(?:\.env(?:\.[^/]*)?|id_(?:rsa|ed25519)[^/]*|(?:credentials|service-account)\.(?:json|ya?ml))$|\.(?:pem|key|p12|pfx|jks|keystore)$/i.test(path);
const fileOrder = (a, b) => a.rootId < b.rootId ? -1 : a.rootId > b.rootId ? 1 : a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
const fileIdentity = info => [info.dev, info.ino, info.size, info.mtimeMs, info.ctimeMs];

// Recheck every ancestor, including registered-root ancestors, on each access.
export async function assertOrdinaryPath(path, { allowMissing = false } = {}) {
  const absolute = resolve(path), parsed = parse(absolute);
  let cursor = parsed.root;
  for (const part of absolute.slice(parsed.root.length).split(sep).filter(Boolean)) {
    cursor = join(cursor, part);
    let info;
    try { info = await lstat(cursor); }
    catch (error) { if (allowMissing && error.code === 'ENOENT') return absolute; throw error; }
    requireProcess(!info.isSymbolicLink(), 'PATH_LINK_REJECTED');
    requireProcess(cursor === absolute || info.isDirectory(), 'PATH_ANCESTOR_INVALID');
  }
  return absolute;
}

export async function createInputRegistry(project, mappings, options = {}) {
  const limits = { ...INPUT_LIMITS, ...options };
  for (const key of Object.keys(INPUT_LIMITS)) requireProcess(Number.isSafeInteger(limits[key]) && limits[key] > 0 && limits[key] <= INPUT_LIMITS[key], 'INPUT_LIMIT_INVALID');
  requireProcess(mappings && typeof mappings === 'object', 'INPUT_ROOT_MAPPING_REQUIRED');
  const roots = new Map();
  for (const [id, mapping] of Object.entries(mappings)) {
    requireProcess(project.inputRoots.some(root => root.id === id), 'INPUT_ROOT_UNREGISTERED');
    requireProcess(mapping && typeof mapping.path === 'string' && Array.isArray(mapping.allow) && mapping.allow.length > 0 && mapping.allow.length <= 100, 'INPUT_ALLOWLIST_REQUIRED');
    const allow = mapping.allow.map(pattern => validateDefinition({ rootId: id, include: [pattern], exclude: [], role: 'source' }, 'selector').include[0]);
    const logical = await assertOrdinaryPath(mapping.path);
    const path = await realpath(logical);
    requireProcess((await lstat(path)).isDirectory(), 'INPUT_ROOT_NOT_DIRECTORY');
    roots.set(id, { path, logical, allow });
  }
  async function registered(id) {
    const root = roots.get(id);
    requireProcess(root, 'INPUT_ROOT_UNMAPPED');
    await assertOrdinaryPath(root.logical);
    requireProcess(await realpath(root.logical) === root.path, 'INPUT_ROOT_CHANGED');
    return root;
  }
  async function fileInfo(rootId, path) {
    validateDefinition({ rootId, path }, 'locator');
    requireProcess(!secretPath(path), 'INPUT_SECRET_PATH');
    if (process.platform === 'win32') requireProcess(!path.split('/').some(part => /[. ]$/.test(part) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part)), 'INPUT_PATH_ALIAS');
    const root = await registered(rootId);
    requireProcess(root.allow.some(pattern => matchPath(pattern, path)), 'INPUT_NOT_AUTHORIZED');
    const absolute = join(root.path, ...path.split('/'));
    requireProcess(inside(root.path, absolute), 'INPUT_PATH_ESCAPE');
    await assertOrdinaryPath(absolute);
    requireProcess(inside(root.path, await realpath(absolute)), 'INPUT_PATH_ESCAPE');
    const info = await lstat(absolute);
    requireProcess(info.isFile(), 'INPUT_NOT_FILE');
    return { root, absolute, info };
  }
  async function hashFile(rootId, path, remaining = limits.maxTotalBytes) {
    const before = await fileInfo(rootId, path);
    requireProcess(before.info.size <= limits.maxFileBytes && before.info.size <= remaining, 'INPUT_SIZE_LIMIT');
    const handle = await open(before.absolute, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    try {
      const opened = await handle.stat();
      requireProcess(sameValue(fileIdentity(before.info), fileIdentity(opened)), 'INPUT_CHANGED_DURING_READ');
      const hash = createHash('sha256'), buffer = Buffer.alloc(64 * 1024);
      let size = 0;
      while (true) {
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
        if (!bytesRead) break;
        size += bytesRead;
        requireProcess(size <= limits.maxFileBytes && size <= remaining, 'INPUT_SIZE_LIMIT');
        hash.update(buffer.subarray(0, bytesRead));
      }
      const after = await fileInfo(rootId, path), final = await handle.stat();
      requireProcess(size === before.info.size && sameValue(fileIdentity(before.info), fileIdentity(final)) && sameValue(fileIdentity(final), fileIdentity(after.info)), 'INPUT_CHANGED_DURING_READ');
      return { digest: hash.digest('hex'), bytes: size };
    } finally { await handle.close(); }
  }
  async function enumerate(selectors) {
    const found = new Map(), unknowns = [];
    let entries = 0;
    async function candidate(selector, path) {
      if (!selector.include.some(pattern => matchPath(pattern, path)) || selector.exclude.some(pattern => matchPath(pattern, path))) return;
      const key = selector.rootId + '/' + path;
      if (found.has(key)) { requireProcess(found.get(key).role === selector.role, 'INPUT_ROLE_CONFLICT'); return; }
      requireProcess(found.size < limits.maxFiles, 'INPUT_FILE_LIMIT');
      const { info } = await fileInfo(selector.rootId, path);
      found.set(key, { rootId: selector.rootId, path, role: selector.role, metadata: fileIdentity(info) });
    }
    for (const selector of selectors) {
      try {
        const root = await registered(selector.rootId);
        requireProcess(selector.include.every(pattern => root.allow.some(allowed => allowed === '**' || allowed === pattern ||
          (allowed.endsWith('/**') && !/[?*]/.test(allowed.slice(0, -3)) && pattern.startsWith(allowed.slice(0, -2))) ||
          (!/[?*]/.test(pattern) && matchPath(allowed, pattern)))), 'INPUT_SELECTOR_NOT_AUTHORIZED');
        for (const pattern of selector.include) {
          if (!/[?*]/.test(pattern)) {
            try { await candidate(selector, pattern); }
            catch (error) {
              if (error.code !== 'ENOENT') throw error;
              if (!selector.exclude.some(excluded => matchPath(excluded, pattern))) found.set(selector.rootId + '/' + pattern, { rootId: selector.rootId, path: pattern, role: selector.role, metadata: null });
            }
            continue;
          }
          const parts = pattern.split('/'), wildcard = parts.findIndex(part => /[?*]/.test(part)), prefix = parts.slice(0, wildcard).join('/');
          async function visit(path, depth) {
            requireProcess(depth <= limits.maxDepth, 'INPUT_DEPTH_LIMIT');
            const absolute = path ? join(root.path, ...path.split('/')) : root.path;
            await assertOrdinaryPath(absolute);
            const children = (await readdir(absolute, { withFileTypes: true })).sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
            for (const child of children) {
              requireProcess(++entries <= limits.maxEntries, 'INPUT_ENTRY_LIMIT');
              const name = path ? path + '/' + child.name : child.name;
              if (selector.exclude.some(excluded => matchPath(excluded, name) && (!child.isDirectory() || excluded.endsWith('**')))) continue;
              requireProcess(!child.isSymbolicLink(), 'PATH_LINK_REJECTED');
              requireProcess(!secretPath(name), 'INPUT_SECRET_PATH');
              if (child.isDirectory()) await visit(name, depth + 1);
              else if (child.isFile()) await candidate(selector, name);
              else throw new ProcessError('INPUT_SPECIAL_FILE');
            }
          }
          try { await visit(prefix, 0); } catch (error) { if (error.code !== 'ENOENT') throw error; }
        }
      } catch (error) { unknowns.push({ rootId: selector.rootId, code: error.code ?? 'INPUT_UNREADABLE' }); }
    }
    return { files: [...found.values()].sort(fileOrder), unknowns };
  }
  async function capture(rawSelectors) {
    requireProcess(Array.isArray(rawSelectors) && rawSelectors.length > 0 && rawSelectors.length <= 100, 'SELECTORS_INVALID');
    const selectors = rawSelectors.map(selector => validateDefinition(selector, 'selector'));
    const before = await enumerate(selectors), files = [], unknowns = [...before.unknowns];
    let remaining = limits.maxTotalBytes;
    for (const item of before.files) {
      const { metadata, ...file } = item;
      if (metadata === null) { files.push({ ...file, state: 'missing', digest: null, bytes: null }); continue; }
      try {
        const result = await hashFile(file.rootId, file.path, remaining); remaining -= result.bytes;
        files.push({ ...file, state: 'present', ...result });
      } catch (error) {
        files.push({ ...file, state: 'unreadable', digest: null, bytes: null });
        unknowns.push({ rootId: file.rootId, path: file.path, code: error.code ?? 'INPUT_UNREADABLE' });
      }
    }
    const after = await enumerate(selectors); unknowns.push(...after.unknowns);
    if (!sameValue(before.files, after.files)) unknowns.push({ code: 'INPUT_CHANGED_DURING_SCAN' });
    const selectionDigest = sha256(selectors);
    return { selectors, selectionDigest, snapshot: { digest: sha256({ selectionDigest, files }), files }, complete: unknowns.length === 0, unknowns };
  }
  async function observe(receipt, observedAt) {
    validateDefinition(observedAt, 'timestamp');
    const currentInputObservations = [], currentSubjectObservations = [], gaps = [];
    for (const input of receipt.inputSets) {
      const result = await capture(input.selectors); gaps.push(...result.unknowns);
      currentInputObservations.push({ inputSetId: input.id, selectionDigest: result.selectionDigest, digest: result.complete ? result.snapshot.digest : null, state: result.complete ? 'readable' : 'unknown', observedAt });
    }
    for (const subject of receipt.subjects) {
      let digest = null;
      if (subject.locator) {
        try { digest = (await hashFile(subject.locator.rootId, subject.locator.path)).digest; }
        catch (error) { gaps.push({ subjectId: subject.id, code: error.code ?? 'SUBJECT_UNREADABLE' }); }
      } else gaps.push({ subjectId: subject.id, code: 'SUBJECT_REQUIRES_OBSERVER' });
      currentSubjectObservations.push({ subjectId: subject.id, identityDigest: digest, state: digest === null ? 'unknown' : 'observed', observedAt });
    }
    return { currentInputObservations, currentSubjectObservations, gaps };
  }
  return Object.freeze({ capture, observe, hashFile });
}
