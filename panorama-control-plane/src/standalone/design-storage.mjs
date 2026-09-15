import { createHash, randomUUID } from 'node:crypto';
import { mkdir, open, readFile, realpath, rename, unlink, lstat } from 'node:fs/promises';
import { dirname, basename, resolve, relative, isAbsolute, sep, join } from 'node:path';
import { assert, domainError } from './design-model.mjs';

const within = (root, file) => { const offset = relative(root, file); return offset === '' || (!offset.startsWith(`..${sep}`) && offset !== '..' && !isAbsolute(offset)); };
export async function canonicalFuturePath(input) {
  let cursor = resolve(input); const suffix = [];
  while (true) {
    try { return join(await realpath(cursor), ...suffix.reverse()); }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      const parent = dirname(cursor); if (parent === cursor) throw error;
      suffix.push(basename(cursor)); cursor = parent;
    }
  }
}
export async function prepareStorage(projectRoot, dataRoot) {
  const project = await realpath(resolve(projectRoot));
  assert((await lstat(project)).isDirectory(), 'projectRoot must be a directory');
  const data = await canonicalFuturePath(dataRoot);
  assert(!within(project, data), 'Panorama dataRoot must be outside project source', 400, 'DATA_ROOT_INSIDE_PROJECT');
  await mkdir(data, { recursive: true });
  const actual = await realpath(data);
  assert(!within(project, actual) && actual === data, 'Data root changed during creation', 400, 'DATA_ROOT_INSIDE_PROJECT');
  return { projectRoot: project, dataRoot: actual };
}
function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; return true; }
}
async function readLock(file) {
  let value;
  try { value = JSON.parse(await readFile(file, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') throw error;
    throw domainError('Unreadable workspace lock; owner cannot be proved absent', 409, 'WORKSPACE_LOCK_INVALID');
  }
  assert(Number.isSafeInteger(value?.pid) && value.pid > 0 && typeof value.token === 'string' && value.token.length > 8, 'Invalid workspace lock; owner cannot be proved absent', 409, 'WORKSPACE_LOCK_INVALID');
  return value;
}
export async function acquireLock(file, depth = 0) {
  assert(depth < 8, 'Workspace lock recovery chain requires inspection', 409, 'WORKSPACE_LOCK_INVALID');
  const owner = { pid: process.pid, token: randomUUID() };
  for (let attempt = 0; attempt < 6; attempt++) {
    let handle;
    try { handle = await open(file, 'wx', 0o600); }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      let previous;
      try { previous = await readLock(file); } catch (readError) { if (readError.code === 'ENOENT') continue; throw readError; }
      assert(!alive(previous.pid), 'Workspace data directory is already open in an active process', 409, 'WORKSPACE_LOCKED');
      // A token-specific recovery lock serializes stale-owner removal. Every
      // contender re-reads the original owner under that lock, so it cannot
      // remove a fresh writer that acquired the main lock in the meantime.
      const recoveryFile = `${file}.recover-${createHash('sha256').update(previous.token).digest('hex').slice(0, 16)}`;
      const recovery = await acquireLock(recoveryFile, depth + 1);
      try {
        let now;
        try { now = await readLock(file); } catch (readError) { if (readError.code !== 'ENOENT') throw readError; }
        if (now?.token === previous.token && now.pid === previous.pid && !alive(now.pid)) await unlink(file);
      } finally { await releaseLock(recovery); }
      continue;
    }
    try { await handle.writeFile(JSON.stringify(owner)); await handle.sync(); }
    catch (error) { await handle.close(); await unlink(file).catch(() => {}); throw error; }
    await handle.close(); return { file, ...owner };
  }
  throw domainError('Workspace lock is contended; retry opening', 409, 'WORKSPACE_LOCKED');
}
export async function releaseLock(lock) {
  if (!lock) return;
  try { const actual = await readLock(lock.file); if (actual.token === lock.token && actual.pid === lock.pid) await unlink(lock.file); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
}
export async function atomicWrite(file, value) {
  const temporary = `${file}.tmp-${process.pid}-${randomUUID()}`;
  const handle = await open(temporary, 'wx', 0o600);
  try { await handle.writeFile(JSON.stringify(value, null, 2), 'utf8'); await handle.sync(); }
  catch (error) { await handle.close(); await unlink(temporary).catch(() => {}); throw error; }
  await handle.close();
  try { await rename(temporary, file); }
  catch (error) { await unlink(temporary).catch(() => {}); throw error; }
  // Directory flushing is unavailable on Windows; the file was flushed before
  // the same-directory atomic rename. Other platforms additionally flush it.
  if (process.platform !== 'win32') {
    let directory;
    try { directory = await open(dirname(file), 'r'); await directory.sync(); }
    catch { /* Atomic replacement already completed; never report a rollback. */ }
    finally { await directory?.close(); }
  }
}
