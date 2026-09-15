import { AsyncLocalStorage } from 'node:async_hooks';
import { performance } from 'node:perf_hooks';

const storage = new AsyncLocalStorage();
export function startTiming() {
  const collector = { started: performance.now(), spans: [] };
  storage.enterWith({ collector, parent: null });
  return collector;
}
export const timingEnabled = () => Boolean(storage.getStore());
export async function timed(phase, fn) {
  const context = storage.getStore();
  if (!context) return fn();
  const span = { phase, start: performance.now(), end: null, children: [] };
  context.collector.spans.push(span);
  context.parent?.children.push(span);
  try { return await storage.run({ collector: context.collector, parent: span }, fn); }
  finally { span.end = performance.now(); }
}
function union(intervals) {
  let total = 0, right = -Infinity;
  for (const [start, end] of intervals.sort((a,b) => a[0] - b[0])) {
    total += Math.max(0, end - Math.max(start, right)); right = Math.max(right, end);
  }
  return total;
}
export function timingResult(collector) {
  const end = performance.now(), groups = new Map(), round = n => Math.round(n * 1000) / 1000;
  for (const span of collector.spans) {
    const stop = span.end ?? end, row = groups.get(span.phase) ?? { phase: span.phase, calls: 0, inclusiveMs: 0, exclusiveMs: 0 };
    row.calls++; row.inclusiveMs += stop - span.start;
    row.exclusiveMs += Math.max(0, stop - span.start - union(span.children.map(child => [child.start, child.end ?? end])));
    groups.set(span.phase, row);
  }
  return { formatVersion: 'panorama.command-timing.v1', measuredMs: round(end - collector.started), processElapsedMs: round(end), startupUnattributedMs: round(collector.started),
    phases: [...groups.values()].map(row => ({...row,inclusiveMs:round(row.inclusiveMs),exclusiveMs:round(row.exclusiveMs)})),
    accounting: 'Durations are local monotonic wall time. Inclusive spans overlap; concurrent exclusive spans may overlap. Worker time is included in parent executor_wait, never add both.' };
}
