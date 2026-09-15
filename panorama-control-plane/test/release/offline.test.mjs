import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile, readdir, cp, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { temporary } from '../process/temp.mjs';
import { python } from '../process/development-helpers.mjs';
import { buildOffline } from '../../scripts/release/build-offline.mjs';
import packageInfo from '../../package.json' with { type: 'json' };

function run(executable,args,{cwd,env={}}={}) {
  return new Promise((done,reject)=>{
    const child=spawn(executable,args,{cwd,env:{...process.env,NODE_PATH:'',NODE_OPTIONS:'',...env},windowsHide:true,shell:false,stdio:['ignore','pipe','pipe']});let stdout='',stderr='';
    child.stdout.on('data',bytes=>stdout+=bytes);child.stderr.on('data',bytes=>stderr+=bytes);child.once('error',reject);
    child.once('close',code=>{let data=null;try{data=JSON.parse(stdout||stderr);}catch{}done({code,data,stdout,stderr});});
  });
}
async function input(base,name,value){const path=join(base,name+'.json');await writeFile(path,JSON.stringify(value));return path;}

test('1.0 offline distribution: isolated installations, existing/empty projects, actual checks, UI and integrity', {timeout:240000}, async t=>{
  const base=await temporary(t),bundle=join(base,'bundle A'),other=join(base,'bundle B');
  const result=await buildOffline({output:bundle});
  assert.equal(result.version,packageInfo.version);assert.equal(result.runtimesIncluded,false);
  const manifest=JSON.parse(await readFile(join(bundle,'release.json'),'utf8'));
  assert.ok(manifest.files.length>100);
  assert.ok(manifest.files.every(row=>!row.path.startsWith('/')&&!/^[A-Z]:|\.node$|\.exe$|\.dll$|\.map$|\.env$|installation.local/.test(row.path)));
  for(const row of manifest.files)assert.equal((await lstat(join(bundle,'app',row.path))).isSymbolicLink(),false);
  await cp(bundle,other,{recursive:true,errorOnExist:true,force:false});
  const env={PANORAMA_STATE_HOME:join(base,'host'),PANORAMA_MODEL_BASE_URL:'',PANORAMA_MODEL_API_KEY:''};
  const call=async(args,expected=0,installation=other)=>{
    const result=await run(process.execPath,[join(installation,'offline.mjs'),...args],{cwd:base,env});
    assert.equal(result.code,expected,result.stderr||result.stdout);return result.data;
  };
  await t.test('installation uses supplied runtimes and is repeatable in different extraction paths',async()=>{
    assert.equal((await call(['doctor'],1)).error,'INSTALL_REQUIRED');
    await call(['install','--python',join(base,'missing-python')],1);
    assert.ok(!(await readdir(other)).includes('installation.local.json'));
    const installer=process.platform==='win32'
      ? await run('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',join(other,'install.ps1'),'-Node',process.execPath,'-Python',python],{cwd:base,env})
      : await run('/bin/sh',[join(other,'install.sh'),'--node',process.execPath,'--python',python],{cwd:base,env});
    assert.equal(installer.code,0,installer.stderr||installer.stdout);assert.equal(installer.data.ready,true);
    await call(['install','--python',python]);
    await call(['install','--python',python],0,bundle);
    const doctor=await call(['doctor']);assert.equal(doctor.ready,true);assert.equal(doctor.filesVerified,manifest.files.length);
    const wrapper=process.platform==='win32'
      ? await run('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',join(other,'panorama.ps1'),'doctor'],{cwd:base,env})
      : await run(join(other,'panorama'),['doctor'],{cwd:base,env});
    assert.equal(wrapper.code,0,wrapper.stderr);assert.equal(wrapper.data.ready,true);
  });
  const project=join(base,'existing 项目'),data=join(base,'data-existing');await mkdir(project);
  const command=(args,expected=0)=>call(['process',...args,'--project',project],expected);
  let originalBaseline;
  await t.test('existing project keeps instructions and records failing/passing checks and later stale evidence',async()=>{
    await writeFile(join(project,'source.txt'),'before');await writeFile(join(project,'AGENTS.md'),'Keep existing project instructions.\n');
    await writeFile(join(project,'check.mjs'),"import {readFileSync} from 'node:fs'; process.exit(readFileSync('source.txt','utf8')==='after'?0:7);\n");
    const bootstrap=await input(base,'existing-bootstrap',{roots:[{id:'project',kind:'project',description:'Actual source and checker',include:['source.txt','check.mjs'],exclude:[]}],runners:[{id:'unit',command:process.execPath,args:['check.mjs'],evidenceKinds:['behavior_test'],timeoutMs:10000}]});
    const before=await readdir(project);const preflight=await command(['preflight','--data',data,'--input',bootstrap]);assert.equal(preflight.valid,true);assert.deepEqual(await readdir(project),before);
    await command(['init','--data',data,'--input',bootstrap]);
    assert.ok((await readFile(join(project,'AGENTS.md'),'utf8')).startsWith('Keep existing project instructions.'));
    const work=await input(base,'work',{goal:'Offline actual development',expectedOutcome:'source contains after',plannedPaths:['source.txt'],publicBehavior:false,subjects:[{id:'source',kind:'source_behavior',locator:null}],criteria:[{id:'AC',version:1,requirement:'source contains after',required:true,subjectIds:['source'],allowedEvidenceKinds:['behavior_test'],acceptedActorKinds:['system']}]});
    originalBaseline=(await command(['begin','--work','work:offline','--input',work])).baseline;
    assert.equal((await command(['check','--work','work:offline','--runner','unit'],2)).result,'failed');
    const outcome=await input(base,'outcome',{outcome:{summary:'Implemented and actually checked',incomplete:[],resumeNotes:[]}});
    assert.equal((await command(['finish','--work','work:offline','--input',outcome],2)).finished,false);
    await writeFile(join(project,'source.txt'),'after');await command(['scan','--work','work:offline']);
    assert.equal((await command(['check','--work','work:offline','--runner','unit'])).result,'passed');
    assert.equal((await command(['finish','--work','work:offline','--input',outcome])).finished,true);
    await writeFile(join(project,'source.txt'),'drift');
    const resume=await command(['resume','--work','work:offline'],2);assert.equal(resume.currentProcessReady,false);assert.deepEqual(resume.baseline,originalBaseline);
  });
  await t.test('empty project completes engineering preparation without inventing a machine runner',async()=>{
    const empty=join(base,'empty'),emptyData=join(base,'data-empty');await mkdir(empty);
    const emptyCall=(args,expected=0)=>call(['process',...args,'--project',empty],expected);
    const bootstrap=await input(base,'empty-bootstrap',{roots:[{id:'project',kind:'project',description:'Engineering preparation document',include:['README.md'],exclude:[]}],runners:[]});
    await emptyCall(['preflight','--data',emptyData,'--input',bootstrap]);assert.deepEqual(await readdir(empty),[]);
    await emptyCall(['init','--data',emptyData,'--input',bootstrap]);
    const work=await input(base,'empty-work',{goal:'Engineering preparation',expectedOutcome:'Document actual starting state',publicBehavior:false,plannedPaths:['README.md'],subjects:[{id:'doc',kind:'document',locator:{rootId:'project',path:'README.md'}}],criteria:[{id:'DOC',version:1,required:true,requirement:'Document actual project state',subjectIds:['doc'],allowedEvidenceKinds:['document_review'],acceptedActorKinds:['coding_agent']}]});
    await emptyCall(['begin','--work','work:setup','--input',work]);
    await writeFile(join(empty,'README.md'),'No business implementation or machine checker exists yet.\n');
    assert.equal(await readFile(join(empty,'README.md'),'utf8'),'No business implementation or machine checker exists yet.\n');
    const review=await input(base,'review',{kind:'document_review',result:'passed',summary:'Read the generated description and confirmed the empty project state.',method:'Read README.md and compare with actual project files.',findings:['The README explicitly says there is no business implementation or machine checker, matching the generated fixture.']});
    await emptyCall(['review','--work','work:setup','--input',review]);
    const outcome=await input(base,'empty-outcome',{outcome:{summary:'Preparation description checked; business development remains to start.',incomplete:[],resumeNotes:['Register the first actual checker when implemented.']}});
    assert.equal((await emptyCall(['finish','--work','work:setup','--input',outcome])).finished,true);
    const first=JSON.parse(await readFile(join(project,'.structure/identity.json'),'utf8')),second=JSON.parse(await readFile(join(empty,'.structure/identity.json'),'utf8'));assert.notEqual(first.project_id,second.project_id);
  });
  await t.test('installed workbench serves bundled assets and process evolution using only local HTTP',async()=>{
    await writeFile(join(project,'module.py'),'def run():\n    return 1\n');
    const child=spawn(process.execPath,[join(other,'offline.mjs'),'workbench','start','--project',project,'--data',data],{cwd:base,env:{...process.env,...env},windowsHide:true,stdio:['ignore','pipe','pipe']});
    let stdout='',stderr='';child.stdout.on('data',v=>stdout+=v);child.stderr.on('data',v=>stderr+=v);
    try {
      const deadline=Date.now()+20000;while(!stdout.includes('#cap=')&&Date.now()<deadline&&child.exitCode===null)await new Promise(resolve=>setTimeout(resolve,50));
      assert.match(stdout,/#cap=/,stderr);assert.match(stdout,/监听地址：0\.0\.0\.0:\d+/);const url=new URL(stdout.match(/http:\/\/127\.0\.0\.1:\d+\/#[^\s]+/)[0]);
      const html=await (await fetch(url.origin)).text();assert.match(html,/<html/);
      const asset=html.match(/src="([^"]+\.js)"/)[1];assert.equal((await fetch(url.origin+asset)).status,200);
      const headers={Authorization:'Bearer '+new URLSearchParams(url.hash.slice(1)).get('cap')};
      const bootstrap=await (await fetch(url.origin+'/api/standalone/bootstrap',{headers})).json();assert.equal(bootstrap.service.version,packageInfo.version);
      assert.ok(bootstrap.workspace.current.nodes.some(row=>JSON.stringify(row).includes('module.py')));
      const detail=await (await fetch(url.origin+'/api/standalone/process/work?id=work%3Aoffline',{headers})).json();assert.ok(detail.evolution);assert.deepEqual(detail.context.baseline,originalBaseline);
    } finally {
      if(process.platform==='win32')spawnSync('taskkill.exe',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});else child.kill('SIGTERM');
      if(child.exitCode===null)await new Promise(resolve=>{child.once('close',resolve);setTimeout(resolve,10000).unref();});
    }
  });
  await t.test('tampered installation is rejected while another extraction remains usable',async()=>{
    await writeFile(join(bundle,'app/src/development/cli.mjs'),'console.log("MUST_NOT_EXECUTE");');
    assert.equal((await call(['process','help'],1,bundle)).error,'BUNDLE_FILES_CHANGED');
    assert.equal((await call(['doctor'])).ready,true);
    const path=join(other,'installation.local.json'),config=JSON.parse(await readFile(path,'utf8'));config.runtimes.python.sha256='0'.repeat(64);await writeFile(path,JSON.stringify(config));
    const failed=await call(['doctor'],2);assert.equal(failed.ready,false);assert.equal(failed.checks.find(row=>row.name==='python').code,'RUNTIME_CHANGED');
  });
});
