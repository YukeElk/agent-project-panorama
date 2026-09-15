import nodeTest from 'node:test';
import assert from 'node:assert/strict';
import { writeFile, readFile, readdir, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture, launch, until } from './development-helpers.mjs';
import { loadDevelopment } from '../../src/development/config.mjs';
import { processStore, runDirectory, workPath } from '../../src/development/journal.mjs';
import { readJson, alive, exists } from '../../src/development/io.mjs';
import { bodyHash } from '../../src/process/json.mjs';
import { locateProject } from '../../src/local-project.mjs';
import { fork, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { sha256 } from '../../src/process/json.mjs';
import { acquireLock, releaseLock } from '../../src/standalone/design-storage.mjs';
const test = (name, run) => nodeTest(name, { timeout: 60000 }, run);

const finishInput = f => f.input('outcome', { outcome: { summary: 'Task outcome recorded', incomplete: [], resumeNotes: ['Continue from the preserved work.'] } });
async function ctxOf(f) { return loadDevelopment({ projectRoot: f.project, dataRoot: f.data }); }

test('P2 actual CLI completes non-Git Chinese-path development with original baseline and real evidence', async t => {
  const f = await fixture(t);
  const started = await f.start();
  assert.equal(started.data.baseline.state, 'observed');
  await writeFile(join(f.project, 'source.txt'), 'after');
  const scan = await f.call(['scan', '--work', 'work:test'], 0);
  assert.deepEqual(scan.data.changedPaths, ['source.txt']);
  const check = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  assert.equal(check.data.result, 'passed');
  assert.ok(!check.stdout.includes('example-secret-marker'));
  const finish = await f.call(['finish', '--work', 'work:test', '--input', await f.input('outcome', { outcome: { summary: 'Implemented and checked', incomplete: [], resumeNotes: ['No outstanding changes.'] } })], 0);
  assert.equal(finish.data.finished, true);
  assert.equal(finish.data.coreState, 'done');
  assert.deepEqual(finish.data.baseline, started.data.baseline);
  const resume = await f.call(['resume', '--work', 'work:test'], 0);
  assert.equal(resume.data.currentProcessReady, true);
  assert.deepEqual(resume.data.baseline, started.data.baseline);
  const ctx = await ctxOf(f), store = await processStore(ctx), receipt = await store.getReceipt(check.data.receiptId);
  assert.equal(check.data.runDirectory, runDirectory(ctx, check.data.executionId));
  assert.ok((await readFile(join(check.data.runDirectory, 'stdout.log'), 'utf8')).includes('example-secret-marker'));
  assert.equal(receipt.checks.find(check => check.kind === 'machine').execution.exitCode, 0);
  assert.ok(!JSON.stringify(receipt).includes('example-secret-marker'));
  assert.ok((await readFile(join(runDirectory(ctx, check.data.executionId), 'stdout.log'), 'utf8')).includes('example-secret-marker'));
  if (process.env.PANORAMA_P2_EVIDENCE_DIR) {
    const directory = join(process.env.PANORAMA_P2_EVIDENCE_DIR, 'real-cli-smoke'); await mkdir(directory, { recursive: true });
    for (const [name, value] of Object.entries({ receipt, assessment: finish.data.assessment, resume: resume.data, commands: { begin: started.data, scan: scan.data, check: check.data, finish: { finished: finish.data.finished, coreState: finish.data.coreState, assessmentId: finish.data.assessment.assessmentId } } })) await writeFile(join(directory, name + '.json'), JSON.stringify(value, null, 2) + '\n');
  }
});

test('P2 init is idempotent, retains user AGENTS and rejects competing data roots or changed configuration', async t => {
  const f = await fixture(t), original = await readFile(join(f.project, 'AGENTS.md'), 'utf8');
  const identity = await readFile(join(f.project, '.structure/identity.json'), 'utf8');
  await f.call(['init', '--data', f.data, '--input', f.bootstrapFile], 0);
  assert.equal(await readFile(join(f.project, 'AGENTS.md'), 'utf8'), original);
  assert.ok(original.startsWith('User project instructions: preserve this line.'));
  assert.equal(await readFile(join(f.project, '.structure/identity.json'), 'utf8'), identity);
  const skill = join(f.project, '.agents/skills/panorama-development-process/SKILL.md');
  assert.equal(await readFile(skill, 'utf8'), await readFile(new URL('../../skills/panorama-development-process/SKILL.md', import.meta.url), 'utf8'));
  const competing = await f.call(['resume', '--data', join(f.directory, 'other-data')], 1);
  assert.equal(competing.data.error, 'MULTIPLE_DATA_ROOTS');
  const changed = await f.call(['init', '--input', await f.input('changed', { ...f.bootstrap, publicPaths: ['source.txt'] })], 1);
  assert.equal(changed.data.error, 'INIT_CONFIGURATION_CONFLICT');
  const sub = join(f.project, 'nested'); await mkdir(sub);
  const locations = await locateProject({ project: sub, stateHome: f.home });
  assert.equal(locations.projectRoot, f.project); assert.equal(locations.dataRoot, f.data);
});

test('P2 real nonzero exit remains a failed criterion; arbitrary unregistered commands cannot run', async t => {
  const f = await fixture(t); await f.start();
  const rejected = await f.call(['check', '--work', 'work:test', '--runner', 'arbitrary-shell'], 1);
  assert.equal(rejected.data.error, 'RUNNER_NOT_REGISTERED');
  const check = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 2);
  assert.equal(check.data.result, 'failed');
  assert.equal(check.data.recovery[0].exitCode, 7);
  const assessed = await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 2);
  assert.equal(assessed.data.finished, false); assert.equal(assessed.data.coreState, 'implementing');
  assert.equal(assessed.data.assessment.criteria[0].satisfaction, 'unsatisfied');
});

