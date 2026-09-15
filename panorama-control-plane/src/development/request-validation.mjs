import { readFileSync } from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { processValue, sameValue } from '../process/json.mjs';
import { matchPath } from '../process/applicability.mjs';
import { validateBindings, validateCheckCoverage } from './check-contracts.mjs';

const common = JSON.parse(readFileSync(new URL('../../contracts/process/schemas/common.schema.json', import.meta.url), 'utf8'));
const defs = common.$defs, context = defs.context.properties;
const object = (properties, required = Object.keys(properties)) => ({type:'object',additionalProperties:false,properties,required});
const ids = {type:'array',items:defs.id,minItems:1,maxItems:1000,uniqueItems:true};
const texts = {type:'array',items:defs.text,maxItems:1000};
const selection = object({receiptIds:ids,reason:defs.text});
const subject = object(Object.fromEntries(['id','kind','locator'].map(key => [key,defs.subject.properties[key]])));
const schemas = {
  begin: object({goal:context.goal,expectedOutcome:context.expectedOutcome,moduleIds:context.moduleIds,
    plannedPaths:{...context.plannedPaths,minItems:1},criteria:context.criteria,subjects:{type:'array',items:subject,minItems:1,maxItems:100},
    kind:{enum:['feature','bugfix','spike','refactor','migration','incident']},impact:{enum:['mechanical','internal','behavior','interface','boundary','architecture']},publicBehavior:{type:['boolean','null']},unknowns:context.unknowns,decisionRefs:context.decisionRefs,
    checkBindings:{type:'object'},candidateBinding:{anyOf:[{type:'null'},object({candidateId:defs.id,candidateVersion:{type:'integer',minimum:0,maximum:Number.MAX_SAFE_INTEGER},baselineSnapshotId:defs.id})]}},['goal','expectedOutcome','plannedPaths','criteria','subjects']),
  check: object({subjectIds:ids,criterionIds:ids},[]),
  review: object({kind:{enum:['document_review','visual_review']},result:{enum:['passed','failed']},subjectIds:ids,criterionIds:ids,summary:defs.text,method:defs.text,findings:{...texts,minItems:1},limitations:texts},['kind','result','summary','method','findings']),
  finish: object({outcome:context.outcome,selection:{anyOf:[{type:'null'},selection]},decisionRefs:context.decisionRefs},['outcome']),
};
const ajv = new Ajv2020({strictTypes:false,allErrors:true,ownProperties:true});
addFormats(ajv); ajv.addSchema(common);
const validators = new Map(Object.entries(schemas).map(([key,value]) => [key,ajv.compile(value)]));
const escape = value => String(value).replaceAll('~','~0').replaceAll('/','~1');
export const requestKinds = Object.keys(schemas);

