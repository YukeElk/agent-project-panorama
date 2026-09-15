import assert from 'node:assert/strict';
import { open, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { temporary } from '../process/temp.mjs';
import { acquireLock } from '../../src/standalone/design-storage.mjs';

test('a newly created lock can finish publishing its owner without permitting a second writer', async t => {
  const root = await temporary(t), path = join(root, 'writer.lock');
  const owner = { pid: process.pid, token: 'owner-publishing-123456' };
  const handle = await open(path, 'wx');
  const published = new Promise((resolve, reject) => setTimeout(async () => {
    try { await handle.writeFile(JSON.stringify(owner)); await handle.sync(); resolve(); }
    catch (error) { reject(error); }
    finally { await handle.close(); }
  }, 50));
  try { await assert.rejects(() => acquireLock(path), error => error.code === 'WORKSPACE_LOCKED'); }
  finally { await published; }
  assert.deepEqual(JSON.parse(await readFile(path, 'utf8')), owner);
});

test('a persistently incomplete lock fails closed without deleting unknown ownership', async t => {
  const root = await temporary(t), path = join(root, 'unknown.lock');
  await writeFile(path, '{"pid":');
  await assert.rejects(() => acquireLock(path), error => error.code === 'WORKSPACE_LOCK_INVALID');
  assert.equal(await readFile(path, 'utf8'), '{"pid":');
});
