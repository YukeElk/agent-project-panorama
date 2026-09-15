import { createHash, createHmac, randomUUID } from 'node:crypto';
import { open, lstat } from 'node:fs/promises';
import { join, resolve, relative, isAbsolute } from 'node:path';
import { sha256, bodyHash, sameValue } from '../process/json.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { validateReceipt } from '../process/validation.mjs';
import { requireProcess } from '../process/errors.mjs';
import { matchPath } from '../process/applicability.mjs';
import { now } from './io.mjs';
import { VERSION } from './read-context.mjs';
import { timed } from './timing.mjs';
import { scopedDefinitions, selectedInputs, receiptBinding, supportsCriterionKind } from './check-contracts.mjs';

export async function captureInputs(ctx, work = null) {
  return timed('input_observation', async () => {
  const sets = [];
  for (const root of ctx.local.bootstrap.roots.filter(root => !root.observeOnly)) {
    const capture = await ctx.registry.capture([root.selector]);
    sets.push({ id: 'input:' + root.id, ...capture });
  }
  for (const definition of scopedDefinitions(ctx, work)) {
    sets.push({id:definition.id, ...await ctx.registry.capture(definition.selectors)});
  }
  return sets;
  });
}
export function changes(work, current) {
  const result = new Set();
  for (const initial of work.context.baseline.inputSets) {
    const next = current.find(set => set.id === initial.id);
    if (!next) continue;
    const before = new Map(initial.snapshot.files.map(file => [file.rootId + '/' + file.path, file]));
    const after = new Map(next.snapshot.files.map(file => [file.rootId + '/' + file.path, file]));
    for (const key of new Set([...before.keys(), ...after.keys()])) {
      const left = before.get(key), right = after.get(key);
      if (!left || !right || left.state !== right.state || left.digest !== right.digest) result.add((left ?? right).path);
    }
  }
  return [...result].sort();
}
export function processIdentity(ctx, work) {
  return sha256({ binding: ctx.project.binding, definition: work.workItemRef, baseline: work.context.baseline, configurationDigest: ctx.local.configurationDigest, ...(work.checkBindings ? {checkBindingsDigest:work.checkBindingsDigest} : {}) });
}
export async function captureSubjects(ctx, work, inputs) {
  return timed('subject_observation', async () => {
  const definitions = [{ id: 'process', kind: 'process', locator: null }, ...work.subjects];
  // Bind actual external files even when they are not explicit acceptance targets.
  for (const input of inputs) for (const file of input.snapshot.files) {
    if (ctx.config.roots.get(file.rootId).kind !== 'external') continue;
    if (!definitions.some(subject => subject.kind === 'verification_input' && subject.locator?.rootId === file.rootId && subject.locator.path === file.path)) definitions.push({ id: 'external:' + sha256([file.rootId, file.path]).slice(0, 24), kind: 'verification_input', locator: { rootId: file.rootId, path: file.path } });
  }
  const result = [];
  for (const subject of definitions) {
    let identityDigest = null;
    if (subject.kind === 'process') identityDigest = processIdentity(ctx, work);
    else if (subject.locator) {
      try { identityDigest = (await ctx.registry.hashFile(subject.locator.rootId, subject.locator.path)).digest; } catch {}
    } else if (subject.kind === 'source_behavior') {
      const selected = work.checkBindings ? selectedInputs(ctx,work,inputs,work.checkBindings.subjects.find(row => row.subjectId === subject.id)?.scopeId) : inputs;
      if (selected.length && selected.every(input => input.complete)) identityDigest = sha256({ work: work.workItemRef, inputs: selected.map(input => ({ id: input.id, selectionDigest: input.selectionDigest, digest: input.snapshot.digest })), ...(work.checkBindings ? {checkBindingsDigest:work.checkBindingsDigest} : {}) });
    }
    result.push({ ...subject, identityDigest, producedByExecutionId: null });
  }
  return result;
  });
}
async function binaryDigest(path) {
  await assertOrdinaryPath(path);
  const before = await lstat(path);
  requireProcess(before.isFile() && before.size <= 268435456, 'TOOL_UNREADABLE');
  const handle = await open(path, 'r');
  try {
    const initial = await handle.stat(), hash = createHash('sha256'), buffer = Buffer.alloc(1024 * 1024);
    requireProcess(initial.ino === before.ino && initial.dev === before.dev, 'TOOL_CHANGED');
    let total = 0;
    for (;;) { const { bytesRead } = await handle.read(buffer, 0, buffer.length, null); if (!bytesRead) break; total += bytesRead; requireProcess(total <= 268435456, 'TOOL_UNREADABLE'); hash.update(buffer.subarray(0, bytesRead)); }
    const after = await handle.stat();
    requireProcess(after.size === initial.size && after.mtimeMs === initial.mtimeMs && after.ctimeMs === initial.ctimeMs, 'TOOL_CHANGED');
    return hash.digest('hex');
  } finally { await handle.close(); }
}
export function runnerEnvironment(runner) {
  const keys = [...new Set(['SystemRoot', 'WINDIR', 'ComSpec', 'TEMP', 'TMP', 'PATH', ...(runner?.envKeys ?? [])])];
  return Object.fromEntries(keys.filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]]));
}
export async function captureEnvironment(ctx, runner, inputs, work = null) {
  return timed('environment_observation', async () => {
  const tools = [], unknowns = [];
  for (const tool of [{ id: 'node', path: process.execPath, version: process.version }, ...(runner ? [{ id: 'runner', path: runner.command, version: runner.version }] : [])]) {
    let digest = null;
    try { digest = await timed('tool_hash', () => binaryDigest(tool.path)); } catch (error) { unknowns.push(error.code ?? 'TOOL_UNREADABLE'); }
    tools.push({ id: tool.id, version: tool.version, binaryDigest: digest });
  }
  const selected = work?.checkBindings && runner ? selectedInputs(ctx,work,inputs,runner.scopeId) : inputs;
  const dependencyDigests = selected.map(set => ({ id: set.id, digest: sha256(set.snapshot.files.filter(file => ['dependency', 'tool', 'config'].includes(file.role))) }));
  if (work?.checkBindings && runner) {
    // Conservative global package/config dependencies are observed from the broad scan.
    // Adds/removes are included; the result never depends on a previously cached file list.
    const critical = file => ['dependency','tool','config'].includes(file.role) || /^(?:package\.json|package-lock\.json|npm-shrinkwrap\.json|pnpm-lock\.yaml|yarn\.lock|bun\.lockb?|pyproject\.toml|poetry\.lock|uv\.lock|Pipfile(?:\.lock)?|requirements[^/]*\.txt|Cargo\.(?:toml|lock)|go\.(?:mod|sum)|composer\.(?:json|lock)|Gemfile(?:\.lock)?|.*\.csproj|packages\.lock\.json)$/.test(file.path.split('/').at(-1));
    const broad = inputs.filter(set => set.id.startsWith('input:'));
    if (broad.some(set => !set.complete)) unknowns.push('GLOBAL_DEPENDENCIES_UNOBSERVED');
    dependencyDigests.push({id:'global-dependency-inventory:v1',digest:sha256(broad.flatMap(set => set.snapshot.files.filter(critical)))});
  }
  for (const [index, arg] of (runner?.args ?? []).entries()) {
    if (arg.startsWith('-') || arg.includes('{subject:') || !/\.(?:mjs|cjs|js|py|ps1|sh|json|yaml|yml|toml)$/.test(arg)) continue;
    const target = resolve(ctx.projectRoot, runner.cwd, arg);
    let mapped = false;
    for (const root of ctx.local.bootstrap.roots) {
      const offset = relative(root.path, target);
      if (!offset || offset === '..' || offset.startsWith('..\\') || offset.startsWith('../') || isAbsolute(offset)) continue;
      try {
        const file = await ctx.registry.hashFile(root.id, offset.replaceAll('\\', '/'));
        dependencyDigests.push({ id: 'argument-file:' + index, digest: file.digest }); mapped = true; break;
      } catch {}
    }
    if (!mapped) unknowns.push('RUNNER_ARGUMENT_FILE_UNOBSERVED:' + index);
  }
  const environmentMac = createHmac('sha256', ctx.local.environmentKey).update(JSON.stringify(runnerEnvironment(runner))).digest('hex');
  const identityDigest = unknowns.length ? null : sha256({ tools, dependencyDigests, environmentMac, platform: process.platform, arch: process.arch, runner: runner ? sha256(runner) : null, configuration: ctx.local.configurationDigest });
  return { identityDigest, coverage: 'declared_subset', tools, dependencyDigests, unknowns };
  });
}
export function currentFacts(ctx, work, inputs) {
  const paths = changes(work, inputs);
  const observedPublic = paths.some(path => ctx.local.bootstrap.publicPaths.some(pattern => matchPath(pattern, path)));
  const external = inputs.some(set => set.selectors.some(selector => ctx.config.roots.get(selector.rootId).kind === 'external'));
  const retained = work.context.criteria.some(criterion => criterion.required && criterion.subjectIds.some(id => work.subjects.some(subject => subject.id === id && subject.kind === 'retained_artifact')));
  const human = work.context.criteria.some(criterion => criterion.required && sameValue(criterion.acceptedActorKinds, ['human']));
  const publicValue = observedPublic || ['interface', 'boundary', 'architecture'].includes(work.impact) ? true : work.publicBehavior;
  const facts = [
    ['inputs.external', external, 'observed'], ['delivery.retained-artifact', retained, 'observed'], ['acceptance.human-review', human, 'observed'],
    ['change.public-behavior', publicValue, publicValue === null ? 'unknown' : observedPublic ? 'observed' : 'declared'],
  ].map(([key, value, status]) => ({ key, value, status, basisRefs: ['work:' + work.workItemRef.id, 'scan:' + sha256(inputs.map(set => set.snapshot))] }));
  return { facts, paths: [{ rootId: 'project', paths, complete: inputs.every(set => set.complete), basisRefs: ['baseline:' + work.context.baseline.id] }] };
}

