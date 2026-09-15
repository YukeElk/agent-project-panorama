// A separate collector process retains the actual exit result after a CLI dies.
// IPC loss interrupts the owned business process tree; resume never re-executes it.
import { spawn } from 'node:child_process';
import { join, resolve, relative, isAbsolute } from 'node:path';
import { randomUUID } from 'node:crypto';
import { loadDevelopment } from './config.mjs';
import { readWork, runDirectory } from './journal.mjs';
import { captureInputs, captureSubjects, captureEnvironment, runnerEnvironment } from './capture.mjs';
import { now, readJson, writeJson, exclusiveText } from './io.mjs';
import { sha256, sameValue } from '../process/json.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { requireProcess } from '../process/errors.mjs';
import { startTiming, timingResult, timed } from './timing.mjs';
import { validateCheckCoverage, checkerFor, verifyCheckerReport } from './check-contracts.mjs';
import { lstat } from 'node:fs/promises';

let business = null, interruption = process.connected ? null : 'PARENT_DISCONNECTED';
async function stop(reason) {
  interruption ??= reason;
  if (!business?.pid) return;
  if (process.platform === 'win32') {
    await new Promise(resolve => {
      const killer = spawn(join(process.env.SystemRoot ?? 'C:\\Windows', 'System32/taskkill.exe'), ['/PID', String(business.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      killer.on('error', resolve); killer.on('close', resolve);
    });
  } else {
    try { process.kill(-business.pid, 'SIGKILL'); } catch {}
  }
}
process.on('disconnect', () => { void stop('PARENT_DISCONNECTED'); });
process.on('message', message => { if (message?.interrupt) void stop('SIGNAL_INTERRUPTED'); });
process.on('SIGINT', () => { void stop('SIGNAL_INTERRUPTED'); });
process.on('SIGTERM', () => { void stop('SIGNAL_INTERRUPTED'); });
const request = await readJson(process.argv[2]);
const directory = request.directory;
const clock = request.timings ? startTiming() : null;
try {
  const ctx = await timed('load', () => loadDevelopment({ projectRoot: request.projectRoot, dataRoot: request.dataRoot },{workId:request.workItemId}));
  requireProcess(directory === runDirectory(ctx, request.executionId), 'RUN_DIRECTORY_MISMATCH');
  const work = await readWork(ctx, request.workItemId);
  requireProcess(sameValue(work.workItemRef, request.workItemRef), 'WORK_BINDING_MISMATCH');
  const runner = ctx.local.bootstrap.runners.find(runner => runner.id === request.runnerId);
  requireProcess(runner, 'RUNNER_NOT_REGISTERED');
  let coverage = validateCheckCoverage(ctx,work,runner,request);
  await writeJson(join(directory, 'status.json'), { status: 'preparing', workerPid: process.pid, businessPid: null, executionId: request.executionId });
  const before = await captureInputs(ctx, work), subjectsBefore = await captureSubjects(ctx, work, before), environmentBefore = await captureEnvironment(ctx, runner, before, work);
  let limitations = [...(coverage?.limitations ?? [])], execution;
  const logs = [], buffers = { stdout: [], stderr: [] }, retained = { stdout: 0, stderr: 0 }, total = { stdout: 0, stderr: 0 };
  const missingInput = request.subjectIds.some(id => {
    const subject = subjectsBefore.find(subject => subject.id === id);
    if (subject.kind === 'retained_artifact') return !before.some(set => set.snapshot.files.some(file => file.rootId === subject.locator?.rootId && file.path === subject.locator.path && file.state === 'present'));
    if (subject.kind === 'fresh_build') return !subject.locator || subject.identityDigest !== null;
    return false;
  });
  await writeJson(join(directory, 'prepared.json'), { executionId: request.executionId, before, subjectsBefore, environmentBefore, preparedAt: now() });
  if (interruption || before.some(set => !set.complete) || !environmentBefore.identityDigest || missingInput) {
    limitations.push(interruption ?? (missingInput ? 'SUBJECT_PRECONDITION_FAILED' : 'INPUT_OR_ENVIRONMENT_INCOMPLETE'));
    execution = { result: 'not_run', exitCode: null, startedAt: null, finishedAt: null };
  } else {
    const cwd = resolve(ctx.projectRoot, runner.cwd), offset = relative(ctx.projectRoot, cwd);
    requireProcess(!offset.startsWith('..') && !isAbsolute(offset), 'RUNNER_CWD_INVALID');
    await assertOrdinaryPath(cwd);
    const usedSubjects = new Set();
    if (coverage) for (const subject of subjectsBefore.filter(subject => request.subjectIds.includes(subject.id) && subject.locator)) {
      const root = ctx.local.bootstrap.roots.find(root => root.id === subject.locator.rootId);
      requireProcess((await lstat(join(root.path,subject.locator.path))).size <= checkerFor(ctx,runner).limits.maxObjectBytes, 'CHECKER_OBJECT_LIMIT');
    }
    const args = runner.args.map(arg => (coverage ? arg.replaceAll('{executionId}',request.executionId).replace(/\{subjectDigest:([A-Za-z0-9._:-]+)\}/g, (_, id) => {
      const subject = subjectsBefore.find(subject => subject.id === id);
      requireProcess(request.subjectIds.includes(id) && subject?.identityDigest, 'RUNNER_SUBJECT_ARGUMENT_INVALID'); return subject.identityDigest;
    }) : arg).replace(/\{subject:([A-Za-z0-9._:-]+)\}/g, (_, id) => {
      const subject = work.subjects.find(subject => subject.id === id);
      requireProcess(subject?.locator && request.subjectIds.includes(id), 'RUNNER_SUBJECT_ARGUMENT_INVALID');
      usedSubjects.add(id);
      const root = ctx.local.bootstrap.roots.find(root => root.id === subject.locator.rootId);
      return join(root.path, subject.locator.path);
    }));
    // File artifact coverage must name the actual object in the registered argv.
    requireProcess(request.subjectIds.every(id => !['retained_artifact', 'fresh_build'].includes(work.subjects.find(subject => subject.id === id)?.kind) || usedSubjects.has(id)), 'RUNNER_OBJECT_NOT_BOUND');
    const startedAt = now();
    const result = await timed('business', () => new Promise(resolveResult => {
      business = spawn(runner.command, args, { cwd, env: runnerEnvironment(runner), shell: false, windowsHide: true, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] });
      const timer = setTimeout(() => { void stop('RUNNER_TIMEOUT'); }, runner.timeoutMs);
      let failed = false;
      business.on('spawn', () => {
        void writeJson(join(directory, 'status.json'), { status: 'running', workerPid: process.pid, businessPid: business.pid, executionId: request.executionId, startedAt }).catch(() => stop('JOURNAL_WRITE_FAILED'));
        if (interruption) void stop(interruption);
      });
      for (const stream of ['stdout', 'stderr']) business[stream].on('data', bytes => {
        total[stream] += bytes.length;
        const available = Math.max(0, runner.maxLogBytes - retained[stream]);
        if (available) { const chunk = Buffer.from(bytes.subarray(0, available)); retained[stream] += chunk.length; buffers[stream].push(chunk); }
      });
      business.on('error', () => { failed = true; });
      business.on('close', (exitCode, signal) => {
        clearTimeout(timer);
        resolveResult({ result: interruption || signal ? 'interrupted' : failed ? 'errored' : exitCode === 0 ? 'passed' : 'failed', exitCode, startedAt, finishedAt: now() });
      });
    }));
    execution = result;
    business = null;
    if (interruption) limitations.push(interruption);
    for (const stream of ['stdout', 'stderr']) {
      const bytes = Buffer.concat(buffers[stream]);
      await exclusiveText(join(directory, stream + '.log'), bytes);
      logs.push({ stream, digest: sha256(bytes), retainedBytes: bytes.length, totalBytes: total[stream], truncated: total[stream] > bytes.length });
    }
  }
  if (coverage) {
    const contract = checkerFor(ctx,runner);
    const report = verifyCheckerReport(contract, {stdout:Buffer.concat(buffers.stdout).toString('utf8'),truncated:total.stdout > retained.stdout,executionId:request.executionId,
      subjects:subjectsBefore.filter(subject => request.subjectIds.includes(subject.id)),claims:[...new Set(work.checkBindings.criteria.filter(row => request.criterionIds.includes(row.criterionId)).flatMap(row => row.claims))],formats:Object.fromEntries(work.checkBindings.subjects.map(row => [row.subjectId,row.format]))});
    coverage = {...coverage,report};
    if (report.reason) limitations.push(report.reason);
  }
  const after = await captureInputs(ctx, work), subjectsAfter = await captureSubjects(ctx, work, after), environmentAfter = await captureEnvironment(ctx, runner, after, work);
  if (environmentBefore.identityDigest !== environmentAfter.identityDigest) {
    limitations.push('ENVIRONMENT_CHANGED_DURING_EXECUTION');
    environmentBefore.identityDigest = null;
    environmentBefore.unknowns.push('ENVIRONMENT_CHANGED_DURING_EXECUTION');
  }
  const subjects = subjectsAfter.map(subject => {
    const prior = subjectsBefore.find(before => before.id === subject.id);
    if (subject.kind === 'retained_artifact' && prior?.identityDigest !== subject.identityDigest) { limitations.push('RETAINED_OBJECT_CHANGED_DURING_EXECUTION:' + subject.id); return { ...subject, identityDigest: prior?.identityDigest ?? null }; }
    if (subject.kind === 'fresh_build' && subject.identityDigest && !prior?.identityDigest && execution.result === 'passed') return { ...subject, producedByExecutionId: request.executionId };
    return subject;
  });
  // Runtime stores the real result even if the final receipt cannot be committed.
  await writeJson(join(directory, 'result.json'), { executionId: request.executionId, workItemRef: work.workItemRef, before, after, subjects, environment: environmentBefore, environmentAfter, execution, limitations, logs, ...(coverage ? {coverage} : {}), ...(clock ? {timing:timingResult(clock)} : {}) });
  await writeJson(join(directory, 'status.json'), { status: 'collected', workerPid: process.pid, businessPid: null, executionId: request.executionId });
  if (process.connected) process.send({ collected: true });
} catch (error) {
  await stop('COLLECTOR_ERROR');
  await writeJson(join(directory, 'error.json'), { executionId: request.executionId, code: error.code ?? 'COLLECTOR_ERROR', recordedAt: now(), ...(clock ? {timing:timingResult(clock)} : {}) });
  process.exitCode = 1;
} finally {
  if (process.connected) process.disconnect();
}