export function validateRequest(ctx, kind, input, {work = null,runnerId = null} = {}) {
  const request = processValue(input), errors = [], warnings = [], suggestions = [];
  const add = (path,code,message) => errors.push({path,code,message});
  const warn = (path,code,message) => warnings.push({path,code,message});
  const validate = validators.get(kind);
  if (!validate) return {valid:false,errors:[{path:'',code:'REQUEST_KIND_INVALID',message:'Unsupported request kind.'}],warnings,suggestions};
  if (!validate(request)) {
    for (const issue of (validate.errors ?? []).slice(0,64)) {
      const suffix = issue.params.missingProperty ?? issue.params.additionalProperty;
      add(issue.instancePath + (suffix ? '/' + escape(suffix) : ''),'REQUEST_' + issue.keyword.toUpperCase(),'字段不符合请求合同：' + (issue.message ?? issue.keyword));
    }
    return {valid:false,errors,warnings,suggestions};
  }
  if (kind === 'begin') {
    try { validateBindings(ctx,request.checkBindings,{subjects:request.subjects,context:{criteria:request.criteria}}); }
    catch (error) { add('/checkBindings',error.code ?? 'CHECK_BINDINGS_INVALID','检查绑定不完整或未登记：' + (error.code ?? 'invalid')); }
    const subjects = new Map(request.subjects.map(item => [item.id,item]));
    if (subjects.size !== request.subjects.length) add('/subjects','SUBJECT_ID_DUPLICATE','对象 ID 必须唯一。');
    if (new Set(request.criteria.map(item => item.id)).size !== request.criteria.length) add('/criteria','CRITERION_ID_DUPLICATE','验收项 ID 必须唯一。');
    for (const [i, id] of (request.moduleIds ?? ctx.project.moduleBindings.map(module => module.moduleId)).entries()) {
      if (!ctx.project.moduleBindings.some(module => module.moduleId === id)) add('/moduleIds/' + i,'MODULE_NOT_REGISTERED','模块尚未登记。');
    }
    request.subjects.forEach((item,i) => {
      if (item.id === 'process' || item.kind === 'process') add('/subjects/' + i,'SUBJECT_RESERVED','process 由采集器建立。');
      const root = item.locator && ctx.local.bootstrap.roots.find(root => root.id === item.locator.rootId);
      if (item.locator && !root) add('/subjects/' + i + '/locator/rootId','ROOT_NOT_REGISTERED','对象根尚未登记。');
      if (root && (!root.allow.some(pattern => matchPath(pattern,item.locator.path)) || root.selector.exclude.some(pattern => matchPath(pattern,item.locator.path)))) add('/subjects/' + i + '/locator/path','SUBJECT_OUTSIDE_INPUT_SCOPE','对象路径不在登记读取范围中。');
      if (['retained_artifact','fresh_build'].includes(item.kind) && !item.locator) add('/subjects/' + i + '/locator','ARTIFACT_LOCATOR_REQUIRED','产物必须绑定实际文件位置。');
      if (root && item.kind === 'fresh_build' && !root.observeOnly) add('/subjects/' + i + '/locator','FRESH_BUILD_ROOT_REQUIRED','新建产物需要 observeOnly artifact 根。');
      if (root && item.kind === 'retained_artifact' && root.observeOnly) add('/subjects/' + i + '/locator','RETAINED_INPUT_REQUIRED','留存产物必须参与输入采集。');
    });
    request.criteria.forEach((criterion,i) => {
      criterion.subjectIds.forEach((id,j) => { if (!subjects.has(id)) add(`/criteria/${i}/subjectIds/${j}`,'SUBJECT_NOT_DEFINED','验收项引用的对象没有定义。'); });
      if (sameValue(criterion.acceptedActorKinds,['human'])) warn(`/criteria/${i}/acceptedActorKinds`,'HUMAN_SOURCE_NOT_AVAILABLE','本地机器或 Coding Agent 评审不能建立人工来源。');
    });
    if (request.publicBehavior == null) warn('/publicBehavior','PUBLIC_BEHAVIOR_UNKNOWN','公开行为影响保留未知；文档类型不提供自动豁免。');
    if (request.plannedPaths.some(path => ctx.local.bootstrap.publicPaths.some(pattern => matchPath(pattern,path))) || ['interface','boundary','architecture'].includes(request.impact)) warn('/plannedPaths','PUBLIC_BEHAVIOR_EXPECTED','预计涉及公开路径／边界；实际扫描仍会计算行为及文档要求。');
  } else {
    if (!work?.context) add('/work','WORK_REQUIRED','需要当前登记工作项。');
    else if (kind === 'finish') {
      if (request.selection) request.selection.receiptIds.forEach((id,i) => { if (!work.receipts.some(receipt => receipt.id === id)) add('/selection/receiptIds/' + i,'RECEIPT_NOT_REGISTERED','选择包含未登记证据。'); });
      if (request.outcome.incomplete.length) warn('/outcome/incomplete','INCOMPLETE_REMAINS','请求可记录，但未完成事项会阻止 finish。');
      warn('/outcome','FINISH_NOT_ASSESSED','仅校验请求；当前证据、活动执行和 core policy 仍由 finish 复查。');
    } else {
      const runner = kind === 'check' && ctx.local.bootstrap.runners.find(item => item.id === runnerId);
      if (kind === 'check' && !runner) add('/runner','RUNNER_NOT_REGISTERED','需要已登记 runner。');
      const kinds = kind === 'check' ? runner?.evidenceKinds ?? [] : [request.kind];
      const compatible = work.context.criteria.filter(criterion => criterion.allowedEvidenceKinds.some(value => kinds.includes(value)));
      suggestions.push(...compatible.map(({id,requirement,subjectIds}) => ({criterionId:id,requirement,subjectIds})));
      const criterionIds = request.criterionIds ?? compatible.map(item => item.id);
      const subjectIds = request.subjectIds ?? [...new Set(compatible.filter(item => criterionIds.includes(item.id)).flatMap(item => item.subjectIds))];
      if (!criterionIds.length) add('/criterionIds','CHECK_CRITERIA_REQUIRED','没有兼容的验收项。');
      if (!request.criterionIds && compatible.length > 1) warn('/criterionIds','IMPLICIT_MULTIPLE_CRITERIA','旧入口会选择全部兼容验收项；请按实际检查覆盖显式选择。');
      for (const [i,id] of criterionIds.entries()) {
        const criterion = compatible.find(item => item.id === id);
        if (!criterion) add('/criterionIds/' + i,'CHECK_CRITERIA_INVALID','验收项不存在或证据类型不匹配。');
        else {
          if (!criterion.subjectIds.every(id => subjectIds.includes(id))) add('/subjectIds','CHECK_CRITERION_SCOPE_INCOMPLETE','对象列表未覆盖所选验收项。');
          if (!criterion.acceptedActorKinds.includes(kind === 'check' ? 'system' : 'coding_agent')) warn('/criterionIds/' + i,'ACTOR_CANNOT_SATISFY','本次来源不能满足此项；记录执行不等于通过验收。');
        }
      }
      for (const [i,id] of subjectIds.entries()) if (!work.subjects.some(item => item.id === id)) add('/subjectIds/' + i,'CHECK_SUBJECTS_INVALID','对象没有在此工作项登记。');
      if (runner) {
        try { validateCheckCoverage(ctx,work,runner,{criterionIds,subjectIds}); }
        catch (error) { add('/criterionIds',error.code ?? 'CHECK_COVERAGE_INVALID','检查器合同无法覆盖所选验收：' + (error.code ?? 'invalid')); }
        const bound = [...runner.args.join('\n').matchAll(/\{subject:([A-Za-z0-9._:-]+)\}/g)].map(match => match[1]);
        for (const id of bound) if (!subjectIds.includes(id) || !work.subjects.find(item => item.id === id)?.locator) add('/subjectIds','RUNNER_SUBJECT_ARGUMENT_INVALID','命令中的对象参数未被本次选择或缺少文件位置。');
        for (const id of subjectIds) if (work.subjects.some(item => item.id === id && ['retained_artifact','fresh_build'].includes(item.kind)) && !bound.includes(id)) add('/subjectIds','RUNNER_OBJECT_NOT_BOUND','产物检查命令必须绑定本次实际对象。');
      }
    }
  }
  return {valid:errors.length === 0,errors,warnings,suggestions};
}
