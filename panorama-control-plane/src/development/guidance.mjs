import { join } from 'node:path';
import { readFile, unlink, lstat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { sha256 } from '../process/json.mjs';
import { requireProcess } from '../process/errors.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { exists, readJson, atomicText } from './io.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const start='<!-- panorama-development-process:v1 -->',end='<!-- /panorama-development-process:v1 -->';
export const guidanceBlock=start+'\n本项目已启用开发过程记录。处理开发任务时先阅读 [.panorama/DEVELOPMENT.md](.panorama/DEVELOPMENT.md)，使用本地 `panorama-process` CLI。\n'+end;
export const guidanceFiles={'.panorama/DEVELOPMENT.md':'references/project-guide.md','.agents/skills/panorama-development-process/SKILL.md':'SKILL.md','.agents/skills/panorama-development-process/agents/openai.yaml':'agents/openai.yaml','.agents/skills/panorama-development-process/references/project-guide.md':'references/project-guide.md'};
async function textAt(path) {await assertOrdinaryPath(path,{allowMissing:true});if(!await exists(path))return null;const info=await lstat(path);requireProcess(info.isFile()&&info.size<=1048576,'GUIDANCE_FILE_LIMIT');return readFile(path,'utf8');}
export async function planGuidance(projectRoot,local,enabled=true) {
  const legacy=await readJson(join(root,'src/development/legacy-guidance.json')), changes=[],retained=[];
  for(const [path,source] of Object.entries(guidanceFiles)) {
    const before=await textAt(join(projectRoot,path)), hash=before===null?null:sha256(before);
    const template=await readFile(join(root,'skills/panorama-development-process',source),'utf8');
    const owned=hash===null || hash===sha256(template) || local?.guidanceOwnership?.[path]===hash || legacy[source]?.includes(hash);
    if(!owned) {if(!enabled){retained.push(path);continue;} requireProcess(false,'PROJECT_GUIDANCE_CONFLICT',{path});}
    const after=enabled?template:null;
    if(before!==after) changes.push({path,beforeHash:hash,after,afterHash:after===null?null:sha256(after)});
  }
  const path='AGENTS.md', before=await textAt(join(projectRoot,path)),value=before??'';
  requireProcess(value.includes(start)===value.includes(end),'AGENTS_GUIDANCE_CONFLICT');
  requireProcess(!value.includes(start)||value.includes(guidanceBlock),'AGENTS_GUIDANCE_CONFLICT');
  requireProcess(value.split(start).length<=2&&value.split(end).length<=2,'AGENTS_GUIDANCE_CONFLICT');
  const after=enabled?(value.includes(guidanceBlock)?value:value+(value?'\n\n':'')+guidanceBlock+'\n'):value.replace(guidanceBlock,'');
  if(before!==after&&(enabled||value.includes(guidanceBlock)))changes.push({path,beforeHash:before===null?null:sha256(before),after,afterHash:sha256(after)});
  return {changes,retained};
}
export async function applyGuidance(projectRoot,plan,onStep) {
  for(const change of plan.changes) {
    requireProcess(change.path==='AGENTS.md'||Object.hasOwn(guidanceFiles,change.path),'GUIDANCE_PATH_INVALID');
    const file=join(projectRoot,change.path),before=await textAt(file),hash=before===null?null:sha256(before);
    if(hash===change.afterHash)continue;
    requireProcess(hash===change.beforeHash,'GUIDANCE_CHANGED_DURING_SWITCH',{path:change.path});
    if(change.after===null)await unlink(file);else await atomicText(file,change.after);
    if(onStep)await onStep('guidance:'+change.path);
  }
}
export async function guidanceOwnership(projectRoot) {
  const result={};
  for(const path of Object.keys(guidanceFiles)) {const value=await textAt(join(projectRoot,path));if(value!==null)result[path]=sha256(value);}
  return result;
}
