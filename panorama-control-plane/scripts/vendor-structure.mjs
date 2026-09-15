#!/usr/bin/env node
import { readFile, readdir, mkdir, copyFile } from 'node:fs/promises';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { sha256 } from '../src/process/json.mjs';
import { readJson, writeJson, exists } from '../src/development/io.mjs';
import { requireProcess } from '../src/process/errors.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const upstream = resolve(process.argv[2] ?? '');
requireProcess(process.argv[2], 'UPSTREAM_DIRECTORY_REQUIRED');
const lock = await readJson(join(root, 'contracts/process/upstream.lock.json'));
const files = [];
async function walk(path = '') {
  for (const entry of await readdir(join(upstream, path), { withFileTypes: true })) {
    const next = path ? path + '/' + entry.name : entry.name;
    if (entry.name === '__pycache__' || entry.name === '.git' || entry.name.endsWith('.pyc')) continue;
    requireProcess(!entry.isSymbolicLink(), 'VENDOR_LINK_REJECTED');
    if (entry.isDirectory()) await walk(next);
    else if (entry.isFile()) files.push({ path: next, sha256: sha256(await readFile(join(upstream, next))) });
  }
}
await walk(); files.sort((a, b) => a.path < b.path ? -1 : 1);
requireProcess(files.length === lock.archivedFileCount && sha256(Object.fromEntries(files.map(file => [file.path, file.sha256]))) === lock.fullTreeDigest, 'UPSTREAM_TREE_MISMATCH');
for (const pin of lock.interfaceFiles) requireProcess(files.find(file => file.path === pin.path)?.sha256 === pin.sha256, 'UPSTREAM_INTERFACE_MISMATCH');
const prefix = 'packages/structure-core/';
const selected = files.filter(file => file.path.startsWith(prefix)).map(file => ({ ...file, path: file.path.slice(prefix.length) })).filter(file => /^(scripts\/structure_core\/.*\.py|scripts\/structure\.py|templates\/|schemas\/|LICENSE$|package\.json$)/.test(file.path));
const destination = join(root, 'vendor/structure-core');
for (const file of selected) {
  const target = join(destination, file.path);
  if (await exists(target)) { requireProcess(sha256(await readFile(target)) === file.sha256, 'VENDOR_CONFLICT'); continue; }
  await mkdir(dirname(target), { recursive: true });
  await copyFile(join(upstream, prefix, file.path), target);
}
await writeJson(join(destination, 'vendor-manifest.json'), { formatVersion: 'panorama.structure-vendor.v1', repository: lock.repository, commit: lock.commit, version: lock.coreVersion, archiveSha256: lock.archiveSha256, fullTreeDigest: lock.fullTreeDigest, license: lock.license, files: selected, digest: sha256(selected) });
console.log(JSON.stringify({ verifiedArchiveFiles: files.length, vendoredFiles: selected.length, commit: lock.commit }));