test('P2 successful execution with source mutation during the command cannot satisfy acceptance', async t => {
  const f = await fixture(t, { script: `import {writeFileSync} from 'node:fs'; writeFileSync('source.txt','after'); process.exit(0);` });
  await f.start(); const checked = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  assert.equal(checked.data.result, 'passed');
  const result = await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 2);
  assert.equal(result.data.finished, false); assert.equal(result.data.assessment.criteria[0].freshness.status, 'stale');
  assert.deepEqual(result.data.changedPaths, ['source.txt']);
});

test('P2 external input changes invalidate a real receipt even when project sources remain unchanged', async t => {
  const f = await fixture(t, { init: false });
  await writeFile(join(f.external, 'golden.json'), '{"version":1}');
  f.bootstrap.roots.push({ id: 'golden', kind: 'external', description: 'Registered external evaluation input', path: f.external, include: ['golden.json'], role: 'fixture' });
  await f.call(['init', '--data', f.data, '--input', await f.input('external-bootstrap', f.bootstrap)], 0);
  await f.start(); await writeFile(join(f.project, 'source.txt'), 'after');
  await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  await writeFile(join(f.external, 'golden.json'), '{"version":2}');
  const result = await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 2);
  assert.equal(result.data.assessment.criteria[0].freshness.status, 'stale');
  assert.ok(result.data.assessment.criteria[0].freshness.changedIds.includes('input:golden'));
});

test('P2 scope changes are scanned and public path rules become applicable', async t => {
  const f = await fixture(t, { init: false });
  f.bootstrap.publicPaths = ['api/**']; await mkdir(join(f.project, 'api'));
  await f.call(['init', '--data', f.data, '--input', await f.input('public-bootstrap', f.bootstrap)], 0);
  await f.start(); await writeFile(join(f.project, 'api/new.js'), 'export const added = true;');
  const scan = await f.call(['scan', '--work', 'work:test'], 2);
  assert.ok(scan.data.changedPaths.includes('api/new.js'));
  assert.equal(scan.data.policy.decision, 'deny');
  assert.equal(scan.data.applicability.find(row => row.ruleId === 'COND-INTERFACE').status, 'applicable');
});

