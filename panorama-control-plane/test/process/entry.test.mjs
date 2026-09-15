import test from 'node:test';
import assert from 'node:assert/strict';
import { writeFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fixture, production, attest } from './helpers.mjs';
import { temporary } from './temp.mjs';

const run = args => spawnSync(process.execPath,['scripts/assess-process.mjs',...args],{encoding:'utf8',windowsHide:true,shell:false});
test('the local data entry writes computed results, preserves exit statuses and refuses overwrite or file-supplied authority', async t => {
  const base = await temporary(t), input = join(base,'请求.json'), output = join(base,'结果.json');
  await writeFile(input,JSON.stringify(await fixture()));
  assert.equal(run(['--input',input,'--output',output]).status,0);
  const saved = await readFile(output,'utf8'); assert.equal(JSON.parse(saved).overall.status,'satisfied');
  assert.equal(run(['--input',input,'--output',output]).status,1); assert.equal(await readFile(output,'utf8'),saved);
  await writeFile(input,JSON.stringify(await fixture('cat-review-missing')));
  const missing = run(['--input',input]); assert.equal(missing.status,2); assert.equal(JSON.parse(missing.stdout).overall.processReady,false);
  await writeFile(input,JSON.stringify(attest(production(await fixture()))));
  const authority = run(['--input',input]); assert.equal(authority.status,1); assert.equal(JSON.parse(authority.stderr).error,'TRUSTED_CONTEXT_REQUIRED');
});
