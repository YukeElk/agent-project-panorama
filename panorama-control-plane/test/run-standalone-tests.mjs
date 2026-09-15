import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';

const tests = readdirSync('test/standalone').filter((name) => name.endsWith('.test.mjs')).sort().map((name) => join('test/standalone', name));
if (!tests.length) throw new Error('STANDALONE_TESTS_MISSING');
const result = spawnSync(process.execPath, ['--test', ...tests], { stdio: 'inherit', windowsHide: true, shell: false });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