test('P2 docs-only work uses a declared coding-agent review without triggering global machine checks', async t => {
  const f = await fixture(t, { bootstrap: { runners: [] }, work: {
    criteria: [{ id: 'DOC', version: 1, requirement: 'Document describes the change', required: true, subjectIds: ['doc'], allowedEvidenceKinds: ['document_review'], acceptedActorKinds: ['coding_agent'] }],
    subjects: [{ id: 'doc', kind: 'document', locator: { rootId: 'project', path: 'source.txt' } }],
  } });
  await f.start(); await writeFile(join(f.project, 'source.txt'), 'after');
  await f.call(['review', '--work', 'work:test', '--input', await f.input('review', { kind: 'document_review', result: 'passed', summary: 'Read the updated document', method: 'Compared the required wording with the file', findings: ['The document contains the requested after statement.'] })], 0);
  const finish = await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 0);
  assert.equal(finish.data.finished, true);
  const ctx = await ctxOf(f), state = await readJson(workPath(ctx, 'work:test'));
  assert.deepEqual(state.runs, []);
});

test('P2 imported self-reported human/local evidence remains untrusted', async t => {
  const f = await fixture(t, { work: {
    criteria: [{ id: 'VISUAL', version: 1, requirement: 'Human checks the visual scene', required: true, subjectIds: ['scene'], allowedEvidenceKinds: ['visual_review'], acceptedActorKinds: ['human'] }],
    subjects: [{ id: 'scene', kind: 'visual_scene', locator: { rootId: 'project', path: 'source.txt' } }],
  } });
  await f.start();
  const review = await f.call(['review', '--work', 'work:test', '--input', await f.input('review', { kind: 'visual_review', result: 'passed', summary: 'Coding agent inspected scene', method: 'Local comparison', findings: ['Recorded a visible result.'] })], 0);
  const ctx = await ctxOf(f), store = await processStore(ctx), receipt = await store.getReceipt(review.data.receiptId);
  receipt.receiptId = 'receipt:external-human'; receipt.executionId = 'execution:external-human';
  const claim = receipt.checks.find(check => check.kind === 'review'); claim.author = { id: 'claimed-human', kind: 'human', authority: 'observed' };
  for (const evidence of receipt.evidence.filter(evidence => evidence.checkId === claim.id)) evidence.author = claim.author;
  receipt.receiptHash = bodyHash(receipt, 'receiptHash');
  const imported = await f.call(['import', '--work', 'work:test', '--input', await f.input('receipt', receipt)], 0);
  assert.equal(imported.data.locallyAttested, false); assert.equal(imported.data.source.kind, 'local_capture');
  const result = await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 2);
  assert.equal(result.data.assessment.criteria[0].satisfaction, 'insufficient');
  assert.ok(result.data.assessment.criteria[0].missing.some(reason => reason.startsWith('EVIDENCE_SOURCE_UNVERIFIED')));
});

