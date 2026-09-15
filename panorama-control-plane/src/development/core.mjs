import { spawn } from 'node:child_process';
import { readFile, readdir, lstat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { sha256, parseProcessJson } from '../process/json.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { requireProcess, ProcessError } from '../process/errors.mjs';
import { readJson, absolute, exists } from './io.mjs';
import { timed } from './timing.mjs';

export const packageRoot = fileURLToPath(new URL('../../', import.meta.url));
export async function verifyCore() {
  const root = join(packageRoot, 'vendor/structure-core');
  const manifest = await readJson(join(root, 'vendor-manifest.json'));
  const lock = await readJson(join(packageRoot, 'contracts/process/upstream.lock.json'));
  requireProcess(manifest.commit === lock.commit && manifest.version === lock.coreVersion && manifest.digest === '66134b80b4d89640dbad07c58116b0de0c3c3556e6f0246a22f8de8f0525f49b' && manifest.digest === sha256(manifest.files), 'CORE_PIN_MISMATCH');
  for (const entry of manifest.files) {
    requireProcess(!entry.path.includes('..') && !entry.path.includes('\\') && !entry.path.startsWith('/'), 'CORE_VENDOR_INVALID');
    const path = join(root, entry.path); await assertOrdinaryPath(path);
    requireProcess(sha256(await readFile(path)) === entry.sha256, 'CORE_VENDOR_CHANGED', { path: entry.path });
  }
  // Added Python modules can shadow imports; verify the entire executable tree.
  const actual = [];
  async function walk(path = '') {
    for (const entry of await readdir(join(root, path), { withFileTypes: true })) {
      const name = path ? path + '/' + entry.name : entry.name;
      requireProcess(!entry.isSymbolicLink(), 'CORE_VENDOR_CHANGED');
      if (entry.isDirectory()) await walk(name);
      else if (name !== 'vendor-manifest.json') actual.push(name);
    }
  }
  await walk();
  requireProcess(sha256(actual.sort()) === sha256(manifest.files.map(entry => entry.path).sort()), 'CORE_VENDOR_CHANGED');
  for (const pin of lock.interfaceFiles.filter(file => file.path.startsWith('packages/structure-core/'))) requireProcess(manifest.files.some(file => 'packages/structure-core/' + file.path === pin.path && file.sha256 === pin.sha256), 'CORE_PIN_MISMATCH');
  return manifest;
}

async function guardState(projectRoot) {
  const root = join(projectRoot, '.structure');
  if (!await exists(root)) return;
  let count = 0;
  async function walk(path) {
    await assertOrdinaryPath(path);
    for (const entry of await readdir(path, { withFileTypes: true })) {
      requireProcess(++count < 100000 && !entry.isSymbolicLink(), 'CORE_STATE_UNSAFE');
      if (entry.isDirectory()) await walk(join(path, entry.name));
    }
  }
  await walk(root);
}
export async function openCore({ projectRoot, python }) {
  absolute(python); await assertOrdinaryPath(python);
  requireProcess((await lstat(python)).isFile(), 'PYTHON_UNAVAILABLE');
  await verifyCore(); await guardState(projectRoot);
  // Upstream uses long hash filenames and directory staging on Windows. Keep a
  // conservative explicit bound instead of leaving half an initialized project.
  const longestRelative = '.structure-stage-' + 'x'.repeat(32) + '/modules/.' + 'x'.repeat(64) + '.json.' + 'x'.repeat(32) + '.tmp';
  const maximumProjectRootLength = 258 - longestRelative.length;
  requireProcess(process.platform !== 'win32' || projectRoot.length <= maximumProjectRootLength, 'CORE_WINDOWS_PATH_TOO_LONG', { maximumProjectRootLength });
  async function call(operation, args = {}) {
    return timed('core', async () => {
    await guardState(projectRoot);
    return new Promise((resolve, reject) => {
      const child = spawn(python, ['-I', '-B', '-X', 'utf8', join(packageRoot, 'src/development/core-bridge.py')], { cwd: projectRoot, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
      const chunks = []; let size = 0, failed = false;
      const timer = setTimeout(() => { failed = true; child.kill(); reject(new ProcessError('CORE_TIMEOUT')); }, 30000);
      child.on('error', () => { clearTimeout(timer); reject(new ProcessError('PYTHON_UNAVAILABLE')); });
      child.stdout.on('data', bytes => { size += bytes.length; if (size > 1048576) { failed = true; child.kill(); reject(new ProcessError('CORE_OUTPUT_LIMIT')); } else chunks.push(bytes); });
      child.stderr.resume(); child.stdin.on('error', () => {});
      child.on('close', code => {
        clearTimeout(timer); if (failed) return;
        try {
          const response = parseProcessJson(Buffer.concat(chunks));
          requireProcess(code === 0 && response.ok, 'CORE_REJECTED', { operation, reason: response.error ?? 'Core transport failed' });
          resolve(response.result);
        } catch (error) { reject(error); }
      });
      child.stdin.end(JSON.stringify({ operation, args, projectRoot }));
    });
    });
  }
  const version = await call('version');
  requireProcess(version.version === '2.0.0-preview.2' && Number(version.python.split('.')[0]) === 3 && Number(version.python.split('.')[1]) >= 11, 'CORE_RUNTIME_VERSION_UNSUPPORTED');
  return { call, version };
}
