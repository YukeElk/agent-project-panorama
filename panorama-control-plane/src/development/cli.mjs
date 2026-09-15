#!/usr/bin/env node
import { locateProject } from '../local-project.mjs';
import { initialize, loadDevelopment } from './config.mjs';
import { readJson } from './io.mjs';
import { begin } from './journal.mjs';
import { check, recordReview, importReceipt, inspect } from './service.mjs';
import { requireProcess } from '../process/errors.mjs';
import { resolve } from 'node:path';
import { exclusiveText } from './io.mjs';
import { readWork } from './journal.mjs';
import { loadReadContext, readCoreWork } from './read-context.mjs';
import { startTiming, timingResult, timed } from './timing.mjs';
import { exitStatus, summarize } from './presentation.mjs';
import { withConfigurationLease } from './configuration-state.mjs';

const help = `panorama-process <init|begin|scan|check|review|import|assess|finish|resume|draft|validate|preflight|config-plan|config-apply|config-status|config-recover|detach|catalog|matrix>
  --project PATH   project directory (default: current directory / owning project)
  --data PATH      external Panorama data root (shared with panorama start)
  --work ID        Structure work item ID (begin creates an ID if omitted)
  --input JSON     bootstrap / work / review / receipt / outcome document
  --runner ID      registered executable for check; never an arbitrary shell string
  --recipe ID      editable begin recipe (draft --for begin only); catalog lists IDs
  --python PATH    Python 3.11+ executable for init/preflight/config-plan
  --operation ID   original configuration operation for config-recover (pending by default)
  --for KIND       begin/check/review/finish request for draft or validate
  --output PATH    draft request file, exclusive creation; never overwritten
  --format MODE    json (default) or summary (compact JSON, same exit status)
  --timings MODE   off (default) or on; local phase times, worker measured separately

init --input bootstrap.json [--python PATH]       initialize or reconcile same configuration
begin --input work.json [--work ID]              capture original baseline and link/create work
scan --work ID                                  compare native writes; reevaluate applicability
check --work ID --runner ID [--input scope.json] execute a registered check and collect its result
review --work ID --input review.json             record a coding-agent review, never human authority
import --work ID --input receipt.json            preserve an external receipt without granting trust
assess --work ID [--input selection.json]        observe current evidence and store an assessment
finish --work ID --input outcome.json            require current evidence, then reconcile core done
resume [--work ID]                               recover committed results; never restart checks
draft --for KIND [--input intent.json]            assemble request and missing fields; no execution
validate --for KIND --input request.json         validate shape and registered scope; no execution
catalog                                         recipes, strength legend and versioned checker contracts
matrix --work ID                                read-only current rule applicability and declared coverage
check/review/finish drafts and validation require --work; check also requires --runner.
preflight [--input bootstrap.json] [--output bootstrap.json]   read-only onboarding and dependency probe
config-plan --input change.json [--output plan.json]          immutable revision candidate and differences
config-apply --input plan.json                               compare expected revision, switch or resume
config-status                                               active/history and pending recovery state
config-recover [--operation ID]                              continue the original switch, no business rerun
detach --input detach.json                                  stop onboarding guidance; retain evidence

JSON output. Exit 0: operation supported; 2: valid result needs work; 1: input/operation error.
Full examples and limitations: src/development/README.md
`;
let clock;
try {
  const args = process.argv.slice(2), command = args.shift();
  if (!command || ['help', '--help', '-h'].includes(command)) process.stdout.write(help);
  else {
    const maintenance=['preflight','config-plan','config-apply','config-status','config-recover','detach'].includes(command);
    requireProcess(maintenance || ['init', 'begin', 'scan', 'check', 'review', 'import', 'assess', 'finish', 'resume', 'draft', 'validate','catalog','matrix'].includes(command), 'COMMAND_UNKNOWN');
    const options = {};
    for (let i = 0; i < args.length; i += 2) {
      const key = args[i]; requireProcess(['--project', '--data', '--work', '--input', '--runner', '--python', '--for', '--output', '--format', '--timings','--operation','--recipe'].includes(key) && !Object.hasOwn(options, key) && typeof args[i + 1] === 'string' && !args[i + 1].startsWith('--'), 'CLI_ARGUMENT_INVALID');
      options[key] = args[i + 1];
    }
    const preparation = ['draft','validate'].includes(command), kind = preparation ? options['--for'] : command;
    requireProcess(['json','summary'].includes(options['--format'] ?? 'json') && ['on','off'].includes(options['--timings'] ?? 'off'), 'CLI_ARGUMENT_INVALID');
    if (options['--timings'] === 'on') clock = startTiming();
    requireProcess(!options['--python'] || ['init','preflight','config-plan'].includes(command), 'CLI_ARGUMENT_INVALID');
    requireProcess(!options['--operation'] || command === 'config-recover', 'CLI_ARGUMENT_INVALID');
    requireProcess(!maintenance || !options['--work'], 'CLI_ARGUMENT_INVALID');
    requireProcess(!['config-status','config-recover'].includes(command)||!options['--input'], 'CLI_ARGUMENT_INVALID');
    requireProcess(!options['--recipe'] || command === 'draft' && kind === 'begin', 'CLI_ARGUMENT_INVALID');
    requireProcess(!options['--runner'] || kind === 'check' || options['--recipe'], 'CLI_ARGUMENT_INVALID');
    requireProcess(!['catalog','matrix'].includes(command) || !options['--input'], 'CLI_ARGUMENT_INVALID');
    requireProcess(preparation ? ['begin','check','review','finish'].includes(kind) : !options['--for'], 'CLI_ARGUMENT_INVALID');
    requireProcess(!options['--output'] || ['draft','preflight','config-plan'].includes(command), 'CLI_ARGUMENT_INVALID');
    requireProcess(!['begin', 'review', 'import', 'finish'].includes(command) || options['--input'], 'INPUT_REQUIRED');
    requireProcess(command !== 'validate' || options['--input'], 'INPUT_REQUIRED');
    requireProcess(!['config-plan','config-apply','detach'].includes(command) || options['--input'], 'INPUT_REQUIRED');
    requireProcess(maintenance || ['init', 'begin', 'resume','catalog'].includes(kind) || options['--work'], 'WORK_REQUIRED');
    requireProcess(kind !== 'check' || options['--runner'], 'RUNNER_REQUIRED');
    const input = options['--input'] ? await readJson(options['--input']) : {};
    const locations = command === 'catalog' ? {} : await timed('location', () => locateProject({ project: options['--project'], data: options['--data'], readOnly: preparation || maintenance || command==='matrix',allowUninitialized:command==='preflight' }));
    let result;
    if (command === 'catalog') result=(await import('./standards.mjs')).standardCatalog();
    else if (command === 'matrix') {
      const ctx=await loadReadContext(locations,{workId:options['--work']});
      result=await withConfigurationLease(ctx,async()=>{const work=await readWork(ctx,options['--work']);await readCoreWork(ctx,work);return (await import('./standards.mjs')).standardMatrix(ctx,work);});
    } else if(maintenance) {
      if(command==='preflight')result=await (await import('./preflight.mjs')).preflight(locations,{input:options['--input']?input:null,python:options['--python']});
      else {
        const admin=await import('./configuration.mjs');
        if(command==='config-plan')result=await admin.planConfiguration(locations,{...input,...(options['--python']?{python:options['--python']}:{})});
        else if(command==='config-apply')result=await admin.applyConfiguration(locations,input);
        else if(command==='config-status')result=await admin.configurationStatus(locations);
        else if(command==='config-recover')result=await admin.recoverConfiguration(locations,options['--operation']);
        else result=await admin.detachConfiguration(locations,input);
      }
      if(options['--output']) {result.requestPath=resolve(options['--output']);await exclusiveText(result.requestPath,JSON.stringify(result.plan??result.draft,null,2)+'\n');}
    } else if (preparation) {
      const ctx = await timed('load', () => loadReadContext(locations,{workId:kind==='begin'?null:options['--work']}));
      const work = kind === 'begin' ? null : await readWork(ctx, options['--work']);
      if (work) await readCoreWork(ctx,work);
      const { validateRequest } = await import('./request-validation.mjs');
      const params = {work,runnerId:options['--runner'],recipeId:options['--recipe']};
      const content = command === 'draft' ? (await import('./drafts.mjs')).draftRequest(ctx,kind,input,params) : validateRequest(ctx,kind,input,params);
      result = {for:kind,workItemId:work?.workItemRef.id ?? null,...content,configuration:ctx.configuration,binding:{project:ctx.project.binding,configurationDigest:ctx.local.configurationDigest,workDefinitionDigest:work?.workItemRef.definitionDigest ?? null},validationScope:'Request shape and declared bindings only; no execution, freshness assessment, recovery or work transition.'};
      if (options['--output']) {
        result.requestPath = resolve(options['--output']);
        await exclusiveText(result.requestPath,JSON.stringify(result.request,null,2) + '\n');
      }
    } else if (command === 'init') result = await timed('load', () => initialize({ ...locations, input, python: options['--python'] }));
    else {
      const ctx = await timed('load', () => loadDevelopment(locations,{workId:command==='begin'?null:options['--work']})), id = options['--work'];
      result=command==='check'?await check(ctx,id,options['--runner'],input):await withConfigurationLease(ctx,async()=>{
        if (command === 'begin') return begin(ctx, input, id);
        if (command === 'review') return recordReview(ctx, id, input);
        if (command === 'import') return importReceipt(ctx, id, input);
        return inspect(ctx, id, command, input);
      });
      result.configuration=ctx.configuration;
    }
    if (clock) result.timing = timingResult(clock);
    process.exitCode = exitStatus(result);
    process.stdout.write(JSON.stringify(options['--format'] === 'summary' ? summarize(command,result,{projectRoot:locations.projectRoot,workId:options['--work'],runnerId:options['--runner']}) : result, null, 2) + '\n');
  }
} catch (error) {
  // Raw subprocess output and local environment values never cross this boundary.
  process.stderr.write(JSON.stringify({ error: error.code ?? 'LOCAL_OPERATION_FAILED', details: error.details ?? {}, message: error.code ? error.message : 'Local operation failed; inspect the local configuration and retained records.', ...(clock ? {timing:timingResult(clock)} : {}) }) + '\n');
  process.exitCode = 1;
}
