// Release template. Only built-in Node modules load before bundle verification.
import { readFile, readdir, lstat, realpath, writeFile, chmod } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const root = dirname(fileURLToPath(import.meta.url)), pin = '__RELEASE_SHA256__';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const requireState = (ok,code) => { if (!ok) throw Object.assign(Error(code),{code}); };
const compare = (a,b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
const psQuote = value => "'" + value.replaceAll("'","''") + "'";
const shQuote = value => "'" + value.replaceAll("'", "'\"'\"'") + "'";

function probe(executable,args) {
  return new Promise((done,reject) => {
    const child=spawn(executable,args,{windowsHide:true,shell:false,stdio:['ignore','pipe','pipe']}); let output='';
    const timer=setTimeout(()=>{child.kill();reject(Object.assign(Error('RUNTIME_PROBE_TIMEOUT'),{code:'RUNTIME_PROBE_TIMEOUT'}));},10000);
    child.stdout.on('data',bytes=>{output+=bytes;if(output.length>8192)child.kill();}); child.stderr.resume();
    child.once('error',error=>{clearTimeout(timer);reject(error);});
    child.once('close',code=>{clearTimeout(timer);code===0 && output.length<=8192?done(output.trim()):reject(Object.assign(Error('RUNTIME_PROBE_FAILED'),{code:'RUNTIME_PROBE_FAILED'}));});
  });
}
async function verifyBundle() {
  const [major,minor]=process.versions.node.split('.').map(Number);
  requireState(major>=24 || major===22 && minor>=19,'NODE_ENGINE_UNSUPPORTED');
  const raw=await readFile(join(root,'release.json'));
  requireState(hash(raw)===pin,'RELEASE_MANIFEST_CHANGED');
  const manifest=JSON.parse(raw);
  requireState(manifest.formatVersion==='panorama.offline-bundle.v1' && manifest.platforms.includes(process.platform),'BUNDLE_PLATFORM_UNSUPPORTED');
  const actual=[],app=join(root,'app');
  requireState(!(await lstat(app)).isSymbolicLink(),'BUNDLE_LINK_REJECTED');
  async function walk(path='') {
    for(const entry of await readdir(join(app,path),{withFileTypes:true})) {
      const name=path?path+'/'+entry.name:entry.name;
      requireState(actual.length<20000 && !entry.isSymbolicLink(),'BUNDLE_LINK_REJECTED');
      if(entry.isDirectory())await walk(name);
      else { requireState(entry.isFile(),'BUNDLE_FILE_INVALID');actual.push({path:name,sha256:hash(await readFile(join(app,name)))}); }
    }
  }
  await walk(); actual.sort(compare);
  requireState(JSON.stringify(actual)===JSON.stringify(manifest.files),'BUNDLE_FILES_CHANGED');
  return manifest;
}
async function runtime(name,path) {
  const canonical=await realpath(resolve(path)), info=await lstat(canonical);
  requireState(info.isFile()&&!info.isSymbolicLink(),'RUNTIME_UNAVAILABLE');
  const version=name==='node'?process.versions.node:await probe(canonical,['-I','-B','-c','import ast, json, hashlib, pathlib, uuid, subprocess, sys; print(".".join(map(str,sys.version_info[:3])))']);
  if(name==='python') {const [major,minor]=version.split('.').map(Number);requireState(major===3&&minor>=11,'PYTHON_VERSION_UNSUPPORTED');}
  return {path:canonical,version,sha256:hash(await readFile(canonical))};
}
async function doctor(manifest,installation) {
  requireState(installation.formatVersion==='panorama.offline-installation.v1' && installation.releaseSha256===pin,'INSTALLATION_RELEASE_MISMATCH');
  const checks=[];
  for(const name of ['node','python']) {
    const expected=installation.runtimes[name];
    try {
      const actual=await runtime(name,expected.path);
      requireState(actual.sha256===expected.sha256 && actual.version===expected.version,'RUNTIME_CHANGED');
      if(name==='node')requireState(await realpath(process.execPath)===actual.path,'NODE_RUNTIME_MISMATCH');
      checks.push({name,status:'available',...actual});
    }catch(error){checks.push({name,status:'unavailable',code:error.code ?? 'RUNTIME_UNAVAILABLE'});}
  }
  return {formatVersion:'panorama.offline-doctor.v1',ready:checks.every(c=>c.status==='available'),version:manifest.version,
    platform:process.platform,architecture:process.arch,filesVerified:manifest.files.length,runtimesIncluded:false,checks};
}

try {
  const [command='doctor',...args]=process.argv.slice(2);
  requireState(['install','doctor','process','workbench'].includes(command),'COMMAND_UNKNOWN');
  const manifest=await verifyBundle(),config=join(root,'installation.local.json');
  if(command==='install') {
    requireState(args.length===2 && args[0]==='--python','USAGE_INSTALL_PYTHON');
    const runtimes={node:await runtime('node',process.execPath),python:await runtime('python',args[1])};
    const installation={formatVersion:'panorama.offline-installation.v1',releaseSha256:pin,runtimes};
    let existing=null;
    try { existing=JSON.parse(await readFile(config,'utf8')); } catch(error) {if(error.code!=='ENOENT')throw error;}
    if(existing)requireState(JSON.stringify(existing)===JSON.stringify(installation),'INSTALLATION_EXISTS_USE_FRESH_BUNDLE');
    const launchers={
      'panorama.ps1':`& ${psQuote(runtimes.node.path)} (Join-Path $PSScriptRoot 'offline.mjs') @args\nexit $LASTEXITCODE\n`,
      panorama:`#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd)\nexec ${shQuote(runtimes.node.path)} "$base/offline.mjs" "$@"\n`,
    }, missing=[];
    for(const [name,content] of Object.entries(launchers)) {
      try {requireState(!(await lstat(join(root,name))).isSymbolicLink(),'LAUNCHER_LINK_REJECTED');requireState(await readFile(join(root,name),'utf8')===content,'LAUNCHER_ALREADY_EXISTS');}
      catch(error){if(error.code!=='ENOENT')throw error;missing.push([name,content]);}
    }
    for(const [name,content] of missing)await writeFile(join(root,name),content,{flag:'wx'});
    await chmod(join(root,'panorama'),0o755);
    if(!existing)await writeFile(config,JSON.stringify(installation,null,2)+'\n',{flag:'wx',mode:0o600});
    const result=await doctor(manifest,installation);requireState(result.ready,'INSTALLATION_RUNTIME_UNAVAILABLE');
    console.log(JSON.stringify({...result,installed:true,launcher:join(root,process.platform==='win32'?'panorama.ps1':'panorama')},null,2));
  } else {
    let installation;
    try {installation=JSON.parse(await readFile(config,'utf8'));}catch(error){if(error.code==='ENOENT')throw Object.assign(Error(),{code:'INSTALL_REQUIRED'});throw error;}
    const result=await doctor(manifest,installation);
    if(command==='doctor') {requireState(!args.length,'ARGUMENT_INVALID');console.log(JSON.stringify(result,null,2));process.exitCode=result.ready?0:2;}
    else {
      requireState(result.ready,'INSTALLATION_RUNTIME_UNAVAILABLE');
      const entry=join(root,'app',command==='process'?'src/development/cli.mjs':'src/cli.mjs');
      const child=spawn(installation.runtimes.node.path,[entry,...args],{cwd:process.cwd(),env:{...process.env,PANORAMA_PYTHON:installation.runtimes.python.path},windowsHide:true,shell:false,stdio:'inherit'});
      const stop=signal=>{if(child.exitCode===null)child.kill(signal);};process.once('SIGINT',()=>stop('SIGINT'));process.once('SIGTERM',()=>stop('SIGTERM'));
      process.exitCode=await new Promise((done,reject)=>{child.once('error',reject);child.once('close',code=>done(code??1));});
    }
  }
} catch(error) {
  console.error(JSON.stringify({error:error.code ?? 'OFFLINE_OPERATION_FAILED',message:'Check README.md and the supplied Node/Python versions. No runtime or package downloads are performed.'}));process.exitCode=1;
}