export function makeReceipt(ctx, work, { executionId = 'execution:' + randomUUID(), before, after, subjects, environment, execution, runner, subjectIds = [], criterionIds = [], review = null, limitations = [], logs = [], coverage = null }) {
  const generatedAt = now(), timestamp = execution?.finishedAt ?? generatedAt;
  const inputSets = after.map(set => ({ id: set.id, selectors: set.selectors, selectionDigest: set.selectionDigest, before: before.find(input => input.id === set.id).snapshot, after: set.snapshot }));
  const contextSnapshot = { ...work.context, changedPaths: changes(work, after), unknowns: [...new Set([...work.context.unknowns, ...after.filter(set => !set.complete).map(set => 'INPUT_INCOMPLETE:' + set.id)])] };
  const actor = { id: 'panorama-local-collector', kind: 'system', authority: 'observed' };
  const record = { id: 'capture', checkerId: 'local-capture', kind: 'record', runnerRef: null, author: actor, execution: { result: 'passed', exitCode: null, startedAt: timestamp, finishedAt: timestamp }, subjectIds: subjects.map(subject => subject.id), inputSetIds: inputSets.map(set => set.id), criterionIds: [], evidenceIds: ['record'], limitations: ['Collection records do not establish business acceptance.'] };
  const evidence = [{ id: 'record', kind: 'execution_record', author: actor, checkId: record.id, subjectIds: ['process'], inputSetIds: record.inputSetIds, criterionIds: [], summary: 'Local collector recorded work identity, start baseline, current scope and execution context.', objectDigest: null, generatedAt: timestamp }];
  for (const subject of subjects.filter(subject => subject.kind === 'verification_input' && ctx.config.roots.get(subject.locator?.rootId)?.kind === 'external')) {
    const id = 'binding:' + sha256(subject.id).slice(0, 16); record.evidenceIds.push(id);
    evidence.push({ id, kind: 'input_binding', author: actor, checkId: record.id, subjectIds: [subject.id], inputSetIds: record.inputSetIds, criterionIds: [], summary: 'Explicitly registered external input identity captured locally.', objectDigest: null, generatedAt: timestamp });
  }
  const checks = [record];
  if (execution) {
    const author = review ? { id: 'local-coding-agent', kind: 'coding_agent', authority: 'declared' } : actor;
    const inputSetIds = work.checkBindings && runner && !review ? selectedInputs(ctx,work,after,runner.scopeId).map(set => set.id) : record.inputSetIds;
    const effectiveExecution = work.checkBindings && runner && execution.result === 'passed' && coverage?.report?.status !== 'verified' ? {...execution,result:'errored'} : execution;
    const check = { id: review ? 'review' : 'command', checkerId: review ? 'local-agent-review' : runner.id, kind: review ? 'review' : 'machine', runnerRef: review ? null : { id: runner.id, definitionDigest: sha256(runner) }, author, execution:effectiveExecution, subjectIds, inputSetIds, criterionIds, evidenceIds: [], limitations };
    for (const kind of execution.result === 'not_run' ? [] : review ? [review.kind] : runner.evidenceKinds) {
      const supported = criterionIds.filter(id => {const criterion=contextSnapshot.criteria.find(criterion => criterion.id === id);return criterion.allowedEvidenceKinds.includes(kind) && (review || supportsCriterionKind(ctx,work,runner,criterion,kind));});
      if(work.checkBindings && !review && !supported.length)continue;
      const id = 'evidence:' + kind; check.evidenceIds.push(id);
      evidence.push({ id, kind, author, checkId: check.id, subjectIds, inputSetIds, criterionIds: supported, summary: review ? review.summary : 'Registered command completed with recorded exit status; coverage follows its declared check contract.', objectDigest: null, generatedAt: timestamp });
    }
    checks.push(check);
  }
  const receipt = {
    formatVersion: 'panorama.process-receipt.v1', purpose: 'record', receiptId: 'receipt:' + executionId.replace(/^execution:/, ''), receiptHash: '', executionId,
    binding: ctx.project.binding, workItemRef: work.workItemRef, contextSnapshot, candidateBinding: work.candidateBinding, rulePacks: ctx.project.rulePacks, sourceSnapshotId: null,
    producer: { id: 'panorama-local-collector', version: work.checkBindings ? 'panorama-development-3.0.0' : VERSION, kind: 'local_capture', authority: 'observed' }, inputSets, subjects, checks, evidence, environment,
    enforcement: [{ mechanism: 'post_write_scan', status: after.every(set => set.complete) ? 'observed' : 'unknown', evidenceIds: ['record'] }, { mechanism: 'guidance', status: 'declared', evidenceIds: [] }],
    upstreamCompletionClaim: null, generatedAt, limitations: ['Local checkout writers are trusted; this is not authenticated human review or host write interception.', 'Environment coverage is a declared subset; external services and undeclared dependencies are not frozen.', ...limitations],
    redaction: { content: 'summary_and_hash_references_only', producerDeclared: true, omittedCategories: ['raw-output', 'environment-values', 'absolute-local-paths'] },
    extensions: { 'panorama.development.capture': { configurationDigest: ctx.local.configurationDigest, logReferences: logs, facts: currentFacts(ctx, work, after).facts }, ...(work.checkBindings ? {'panorama.development.check-binding':receiptBinding(work,coverage,execution ?? null)} : {}) },
  };
  receipt.receiptHash = bodyHash(receipt, 'receiptHash');
  return validateReceipt(receipt, ctx.config, { workItemRef: work.workItemRef, candidateBinding: work.candidateBinding });
}