test('P2 killing the real CLI interrupts its owned check and fresh-process resume keeps the original baseline', async t => {
  const f = await fixture(t, { script: `import {mkdirSync,appendFileSync} from 'node:fs'; mkdirSync('out',{recursive:true}); appendFileSync('out/starts','one\\n'); setInterval(()=>{},500);` });
  const began = await f.start(), ctx = await ctxOf(f);
  const child = launch(['check', '--project', f.project, '--work', 'work:test', '--runner', 'test'], { cwd: f.project, env: f.env });
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  const status = await until(async () => {
    try {
    const state = await readJson(workPath(ctx, 'work:test'));
    if (!state.runs.length) return null;
    const result = await readJson(join(runDirectory(ctx, state.runs[0].executionId), 'status.json'), { optional: true });
    return result?.status === 'running' ? result : null;
    } catch (error) {
      // The collector atomically replaces these live files. Retry observation,
      // without accepting a torn snapshot or weakening production read checks.
      if (error.code === 'LOCAL_PATH_CHANGED') return null;
      throw error;
    }
  });
  assert.ok(alive(status.businessPid));
  await until(() => exists(join(f.project, 'out/starts')));
  child.kill('SIGKILL'); await child.completed;
  await until(() => !alive(status.workerPid) && !alive(status.businessPid));
  const resume = await f.call(['resume', '--work', 'work:test'], 2);
  assert.deepEqual(resume.data.baseline, began.data.baseline);
  assert.equal(resume.data.recovery[0].result, 'interrupted');
  assert.equal(await readFile(join(f.project, 'out/starts'), 'utf8'), 'one\n');
  await f.call(['resume', '--work', 'work:test'], 2);
  assert.equal(await readFile(join(f.project, 'out/starts'), 'utf8'), 'one\n');
});

