import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createInputRegistry } from '../../src/process/inputs.mjs';
import { bodyHash, sha256, workDefinition } from '../../src/process/json.mjs';

export const root = new URL('../../contracts/process/', import.meta.url);
export const read = async path => JSON.parse(await readFile(new URL(path, root), 'utf8'));
export const catalog = await read('examples/catalog.json');
export const pack = await read('rules/development-process.v0.1.json');
export const now = '2026-09-14T00:05:00Z';
export function seal(receipt) {
  receipt.workItemRef.definitionDigest = sha256(workDefinition(receipt.contextSnapshot));
  receipt.receiptHash = bodyHash(receipt, 'receiptHash');
  return receipt;
}
export async function fixture(id = 'cat-valid') {
  const entry = catalog.cases.find(item => item.id === id);
  const project = await read(entry.project), receipt = await read(entry.receipt), factsFile = await read(entry.facts);
  const usedRoots = new Set([...receipt.inputSets.flatMap(set => set.selectors.map(selector => selector.rootId)), ...receipt.subjects.filter(subject => subject.locator).map(subject => subject.locator.rootId)]);
  const mappings = Object.fromEntries(project.inputRoots.filter(item => usedRoots.has(item.id)).map(item => [item.id, { path: fileURLToPath(new URL(entry.inputRoot + '/' + item.id, root)), allow: ['**'] }]));
  const registry = await createInputRegistry(project, mappings);
  const observed = await registry.observe(receipt, now);
  // Non-file identities are authored test inputs, never production observations.
  for (const row of observed.currentSubjectObservations) {
    const subject = receipt.subjects.find(item => item.id === row.subjectId);
    if (!subject.locator && subject.identityDigest) { row.identityDigest = subject.identityDigest; row.state = 'observed'; }
  }
  return { mode: 'contract_example', project, packs: [structuredClone(pack)], receipts: [receipt], workItemRef: receipt.workItemRef, candidateBinding: receipt.candidateBinding,
    assessedAt: now, currentInputObservations: observed.currentInputObservations, currentSubjectObservations: observed.currentSubjectObservations,
    facts: Object.entries(factsFile.facts).map(([key, value]) => ({ key, value, status: value === null ? 'unknown' : 'declared', basisRefs: ['fixture:' + id] })) };
}

export function production(request) {
  const result = structuredClone(request); result.mode = 'record'; result.project.purpose = 'record';
  for (const receipt of result.receipts) {
    receipt.purpose = 'record'; receipt.producer = {id:'test-local-capture',version:'1',kind:'local_capture',authority:'observed'};
    receipt.environment.identityDigest = sha256({testEnvironment:'synthetic-runtime-unit'});
    seal(receipt);
  }
  result.environmentObservations = result.receipts.map(receipt => ({executionId:receipt.executionId,identityDigest:receipt.environment.identityDigest,state:'observed',observedAt:now}));
  result.workItemRef = result.receipts[0].workItemRef;
  return result;
}
export function attest(request) {
  request.verifiedEvidence = request.receipts.flatMap(receipt => receipt.evidence.map(evidence => ({receiptId:receipt.receiptId,receiptHash:receipt.receiptHash,evidenceId:evidence.id,actorId:evidence.author.id,actorKind:evidence.author.kind})));
  return request;
}
