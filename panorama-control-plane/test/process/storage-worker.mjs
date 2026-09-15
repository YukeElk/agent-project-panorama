import { readFile } from 'node:fs/promises';
import { openProcessStore } from '../../src/process/storage.mjs';
const request = JSON.parse(await readFile(process.argv[2],'utf8'));
const store = await openProcessStore({...request.options,onCommitPhase:request.pause ? async () => {
  process.send({phase:'objects_written'});
  await new Promise(() => { setInterval(() => {},1000); });
} : undefined});
if (process.send) process.send({phase:'ready'});
if (request.waitForGo) await new Promise(resolve => process.once('message',resolve));
try {
  const result = await store.importReceipt(request.operation);
  if (process.send) process.send({result});
} catch (error) { if (process.send) process.send({error:error.code ?? error.message}); }
if (process.connected) process.disconnect();