test('P2 actual process death between core create and local commit reconciles one work item', async t => {
  const f = await fixture(t);
  const child = fork(fileURLToPath(new URL('./development-crash-worker.mjs', import.meta.url)), [await f.input('crash', { projectRoot: f.project, dataRoot: f.data, workItemId: 'work:crash', command: 'begin', input: f.work, pause: 'begin.core_created' })], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  await new Promise((resolve, reject) => { child.once('message', resolve); child.once('exit', code => reject(new Error('Unexpected worker exit ' + code))); });
  const ctx = await ctxOf(f), pending = await readJson(workPath(ctx, 'work:crash'));
  assert.equal(pending.operations.begin.status, 'pending');
  child.kill('SIGKILL'); await new Promise(resolve => child.once('exit', resolve));
  const result = await f.call(['resume', '--work', 'work:crash'], 2);
  assert.deepEqual(result.data.baseline, JSON.parse(JSON.stringify(pending.context.baseline)));
  const core = (await ctx.core.call('work')).work_items;
  assert.equal(core.filter(work => work.id === 'work:crash').length, 1);
  assert.equal(core.find(work => work.id === 'work:crash').state, 'implementing');
  assert.equal((await readJson(workPath(ctx, 'work:crash'))).operations.begin.status, 'committed');
});

test('P2 receipt commit interruption recovers provenance and does not duplicate immutable records', async t => {
  const f = await fixture(t); await f.start();
  const request = { projectRoot: f.project, dataRoot: f.data, workItemId: 'work:test', command: 'scan', input: {}, pause: 'receipt.store_committed' };
  const child = fork(fileURLToPath(new URL('./development-crash-worker.mjs', import.meta.url)), [await f.input('crash', request)], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  await new Promise((resolve, reject) => { child.once('message', resolve); child.once('exit', code => reject(new Error('Unexpected worker exit ' + code))); });
  const ctx = await ctxOf(f), pending = (await readJson(workPath(ctx, 'work:test'))).pendingReceipts[0];
  child.kill('SIGKILL'); await new Promise(resolve => child.once('exit', resolve));
  await f.call(['resume', '--work', 'work:test'], 2);
  const state = await readJson(workPath(ctx, 'work:test')), store = await processStore(ctx);
  assert.equal(state.pendingReceipts.length, 0);
  assert.equal(state.receipts.filter(receipt => receipt.id === pending.id).length, 1);
  assert.equal((await store.list()).records.filter(receipt => receipt.id === pending.id).length, 1);
});

test('P2 core done after a crash cannot certify stale process evidence on resume', async t => {
  const f = await fixture(t); await f.start(); await writeFile(join(f.project, 'source.txt'), 'after');
  await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  const request = { projectRoot: f.project, dataRoot: f.data, workItemId: 'work:test', command: 'finish', input: { outcome: { summary: 'Checked', incomplete: [], resumeNotes: ['Resume after interruption'] } }, pause: 'finish.core_done' };
  const child = fork(fileURLToPath(new URL('./development-crash-worker.mjs', import.meta.url)), [await f.input('crash', request)], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  await new Promise((resolve, reject) => { child.once('message', resolve); child.once('exit', code => reject(new Error('Unexpected worker exit ' + code))); });
  child.kill('SIGKILL'); await new Promise(resolve => child.once('exit', resolve));
  await writeFile(join(f.project, 'source.txt'), 'changed after the core completion');
  const resume = await f.call(['resume', '--work', 'work:test'], 2);
  assert.equal(resume.data.finished, false); assert.equal(resume.data.coreState, 'done');
  assert.equal(resume.data.assessment.overall.processReady, false);
  assert.notEqual(resume.data.phase, 'finished');
});

test('P2 an existing workspace lock remains intact during local process operations', async t => {
  const f = await fixture(t), marker = { preserved: 'old workspace data' };
  await writeFile(join(f.data, 'workspace.json'), JSON.stringify(marker));
  const lockPath = join(f.data, '.panorama.lock'), lock = await acquireLock(lockPath);
  t.after(() => releaseLock(lock));
  const bytes = await readFile(lockPath, 'utf8');
  await f.start(); await f.call(['scan', '--work', 'work:test'], 0);
  assert.equal(await readFile(lockPath, 'utf8'), bytes);
  assert.deepEqual(JSON.parse(await readFile(join(f.data, 'workspace.json'), 'utf8')), marker);
});

test('P2 Harness-managed work is linked read-only and never transitioned or duplicated', async t => {
  const f = await fixture(t), ctx = await ctxOf(f);
  await ctx.core.call('work', { action: 'create', id: 'legacy', title: 'Legacy Harness task', kind: 'feature', impact: 'internal', modules: ctx.project.moduleBindings.map(module => module.moduleId), scope: ['source.txt'], acceptance: ['Legacy claim'] });
  const path = join(f.project, '.structure/work-items', sha256('legacy') + '.json');
  const legacy = JSON.parse(await readFile(path, 'utf8')); legacy.managed_by = 'pi-development-harness'; legacy.state = 'done';
  await writeFile(path, JSON.stringify(legacy)); const original = await readFile(path, 'utf8');
  const linked = await f.call(['begin', '--work', 'legacy', '--input', await f.input('legacy-work', f.work)], 2);
  assert.equal(linked.data.phase, 'legacy_read_only');
  const resumed = await f.call(['resume', '--work', 'legacy'], 2);
  assert.equal(resumed.data.processReady, false); assert.equal(resumed.data.legacy.state, 'done');
  assert.equal(await readFile(path, 'utf8'), original);
  assert.equal((await ctx.core.call('work')).work_items.length, 1);
});

test('P2 missing Python and Windows deep roots fail before core initialization', async t => {
  const f = await fixture(t, { init: false });
  const missing = await f.call(['init', '--data', f.data, '--python', join(f.directory, 'missing-python.exe'), '--input', f.bootstrapFile], 1);
  assert.equal(await exists(join(f.project, '.structure')), false);
  if (process.platform === 'win32') {
    const deep = join(f.directory, 'a'.repeat(65), 'b'.repeat(65)); await mkdir(deep, { recursive: true });
    const result = await launch(['init', '--project', deep, '--data', join(f.directory, 'deep-data'), '--input', f.bootstrapFile], { cwd: deep, env: f.env }).completed;
    assert.equal(result.code, 1, result.stdout); assert.equal(result.data.error, 'CORE_WINDOWS_PATH_TOO_LONG');
    assert.equal(await exists(join(deep, '.structure')), false);
  }
});

test('P2 current runner environment changes invalidate otherwise identical inputs', async t => {
  const f = await fixture(t, { init: false });
  f.bootstrap.runners[0].envKeys = ['PANORAMA_TEST_MODE']; f.env.PANORAMA_TEST_MODE = 'first';
  await f.call(['init', '--data', f.data, '--input', await f.input('environment-bootstrap', f.bootstrap)], 0);
  await f.start(); await writeFile(join(f.project, 'source.txt'), 'after');
  const check = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  f.env.PANORAMA_TEST_MODE = 'second';
  const result = await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 2);
  assert.ok(result.data.assessment.criteria[0].freshness.changedIds.includes(check.data.executionId));
  assert.equal(result.data.assessment.criteria[0].satisfaction, 'insufficient');
});

test('P2 corruption of an actually checked retained GLB is not covered by a successful source check', async t => {
  const f = await fixture(t, { init: false, script: `import {readFileSync} from 'node:fs'; const b=readFileSync(process.argv[2]); process.exit(b.toString('ascii',0,4)==='glTF' && b.readUInt32LE(4)===2 && b.readUInt32LE(8)===b.length ? 0 : 8);`, work: {
    criteria: [
      { id: 'MODEL', version: 1, requirement: 'Retained GLB passes its structural check', required: true, subjectIds: ['model'], allowedEvidenceKinds: ['artifact_integrity'], acceptedActorKinds: ['system'] },
      { id: 'SOURCE', version: 1, requirement: 'Source checker is available', required: false, subjectIds: ['source'], allowedEvidenceKinds: ['behavior_test'], acceptedActorKinds: ['system'] },
    ], subjects: [{ id: 'model', kind: 'retained_artifact', locator: { rootId: 'modelRoot', path: 'model.glb' } }, { id: 'source', kind: 'source_behavior', locator: null }],
  } });
  const output = join(f.project, 'out'); await mkdir(output);
  // Small real GLB 2.0 container: header + padded JSON chunk.
  const json = Buffer.from('{"asset":{"version":"2.0"}} '), glb = Buffer.alloc(20 + json.length);
  glb.write('glTF'); glb.writeUInt32LE(2, 4); glb.writeUInt32LE(glb.length, 8); glb.writeUInt32LE(json.length, 12); glb.writeUInt32LE(0x4e4f534a, 16); json.copy(glb, 20);
  await writeFile(join(output, 'model.glb'), glb);
  f.bootstrap.roots.push({ id: 'modelRoot', kind: 'artifact', description: 'Retained GLB', path: output, include: ['model.glb'], role: 'artifact' });
  f.bootstrap.runners = [{ id: 'model', command: process.execPath, args: ['check.mjs', '{subject:model}'], evidenceKinds: ['artifact_integrity'] }, { id: 'source', command: process.execPath, args: ['-e', 'process.exit(0)'], evidenceKinds: ['behavior_test'] }];
  await f.call(['init', '--data', f.data, '--input', await f.input('model-bootstrap', f.bootstrap)], 0);
  await f.start(); await f.call(['check', '--work', 'work:test', '--runner', 'model'], 0);
  await writeFile(join(output, 'model.glb'), 'corrupt retained model');
  await f.call(['check', '--work', 'work:test', '--runner', 'source'], 0);
  const result = await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 2);
  assert.equal(result.data.assessment.criteria.find(criterion => criterion.criterionId === 'MODEL').satisfaction, 'insufficient');
  assert.equal(result.data.assessment.criteria.find(criterion => criterion.criterionId === 'MODEL').freshness.status, 'stale');
  assert.equal(result.data.assessment.criteria.find(criterion => criterion.criterionId === 'SOURCE').satisfaction, 'satisfied');
});

test('P2 a fresh output has its own identity and producing execution; preexisting output cannot be relabeled fresh', async t => {
  const f = await fixture(t, { init: false, script: `import {writeFileSync} from 'node:fs'; writeFileSync(process.argv[2],'new build');`, work: {
    criteria: [{ id: 'BUILD', version: 1, requirement: 'Produce the new build', required: true, subjectIds: ['build'], allowedEvidenceKinds: ['artifact_integrity'], acceptedActorKinds: ['system'] }],
    subjects: [{ id: 'build', kind: 'fresh_build', locator: { rootId: 'output', path: 'new.bin' } }],
  } });
  await mkdir(join(f.project, 'out'));
  f.bootstrap.roots.push({ id: 'output', kind: 'artifact', description: 'New build output', path: join(f.project, 'out'), include: ['new.bin'], observeOnly: true });
  f.bootstrap.runners = [{ id: 'build', command: process.execPath, args: ['check.mjs', '{subject:build}'], evidenceKinds: ['artifact_integrity'] }];
  await f.call(['init', '--data', f.data, '--input', await f.input('build-bootstrap', f.bootstrap)], 0); await f.start();
  const check = await f.call(['check', '--work', 'work:test', '--runner', 'build'], 0);
  const ctx = await ctxOf(f), receipt = await (await processStore(ctx)).getReceipt(check.data.receiptId);
  assert.equal(receipt.subjects.find(subject => subject.id === 'build').producedByExecutionId, check.data.executionId);
  await f.call(['finish', '--work', 'work:test', '--input', await finishInput(f)], 0);
  await f.start('work:second');
  const reused = await f.call(['check', '--work', 'work:second', '--runner', 'build'], 2);
  assert.equal(reused.data.result, 'not_run');
});

test('P2 contradictory current checks require explicit receipt selection and preserve failed history', async t => {
  const f = await fixture(t, { script: `import {existsSync,mkdirSync,writeFileSync} from 'node:fs'; mkdirSync('out',{recursive:true}); const previous=existsSync('out/toggle'); writeFileSync('out/toggle','yes'); process.exit(previous ? 0 : 7);` });
  await f.start();
  const fail = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 2);
  const pass = await f.call(['check', '--work', 'work:test', '--runner', 'test'], 0);
  const conflict = await f.call(['assess', '--work', 'work:test'], 2);
  assert.equal(conflict.data.assessment.criteria[0].satisfaction, 'conflict');
  const selected = await f.call(['finish', '--work', 'work:test', '--input', await f.input('selected-outcome', { outcome: { summary: 'Explicitly choose the subsequent observation', incomplete: [], resumeNotes: ['First failed execution remains historical evidence'] }, selection: { receiptIds: [pass.data.receiptId], reason: 'This scenario deliberately selects the second observation; the first is not deleted.' } })], 0);
  assert.equal(selected.data.finished, true);
  assert.ok(!selected.data.selectedReceiptIds.includes(fail.data.receiptId));
  const ctx = await ctxOf(f), failedReceipt = await (await processStore(ctx)).getReceipt(fail.data.receiptId);
  assert.equal(failedReceipt.checks.find(check => check.kind === 'machine').execution.exitCode, 7);
  const resume = await f.call(['resume', '--work', 'work:test'], 0);
  assert.equal(resume.data.currentProcessReady, true);
  assert.ok(!resume.data.selectedReceiptIds.includes(fail.data.receiptId));
});

