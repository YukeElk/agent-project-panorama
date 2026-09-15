import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, relative, sep, basename } from 'node:path';
import assert from 'node:assert/strict';
export async function temporary(test) {
  const base = await realpath(tmpdir()), path = await mkdtemp(join(base,'pp1-'));
  test.after(async () => {
    const actual = await realpath(path), suffix = relative(base,actual);
    assert.ok(actual === resolve(path) && suffix && !suffix.startsWith('..' + sep) && suffix !== '..' && basename(actual).startsWith('pp1-'));
    await rm(actual,{recursive:true,force:true});
  });
  return path;
}
