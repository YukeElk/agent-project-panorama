import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, readdir, realpath } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';
import { spawn } from 'node:child_process';
import { installLocal } from '../../scripts/install-local.mjs';
import { temporary } from './temp.mjs';
import { python } from './development-helpers.mjs';

test('P6 fixed installation verifies copied code and dependencies and runs without a project wrapper',async t=>{
  const base=await temporary(t),destination=join(base,'installed'),project=join(base,'empty project'),host=join(base,'host');await mkdir(project);
  const installed=await installLocal({destination,python});assert.equal(installed.installed,true);
  const manifest=JSON.parse(await readFile(join(destination,'installation.json'),'utf8'));
  assert.ok(manifest.files.length>1000);assert.ok(manifest.files.some(row=>row.path.startsWith('dist/workbench/')));
  for(const entry of manifest.files.filter(row=>row.target)) {
    const actual=await realpath(join(destination,'app',entry.path));
    assert.equal(actual,await realpath(join(destination,'app',entry.target)));
    assert.ok(!relative(join(destination,'app'),actual).startsWith('..'+sep));
  }
  const run=async(...args)=>new Promise((done,reject)=>{
    const child=spawn(process.execPath,[join(destination,'panorama-launch.mjs'),...args],{cwd:project,env:{...process.env,PANORAMA_STATE_HOME:host},windowsHide:true,stdio:['ignore','pipe','pipe']});let stdout='',stderr='';
    child.stdout.on('data',value=>stdout+=value);child.stderr.on('data',value=>stderr+=value);child.once('error',reject);child.once('close',code=>done({code,stdout,stderr}));
  });
  const doctor=await run('doctor');assert.equal(doctor.code,0,doctor.stderr);assert.equal(JSON.parse(doctor.stdout).ready,true);
  const request=join(base,'bootstrap with spaces.json');
  const preflight=await run('process','preflight','--project',project,'--data',join(base,'data'),'--output',request);
  assert.equal(preflight.code,0,preflight.stderr);assert.equal(JSON.parse(preflight.stdout).python,manifest.runtimes.python.path);
  assert.deepEqual((await readdir(project)),[]);
  assert.deepEqual(JSON.parse(await readFile(request,'utf8')).runners,[]);
  await assert.rejects(installLocal({destination,python}),{code:'INSTALLATION_DESTINATION_EXISTS'});
  const changed=join(destination,'app/src/development/cli.mjs');await writeFile(changed,'process.stdout.write("SHOULD_NOT_RUN");');
  const refused=await run('process','help');assert.equal(refused.code,1);assert.match(refused.stderr,/INSTALLATION_FILES_CHANGED/);assert.doesNotMatch(refused.stdout,/SHOULD_NOT_RUN/);
});

test('P6 installer rejects missing runtimes before publishing an installation',async t=>{
  const base=await temporary(t),destination=join(base,'missing');
  await assert.rejects(installLocal({destination,python:join(base,'missing-python.exe')}));
  assert.deepEqual(await readdir(base),[]);
});
