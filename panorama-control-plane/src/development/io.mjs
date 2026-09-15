import { open, lstat, mkdir, rename, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, isAbsolute } from 'node:path';
import { acquireLock, releaseLock, atomicWrite } from '../standalone/design-storage.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { parseProcessJson, processValue } from '../process/json.mjs';
import { requireProcess } from '../process/errors.mjs';
import { timed } from './timing.mjs';

export const now = () => new Date().toISOString();
export const alive = pid => {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code !== 'ESRCH'; }
};
export async function exists(path) {
  try { await lstat(path); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}
export async function readJson(path, { optional = false, maxBytes = 1048576 } = {}) {
  return timed('load_read', async () => {
  try {
    await assertOrdinaryPath(path);
    const stat = await lstat(path);
    requireProcess(stat.isFile() && stat.size <= maxBytes, 'LOCAL_FILE_LIMIT');
    const handle = await open(path, 'r');
    try {
      const actual = await handle.stat();
      requireProcess(actual.ino === stat.ino && actual.dev === stat.dev, 'LOCAL_PATH_CHANGED');
      const bytes = Buffer.alloc(maxBytes + 1);
      let size = 0;
      while (size < bytes.length) { const r = await handle.read(bytes, size, bytes.length - size, null); if (!r.bytesRead) break; size += r.bytesRead; }
      return parseProcessJson(bytes.subarray(0, size), { maxBytes, maxDepth: 24 });
    } finally { await handle.close(); }
  } catch (error) { if (optional && error.code === 'ENOENT') return null; throw error; }
  });
}
export async function writeJson(path, value) {
  return timed('storage', async () => {
  value = processValue(value, { maxBytes: 1048576, maxDepth: 24 });
  await assertOrdinaryPath(path, { allowMissing: true });
  await mkdir(dirname(path), { recursive: true });
  await atomicWrite(path, value);
  });
}
export async function atomicText(path, text) {
  await assertOrdinaryPath(path, { allowMissing: true });
  const temporary = path + '.' + randomUUID() + '.tmp';
  await exclusiveText(temporary, text);
  try { await rename(temporary, path); } finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}
export async function exclusiveText(path, text) {
  await assertOrdinaryPath(path, { allowMissing: true });
  await mkdir(dirname(path), { recursive: true });
  const handle = await open(path, 'wx', 0o600);
  try { await handle.writeFile(text); await handle.sync(); } finally { await handle.close(); }
}
export async function withLock(file, fn) {
  const lock = await timed('lock_wait', async () => {
    await assertOrdinaryPath(file, { allowMissing: true });
    await mkdir(dirname(file), { recursive: true });
    for (let n = 0; n < 80; n++) {
      try { return await acquireLock(file); }
      catch (error) {
        if (error.code !== 'WORKSPACE_LOCKED' || n === 79) throw error;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
    }
  });
  try { return await fn(); } finally { await releaseLock(lock); }
}
export function keys(value, allowed, required = []) {
  requireProcess(value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).every(key => allowed.includes(key)) && required.every(key => Object.hasOwn(value, key)), 'LOCAL_CONFIG_INVALID');
}
export function absolute(value) {
  requireProcess(typeof value === 'string' && isAbsolute(value) && !/[\r\n\0]/.test(value), 'ABSOLUTE_PATH_REQUIRED');
  return value;
}
