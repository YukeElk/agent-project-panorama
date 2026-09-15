import { open } from 'node:fs/promises';
import { parseProcessJson } from '../src/process/json.mjs';
import { assessProcess } from '../src/process/evaluate.mjs';
import { ProcessError, requireProcess } from '../src/process/errors.mjs';

// P1 data-only entry. It never discovers a project, runs a checker, or accepts
// provenance credentials from an untrusted request file as verified authority.
async function main() {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === '--help') {
    process.stdout.write('Usage: node scripts/assess-process.mjs --input REQUEST.json [--output ASSESSMENT.json]\n');
    return;
  }
  requireProcess((args.length === 2 || args.length === 4) && args[0] === '--input' && (args.length === 2 || args[2] === '--output'), 'ARGUMENTS_INVALID');
  const limit = 16 * 1024 * 1024, handle = await open(args[1], 'r');
  let bytes;
  try {
    const info = await handle.stat(); requireProcess(info.isFile() && info.size <= limit, 'REQUEST_FILE_INVALID');
    const buffer = Buffer.alloc(limit + 1); let size = 0;
    while (size < buffer.length) { const read = await handle.read(buffer, size, buffer.length - size, null); if (!read.bytesRead) break; size += read.bytesRead; }
    requireProcess(size <= limit, 'JSON_TOO_LARGE'); bytes = buffer.subarray(0, size);
  } finally { await handle.close(); }
  const request = parseProcessJson(bytes, { maxBytes: limit, maxDepth: 24 });
  if ((request.mode ?? 'record') !== 'contract_example') requireProcess(!request.verifiedEvidence?.length, 'TRUSTED_CONTEXT_REQUIRED');
  const assessment = assessProcess(request);
  const serialized = JSON.stringify(assessment, null, 2) + '\n';
  if (args[3]) {
    const output = await open(args[3], 'wx', 0o600);
    try { await output.writeFile(serialized); await output.sync(); } finally { await output.close(); }
    process.stdout.write(JSON.stringify({ assessmentId: assessment.assessmentId, assessmentHash: assessment.assessmentHash, status: assessment.overall.status, processReady: assessment.overall.processReady }) + '\n');
  } else process.stdout.write(serialized);
  // A valid blocked/unknown/conflict assessment is data, not an importer crash.
  process.exitCode = assessment.overall.processReady ? 0 : 2;
}
try { await main(); }
catch (error) {
  process.stderr.write(JSON.stringify({ error: error instanceof ProcessError ? error.code : error.code ?? 'ASSESSMENT_ERROR' }) + '\n');
  process.exitCode = 1;
}
