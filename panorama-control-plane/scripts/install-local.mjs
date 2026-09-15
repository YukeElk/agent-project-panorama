#!/usr/bin/env node
import { readFile, readdir, lstat, realpath, mkdir, copyFile, writeFile, symlink } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, join, resolve, relative, sep, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { verifyCore } from '../src/development/core.mjs';

const packageRoot=fileURLToPath(new URL('../',import.meta.url));
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const requireState=(condition,code)=>{if(!condition)throw Object.assign(Error(code),{code});};
const contained=(base,path)=>{const suffix=relative(base,path);return suffix&&!suffix.startsWith('..'+sep)&&suffix!=='..'&&!isAbsolute(suffix);};
async function ordinary(path,{missing=false}={}) {
  const absolute=resolve(path),parent=dirname(absolute);if(parent!==absolute)await ordinary(parent,{missing});
  try {const info=await lstat(absolute);requireState(!info.isSymbolicLink(),'INSTALLATION_PATH_LINK');return info;}
  catch(error){if(missing&&error.code==='ENOENT')return null;throw error;}
}
function probe(executable,args) {
  return new Promise((done,reject)=>{
    const child=spawn(executable,args,{windowsHide:true,shell:false,stdio:['ignore','pipe','pipe']});let text='';
    const timer=setTimeout(()=>{child.kill();reject(Object.assign(Error(),{code:'RUNTIME_PROBE_TIMEOUT'}));},10000);
    child.stdout.on('data',bytes=>{text+=bytes;if(text.length>4000){child.kill();}});child.stderr.resume();
    child.once('error',error=>{clearTimeout(timer);reject(error);});child.once('close',code=>{clearTimeout(timer);code===0&&text.length<=4000?done(text.trim()):reject(Object.assign(Error(),{code:'RUNTIME_PROBE_FAILED'}));});
  });
}
export async function installLocal({destination,python}) {
  requireState(typeof destination==='string'&&isAbsolute(destination)&&typeof python==='string'&&isAbsolute(python),'ABSOLUTE_PATH_REQUIRED');
  const target=resolve(destination),source=await realpath(packageRoot);
  requireState(target!==source&&!contained(source,target)&&!contained(target,source),'INSTALLATION_DESTINATION_CONFLICT');
  requireState(!await ordinary(target,{missing:true}),'INSTALLATION_DESTINATION_EXISTS');
  const [major,minor]=process.versions.node.split('.').map(Number);
  requireState(major>=24||major===22&&minor>=19,'NODE_ENGINE_UNSUPPORTED');
  await verifyCore();
  const runtimes={};
  for(const [name,path] of Object.entries({node:process.execPath,python:resolve(python)})) {
    requireState((await ordinary(path)).isFile(),'RUNTIME_UNAVAILABLE');
    const version=name==='node'?process.version:await probe(path,['-I','-B','-c','import sys; print(".".join(map(str, sys.version_info[:3])))']);
    if(name==='python'){const [major,minor]=version.split('.').map(Number);requireState(major===3&&minor>=11,'PYTHON_VERSION_UNSUPPORTED');}
    runtimes[name]={path,version,sha256:digest(await readFile(path))};
  }
  // Validate required source files before creating any install directory.
  for(const path of ['src/development/cli.mjs','dist/workbench/index.html','pnpm-lock.yaml','node_modules/ajv/package.json'])await readFile(join(source,path));
  await mkdir(dirname(target),{recursive:true});await mkdir(target);
  const app=join(target,'app');await mkdir(app);const files=[],links=[];
  async function copy(path) {
    const from=join(source,path),to=join(app,path),info=await lstat(from);
    requireState(files.length+links.length<20000,'INSTALLATION_FILE_LIMIT');
    if(info.isSymbolicLink()) {
      requireState(path.startsWith('node_modules/'),'INSTALLATION_SOURCE_LINK');
      const actual=await realpath(from);requireState(contained(join(source,'node_modules'),actual),'INSTALLATION_DEPENDENCY_ESCAPED');
      links.push({path,target:relative(source,actual).split(sep).join('/'),directory:(await lstat(actual)).isDirectory()});
    } else if(info.isDirectory()) {
      await mkdir(to,{recursive:true});
      for(const entry of await readdir(from)) {
        if(['.bin','.vite','.vite-temp','__pycache__'].includes(entry))continue;
        if(path==='node_modules'&&['.modules.yaml','.package-map.json','.pnpm-workspace-state-v1.json'].includes(entry))continue;
        await copy(path+'/'+entry);
      }
    } else {
      requireState(info.isFile(),'INSTALLATION_SOURCE_INVALID');await copyFile(from,to);
      const hash=digest(await readFile(to));requireState(hash===digest(await readFile(from)),'INSTALLATION_SOURCE_CHANGED');files.push({path,sha256:hash});
    }
  }
  for(const path of ['src','contracts','vendor','skills','docs','dist','node_modules','package.json','pnpm-lock.yaml','README.md'])await copy(path);
  for(const entry of links) {
    const to=join(app,entry.path),mapped=join(app,entry.target);await lstat(mapped);
    await symlink(process.platform==='win32'?mapped:relative(dirname(to),mapped),to,entry.directory?(process.platform==='win32'?'junction':'dir'):'file');
    files.push({path:entry.path,target:entry.target});
  }
  files.sort((a,b)=>a.path.localeCompare(b.path));
  const pkg=JSON.parse(await readFile(join(app,'package.json'),'utf8'));
  const manifest={formatVersion:'panorama.local-installation.v1',id:'installation:'+digest(JSON.stringify({files,runtimes})),installedAt:new Date().toISOString(),packageVersion:pkg.version,nodeEngine:pkg.engines.node,runtimes,files};
  const raw=JSON.stringify(manifest,null,2)+'\n';await writeFile(join(target,'installation.json'),raw,{flag:'wx'});
  const launcher=(await readFile(new URL('local-launcher.mjs',import.meta.url),'utf8')).replace("'__MANIFEST_SHA256__'",JSON.stringify(digest(raw)));
  await writeFile(join(target,'panorama-launch.mjs'),launcher,{flag:'wx'});
  const quote=value=>"'"+value.replaceAll("'","''")+"'";
  await writeFile(join(target,'panorama.ps1'),`& ${quote(runtimes.node.path)} (Join-Path $PSScriptRoot 'panorama-launch.mjs') @args\nexit $LASTEXITCODE\n`,{flag:'wx'});
  await writeFile(join(target,'INSTALLATION.md'),`# Panorama 固定本机安装\n\n此目录保存 CLI、工作台、合同、技能、固定 core 和已安装的依赖快照（含构建依赖）。入口在执行前校验清单；Node/Python 保留为已记录路径与摘要的外部运行时。\n\n在 PowerShell 中运行 \`& '${target.replaceAll("'","''")}/panorama.ps1' doctor\`；开发命令以 \`process\` 开头，工作台以 \`workbench start\` 开头。也可用清单中的 Node 直接运行 \`panorama-launch.mjs\`。不修改系统 PATH，不运行依赖安装脚本。\n\n固定安装目录不要移动（Windows 依赖 junction 绑定本目录）；升级或变更运行时请从已构建版本安装到新目录。失败的安装目录保留用于诊断，不作为可用入口。安装资料不含业务项目身份或证据。\n`,{flag:'wx'});
  return {installed:true,id:manifest.id,destination:target,launcher:join(target,'panorama.ps1'),files:files.length,runtimes};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  try {
    const args=process.argv.slice(2),options={};
    for(let i=0;i<args.length;i+=2){requireState(['--destination','--python'].includes(args[i])&&!Object.hasOwn(options,args[i])&&args[i+1],'INSTALLATION_ARGUMENT_INVALID');options[args[i]]=args[i+1];}
    process.stdout.write(JSON.stringify(await installLocal({destination:options['--destination'],python:options['--python']}),null,2)+'\n');
  }catch(error){process.stderr.write(JSON.stringify({error:error.code??'INSTALLATION_FAILED',message:'未覆盖任何既有安装；若已创建目标目录，请保留诊断并用新目录重试。'})+'\n');process.exitCode=1;}
}
