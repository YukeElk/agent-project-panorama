import { processValue } from '../process/json.mjs';
import { validateRequest } from './request-validation.mjs';
import { BOOTSTRAP_V2, BINDINGS_V1 } from './check-contracts.mjs';
import { recipeSeed } from './standards.mjs';

const own = (object,key) => Object.hasOwn(object,key);
export function draftRequest(ctx, kind, seed = {}, options = {}) {
  seed = processValue(seed);
  const defaults = [], issues = [];
  if (!seed || typeof seed !== 'object' || Array.isArray(seed)) return {request:seed,defaults,valid:false,errors:[{path:'',code:'REQUEST_OBJECT_REQUIRED',message:'草稿输入必须是对象。'}],warnings:[],suggestions:[]};
  let request = structuredClone(seed);
  let recipe = null;
  if (options.recipeId) { const prepared = recipeSeed(ctx,options.recipeId,request,options.runnerId); request=prepared.seed; recipe=prepared.recipe; }
  function fill(object,key,value,path,source) {
    if (!own(object,key)) { object[key] = value; defaults.push({path:path + '/' + key,value:structuredClone(value),source}); }
  }
  const missing = (object,key,path) => fill(object,key,null,path,'requires_agent_input');
  if (kind === 'begin') {
    const scoped = ctx.local.bootstrap.formatVersion === BOOTSTRAP_V2;
    const subjectMetadata = [], criterionMetadata = [];
    for (const field of ['goal','expectedOutcome','plannedPaths','subjects','criteria']) missing(request,field,'');
    fill(request,'moduleIds',ctx.project.moduleBindings.map(item => item.moduleId),'','registered_project_modules');
    fill(request,'kind','feature','','existing_begin_default');
    fill(request,'impact','internal','','existing_begin_default');
    fill(request,'publicBehavior',null,'','unknown_until_declared');
    if (Array.isArray(request.subjects)) request.subjects.forEach((subject,i) => {
      if (!subject || typeof subject !== 'object' || Array.isArray(subject)) return;
      const path = '/subjects/' + i;
      fill(subject,'id','subject-' + String(i + 1).padStart(2,'0'),path,'generated_local_identifier');
      missing(subject,'kind',path);
      fill(subject,'locator',null,path,'no_file_binding_supplied');
      if (scoped) { subjectMetadata.push({subjectId:subject.id,format:subject.format ?? null,scopeId:subject.scopeId ?? null}); delete subject.format; delete subject.scopeId; }
    });
    if (Array.isArray(request.criteria)) request.criteria.forEach((criterion,i) => {
      if (!criterion || typeof criterion !== 'object' || Array.isArray(criterion)) return;
      const path = '/criteria/' + i;
      const {runnerId,reviewKind} = criterion;
      delete criterion.runnerId; delete criterion.reviewKind;
      if (runnerId !== undefined && reviewKind !== undefined) issues.push({path,code:'CRITERION_SOURCE_AMBIGUOUS',message:'runnerId 与 reviewKind 只能选择一个。'});
      const runner = ctx.local.bootstrap.runners.find(item => item.id === runnerId);
      if (runnerId !== undefined && !runner) issues.push({path:path + '/runnerId',code:'RUNNER_NOT_REGISTERED',message:'指定 runner 未登记。'});
      if (reviewKind !== undefined && !['document_review','visual_review'].includes(reviewKind)) issues.push({path:path + '/reviewKind',code:'REVIEW_KIND_INVALID',message:'评审类型不受支持。'});
      fill(criterion,'id','AC-' + String(i + 1).padStart(2,'0'),path,'generated_local_identifier');
      if (scoped) { criterionMetadata.push({criterionId:criterion.id,claims:criterion.claims ?? []}); delete criterion.claims; }
      fill(criterion,'version',1,path,'initial_criterion_version');
      fill(criterion,'required',true,path,'required_by_default');
      missing(criterion,'requirement',path);
      if (Array.isArray(request.subjects) && request.subjects.length === 1 && request.subjects[0]?.id) fill(criterion,'subjectIds',[request.subjects[0].id],path,'single_declared_subject');
      else missing(criterion,'subjectIds',path);
      if (runner) {
        fill(criterion,'allowedEvidenceKinds',[...runner.evidenceKinds],path,'registered_runner:' + runner.id);
        fill(criterion,'acceptedActorKinds',['system'],path,'local_runner_source');
      } else if (['document_review','visual_review'].includes(reviewKind)) {
        fill(criterion,'allowedEvidenceKinds',[reviewKind],path,'explicit_review_kind');
        fill(criterion,'acceptedActorKinds',['coding_agent'],path,'local_review_source_not_human');
      } else {
        missing(criterion,'allowedEvidenceKinds',path); missing(criterion,'acceptedActorKinds',path);
      }
    });
    if (scoped) fill(request,'checkBindings',{formatVersion:BINDINGS_V1,subjects:subjectMetadata,criteria:criterionMetadata},'','explicit_compact_subject_formats_scopes_and_criterion_claims');
  } else if (kind === 'check' || kind === 'review') {
    if (kind === 'review') {
      for (const field of ['kind','result','summary','method','findings']) missing(request,field,'');
      fill(request,'limitations',[],'','no_additional_limitations_supplied');
    }
    const kinds = kind === 'check' ? ctx.local.bootstrap.runners.find(item => item.id === options.runnerId)?.evidenceKinds ?? [] : [request.kind];
    const criteria = options.work?.context?.criteria ?? [];
    const compatible = criteria.filter(criterion => criterion.allowedEvidenceKinds.some(value => kinds.includes(value)));
    if (compatible.length === 1) fill(request,'criterionIds',[compatible[0].id],'','single_compatible_criterion');
    else missing(request,'criterionIds','');
    if (Array.isArray(request.criterionIds) && request.criterionIds.every(id => criteria.some(item => item.id === id))) fill(request,'subjectIds',[...new Set(request.criterionIds.flatMap(id => criteria.find(item => item.id === id).subjectIds))],'','explicit_criteria_subjects');
    else missing(request,'subjectIds','');
  } else if (kind === 'finish') {
    if (!own(request,'outcome')) {
      request.outcome = {};
      for (const key of ['summary','incomplete','resumeNotes']) if (own(request,key)) { request.outcome[key] = request[key]; delete request[key]; }
    }
    if (request.outcome && typeof request.outcome === 'object' && !Array.isArray(request.outcome)) {
      for (const key of ['summary','incomplete','resumeNotes']) missing(request.outcome,key,'/outcome');
    }
  }
  const result = validateRequest(ctx,kind,request,options);
  const errors = [...issues,...result.errors];
  // Even an incomplete draft should list compatible choices without selecting all.
  let suggestions = result.suggestions;
  if (['check','review'].includes(kind) && options.work?.context) {
    const kinds = kind === 'check' ? ctx.local.bootstrap.runners.find(item => item.id === options.runnerId)?.evidenceKinds ?? [] : [request.kind];
    suggestions = options.work.context.criteria.filter(item => item.allowedEvidenceKinds.some(value => kinds.includes(value))).map(({id,requirement,subjectIds}) => ({criterionId:id,requirement,subjectIds}));
  }
  return {request,defaults,...result,errors,suggestions,valid:errors.length === 0,...(recipe ? {recipe} : {})};
}
