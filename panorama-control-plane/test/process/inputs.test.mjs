import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile, unlink, symlink, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { createInputRegistry } from '../../src/process/inputs.mjs';
import { sha256 } from '../../src/process/json.mjs';
import { temporary } from './temp.mjs';

const project = {inputRoots:[{id:'project',kind:'project'},{id:'external',kind:'external'}]};
const selector = (rootId,include,exclude=[]) => ({rootId,include,exclude,role:'source'});

test('glob snapshots detect added/deleted files, changed selectors and declared external bytes without source writes', async t => {
  const base = await temporary(t), source = join(base,'中文 项目'), external = join(base,'外部');
  await mkdir(join(source,'src'),{recursive:true}); await mkdir(external);
  await writeFile(join(source,'src','a.txt'),'first'); await writeFile(join(external,'golden.json'),'{"enabled":true}');
  const registry = await createInputRegistry(project,{project:{path:source,allow:['src/**']},external:{path:external,allow:['golden.json']}});
  const selected = [selector('project',['src/**/*.txt']),selector('external',['golden.json'])];
  const initial = await registry.capture(selected);
  assert.equal(initial.complete,true); assert.equal(initial.snapshot.files.length,2);
  assert.equal(initial.snapshot.digest,sha256({selectionDigest:initial.selectionDigest,files:initial.snapshot.files}));
  await writeFile(join(source,'src','b.txt'),'second');
  const added = await registry.capture(selected); assert.notEqual(added.snapshot.digest,initial.snapshot.digest);
  await unlink(join(source,'src','a.txt'));
  assert.notEqual((await registry.capture(selected)).snapshot.digest,added.snapshot.digest);
  await writeFile(join(external,'golden.json'),'{"enabled":false}');
  const externalChanged = await registry.capture(selected); assert.equal(externalChanged.complete,true);
  assert.notEqual(externalChanged.snapshot.digest,added.snapshot.digest);
  assert.notEqual((await registry.capture([selector('project',['src/*.txt'])])).selectionDigest,initial.selectionDigest);
  assert.equal(await readFile(join(source,'src','b.txt'),'utf8'),'second');
});

test('undeclared roots, missing mappings, secret paths and bounded reads remain incomplete', async t => {
  const base = await temporary(t); await writeFile(join(base,'large.bin'),'x'.repeat(100)); await writeFile(join(base,'.env'),'secret=unit-test');
  const registry = await createInputRegistry(project,{project:{path:base,allow:['**']}},{maxFileBytes:10});
  assert.equal((await registry.capture([selector('external',['golden.json'])])).complete,false);
  const large = await registry.capture([selector('project',['large.bin'])]);
  assert.equal(large.complete,false); assert.equal(large.snapshot.files[0].state,'unreadable');
  assert.equal((await registry.capture([selector('project',['.env'])])).complete,false);
  await assert.rejects(() => registry.capture([selector('project',['../escape'])]),error => error.code === 'SCHEMA_INVALID');
  await assert.rejects(() => createInputRegistry(project,{other:{path:base,allow:['**']}}),error => error.code === 'INPUT_ROOT_UNREGISTERED');
  const restricted = await createInputRegistry(project,{project:{path:base,allow:['large.bin']}});
  assert.equal((await restricted.capture([selector('project',['**'])])).complete,false);
  const missing = await restricted.capture([selector('project',['large.bin'])]);
  assert.equal(missing.complete,true);
});

test('a directory junction and a replaced registered ancestor are rejected without following their target', async t => {
  const base = await temporary(t), source = join(base,'source'), other = join(base,'other');
  await mkdir(source); await mkdir(other); await writeFile(join(other,'private.txt'),'unread-target');
  await symlink(other,join(source,'link'),process.platform === 'win32' ? 'junction' : 'dir');
  const registry = await createInputRegistry(project,{project:{path:source,allow:['**']}});
  assert.equal((await registry.capture([selector('project',['link/private.txt'])])).complete,false);
  await rename(source,join(base,'old-source')); await symlink(other,source,process.platform === 'win32' ? 'junction' : 'dir');
  const result = await registry.capture([selector('project',['private.txt'])]);
  assert.equal(result.complete,false); assert.equal(result.snapshot.files.length,0);
  assert.equal(await readFile(join(other,'private.txt'),'utf8'),'unread-target');
});

test('missing literal files have an explicit identity and non-file subjects require their own observer', async t => {
  const base = await temporary(t), registry = await createInputRegistry(project,{project:{path:base,allow:['**']}});
  const result = await registry.capture([selector('project',['missing.txt'])]);
  assert.equal(result.complete,true); assert.equal(result.snapshot.files[0].state,'missing');
  const receipt = {inputSets:[{id:'INPUT',selectors:[selector('project',['missing.txt'])]}],subjects:[{id:'SUB',locator:null,identityDigest:'a'.repeat(64)}]};
  const observed = await registry.observe(receipt,'2026-09-14T00:00:00Z');
  assert.equal(observed.currentSubjectObservations[0].state,'unknown');
});
