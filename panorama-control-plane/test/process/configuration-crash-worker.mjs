import { readJson } from '../../src/development/io.mjs';
import { applyConfiguration } from '../../src/development/configuration.mjs';
const request=await readJson(process.argv[2]);
process.on('message',()=>{});
await applyConfiguration(request,request.plan,{onPhase:async phase=>{
  if(phase===request.pause){process.send({phase});await new Promise(()=>{});}
}});
