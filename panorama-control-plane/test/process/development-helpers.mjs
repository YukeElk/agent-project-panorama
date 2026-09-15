import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { temporary } from './temp.mjs';

export const cli = fileURLToPath(new URL('../../src/development/cli.mjs', import.meta.url));
export const python = process.env.PANORAMA_TEST_PYTHON ?? process.env.PANORAMA_PYTHON ?? spawnSync(process.platform === 'win32' ? 'python' : 'python3', ['-I', '-B', '-c', 'import os, sys; print(os.path.realpath(sys.executable))'], { encoding: 'utf8', windowsHide: true }).stdout?.trim();
export function launch(args, { cwd, env } = {}) {
  const child = spawn(process.execPath, [cli, ...args], { cwd, env: { ...process.env, ...env }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.on('data', chunk => { stdout += chunk; }); child.stderr.on('data', chunk => { stderr += chunk; });
  child.completed = new Promise((resolve, reject) => { child.on('error', reject); child.on('close', code => {
    let data = null; try { data = JSON.parse(stdout || stderr); } catch {}
    resolve({ code, data, stdout, stderr });
  }); }); return child;
}
export async function fixture(t, options = {}) {
  const directory = await temporary(t), project = join(directory, '项目'), data = join(directory, 'data'), home = join(directory, 'host'), external = join(directory, 'external');
  await mkdir(project); await mkdir(external);
  await writeFile(join(project, 'source.txt'), 'before');
  await writeFile(join(project, 'check.mjs'), options.script ?? `import {readFileSync} from 'node:fs';\nconsole.log('private output: example-secret-marker');\nprocess.exit(readFileSync('source.txt','utf8') === 'after' ? 0 : 7);\n`);
  await writeFile(join(project, 'AGENTS.md'), 'User project instructions: preserve this line.\n');
  const bootstrap = { roots: [{ id: 'project', kind: 'project', description: 'Source and verification scripts', include: ['**'], exclude: ['out/**'] }], runners: [{ id: 'test', command: process.execPath, args: ['check.mjs'], evidenceKinds: ['behavior_test'], timeoutMs: 10000 }], ...options.bootstrap };
  const bootstrapFile = join(directory, 'bootstrap.json'); await writeFile(bootstrapFile, JSON.stringify(bootstrap));
  const work = { goal: '验证实际开发过程', expectedOutcome: '源文件符合约定并提供实际检查', plannedPaths: ['source.txt'], publicBehavior: false,
    criteria: [{ id: 'AC', version: 1, requirement: 'source.txt contains after', required: true, subjectIds: ['source'], allowedEvidenceKinds: ['behavior_test'], acceptedActorKinds: ['system'] }], subjects: [{ id: 'source', kind: 'source_behavior', locator: null }], ...options.work };
  const env = { PANORAMA_STATE_HOME: home, PANORAMA_PYTHON: python };
  async function call(args, expected) {
    const result = await launch([...args, '--project', project], { cwd: project, env }).completed;
    if (expected !== undefined) assert.equal(result.code, expected, result.stderr || result.stdout);
    return result;
  }
  async function input(name, value) { const path = join(directory, name + '.json'); await writeFile(path, JSON.stringify(value)); return path; }
  async function start(id = 'work:test') { return call(['begin', '--work', id, '--input', await input('work', work)], 0); }
  if (options.init !== false) await call(['init', '--data', data, '--input', bootstrapFile], 0);
  return { directory, project, data, external, home, bootstrap, bootstrapFile, work, env, call, input, start };
}
export async function until(fn, timeout = 12000) {
  const start = Date.now();
  while (Date.now() - start < timeout) { const result = await fn(); if (result) return result; await new Promise(resolve => setTimeout(resolve, 30)); }
  assert.fail('Expected process milestone was not reached');
}
