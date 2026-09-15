import { checkerCatalog, checkerFor, validateCheckCoverage } from './check-contracts.mjs';
import { captureInputs, currentFacts } from './capture.mjs';
import { evaluateApplicability } from '../process/applicability.mjs';
import { sha256 } from '../process/json.mjs';
import { requireProcess } from '../process/errors.mjs';

export const recipes = [
  {id:'document-maintenance',version:'1',title:'文档维护',subjectKind:'source_behavior',reviewKind:'document_review',impact:'mechanical',suggestedRequirement:'说明与本次实际变更一致，链接和示例经过核对。',notes:['公开路径或接口承诺变化仍触发行为证据；配方不设置 publicBehavior=false。','说明审阅不自动产生已评审记录。']},
  {id:'public-behavior-fix',version:'1',title:'公开行为修复',subjectKind:'source_behavior',reviewKind:null,impact:'interface',suggestedRequirement:null,notes:['补充具体输入、预期行为及回归反例；登记支持 interface_test 的检查器。','报告实际失败与修复后的重试；不得删除失败历史。']},
  {id:'retained-artifact-delivery',version:'1',title:'留存产物交付',subjectKind:'retained_artifact',reviewKind:null,impact:'internal',suggestedRequirement:null,notes:['绑定实际留存文件，不能重建替代对象。','完整性与视觉/语义质量分开写验收项；需要人工来源时保留 human 要求。']},
];
export function standardCatalog() {
  return {formatVersion:'panorama.standard-catalog.v1',recipes, ...{checkerContracts:checkerCatalog().contracts},
    strengthLegend:{required:'必备',conditional:'条件必备',recommended:'推荐',optional:'可选'},
    limitations:['目录和配方提供可编辑建议，不产生业务通过、评审事实或规则豁免。','现有规则包仍为六条基础和四条条件规则；未新增行业标准认证。']};
}
export function recipeSeed(ctx, id, seed, runnerId) {
  const recipe = recipes.find(recipe => recipe.id === id); requireProcess(recipe, 'RECIPE_NOT_REGISTERED');
  const runner = ctx.local.bootstrap.runners.find(runner => runner.id === runnerId), contract = checkerFor(ctx,runner);
  const property = contract?.claims.length === 1 ? contract.claims[0] : null;
  const defaultSubject = {id:'subject',kind:recipe.subjectKind,locator:null,...(ctx.local.bootstrap.formatVersion ? {format:contract?.formats.length === 1 ? contract.formats[0] : recipe.reviewKind ? 'markdown' : null,scopeId:runner?.scopeId ?? null} : {})};
  const defaultCriterion = {id:'AC',requirement:recipe.suggestedRequirement ?? property?.property ?? null,
    ...(recipe.reviewKind ? {reviewKind:recipe.reviewKind} : {runnerId:runnerId ?? null}),
    ...(ctx.local.bootstrap.formatVersion ? {claims:property ? [property.id] : []} : {})};
  return {seed:{kind:id === 'public-behavior-fix' ? 'bugfix' : 'feature',impact:recipe.impact,subjects:[defaultSubject],criteria:[defaultCriterion],...seed},
    recipe:{id:recipe.id,version:recipe.version,notes:recipe.notes,source:'Editable suggestions from the named recipe and explicitly selected runner; no execution.'}};
}
export async function standardMatrix(ctx, work) {
  const inputs = await captureInputs(ctx,work), observed = currentFacts(ctx,work,inputs);
  const facts = [...ctx.project.features,...observed.facts];
  const rules = ctx.config.rules.map(rule => ({ruleId:rule.id,ruleVersion:rule.version,title:rule.title ?? rule.description ?? rule.id,
    strength:rule.strength,when:rule.when,evaluator:rule.evaluator,evidenceRequirements:rule.evidenceRequirements,
    applicability:evaluateApplicability(rule.when,{facts,paths:observed.paths})}));
  const coverage = work.context.criteria.map(criterion => ({criterionId:criterion.id,requirement:criterion.requirement,required:criterion.required,
    claims:work.checkBindings?.criteria.find(row => row.criterionId === criterion.id)?.claims ?? null,
    runners:ctx.local.bootstrap.runners.filter(runner => criterion.allowedEvidenceKinds.some(kind => runner.evidenceKinds.includes(kind))).map(runner => {
      try { const contract=checkerFor(ctx,runner);return {runnerId:runner.id,status:work.checkBindings ? 'declared_coverage' : 'legacy_evidence_kinds_only',binding:validateCheckCoverage(ctx,work,runner,{criterionIds:[criterion.id],subjectIds:criterion.subjectIds}),contract:contract ? {id:contract.id,version:contract.version,claims:contract.claims,notChecked:contract.notChecked,limits:contract.limits} : null,error:null}; }
      catch(error) { return {runnerId:runner.id,status:'uncovered',binding:null,error:error.code ?? 'CHECK_COVERAGE_UNKNOWN'}; }
    }), review:{kinds:criterion.allowedEvidenceKinds.filter(kind => ['document_review','visual_review'].includes(kind)),acceptedActorKinds:criterion.acceptedActorKinds}}));
  return {formatVersion:'panorama.standard-matrix.v1',workItemId:work.workItemRef.id,configuration:ctx.configuration,rulePacks:ctx.project.rulePacks,
    inputObservationDigest:sha256(inputs.map(set => [set.id,set.snapshot.digest])),scopeComplete:inputs.every(set => set.complete),changedPaths:observed.paths.flatMap(row => row.paths),facts,rules,coverage,
    checkBindings:work.checkBindings ?? null,checkBindingsDigest:work.checkBindingsDigest ?? null,
    limitations:['当前观察与适用性/声明覆盖矩阵；不执行检查、不保存 Receipt 或 Assessment、不迁移工作状态。','declared_coverage 不表示证据已经通过；不完整依赖采用更宽范围，未声明服务仍未冻结。']};
}
