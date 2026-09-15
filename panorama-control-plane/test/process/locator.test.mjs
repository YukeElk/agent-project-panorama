import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { temporary } from './temp.mjs';
import { findProject } from '../../src/local-project.mjs';

test('P2 module identity is checked against its actual owning project, path and nested boundaries', async t => {
  const root = await temporary(t), module = join(root, 'module'), source = join(module, 'src');
  await mkdir(join(root, '.structure')); await mkdir(join(module, '.structure'), { recursive: true }); await mkdir(source);
  await writeFile(join(root, '.structure/identity.json'), JSON.stringify({ schema_version: 1, kind: 'project', project_id: 'owner' }));
  await writeFile(join(root, '.structure/manifest.json'), JSON.stringify({ modules: { module: { path: 'module' } } }));
  const file = join(module, '.structure/identity.json');
  const marker = { schema_version: 1, kind: 'module', project_id: 'owner', module_id: 'module', project_root: '..' };
  await writeFile(file, JSON.stringify(marker));
  assert.equal(await findProject(source), root);
  await writeFile(file, JSON.stringify({ ...marker, project_id: 'different' }));
  await assert.rejects(findProject(source), error => error.code === 'MODULE_OWNER_MISMATCH');
  await writeFile(file, JSON.stringify({ ...marker, project_root: '../..' }));
  await assert.rejects(findProject(source), error => error.code === 'MODULE_OWNER_MISMATCH');
  await writeFile(file, JSON.stringify(marker));
  await mkdir(join(source, '.structure'));
  await writeFile(join(source, '.structure/identity.json'), JSON.stringify({ schema_version: 1, kind: 'project', project_id: 'independent' }));
  assert.equal(await findProject(source), source);
});
