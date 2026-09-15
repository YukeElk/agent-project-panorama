import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
const tests = readdirSync('test/process').filter(name => name.endsWith('.test.mjs') && name !== 'contracts.test.mjs').sort().map(name => join('test/process',name));
if (!tests.length) throw new Error('PROCESS_TESTS_MISSING');
const result = spawnSync(process.execPath,['--test',...tests],{stdio:'inherit',shell:false,windowsHide:true});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