test('P2 only one registered check runs in a checkout, including checks belonging to different work items', async t => {
  const f = await fixture(t, { script: `import {mkdirSync,writeFileSync} from 'node:fs'; mkdirSync('out',{recursive:true}); writeFileSync('out/started','yes'); setInterval(()=>{},500);` });
  await f.start(); await f.start('work:other');
  const child = launch(['check', '--project', f.project, '--work', 'work:test', '--runner', 'test'], { cwd: f.project, env: f.env });
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  await until(() => exists(join(f.project, 'out/started')));
  const concurrent = await f.call(['check', '--work', 'work:other', '--runner', 'test'], 1);
  assert.equal(concurrent.data.error, 'CHECKOUT_EXECUTION_BUSY');
  const configuration = await f.call(['config-plan', '--input', await f.input('during-check-config', { reason: 'Cannot switch during an actual running check' })], 2);
  assert.ok(configuration.data.blockers.some(row => row.workItemId === 'work:test' && row.code === 'WORK_RECOVERY_REQUIRED'));
  await f.call(['config-apply', '--input', await f.input('during-check-plan', configuration.data.plan)], 1);
  const ctx = await ctxOf(f), run = (await readJson(workPath(ctx, 'work:test'))).runs[0], status = await readJson(join(runDirectory(ctx, run.executionId), 'status.json'));
  child.kill('SIGKILL'); await child.completed;
  await until(() => !alive(status.workerPid) && !alive(status.businessPid));
  await f.call(['resume', '--work', 'work:test'], 2);
  assert.equal((await readJson(workPath(ctx, 'work:other'))).runs.length, 0);
});

