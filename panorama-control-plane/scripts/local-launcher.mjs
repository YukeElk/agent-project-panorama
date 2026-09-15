#!/usr/bin/env node
// Installation template: the installer pins the manifest before writing this entry.
import { readFile, readdir, lstat, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const root=dirname(fileURLToPath(import.meta.url)), digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const pin='__MANIFEST_SHA256__';
const requireState=(condition,code)=>{if(!condition)throw Object.assign(Error(code),{code});};
function inside(base,path) {const suffix=relative(base,path);return suffix&&!suffix.startsWith('..'+sep)&&suffix!=='..'&&!/^[A-Za-z]:/.test(suffix);}
async function inspectInstallation() {
  const raw=await readFile(join(root,'installation.json'));
  requireState(digest(raw)===pin,'INSTALLATION_MANIFEST_CHANGED');
  const manifest=JSON.parse(raw),app=join(root,'app');
  requireState(manifest.formatVersion==='panorama.local-installation.v1','INSTALLATION_FORMAT_UNSUPPORTED');
  const actual=[];
  async function walk(path='') {
    for(const entry of await readdir(join(app,path),{withFileTypes:true})) {
      const name=path?path+'/'+entry.name:entry.name,full=join(app,name);
      requireState(actual.length<20000,'INSTALLATION_FILE_LIMIT');
      if(entry.isSymbolicLink()) {
        const target=await realpath(full);requireState(inside(app,target),'INSTALLATION_LINK_ESCAPED');
        actual.push({path:name,target:relative(app,target).split(sep).join('/')});
      } else if(entry.isDirectory())await walk(name);
      else {requireState(entry.isFile(),'INSTALLATION_FILE_INVALID');actual.push({path:name,sha256:digest(await readFile(full))});}
    }
  }
  await walk();actual.sort((a,b)=>a.path.localeCompare(b.path));
  requireState(JSON.stringify(actual)===JSON.stringify(manifest.files),'INSTALLATION_FILES_CHANGED');
  const checks=[];
  for(const [name,runtime] of Object.entries(manifest.runtimes)) {
    try {
      const info=await lstat(runtime.path);
      requireState(info.isFile()&&!info.isSymbolicLink()&&digest(await readFile(runtime.path))===runtime.sha256,'RUNTIME_CHANGED');
      checks.push({name,status:'available',path:runtime.path,version:runtime.version});
    } catch(error) {checks.push({name,status:'unavailable',path:runtime.path,code:error.code??'RUNTIME_UNAVAILABLE'});}
  }
  requireState(await realpath(process.execPath)===await realpath(manifest.runtimes.node.path),'NODE_RUNTIME_MISMATCH');
  return {manifest,checks};
}
try {
  const [command='doctor',...args]=process.argv.slice(2);
  requireState(['doctor','process','workbench'].includes(command),'INSTALLATION_COMMAND_UNKNOWN');
  requireState(command!=='doctor'||!args.length,'INSTALLATION_ARGUMENT_INVALID');
  const {manifest,checks}=await inspectInstallation(),ready=checks.every(row=>row.status==='available');
  if(command==='doctor') {
    process.stdout.write(JSON.stringify({formatVersion:'panorama.installation-doctor.v1',ready,installationId:manifest.id,packageVersion:manifest.packageVersion,nodeEngine:manifest.nodeEngine,filesVerified:manifest.files.length,checks},null,2)+'\n');
    process.exitCode=ready?0:2;
  } else {
    requireState(ready,'INSTALLATION_RUNTIME_UNAVAILABLE');
    const entry=join(root,'app',command==='process'?'src/development/cli.mjs':'src/cli.mjs');
    const child=spawn(manifest.runtimes.node.path,[entry,...args],{cwd:process.cwd(),env:{...process.env,PANORAMA_PYTHON:manifest.runtimes.python.path},windowsHide:true,shell:false,stdio:'inherit'});
    const stop=signal=>{if(child.exitCode===null)child.kill(signal);};
    process.once('SIGINT',()=>stop('SIGINT'));process.once('SIGTERM',()=>stop('SIGTERM'));
    process.exitCode=await new Promise((done,reject)=>{child.once('error',reject);child.once('close',code=>done(code??1));});
  }
} catch(error) {
  process.stderr.write(JSON.stringify({error:error.code??'INSTALLATION_UNAVAILABLE',message:'安装或固定运行依赖校验未通过；请检查此版本的安装资料并重新安装到新目录。'})+'\n');
  process.exitCode=1;
}
