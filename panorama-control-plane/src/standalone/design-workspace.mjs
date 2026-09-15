import { randomUUID } from 'node:crypto';
import { readFile, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { applyOperations, diffModels, clone, assert, object, text, stringList, validateGraph, validateOperations, equal, stateOwnershipQuestions, domainError } from './design-model.mjs';
import { prepareStorage, acquireLock, releaseLock, atomicWrite } from './design-storage.mjs';
import { exportHandoff } from './handoff.mjs';
import { reconcileCandidate } from './reconcile.mjs';
export { applyOperations, diffModels } from './design-model.mjs';

const FORMAT = 'panorama.workspace.v1';
const timestamp = () => new Date().toISOString();
const identifier = prefix => `${prefix}:${randomUUID()}`;
const requiredText = (value, field, max = 10000) => text(value, field, { max });
const optionalText = (value, field, max = 20000) => text(value ?? '', field, { max, empty: true });
const constraints = value => Array.isArray(value) ? clone(stringList(value, 'constraints')) : optionalText(value, 'constraints');
function integer(value, field) { assert(Number.isSafeInteger(value) && value >= 0, `Invalid ${field}`); }
function boundedArray(value, name, max = 1000) { assert(Array.isArray(value) && value.length <= max, `Invalid ${name}`); return value; }
function assertUnique(items, name) { const ids = items.map(item => requiredText(item.id, `${name}.id`, 512)); assert(new Set(ids).size === ids.length, `Duplicate ${name} id`); }
function modelShape(model, projectRoot) {
  validateGraph(model);
  assert(model.schemaVersion === 'panorama.source.v1', 'Unknown source model version');
  object(model.project, 'project'); assert(model.project.root === projectRoot, 'Source project identity mismatch');
  requiredText(model.project.id, 'project.id'); requiredText(model.project.name, 'project.name');
  object(model.snapshot, 'snapshot'); for (const key of ['id', 'contentDigest', 'parserVersion', 'generatedAt']) requiredText(model.snapshot[key], `snapshot.${key}`);
  for (const file of boundedArray(model.files, 'files', 50000)) { object(file, 'file'); requiredText(file.path, 'file.path'); requiredText(file.digest, 'file.digest'); integer(file.size, 'file.size'); requiredText(file.language, 'file.language'); }
  assert(new Set(model.files.map(file => file.path)).size === model.files.length, 'Duplicate source file path');
  for (const item of boundedArray(model.capabilities, 'capabilities', 1000)) { object(item, 'capability'); requiredText(item.language, 'capability.language'); requiredText(item.status, 'capability.status'); integer(item.files, 'capability.files'); optionalText(item.detail, 'capability.detail'); }
  for (const item of boundedArray(model.gaps, 'gaps', 100000)) { object(item, 'gap'); requiredText(item.code, 'gap.code'); optionalText(item.path, 'gap.path'); requiredText(item.message, 'gap.message'); }
  for (const item of [...model.nodes, ...model.edges]) {
    for (const evidence of boundedArray(item.evidence, 'evidence', 10000)) { object(evidence, 'evidence'); requiredText(evidence.path, 'evidence.path'); if (evidence.line != null) integer(evidence.line, 'evidence.line'); requiredText(evidence.kind, 'evidence.kind'); optionalText(evidence.detail, 'evidence.detail'); }
    if ('from' in item) continue;
    assert(item.parentId === null || typeof item.parentId === 'string', 'Invalid node.parentId');
    assert(item.sourcePath === null || typeof item.sourcePath === 'string', 'Invalid node.sourcePath');
    if (item.line != null) integer(item.line, 'node.line'); optionalText(item.description, 'node.description'); object(item.attributes, 'node.attributes');
  }
}
function validateWorkspace(workspace, projectRoot) {
  object(workspace, 'workspace'); assert(workspace.formatVersion === FORMAT, 'Unknown workspace format version');
  assert(workspace.projectRoot === projectRoot, 'Workspace belongs to another project', 400, 'PROJECT_MISMATCH'); integer(workspace.revision, 'revision');
  modelShape(workspace.current, projectRoot);
  for (const key of ['sessions', 'candidates', 'reviews', 'views', 'results']) { boundedArray(workspace[key], key, key === 'reviews' ? 5000 : 1000); assertUnique(workspace[key], key); }
  const sessions = new Map(workspace.sessions.map(item => [item.id, item])); const candidates = new Map(workspace.candidates.map(item => [item.id, item]));
  for (const session of sessions.values()) {
    requiredText(session.title, 'session.title'); requiredText(session.kind, 'session.kind'); optionalText(session.question, 'question'); constraints(session.constraints); stringList(session.unknowns, 'unknowns'); requiredText(session.createdAt, 'createdAt'); optionalText(session.decisionReason, 'decisionReason');
    assert(session.selectedCandidateId === null || candidates.get(session.selectedCandidateId)?.sessionId === session.id, 'Invalid selected candidate');
    for (const message of boundedArray(session.messages, 'messages', 80)) {
      assert(['user', 'assistant'].includes(message.role), 'Invalid conversation role'); optionalText(message.content, 'message.content', 50000); requiredText(message.baseSnapshotId, 'message.baseSnapshotId'); requiredText(message.createdAt, 'message.createdAt');
      if (message.candidateId != null) assert(candidates.get(message.candidateId)?.sessionId === session.id, 'Invalid message candidate');
      if (message.candidateVersion != null) integer(message.candidateVersion, 'message.candidateVersion');
      if (message.meta != null) {
        object(message.meta, 'message.meta'); assert(['answer', 'clarification', 'proposal', 'external'].includes(message.meta.kind), 'Invalid message.meta.kind');
        if (message.meta.prompt != null) optionalText(message.meta.prompt, 'message.meta.prompt', 200000);
        if (message.meta.operations != null) validateOperations(message.meta.operations);
      }
    }
  }
  for (const candidate of candidates.values()) {
    assert(sessions.has(candidate.sessionId), 'Candidate session missing'); requiredText(candidate.title, 'candidate.title'); optionalText(candidate.description, 'candidate.description'); requiredText(candidate.baseSnapshotId, 'candidate.baseSnapshotId'); integer(candidate.version, 'candidate.version');
    modelShape(candidate.baseModel, projectRoot); modelShape(candidate.target, projectRoot); assert(candidate.baseSnapshotId === candidate.baseModel.snapshot.id, 'Candidate baseline mismatch');
    assert(candidate.baseModel.project.id === workspace.current.project.id && candidate.target.project.id === workspace.current.project.id, 'Candidate project mismatch');
    boundedArray(candidate.history, 'history', 100); assert(candidate.history.length > 0, 'History cannot be empty'); integer(candidate.historyIndex, 'historyIndex'); assert(candidate.historyIndex < candidate.history.length, 'History cursor out of range');
    for (const entry of candidate.history) { requiredText(entry.title, 'history.title'); optionalText(entry.description, 'history.description'); requiredText(entry.createdAt, 'history.createdAt'); optionalText(entry.note, 'history.note'); modelShape(entry.target, projectRoot); }
    const active = candidate.history[candidate.historyIndex]; assert(equal(active.target, candidate.target) && active.title === candidate.title && active.description === candidate.description, 'Candidate history does not match active state');
    assert(candidate.savedAt === null || typeof candidate.savedAt === 'string', 'Invalid savedAt');
    for (const binding of boundedArray(candidate.bindings, 'bindings', 50000)) { requiredText(binding.targetNodeId, 'binding.targetNodeId'); requiredText(binding.sourceNodeId, 'binding.sourceNodeId'); requiredText(binding.sourceSnapshotId, 'binding.sourceSnapshotId'); assert(candidate.target.nodes.some(node => node.id === binding.targetNodeId), 'Binding target does not exist'); }
    assert(new Set(candidate.bindings.map(item => item.targetNodeId)).size === candidate.bindings.length, 'Duplicate target binding');
  }
  for (const review of workspace.reviews) {
    const candidate = candidates.get(review.candidateId); assert(candidate && candidate.sessionId === review.sessionId, 'Review candidate missing');
    integer(review.candidateVersion, 'review.candidateVersion'); requiredText(review.baseSnapshotId, 'review.baseSnapshotId'); requiredText(review.source, 'review.source'); optionalText(review.markdown, 'review.markdown', 200000); validateOperations(review.operations);
    assert(['pending', 'accepted', 'rejected', 'partially_accepted'].includes(review.decision), 'Invalid review decision'); optionalText(review.reason, 'review.reason'); requiredText(review.createdAt, 'review.createdAt');
    for (const key of ['selectedOperationIndices', 'appliedOperationIndices', 'unappliedOperationIndices']) if (review[key] != null) { boundedArray(review[key], key, 500); assert(review[key].every(index => Number.isInteger(index) && index >= 0 && index < review.operations.length), 'Invalid review operation index'); }
  }
  for (const view of workspace.views) { requiredText(view.title, 'view.title'); assert(['modules', 'dependencies', 'deployment'].includes(view.kind), 'Invalid view kind'); assert(view.scopeId === null || typeof view.scopeId === 'string', 'Invalid view.scopeId'); optionalText(view.search, 'view.search', 4000); stringList(view.collapsedIds, 'view.collapsedIds', 1000); }
  for (const result of workspace.results) { assert(candidates.has(result.candidateId), 'Result candidate missing'); requiredText(result.source, 'result.source'); requiredText(result.summary, 'result.summary', 100000); stringList(result.references, 'result.references', 200); requiredText(result.createdAt, 'result.createdAt'); }
  return workspace;
}
function checkBinding(candidate, version, baseSnapshotId, current, checkCurrent = true) {
  assert(version === candidate.version, 'Candidate version changed; preview again', 409, 'STALE_CANDIDATE');
  assert(baseSnapshotId === candidate.baseSnapshotId, 'Candidate baseline changed', 409, 'STALE_CANDIDATE');
  if (checkCurrent) assert(candidate.baseSnapshotId === current.snapshot.id, 'Source changed; create a new candidate from current source', 409, 'STALE_SOURCE');
}
function historyEntry(candidate, note = '') { return { title: candidate.title, description: candidate.description, target: clone(candidate.target), note, createdAt: timestamp() }; }
function pushHistory(candidate, note = '') {
  candidate.history = candidate.history.slice(0, candidate.historyIndex + 1);
  candidate.history.push(historyEntry(candidate, note)); if (candidate.history.length > 100) candidate.history.shift();
  candidate.historyIndex = candidate.history.length - 1; candidate.version++; candidate.savedAt = null;
  candidate.unknowns = stateOwnershipQuestions(candidate.target);
  candidate.bindings = candidate.bindings.filter(binding => candidate.target.nodes.some(node => node.id === binding.targetNodeId));
}

export class WorkspaceStore {
  constructor({ projectRoot, dataRoot, analyzeProject }) {
    assert(typeof projectRoot === 'string' && typeof dataRoot === 'string' && typeof analyzeProject === 'function', 'Workspace requires projectRoot, dataRoot and analyzeProject');
    this.projectRoot = projectRoot; this.dataRoot = dataRoot; this.analyzeProject = analyzeProject; this.queue = Promise.resolve(); this.closed = false; this.state = null;
  }
  async init() {
    assert(!this.state && !this.lock && !this.closed, 'Workspace already opened or closed');
    Object.assign(this, await prepareStorage(this.projectRoot, this.dataRoot));
    this.file = join(this.dataRoot, 'workspace.json'); this.lock = await acquireLock(join(this.dataRoot, '.panorama.lock'));
    try {
      let persisted = null;
      try {
        const info = await lstat(this.file); assert(info.isFile() && !info.isSymbolicLink() && info.size <= 256 * 1024 * 1024, 'Invalid workspace file');
        persisted = JSON.parse(await readFile(this.file, 'utf8')); validateWorkspace(persisted, this.projectRoot);
      } catch (error) {
        if (error.code !== 'ENOENT') throw domainError(`Cannot open persisted workspace without overwriting it: ${error.message}`, 400, 'WORKSPACE_CORRUPT');
      }
      const current = await this.analyzeProject({ projectRoot: this.projectRoot }); modelShape(current, this.projectRoot);
      if (persisted) {
        assert(persisted.current.project.id === current.project.id, 'Project identity changed', 400, 'PROJECT_MISMATCH');
        if (persisted.current.snapshot.id !== current.snapshot.id) { persisted.current = clone(current); persisted.revision++; await atomicWrite(this.file, persisted); }
        this.state = persisted;
      } else {
        const initial = { formatVersion: FORMAT, projectRoot: this.projectRoot, revision: 0, current: clone(current), sessions: [], candidates: [], reviews: [], views: [], results: [] };
        validateWorkspace(initial, this.projectRoot); await atomicWrite(this.file, initial); this.state = initial;
      }
      return this.snapshot();
    } catch (error) { await releaseLock(this.lock); this.lock = null; throw error; }
  }
  snapshot() {
    assert(this.state && !this.closed, 'Workspace is not open'); const result = clone(this.state);
    for (const candidate of result.candidates) { candidate.stale = candidate.baseSnapshotId !== result.current.snapshot.id; candidate.unknowns = stateOwnershipQuestions(candidate.target); }
    for (const review of result.reviews) { const candidate = result.candidates.find(item => item.id === review.candidateId); review.stale = candidate.stale || review.candidateVersion !== candidate.version || review.baseSnapshotId !== candidate.baseSnapshotId; }
    return result;
  }
  command(type, input = {}, expectedRevision) {
    if (this.closed || this.closing || !this.state) return Promise.reject(domainError('Workspace is not open'));
    let request; try { request = clone(input); } catch { return Promise.reject(domainError('Command input must be JSON-compatible data')); }
    const run = this.queue.then(() => this.execute(type, request, expectedRevision)); this.queue = run.catch(() => {}); return run;
  }
  async execute(type, input, expectedRevision) {
    assert(Number.isSafeInteger(expectedRevision) && expectedRevision === this.state.revision, 'Workspace changed; refresh before retrying', 409, 'REVISION_CONFLICT');
    object(input, 'command input'); assert(typeof type === 'string', 'Command type must be text');
    const draft = clone(this.state); let output; let mutates = true;
    const candidateFor = id => { const item = draft.candidates.find(entry => entry.id === id); assert(item, `Unknown candidate ${id}`); return item; };
    const sessionFor = id => { const item = draft.sessions.find(entry => entry.id === id); assert(item, `Unknown session ${id}`); return item; };
    switch (type) {
      case 'refresh': {
        const current = await this.analyzeProject({ projectRoot: this.projectRoot }); modelShape(current, this.projectRoot);
        assert(current.project.id === draft.current.project.id, 'Project identity changed'); draft.current = clone(current); break;
      }
      case 'create-session': {
        const session = { id: identifier('session'), title: requiredText(input.title, 'title', 500), kind: requiredText(input.kind, 'kind', 100), question: optionalText(input.question, 'question'), constraints: constraints(input.constraints), unknowns: clone(stringList(input.unknowns ?? [], 'unknowns')), createdAt: timestamp(), selectedCandidateId: null, decisionReason: '', messages: [] };
        draft.sessions.push(session); output = { id: session.id }; break;
      }
      case 'update-session': {
        const session = sessionFor(input.id);
        if ('title' in input) session.title = requiredText(input.title, 'title', 500);
        if ('question' in input) session.question = optionalText(input.question, 'question');
        if ('constraints' in input) session.constraints = constraints(input.constraints);
        if ('unknowns' in input) session.unknowns = clone(stringList(input.unknowns, 'unknowns')); break;
      }
      case 'create-candidate': {
        sessionFor(input.sessionId);
        const candidate = { id: identifier('candidate'), sessionId: input.sessionId, title: requiredText(input.title, 'title', 500), description: optionalText(input.description, 'description'), baseSnapshotId: draft.current.snapshot.id, baseModel: clone(draft.current), target: clone(draft.current), version: 0, history: [], historyIndex: 0, savedAt: null, bindings: [] };
        candidate.history.push(historyEntry(candidate, 'Initial source baseline')); draft.candidates.push(candidate); output = { id: candidate.id }; break;
      }
      case 'update-candidate': {
        const candidate = candidateFor(input.candidateId);
        assert(candidate.baseSnapshotId === draft.current.snapshot.id, 'Source changed; create a new candidate', 409, 'STALE_SOURCE');
        assert('title' in input || 'description' in input, 'No candidate changes');
        if ('title' in input) candidate.title = requiredText(input.title, 'title', 500);
        if ('description' in input) candidate.description = optionalText(input.description, 'description');
        pushHistory(candidate, 'Edited candidate description'); break;
      }
      case 'apply-operations': {
        const candidate = candidateFor(input.candidateId); checkBinding(candidate, input.candidateVersion, input.baseSnapshotId, draft.current);
        candidate.target = applyOperations(candidate.target, input.operations); pushHistory(candidate, optionalText(input.note, 'note')); break;
      }
      case 'undo': case 'redo': {
        const candidate = candidateFor(input.candidateId);
        assert(candidate.baseSnapshotId === draft.current.snapshot.id, 'Source changed; historical candidate is read-only', 409, 'STALE_SOURCE');
        const index = candidate.historyIndex + (type === 'undo' ? -1 : 1); assert(index >= 0 && index < candidate.history.length, `Nothing to ${type}`);
        const entry = candidate.history[index]; candidate.target = clone(entry.target); candidate.title = entry.title; candidate.description = entry.description;
        candidate.historyIndex = index; candidate.version++; candidate.savedAt = null; candidate.unknowns = stateOwnershipQuestions(candidate.target); break;
      }
      case 'save-candidate': { const candidate = candidateFor(input.candidateId); candidate.savedAt = timestamp(); candidate.savedVersion = candidate.version; break; }
      case 'import-review': {
        const candidate = candidateFor(input.candidateId); checkBinding(candidate, input.candidateVersion, input.baseSnapshotId, draft.current, false);
        const operations = input.operations ?? []; applyOperations(candidate.target, operations);
        const review = { id: identifier('review'), sessionId: candidate.sessionId, candidateId: candidate.id, candidateVersion: candidate.version, baseSnapshotId: candidate.baseSnapshotId, source: requiredText(input.source, 'source', 1000), markdown: optionalText(input.markdown, 'markdown', 200000), operations: clone(operations), decision: 'pending', reason: '', createdAt: timestamp() };
        draft.reviews.push(review); output = { id: review.id }; break;
      }
      case 'decide-review': {
        const review = draft.reviews.find(item => item.id === input.reviewId); assert(review, 'Unknown review'); assert(review.decision === 'pending', 'Review already decided; import a new review');
        assert(['accepted', 'rejected'].includes(input.decision), 'Invalid review decision'); requiredText(input.reason, 'reason'); assert(typeof input.applyOperations === 'boolean', 'applyOperations must be explicit');
        if (input.decision === 'rejected') { assert(input.applyOperations === false, 'Rejected review cannot apply operations'); review.decision = 'rejected'; review.reason = input.reason; review.appliedOperationIndices = []; review.unappliedOperationIndices = review.operations.map((_, index) => index); break; }
        const candidate = candidateFor(review.candidateId); checkBinding(candidate, review.candidateVersion, review.baseSnapshotId, draft.current);
        const selected = input.selectedOperationIndices ?? review.operations.map((_, index) => index);
        boundedArray(selected, 'selectedOperationIndices', 500); assert(new Set(selected).size === selected.length && selected.every(index => Number.isInteger(index) && index >= 0 && index < review.operations.length), 'Invalid selected review indices');
        assert(!input.applyOperations || selected.length > 0, 'Select at least one operation to apply');
        if (input.applyOperations) { candidate.target = applyOperations(candidate.target, selected.map(index => review.operations[index])); pushHistory(candidate, `Review ${review.id}: ${input.reason}`); }
        review.selectedOperationIndices = clone(selected); review.appliedOperationIndices = input.applyOperations ? clone(selected) : [];
        review.unappliedOperationIndices = review.operations.map((_, index) => index).filter(index => !review.appliedOperationIndices.includes(index));
        review.decision = selected.length < review.operations.length ? 'partially_accepted' : 'accepted'; review.reason = input.reason; review.decidedAt = timestamp(); break;
      }
      case 'select-candidate': {
        const session = sessionFor(input.sessionId); const candidate = candidateFor(input.candidateId); assert(candidate.sessionId === session.id, 'Candidate belongs to another session');
        session.selectedCandidateId = candidate.id; session.decisionReason = requiredText(input.reason, 'reason'); break;
      }
      case 'record-conversation': {
        const session = sessionFor(input.sessionId); const message = requiredText(input.message, 'message', 30000); const response = object(input.response, 'response');
        assert(['answer', 'clarification', 'proposal', 'external'].includes(response.kind), 'Invalid response kind'); const content = optionalText(response.text, 'response.text', 50000);
        assert(input.baseSnapshotId === draft.current.snapshot.id, 'Conversation source snapshot changed', 409, 'STALE_SOURCE');
        if (input.candidateId != null) { const candidate = candidateFor(input.candidateId); assert(candidate.sessionId === session.id, 'Conversation candidate belongs to another session'); checkBinding(candidate, input.candidateVersion, input.baseSnapshotId, draft.current); }
        const meta = { kind: response.kind }; if (response.prompt != null) meta.prompt = optionalText(response.prompt, 'response.prompt', 200000);
        if (response.operations != null) { validateOperations(response.operations); meta.operations = clone(response.operations); }
        const binding = { baseSnapshotId: input.baseSnapshotId, ...(input.candidateId != null ? { candidateId: input.candidateId, candidateVersion: input.candidateVersion } : {}), createdAt: timestamp() };
        session.messages.push({ role: 'user', content: message, ...binding }, { role: 'assistant', content, source: response.kind === 'external' ? 'external-handoff' : 'configured-model', meta, ...binding });
        session.messages = session.messages.slice(-80); break;
      }
      case 'save-view': {
        const id = input.id ?? identifier('view'); requiredText(id, 'view.id', 512);
        const view = { id, title: requiredText(input.title, 'title', 500), kind: input.kind, scopeId: input.scopeId ?? null, search: optionalText(input.search, 'search', 4000), collapsedIds: clone(stringList(input.collapsedIds ?? [], 'collapsedIds', 1000)) };
        assert(['modules', 'dependencies', 'deployment'].includes(view.kind), 'Invalid view kind'); assert(view.scopeId === null || typeof view.scopeId === 'string', 'Invalid scopeId');
        const index = draft.views.findIndex(item => item.id === id); if (index === -1) draft.views.push(view); else draft.views[index] = view; output = { id }; break;
      }
      case 'import-result': {
        candidateFor(input.candidateId); const result = { id: identifier('result'), candidateId: input.candidateId, source: requiredText(input.source, 'source', 1000), summary: requiredText(input.summary, 'summary', 100000), references: clone(stringList(input.references ?? [], 'references', 200)), createdAt: timestamp() };
        draft.results.push(result); output = { id: result.id }; break;
      }
      case 'bind-target': {
        const candidate = candidateFor(input.candidateId); assert(input.sourceSnapshotId === draft.current.snapshot.id, 'Binding source snapshot changed', 409, 'STALE_SOURCE');
        const target = candidate.target.nodes.find(item => item.id === input.targetNodeId); const source = draft.current.nodes.find(item => item.id === input.sourceNodeId);
        assert(target && source && target.kind === source.kind, 'Binding requires existing nodes of the same kind');
        candidate.bindings = candidate.bindings.filter(item => item.targetNodeId !== target.id); candidate.bindings.push({ targetNodeId: target.id, sourceNodeId: source.id, sourceSnapshotId: draft.current.snapshot.id }); break;
      }
      case 'export-handoff': {
        const candidate = candidateFor(input.candidateId); assert(['design', 'review', 'implementation'].includes(input.purpose), 'Invalid handoff purpose');
        output = exportHandoff(draft, candidate, input.purpose); mutates = false; break;
      }
      case 'reconcile': { output = reconcileCandidate(candidateFor(input.candidateId), draft.current); mutates = false; break; }
      default: throw domainError(`Unknown command: ${type}`);
    }
    if (mutates) { draft.revision++; validateWorkspace(draft, this.projectRoot); await atomicWrite(this.file, draft); this.state = draft; }
    return { workspace: this.snapshot(), ...(output === undefined ? {} : { output }) };
  }
  async close() {
    if (this.closed) return; this.closing = true; await this.queue; await releaseLock(this.lock); this.lock = null; this.closed = true;
  }
}
