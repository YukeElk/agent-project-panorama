export function exitStatus(result) {
  return result.ready === false || result.valid === false || result.finished === false || result.currentProcessReady === false || result.assessment && (!result.assessment.overall.processReady || result.policy?.decision === 'deny' || !result.scopeComplete) || result.result && result.result !== 'passed' || result.policy?.decision === 'deny' || result.scopeComplete === false || result.phase === 'legacy_read_only' ? 2 : 0;
}
export function summarize(command, result, invocation = {}) {
  if(['preflight','config-plan','config-apply','config-status','config-recover','detach','catalog','matrix'].includes(command))return {command,operationExitCode:exitStatus(result),...result};
  const assessment = result.assessment;
  const execution = result.execution ?? result.recovery?.find(item => item.executionId === result.executionId) ?? null;
  const recordedWorkId = result.workItemId ?? (['check','review','import','assess','finish','scan','resume'].includes(command) ? invocation.workId : null);
  const nextActions = [];
  const base = invocation.projectRoot ? ['--project',invocation.projectRoot] : [];
  if (recordedWorkId) nextActions.push({action:'resume',args:['resume',...base,'--work',recordedWorkId,'--format','summary'],purpose:'读取原工作及当前缺口；不会重跑业务检查。'});
  if (result.requestPath) nextActions.push({action:'validate',args:['validate','--for',result.for,...base,...(result.workItemId ? ['--work',result.workItemId] : []),...(invocation.runnerId ? ['--runner',invocation.runnerId] : []),'--input',result.requestPath],purpose:'补齐或回读草稿后校验请求；不执行任务。'});
  const summary = {
    formatVersion:'panorama.command-summary.v1',command,operationExitCode:exitStatus(result),
    workItemId:result.workItemId ?? invocation.workId ?? null,phase:result.phase ?? null,coreState:result.coreState ?? null,
    finished:result.finished ?? null,currentProcessReady:result.currentProcessReady ?? null,
    processEvidenceReady:assessment?.overall.processReady ?? null,
    goal:result.goal ?? null,expectedOutcome:result.expectedOutcome ?? null,
    baseline:result.baseline ? {id:result.baseline.id,state:result.baseline.state,capturedAt:result.baseline.capturedAt,scope:result.baseline.scope,unknowns:result.baseline.unknowns} : null,
    scopeComplete:result.scopeComplete ?? null,plannedPaths:result.plannedPaths ?? [],changedPaths:result.changedPaths ?? [],
    policy:result.policy ?? null,applicability:result.applicability ?? [],
    criteria:assessment?.criteria ?? result.criteria ?? [],rules:assessment?.rules ?? [],overall:assessment?.overall ?? null,
    criterionDefinitions:assessment ? result.criteria ?? [] : [],
    configuration:result.configuration??null,
    outcome:result.outcome ?? null,selection:{receiptIds:result.selectedReceiptIds ?? [],reason:result.selectionReason ?? null},
    receiptId:result.receiptId ?? null,executionId:result.executionId ?? null,result:result.result ?? null,execution,
    runDirectory:result.runDirectory ?? null,recovery:result.recovery ?? [],
    inputGaps:result.inputGaps ?? [],next:result.next ?? null,nextActions,
  };
  if (result.works) {summary.works=result.works;summary.coreWorks=result.coreWorks;}
  if (result.initialized) {summary.initialized=true;summary.binding=result.binding;summary.guidance=result.guidance;summary.modules=result.modules;summary.projectRoot=result.projectRoot;summary.dataRoot=result.dataRoot;}
  if (result.legacy) summary.legacy = result.legacy;
  if (result.valid !== undefined) {
    Object.assign(summary,{for:result.for,valid:result.valid,errors:result.errors,warnings:result.warnings,suggestions:result.suggestions,
      requestPath:result.requestPath ?? null,defaults:result.defaults ?? [],binding:result.binding,validationScope:result.validationScope});
    // Preserve a draft when no file was requested; otherwise its path is enough.
    if (!result.requestPath && result.request) summary.request=result.request;
  }
  for (const field of ['source','locallyAttested','actorKind','reason','idempotent','timing','recipe']) if (result[field] !== undefined) summary[field]=result[field];
  return summary;
}
