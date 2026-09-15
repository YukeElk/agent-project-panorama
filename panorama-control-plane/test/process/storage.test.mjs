import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { openProcessStore } from '../../src/process/storage.mjs';
import { fixture, seal } from './helpers.mjs';
import { temporary } from './temp.mjs';

async function setup(t) {
  const base = await temporary(t), request = await fixture(), projectRoot = join(base,'project'), dataRoot = join(base,'data');
  await mkdir(projectRoot); await writeFile(join(projectRoot,'sentinel.txt'),'original');
  const options = {projectRoot,dataRoot,project:request.project,packs:request.packs,mode:request.mode};
  const store = await openProcessStore(options);
  return {base,request,options,store};
}
function message(child, predicate) {
  return new Promise((resolve,reject) => {
    const timeout = setTimeout(() => { cleanup(); reject(new Error('WORKER_TIMEOUT')); },10000);
    const receive = value => { if (predicate(value)) { cleanup(); resolve(value); } };
    const stopped = () => { cleanup(); reject(new Error('WORKER_EARLY_EXIT')); };
    const cleanup = () => {clearTimeout(timeout); child.off('message',receive); child.off('exit',stopped);};
    child.on('message',receive); child.on('exit',stopped);
  });
}
function worker(file) { return fork(fileURLToPath(new URL('./storage-worker.mjs',import.meta.url)),[file],{windowsHide:true,stdio:['ignore','ignore','pipe','ipc']}); }

test('receipt imports are immutable and idempotent; same ID with different content cannot overwrite', async t => {
  const {store,request,options} = await setup(t), receipt = request.receipts[0];
  const operation = {receipt,expectedRevision:0,operationId:'import-one'};
  const first = await store.importReceipt(operation); assert.equal(first.revision,1);
  assert.equal((await store.importReceipt(operation)).idempotent,true);
  const changed = structuredClone(receipt); changed.contextSnapshot.outcome.summary = 'changed'; seal(changed);
  await assert.rejects(() => store.importReceipt({receipt:changed,expectedRevision:1,operationId:'import-two'}),error => error.code === 'RECORD_ID_CONFLICT');
  await assert.rejects(() => store.importReceipt({...operation,receipt:changed}),error => error.code === 'OPERATION_ID_CONFLICT');
  const rebaselined = structuredClone(receipt); rebaselined.receiptId += ':continued'; rebaselined.contextSnapshot.baseline.id += ':other'; seal(rebaselined);
  await assert.rejects(() => store.importReceipt({receipt:rebaselined,expectedRevision:1,operationId:'changed-baseline'}),error => error.code === 'WORK_BASELINE_CONFLICT');
  const reopened = await openProcessStore(options); assert.equal((await reopened.getReceipt(receipt.receiptId)).receiptHash,receipt.receiptHash);
  assert.equal((await reopened.list()).revision,1);
  assert.equal(await readFile(join(options.projectRoot,'sentinel.txt'),'utf8'),'original');
});

test('stored assessments derive from committed receipts and replay with their immutable evaluation inputs', async t => {
  const {store,request,options} = await setup(t), receipt = request.receipts[0];
  await store.importReceipt({receipt,expectedRevision:0,operationId:'import-one'});
  const {project,packs,receipts,mode,...evaluation} = request;
  const result = await store.assess({receiptIds:[receipt.receiptId],evaluation,expectedRevision:1,operationId:'assess-one'});
  assert.equal(result.assessment.overall.processReady,true);
  const reopened = await openProcessStore(options);
  assert.deepEqual(await reopened.replayAssessment(result.id),result.assessment);
  const bytes = await readFile(join(reopened.directory,'index.json'),'utf8'), index = JSON.parse(bytes);
  const record = index.records.find(item => item.kind === 'receipt');
  await writeFile(join(reopened.directory,'objects',record.objectDigest.slice(0,2),record.objectDigest+'.json'),'{}');
  await assert.rejects(() => reopened.getReceipt(receipt.receiptId),error => error.code === 'STORE_OBJECT_CORRUPT');
});

test('separate writers using one expected revision produce exactly one commit', async t => {
  const {base,request,options,store} = await setup(t);
  const workers = [];
  t.after(() => {for (const child of workers) if (child.exitCode === null) child.kill();});
  for (let i=0;i<2;i++) {
    const file = join(base,`worker-${i}.json`), receipt = structuredClone(request.receipts[0]); receipt.receiptId += ':' + i; seal(receipt);
    await writeFile(file,JSON.stringify({options,waitForGo:true,operation:{receipt,expectedRevision:0,operationId:'parallel-'+i}}));
    const child = worker(file); workers.push(child); await message(child,item => item.phase === 'ready');
  }
  const results = workers.map(child => message(child,item => item.result || item.error));
  workers.forEach(child => child.send('go'));
  const completed = await Promise.all(results);
  assert.equal(completed.filter(item => item.result).length,1);
  assert.equal(completed.filter(item => item.error === 'STORE_REVISION_CONFLICT').length,1);
  assert.equal((await store.list()).revision,1); assert.equal((await store.list()).records.length,1);
  await Promise.all(workers.filter(child => child.exitCode === null).map(child => once(child,'exit')));
});

test('a real process kill after object publication leaves no committed receipt; retry recovers the stale lock', async t => {
  const {base,request,options,store} = await setup(t), receipt = request.receipts[0], file = join(base,'crash.json');
  const operation = {receipt,expectedRevision:0,operationId:'crash-retry'};
  await writeFile(file,JSON.stringify({options,pause:true,operation}));
  const child = worker(file); t.after(() => {if (child.exitCode === null) child.kill();});
  await message(child,item => item.phase === 'objects_written');
  const exited = once(child,'exit'); child.kill('SIGKILL'); await exited;
  assert.equal((await store.list()).records.length,0);
  await assert.rejects(() => store.getReceipt(receipt.receiptId),error => error.code === 'RECORD_NOT_FOUND');
  const reopened = await openProcessStore(options);
  assert.equal((await reopened.importReceipt(operation)).revision,1);
  assert.equal((await reopened.getReceipt(receipt.receiptId)).receiptHash,receipt.receiptHash);
});

test('store identity, fixture purpose and the external data-root boundary are enforced', async t => {
  const {options,request,store} = await setup(t);
  await store.importReceipt({receipt:request.receipts[0],expectedRevision:0,operationId:'init'});
  const different = structuredClone(options.project); different.binding.checkoutId = 'different-checkout';
  await assert.rejects(() => openProcessStore({...options,project:different}),error => error.code === 'STORE_BINDING_MISMATCH');
  await assert.rejects(() => openProcessStore({...options,dataRoot:join(options.projectRoot,'data')}),error => error.code === 'DATA_ROOT_INSIDE_PROJECT');
  const productionProject = structuredClone(options.project); productionProject.purpose = 'record';
  const productionStore = await openProcessStore({...options,dataRoot:join(options.dataRoot,'real'),project:productionProject,mode:'record'});
  await assert.rejects(() => productionStore.importReceipt({receipt:request.receipts[0],expectedRevision:0,operationId:'fixture'}),error => error.code === 'PURPOSE_MISMATCH');
});
