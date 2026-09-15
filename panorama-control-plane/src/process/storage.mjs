import { randomUUID } from 'node:crypto';
import { mkdir, open, unlink, lstat, realpath, link } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { prepareStorage, acquireLock, releaseLock, atomicWrite } from '../standalone/design-storage.mjs';
import { assertOrdinaryPath } from './inputs.mjs';
import { validateConfiguration, validateReceipt, uniqueBy } from './validation.mjs';
import { validateDocument } from './schema.mjs';
import { processValue, parseProcessJson, canonicalJson, sha256, sameValue, bodyHash } from './json.mjs';
import { assessProcess } from './evaluate.mjs';
import { ProcessError, requireProcess } from './errors.mjs';

const HASH = /^[a-f0-9]{64}$/;
async function readBounded(file) {
  await assertOrdinaryPath(file);
  const before = await lstat(file);
  requireProcess(before.isFile() && before.size <= 1048576, 'STORE_FILE_INVALID');
  const handle = await open(file, 'r');
  try {
    const opened = await handle.stat();
    requireProcess(opened.isFile() && opened.dev === before.dev && opened.ino === before.ino, 'STORE_PATH_CHANGED');
    const bytes = Buffer.alloc(1048577); let size = 0;
    while (size < bytes.length) { const result = await handle.read(bytes, size, bytes.length - size, null); if (!result.bytesRead) break; size += result.bytesRead; }
    requireProcess(size <= 1048576, 'STORE_FILE_INVALID');
    await assertOrdinaryPath(file);
    return bytes.subarray(0, size);
  } finally { await handle.close(); }
}

