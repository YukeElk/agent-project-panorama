import { readdir, lstat } from 'node:fs/promises';
import { join, resolve, relative, delimiter, isAbsolute } from 'node:path';
import { normalizeBootstrap, EXCLUDES } from './config.mjs';
import { openCore } from './core.mjs';
import { readJson, exists } from './io.mjs';
import { assertOrdinaryPath } from '../process/inputs.mjs';
import { matchPath } from '../process/applicability.mjs';

export function bootstrapInput(bootstrap) {
  return {profile:bootstrap.profile,roots:bootstrap.roots.map(root=>({id:root.id,kind:root.kind,description:root.description,...(root.kind==='project'?{}:{path:root.path}),include:root.selector.include,exclude:root.selector.exclude,role:root.selector.role,observeOnly:root.observeOnly})),
    runners:bootstrap.runners,publicPaths:bootstrap.publicPaths,features:bootstrap.features,...(bootstrap.formatVersion ? {formatVersion:bootstrap.formatVersion,inputScopes:bootstrap.inputScopes,checkerContracts:bootstrap.checkerContracts} : {})};
}
export async function findExecutable(name) {
  if(!name)return null;
  const candidates=isAbsolute(name)?[name]:(process.env.PATH??'').split(delimiter).slice(0,100).flatMap(directory=>[join(directory,name),...(process.platform==='win32'&&!name.endsWith('.exe')?[join(directory,name+'.exe')]:[])]);
  for(const candidate of candidates)try{await assertOrdinaryPath(candidate);if((await lstat(candidate)).isFile())return resolve(candidate);}catch{}
  return null;
}
export async function inspectBootstrap(projectRoot,bootstrap,{checkPython=true}={}) {
  const errors=[],checks=[];
  async function file(path,field,directory=false) {
    try {await assertOrdinaryPath(path);const info=await lstat(path);if(directory?!info.isDirectory():!info.isFile())throw Error();checks.push({field,status:'available'});}
    catch(error) {errors.push({path:field,code:'PATH_UNAVAILABLE',message:'登记位置不存在、类型不符或不可安全读取。'});}
  }
  for(const [index,root] of bootstrap.roots.entries())await file(root.path,'/roots/'+index+'/path',true);
  for(const [index,runner] of bootstrap.runners.entries()) {
    await file(runner.command,'/runners/'+index+'/command');
    await file(resolve(projectRoot,runner.cwd),'/runners/'+index+'/cwd',true);
    for(const [argument,arg] of runner.args.entries()) {
      if(arg.startsWith('-')||arg.includes('{subject:')||!/\.(?:mjs|cjs|js|py|ps1|sh|json|yaml|yml|toml)$/.test(arg))continue;
      const target=resolve(projectRoot,runner.cwd,arg),field=`/runners/${index}/args/${argument}`;
      const observed=bootstrap.roots.some(root=>{
        const path=relative(root.path,target).replaceAll('\\','/');
        return path&&path!=='..'&&!path.startsWith('../')&&!isAbsolute(path)&&!root.observeOnly&&root.selector.include.some(pattern=>matchPath(pattern,path))&&!root.selector.exclude.some(pattern=>matchPath(pattern,path));
      });
      if(!observed)errors.push({path:field,code:'RUNNER_ARGUMENT_FILE_UNOBSERVED',message:'脚本或配置参数必须位于声明的观察范围。'});
      else await file(target,field);
    }
  }
  if(checkPython)try{const core=await openCore({projectRoot,python:bootstrap.python});checks.push({field:'/python',status:'available',version:core.version.python,coreVersion:core.version.version});}
  catch(error){errors.push({path:'/python',code:error.code??'PYTHON_UNAVAILABLE',message:'固定 core 的依赖检查未通过。'});}
  return {valid:errors.length===0,errors,checks};
}
export async function preflight(locations,{input=null,python=null}={}) {
  const recommendedExcludes=['.agents/**','**/.coverage','**/.coverage.*','**/.pytest_cache/**','**/htmlcov/**','**/dist/**','**/build/**','**/out/**','**/.cache/**','**/node_modules/**','**/__pycache__/**'];
  const observations=[],warnings=[];let count=0;
  async function walk(relative='',depth=0) {
    if(depth>4||count>1500)return;
    for(const entry of await readdir(join(locations.projectRoot,relative),{withFileTypes:true})) {
      if(count>1500)return;
      if(++count>1500){warnings.push({code:'DISCOVERY_LIMIT',message:'目录发现已达到上限；草稿需要回读补充。'});return;}
      const path=relative?relative+'/'+entry.name:entry.name;
      if(entry.isSymbolicLink()){observations.push({path,kind:'link_not_followed'});continue;}
      if(entry.name==='.env'||entry.name.startsWith('.env.')||entry.name==='API.txt'){observations.push({path,kind:'excluded_sensitive_name'});continue;}
      if(/^(\.git|\.structure|\.panorama|\.agents|node_modules|\.venv|venv|__pycache__|\.pytest_cache|\.cache|dist|build|out|htmlcov)$/.test(entry.name)||entry.name.startsWith('.coverage')) {observations.push({path,kind:'cache_dependency_generated_or_tool_state'});continue;}
      if(entry.isDirectory())await walk(path,depth+1);
      else if(['package.json','pyproject.toml','requirements.txt','Cargo.toml'].includes(entry.name))observations.push({path,kind:'package_descriptor'});
    }
  }
  await walk();
  let packageScripts=[];
  if(await exists(join(locations.projectRoot,'package.json')))try{const data=await readJson(join(locations.projectRoot,'package.json'),{maxBytes:65536});packageScripts=Object.keys(data.scripts??{}).slice(0,100);}catch(error){warnings.push({code:error.code??'PACKAGE_UNREADABLE',message:'包描述未能解析，未运行项目脚本。'});}
  const selectedPython=await findExecutable(python??process.env.PANORAMA_PYTHON??'python');
  const draft=input??{roots:[{id:'project',kind:'project',description:'源码、配置、检查脚本和明确样本；按实际需要回读范围',include:['**'],exclude:recommendedExcludes}],runners:[]};
  let normalized=null,validation={valid:false,errors:[],checks:[]};
  try {normalized=normalizeBootstrap(draft,locations.projectRoot,selectedPython??python);validation=await inspectBootstrap(locations.projectRoot,normalized);}
  catch(error){validation.errors.push({path:selectedPython?'':'/python',code:error.code??'BOOTSTRAP_INVALID',message:'需要完整配置及可用 Python 绝对路径。'});}
  if(!draft.runners?.length)warnings.push({code:'NO_RUNNER_REGISTERED',message:'尚无机器检查器；可先完成工程准备，再在工作边界登记首个 runner。'});
  if(packageScripts.length)warnings.push({code:'SCRIPTS_REQUIRE_EXPLICIT_REGISTRATION',message:'仅发现脚本名称；需要明确可执行文件、参数和覆盖合同。'});
  return {formatVersion:'panorama.onboarding-preflight.v1',...validation,draft,python:selectedPython,projectRoot:locations.projectRoot,dataRoot:locations.dataRoot,
    observations,packageScripts,warnings,defaults:input?[]:[{path:'/roots/0/include',source:'broad_initial_scope_requires_review'},{path:'/roots/0/exclude',source:'proposed_cache_dependency_and_generated_exclusions',values:recommendedExcludes}],
    builtInExcludes:EXCLUDES,execution:'Only the pinned core runtime version probe may execute; project scripts and registered runners were not executed.'};
}
