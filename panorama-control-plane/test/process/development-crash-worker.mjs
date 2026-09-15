import { loadDevelopment } from '../../src/development/config.mjs';
import { begin } from '../../src/development/journal.mjs';
import { inspect, check } from '../../src/development/service.mjs';
import { readJson } from '../../src/development/io.mjs';
const request = await readJson(process.argv[2]);
// Keep IPC referenced while paused so the parent, rather than an empty event
// loop, is responsible for the actual process termination under test.
process.on('message', () => {});
const ctx = await loadDevelopment(request);
const pause = async phase => { if (phase === request.pause) { process.send({ phase }); await new Promise(() => {}); } };
ctx.onCoreMutation = pause;
ctx.onReceiptCommit = pause;
if (request.command === 'begin') await begin(ctx, request.input, request.workItemId);
else if (request.command === 'check') await check(ctx, request.workItemId, request.input.runnerId);
else await inspect(ctx, request.workItemId, request.command, request.input);