export async function openProcessStore({ projectRoot, dataRoot, project, packs, mode = 'record', onCommitPhase } = {}) {
  const config = validateConfiguration(project, packs, { mode });
  await assertOrdinaryPath(projectRoot);
  await assertOrdinaryPath(dataRoot, { allowMissing: true });
  const locations = await prepareStorage(projectRoot, dataRoot);
  const directory = join(locations.dataRoot, 'process', 'v1');
  await assertOrdinaryPath(directory, { allowMissing: true });
  await mkdir(directory, { recursive: true });
  const indexFile = join(directory, 'index.json'), lockFile = join(directory, 'txn.lock');
  async function guard() {
    await assertOrdinaryPath(directory);
    requireProcess(await realpath(directory) === directory, 'STORE_PATH_CHANGED');
    await assertOrdinaryPath(locations.projectRoot);
    requireProcess(await realpath(locations.projectRoot) === locations.projectRoot, 'STORE_PROJECT_CHANGED');
  }
  function initial() { return { formatVersion: 'panorama.process-store.v1', projectRoot: locations.projectRoot, binding: config.project.binding, mode, revision: 0, records: [], operations: [] }; }
  async function index() {
    await guard();
    let result;
    try { result = parseProcessJson(await readBounded(indexFile)); }
    catch (error) { if (error.code === 'ENOENT') return initial(); throw error; }
    requireProcess(result.formatVersion === 'panorama.process-store.v1' && Number.isSafeInteger(result.revision) && result.revision >= 0 && Array.isArray(result.records) && Array.isArray(result.operations), 'STORE_INDEX_INVALID');
    requireProcess(sameValue(result.binding, config.project.binding) && result.projectRoot === locations.projectRoot && result.mode === mode, 'STORE_BINDING_MISMATCH');
    uniqueBy(result.records, record => record.kind + '/' + record.id, 'STORE_INDEX_INVALID');
    uniqueBy(result.operations, operation => operation.id, 'STORE_INDEX_INVALID');
    requireProcess(result.operations.length === result.revision && result.records.length <= result.revision, 'STORE_INDEX_INVALID');
    for (const record of result.records) requireProcess(['receipt', 'assessment'].includes(record.kind) && typeof record.id === 'string' && HASH.test(record.hash) && HASH.test(record.objectDigest) &&
      (record.kind === 'assessment' ? HASH.test(record.evaluationInputDigest) : typeof record.workItemId === 'string' && HASH.test(record.baselineDigest)), 'STORE_INDEX_INVALID');
    for (const operation of result.operations) requireProcess(typeof operation.id === 'string' && HASH.test(operation.requestDigest) && operation.result && Number.isSafeInteger(operation.result.revision) && operation.result.revision <= result.revision, 'STORE_INDEX_INVALID');
    return result;
  }
  function objectPath(digest) { requireProcess(HASH.test(digest), 'OBJECT_DIGEST_INVALID'); return join(directory, 'objects', digest.slice(0, 2), digest + '.json'); }
  async function object(digest) {
    await guard();
    const bytes = await readBounded(objectPath(digest));
    requireProcess(sha256(bytes) === digest, 'STORE_OBJECT_CORRUPT');
    return parseProcessJson(bytes);
  }
  async function writeObject(value) {
    const normalized = processValue(value), bytes = Buffer.from(canonicalJson(normalized)), digest = sha256(bytes), path = objectPath(digest);
    await guard(); await assertOrdinaryPath(dirname(path), { allowMissing: true }); await mkdir(dirname(path), { recursive: true });
    try { await object(digest); return digest; } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const temporary = join(dirname(path), 'tmp-' + randomUUID());
    const handle = await open(temporary, 'wx', 0o600);
    try { await handle.writeFile(bytes); await handle.sync(); }
    catch (error) { await handle.close(); await unlink(temporary).catch(() => {}); throw error; }
    await handle.close();
    // A complete flushed object is published without replacing an existing hash.
    try { await link(temporary, path); }
    catch (error) { if (error.code !== 'EEXIST') throw error; await object(digest); }
    finally { await unlink(temporary).catch(() => {}); }
    if (process.platform !== 'win32') { const parent = await open(dirname(path), 'r'); try { await parent.sync(); } finally { await parent.close(); } }
    return digest;
  }
  async function locked(callback) {
    let lock;
    for (let attempt = 0; attempt < 30; attempt++) {
      await guard();
      try { lock = await acquireLock(lockFile); break; }
      catch (error) {
        if (error.code !== 'WORKSPACE_LOCKED') throw new ProcessError('STORE_LOCK_INVALID');
        if (attempt === 29) throw new ProcessError('STORE_BUSY');
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    }
    try { return await callback(); }
    finally { await releaseLock(lock); }
  }
  async function commit({ kind, value, evaluationInput, expectedRevision, operationId }) {
    requireProcess(Number.isSafeInteger(expectedRevision) && expectedRevision >= 0, 'EXPECTED_REVISION_REQUIRED');
    requireProcess(typeof operationId === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(operationId), 'OPERATION_ID_REQUIRED');
    const id = kind === 'receipt' ? value.receiptId : value.assessmentId, hash = kind === 'receipt' ? value.receiptHash : value.assessmentHash;
    const requestDigest = sha256({ kind, id, hash, evaluationInput: evaluationInput ?? null, configurationDigest: sha256(config.project) });
    return locked(async () => {
      const state = await index();
      const previousOperation = state.operations.find(operation => operation.id === operationId);
      if (previousOperation) {
        requireProcess(previousOperation.requestDigest === requestDigest, 'OPERATION_ID_CONFLICT');
        return { ...previousOperation.result, idempotent: true };
      }
      requireProcess(state.revision === expectedRevision, 'STORE_REVISION_CONFLICT', { currentRevision: state.revision });
      requireProcess(state.records.length < 10000 && state.operations.length < 10000, 'STORE_CAPACITY');
      const previous = state.records.find(record => record.kind === kind && record.id === id);
      requireProcess(!previous || previous.hash === hash, 'RECORD_ID_CONFLICT');
      if (kind === 'receipt') {
        const baselineDigest = sha256(value.contextSnapshot.baseline);
        requireProcess(!state.records.some(record => record.kind === 'receipt' && record.workItemId === value.workItemRef.id && record.baselineDigest !== baselineDigest), 'WORK_BASELINE_CONFLICT');
      }
      if (previous) {
        const saved = await object(previous.objectDigest);
        requireProcess(sameValue(saved, value), 'STORE_OBJECT_CORRUPT');
      }
      if (kind === 'assessment') for (const receipt of evaluationInput.receipts) requireProcess(state.records.some(record => record.kind === 'receipt' && record.id === receipt.receiptId && record.hash === receipt.receiptHash), 'ASSESSMENT_RECEIPT_NOT_COMMITTED');
      const objectDigest = previous?.objectDigest ?? await writeObject(value);
      const evaluationInputDigest = evaluationInput ? await writeObject(evaluationInput) : null;
      // Test fault injection is an in-process callback, never accepted from JSON.
      if (onCommitPhase) await onCommitPhase('objects_written');
      if (!previous) state.records.push({ kind, id, hash, objectDigest, ...(kind === 'receipt' ? { workItemId: value.workItemRef.id, baselineDigest: sha256(value.contextSnapshot.baseline) } : {}), ...(evaluationInputDigest ? { evaluationInputDigest } : {}) });
      const result = { revision: state.revision + 1, kind, id, hash, reused: Boolean(previous), idempotent: false };
      state.revision++; state.operations.push({ id: operationId, requestDigest, result });
      processValue(state);
      await guard(); await assertOrdinaryPath(indexFile, { allowMissing: true });
      await atomicWrite(indexFile, state);
      return result;
    });
  }
  async function get(kind, id) {
    const state = await index(), entry = state.records.find(record => record.kind === kind && record.id === id);
    requireProcess(entry, 'RECORD_NOT_FOUND');
    const value = validateDocument(await object(entry.objectDigest), kind === 'receipt' ? 'panorama.process-receipt.v1' : 'panorama.process-assessment.v1');
    const hashField = kind === 'receipt' ? 'receiptHash' : 'assessmentHash', idField = kind === 'receipt' ? 'receiptId' : 'assessmentId';
    requireProcess(value[idField] === id && value[hashField] === entry.hash && bodyHash(value, hashField) === entry.hash && sameValue(value.binding, config.project.binding) && value.purpose === mode, 'STORE_RECORD_MISMATCH');
    if (kind === 'receipt') requireProcess(entry.workItemId === value.workItemRef.id && entry.baselineDigest === sha256(value.contextSnapshot.baseline), 'STORE_RECORD_MISMATCH');
    return value;
  }
  await index();
  return Object.freeze({
    directory,
    async list() { const state = await index(); return { revision: state.revision, records: structuredClone(state.records) }; },
    async importReceipt({ receipt, expectedRevision, operationId }) { return commit({ kind: 'receipt', value: validateReceipt(receipt, config), expectedRevision, operationId }); },
    getReceipt: id => get('receipt', id), getAssessment: id => get('assessment', id),
    async assess({ receiptIds, evaluation, expectedRevision, operationId }) {
      requireProcess(Array.isArray(receiptIds) && receiptIds.length > 0 && receiptIds.length <= 100 && new Set(receiptIds).size === receiptIds.length, 'RECEIPT_IDS_INVALID');
      const receipts = await Promise.all(receiptIds.map(id => get('receipt', id)));
      const context = processValue(evaluation);
      requireProcess(!['project', 'packs', 'receipts', 'mode'].some(key => Object.hasOwn(context, key)), 'EVALUATION_RESERVED_FIELD');
      for (const digest of context.verifiedObjectDigests ?? []) await object(digest);
      const evaluationInput = { ...context, mode, project: config.project, packs: config.packs, receipts };
      const assessment = assessProcess(evaluationInput);
      const result = await commit({ kind: 'assessment', value: assessment, evaluationInput, expectedRevision, operationId });
      return { ...result, assessment };
    },
    async replayAssessment(id) {
      const state = await index(), entry = state.records.find(record => record.kind === 'assessment' && record.id === id);
      requireProcess(entry, 'RECORD_NOT_FOUND');
      const expected = await get('assessment', id), computed = assessProcess(await object(entry.evaluationInputDigest));
      requireProcess(sameValue(expected, computed), 'ASSESSMENT_REPLAY_MISMATCH');
      return computed;
    },
  });
}