test('P2 simultaneous initial data-root claims cannot establish two active destinations', async t => {
  const f = await fixture(t, { init: false });
  const results = await Promise.allSettled([
    locateProject({ project: f.project, data: join(f.directory, 'first'), stateHome: f.home }),
    locateProject({ project: f.project, data: join(f.directory, 'second'), stateHome: f.home }),
  ]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.find(result => result.status === 'rejected').reason.code, 'MULTIPLE_DATA_ROOTS');
});

test('P2 actual workbench launcher resolves the same project and data root from a subdirectory', async t => {
  const f = await fixture(t), nested = join(f.project, 'nested'); await mkdir(nested);
  const entry = fileURLToPath(new URL('../../src/cli.mjs', import.meta.url));
  const server = spawn(process.execPath, [entry, 'start'], { cwd: nested, env: { ...process.env, ...f.env }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let started = false; server.stdout.on('data', bytes => { started ||= bytes.toString().includes('已启动'); }); server.stderr.resume();
  const closed = new Promise(resolve => server.once('exit', resolve));
  try {
    await until(() => started);
    assert.ok(await exists(join(f.data, 'workspace.json')));
    const lock = await readJson(join(f.data, '.panorama.lock')); assert.equal(lock.pid, server.pid);
    await f.start(); await f.call(['scan', '--work', 'work:test'], 0);
    assert.equal((await readJson(join(f.data, '.panorama.lock'))).pid, server.pid);
  } finally { if (server.exitCode === null) server.kill('SIGTERM'); await closed; }
});

test('P2 completed check receipt keeps its exact ID/hash across a crash before execution journal commit', async t => {
  const f = await fixture(t, { script: `import {mkdirSync,appendFileSync} from 'node:fs'; mkdirSync('out',{recursive:true}); appendFileSync('out/count','one\\n'); process.exit(0);` });
  await f.start();
  const request = { projectRoot: f.project, dataRoot: f.data, workItemId: 'work:test', command: 'check', input: { runnerId: 'test' }, pause: 'receipt.store_committed' };
  const child = fork(fileURLToPath(new URL('./development-crash-worker.mjs', import.meta.url)), [await f.input('crash', request)], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  await new Promise((resolve, reject) => { child.once('message', resolve); child.once('exit', code => reject(new Error('Unexpected worker exit ' + code))); });
  const ctx = await ctxOf(f), pending = (await readJson(workPath(ctx, 'work:test'))).pendingReceipts[0];
  const receipt = await (await processStore(ctx)).getReceipt(pending.id);
  child.kill('SIGKILL'); await new Promise(resolve => child.once('exit', resolve));
  const resume = await f.call(['resume', '--work', 'work:test'], 2);
  assert.equal(resume.data.recovery[0].result, 'passed');
  const recovered = await (await processStore(ctx)).getReceipt(pending.id);
  assert.equal(recovered.receiptHash, receipt.receiptHash);
  assert.equal(recovered.generatedAt, receipt.generatedAt);
  assert.equal(await readFile(join(f.project, 'out/count'), 'utf8'), 'one\n');
  assert.equal((await readJson(workPath(ctx, 'work:test'))).runs[0].status, 'committed');
});
