"""Schema, cross-reference, and engineering-rule validation for Panorama data."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from panorama_cli import ChineseArgumentParser
from panorama_io import PanoramaIOError, extract_data
from schema_support import SCHEMA_BY_VERSION, schema_for_data


ENTITY_REF_TYPES = {
    "project",
    "intent",
    "requirement",
    "architecture_version",
    "baseline",
    "layer",
    "module",
    "connection",
    "transition",
    "stage",
    "work_item",
    "release",
    "deployment",
    "resource",
    "decision",
    "risk",
    "acceptance",
    "gate",
    "reference",
    "review",
    "change",
    "update_batch",
    "observation_batch",
    "current_architecture_snapshot",
    "standard_assessment",
}

SCOPE_TYPE_MAP = {
    "project": "project",
    "stage": "stage",
    "module": "module",
    "requirement": "requirement",
    "release": "release",
    "deployment": "deployment",
}

SUPPORTED_SCHEMA_VERSIONS = set(SCHEMA_BY_VERSION)
SUPPORTED_TEMPLATE_VERSIONS = {"0.1.0", "0.1.1"}


class ValidationRuntimeError(RuntimeError):
    """An input, marker, schema, or dependency failure (CLI exit code 2)."""


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    code: str
    message: str
    path: str = ""
    severity: str = "warning"
    related_entities: tuple[dict[str, str], ...] = ()

    def render(self) -> str:
        location = f" [{self.path}]" if self.path else ""
        level_label = {"ERROR": "错误", "WARNING": "警告", "INFO": "信息"}.get(
            self.level, self.level
        )
        severity = (
            f" [严重程度={self.severity}]"
            if self.severity not in {"error", self.level.lower()}
            else ""
        )
        return f"{level_label} {self.code}{severity}{location}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "relatedEntities": [dict(item) for item in self.related_entities],
        }


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(
        self,
        level: str,
        code: str,
        message: str,
        path: str = "",
        *,
        severity: str | None = None,
        related_entities: Iterable[dict[str, str]] = (),
    ) -> None:
        default_severity = {"ERROR": "error", "WARNING": "warning", "INFO": "info"}
        self.issues.append(
            ValidationIssue(
                level,
                code,
                message,
                path,
                severity or default_severity.get(level, "warning"),
                tuple(dict(item) for item in related_entities),
            )
        )

    def error(
        self,
        code: str,
        message: str,
        path: str = "",
        *,
        related_entities: Iterable[dict[str, str]] = (),
    ) -> None:
        self.add(
            "ERROR", code, message, path, related_entities=related_entities
        )

    def warning(
        self,
        code: str,
        message: str,
        path: str = "",
        *,
        severity: str = "warning",
        related_entities: Iterable[dict[str, str]] = (),
    ) -> None:
        self.add(
            "WARNING",
            code,
            message,
            path,
            severity=severity,
            related_entities=related_entities,
        )

    def high(
        self,
        code: str,
        message: str,
        path: str = "",
        *,
        related_entities: Iterable[dict[str, str]] = (),
    ) -> None:
        self.warning(
            code,
            message,
            path,
            severity="high",
            related_entities=related_entities,
        )

    def info(
        self,
        code: str,
        message: str,
        path: str = "",
        *,
        related_entities: Iterable[dict[str, str]] = (),
    ) -> None:
        self.add("INFO", code, message, path, related_entities=related_entities)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "WARNING"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": not self.errors,
            "counts": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "infos": sum(issue.level == "INFO" for issue in self.issues),
                "high": sum(issue.severity == "high" for issue in self.issues),
                "critical": sum(issue.severity == "critical" for issue in self.issues),
            },
            "findings": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class EntityRegistry:
    by_type: dict[str, dict[str, dict[str, Any]]]
    all_ids: dict[str, str]

    def has(self, entity_type: str, entity_id: str | None) -> bool:
        return bool(entity_id) and entity_id in self.by_type.get(entity_type, {})


def _items(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key, [])
    return value if isinstance(value, list) else []


def _architecture_items(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    architecture = data.get("architecture", {})
    value = architecture.get(key, []) if isinstance(architecture, dict) else []
    return value if isinstance(value, list) else []


def _path(parts: Iterable[Any]) -> str:
    return "/" + "/".join(str(part) for part in parts)


def build_registry(data: dict[str, Any], report: ValidationReport) -> EntityRegistry:
    collections: dict[str, list[dict[str, Any]]] = {
        "project": [data.get("project", {})],
        "requirement": _items(data, "requirements"),
        "baseline": _architecture_items(data, "baselines"),
        "layer": _architecture_items(data, "layers"),
        "architecture_version": _architecture_items(data, "versions"),
        "module": _architecture_items(data, "modules"),
        "connection": _architecture_items(data, "connections"),
        "transition": _architecture_items(data, "transitions"),
        "stage": _items(data, "stages"),
        "work_item": _items(data, "workItems"),
        "release": _items(data, "releases"),
        "deployment": _items(data, "deployments"),
        "resource": _items(data, "resources"),
        "decision": _items(data, "decisions"),
        "risk": _items(data, "risks"),
        "acceptance": _items(data, "acceptanceCriteria"),
        "gate": _items(data, "gates"),
        "reference": _items(data, "references"),
        "review": _items(data, "reviews"),
        "change": _items(data, "changes"),
        "update_batch": _items(data, "updateBatches"),
        "fact_provenance": _items(data, "factProvenance"),
        "observation_batch": _items(data, "observationBatches"),
        "current_architecture_snapshot": _items(data, "currentArchitectureSnapshots"),
        "standard_assessment": _items(data, "standardAssessments"),
        "decision_option": [
            option
            for decision in _items(data, "decisions")
            for option in decision.get("options", [])
            if isinstance(option, dict)
        ],
        "next_focus": [
            option
            for option in data.get("guidance", {}).get("options", [])
            if isinstance(option, dict)
        ]
        + [
            record.get("option")
            for record in data.get("guidance", {})
            .get("extensions", {})
            .get("historicalOptions", [])
            if isinstance(record, dict) and isinstance(record.get("option"), dict)
        ],
    }

    by_type: dict[str, dict[str, dict[str, Any]]] = {}
    all_ids: dict[str, str] = {}
    for entity_type, entities in collections.items():
        typed: dict[str, dict[str, Any]] = {}
        for entity in entities:
            entity_id = entity.get("id") if isinstance(entity, dict) else None
            if not isinstance(entity_id, str) or not entity_id:
                continue
            if entity_id in all_ids:
                report.error(
                    "DUPLICATE_ID",
                    f"ID {entity_id} 同时被 {all_ids[entity_id]} 与 {entity_type} 使用。",
                )
            else:
                all_ids[entity_id] = entity_type
            typed[entity_id] = entity
        by_type[entity_type] = typed

    # Schema v0.1 permits an intent entityRef but Intent has no id.  Reference
    # data consistently uses the Project id as its stable Intent alias.
    project_id = data.get("project", {}).get("id")
    by_type["intent"] = (
        {project_id: data.get("intent", {})} if isinstance(project_id, str) else {}
    )
    return EntityRegistry(by_type=by_type, all_ids=all_ids)


def validate_schema(
    data: dict[str, Any], schema_path: str | Path, report: ValidationReport
) -> None:
    try:
        import jsonschema
    except ModuleNotFoundError as exc:
        raise ValidationRuntimeError(
            "Draft 2020-12 校验需要 jsonschema。请执行："
            "python -m pip install jsonschema"
        ) from exc

    path = Path(schema_path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationRuntimeError(f"无法读取 Schema {path}：{exc}") from exc

    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        report.error("SCHEMA_INVALID", f"Schema 定义无效：{exc.message}", _path(exc.path))
        return

    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
        report.error("SCHEMA", f"Schema 校验失败：{error.message}", _path(error.absolute_path))


def validate_cross_references(
    data: dict[str, Any], registry: EntityRegistry, report: ValidationReport
) -> None:
    def require(
        entity_id: str | None,
        entity_type: str,
        owner_path: str,
        *,
        optional: bool = False,
    ) -> None:
        if entity_id is None and optional:
            return
        if not isinstance(entity_id, str) or not registry.has(entity_type, entity_id):
            report.error(
                "BROKEN_REFERENCE",
                f"应引用已存在的 {entity_type} ID，实际为 {entity_id!r}。",
                owner_path,
            )

    def require_many(ids: Any, entity_type: str, owner_path: str) -> None:
        if not isinstance(ids, list):
            return
        for index, entity_id in enumerate(ids):
            require(entity_id, entity_type, f"{owner_path}/{index}")

    def require_entity_refs(refs: Any, owner_path: str) -> None:
        if not isinstance(refs, list):
            return
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            entity_type = ref.get("type")
            if entity_type not in ENTITY_REF_TYPES:
                report.error(
                    "BROKEN_ENTITY_REF",
                    f"不支持的 entityRef 类型：{entity_type!r}。",
                    f"{owner_path}/{index}/type",
                )
                continue
            require(ref.get("id"), entity_type, f"{owner_path}/{index}/id")

    meta = data.get("meta", {})
    require(
        meta.get("latestUpdateBatchId"),
        "update_batch",
        "/meta/latestUpdateBatchId",
        optional=True,
    )
    project = data.get("project", {})
    require(project.get("currentStageId"), "stage", "/project/currentStageId")
    require(
        project.get("currentReleaseId"),
        "release",
        "/project/currentReleaseId",
        optional=True,
    )
    require(data.get("intent", {}).get("reviewId"), "review", "/intent/reviewId", optional=True)

    stages = _items(data, "stages")
    current_stages = [stage for stage in stages if stage.get("status") == "current"]
    if len(current_stages) != 1:
        report.error(
            "CURRENT_STAGE_COUNT",
            f"必须且只能有一个当前阶段，实际找到 {len(current_stages)} 个。",
            "/stages",
        )
    elif current_stages[0].get("id") != project.get("currentStageId"):
        report.error(
            "CURRENT_STAGE_MISMATCH",
            "project.currentStageId 与标记为 current 的阶段不一致。",
            "/project/currentStageId",
        )

    architecture = data.get("architecture", {})
    require(
        architecture.get("currentVersionId"),
        "architecture_version",
        "/architecture/currentVersionId",
    )
    require(
        architecture.get("targetVersionId"),
        "architecture_version",
        "/architecture/targetVersionId",
        optional=True,
    )

    for index, requirement in enumerate(_items(data, "requirements")):
        base = f"/requirements/{index}"
        require_many(requirement.get("targetReleaseIds"), "release", f"{base}/targetReleaseIds")
        require_many(requirement.get("moduleIds"), "module", f"{base}/moduleIds")
        require_many(requirement.get("acceptanceCriteriaIds"), "acceptance", f"{base}/acceptanceCriteriaIds")
        require_many(requirement.get("sourceReferenceIds"), "reference", f"{base}/sourceReferenceIds")
        require(requirement.get("reviewId"), "review", f"{base}/reviewId", optional=True)

    for index, baseline in enumerate(_architecture_items(data, "baselines")):
        require_many(baseline.get("referenceIds"), "reference", f"/architecture/baselines/{index}/referenceIds")

    for index, version in enumerate(_architecture_items(data, "versions")):
        base = f"/architecture/versions/{index}"
        require_many(version.get("baselineIds"), "baseline", f"{base}/baselineIds")
        delta = version.get("delta", {})
        for field_name in ("addedModuleIds", "modifiedModuleIds", "removedModuleIds"):
            require_many(delta.get(field_name), "module", f"{base}/delta/{field_name}")
        for field_name in (
            "addedConnectionIds",
            "modifiedConnectionIds",
            "removedConnectionIds",
        ):
            require_many(delta.get(field_name), "connection", f"{base}/delta/{field_name}")
        require(version.get("reviewId"), "review", f"{base}/reviewId", optional=True)
        require_many(version.get("referenceIds"), "reference", f"{base}/referenceIds")

    for index, module in enumerate(_architecture_items(data, "modules")):
        base = f"/architecture/modules/{index}"
        require(module.get("layerId"), "layer", f"{base}/layerId")
        require(module.get("targetLayerId"), "layer", f"{base}/targetLayerId", optional=True)
        require_many(module.get("requirementIds"), "requirement", f"{base}/requirementIds")
        require(module.get("source", {}).get("baselineId"), "baseline", f"{base}/source/baselineId", optional=True)
        for design_name in ("currentDesign", "targetDesign"):
            design = module.get(design_name)
            if not isinstance(design, dict):
                continue
            require_many(design.get("referenceIds"), "reference", f"{base}/{design_name}/referenceIds")
            for tech_index, technology in enumerate(design.get("technologies", [])):
                require(
                    technology.get("decisionId"),
                    "decision",
                    f"{base}/{design_name}/technologies/{tech_index}/decisionId",
                    optional=True,
                )
        for field_name, entity_type in (
            ("decisionIds", "decision"),
            ("riskIds", "risk"),
            ("acceptanceCriteriaIds", "acceptance"),
            ("gateIds", "gate"),
            ("workItemIds", "work_item"),
            ("reviewIds", "review"),
        ):
            require_many(module.get(field_name), entity_type, f"{base}/{field_name}")

    for index, connection in enumerate(_architecture_items(data, "connections")):
        base = f"/architecture/connections/{index}"
        require(connection.get("fromModuleId"), "module", f"{base}/fromModuleId")
        require(connection.get("toModuleId"), "module", f"{base}/toModuleId")
        require_many(connection.get("contractReferenceIds"), "reference", f"{base}/contractReferenceIds")
        require(connection.get("transitionId"), "transition", f"{base}/transitionId", optional=True)

    transition_subject_type = {
        "module": "module",
        "connection": "connection",
        "baseline": "baseline",
        "deployment": "deployment",
    }
    for index, transition in enumerate(_architecture_items(data, "transitions")):
        base = f"/architecture/transitions/{index}"
        subject_type = transition_subject_type.get(transition.get("subjectType"))
        if subject_type:
            require(transition.get("subjectId"), subject_type, f"{base}/subjectId")
        elif transition.get("subjectType") == "data_flow":
            subject_id = transition.get("subjectId")
            if subject_id not in registry.all_ids:
                report.error(
                    "BROKEN_REFERENCE",
                    f"数据流迁移对象 {subject_id!r} 无法解析到实体。",
                    f"{base}/subjectId",
                )
        require(transition.get("fromVersionId"), "architecture_version", f"{base}/fromVersionId")
        require(transition.get("toVersionId"), "architecture_version", f"{base}/toVersionId")
        require_many(transition.get("workItemIds"), "work_item", f"{base}/workItemIds")
        require_many(transition.get("decisionIds"), "decision", f"{base}/decisionIds")
        require_many(transition.get("riskIds"), "risk", f"{base}/riskIds")

    for index, stage in enumerate(stages):
        base = f"/stages/{index}"
        require_many(stage.get("entryAcceptanceIds"), "acceptance", f"{base}/entryAcceptanceIds")
        require_many(stage.get("exitAcceptanceIds"), "acceptance", f"{base}/exitAcceptanceIds")
        require_many(stage.get("moduleIds"), "module", f"{base}/moduleIds")
        require_many(stage.get("blockerDecisionIds"), "decision", f"{base}/blockerDecisionIds")
        require_many(stage.get("blockerRiskIds"), "risk", f"{base}/blockerRiskIds")
        require(stage.get("reviewId"), "review", f"{base}/reviewId", optional=True)

    for index, work_item in enumerate(_items(data, "workItems")):
        base = f"/workItems/{index}"
        require(work_item.get("stageId"), "stage", f"{base}/stageId", optional=True)
        for field_name, entity_type in (
            ("moduleIds", "module"),
            ("requirementIds", "requirement"),
            ("decisionIds", "decision"),
            ("riskIds", "risk"),
            ("acceptanceCriteriaIds", "acceptance"),
            ("referenceIds", "reference"),
        ):
            require_many(work_item.get(field_name), entity_type, f"{base}/{field_name}")

    for index, decision in enumerate(_items(data, "decisions")):
        base = f"/decisions/{index}"
        require_many(decision.get("relatedModuleIds"), "module", f"{base}/relatedModuleIds")
        require_many(decision.get("relatedRequirementIds"), "requirement", f"{base}/relatedRequirementIds")
        require(decision.get("stageId"), "stage", f"{base}/stageId", optional=True)
        require_many(decision.get("relatedReleaseIds"), "release", f"{base}/relatedReleaseIds")
        option_ids = {option.get("id") for option in decision.get("options", [])}
        for field_name in ("recommendedOptionId", "selectedOptionId"):
            option_id = decision.get(field_name)
            if option_id is not None and option_id not in option_ids:
                report.error(
                    "BROKEN_DECISION_OPTION",
                    f"{option_id!r} 不是决策 {decision.get('id')} 的有效选项。",
                    f"{base}/{field_name}",
                )
        require(decision.get("reviewId"), "review", f"{base}/reviewId", optional=True)
        require_many(decision.get("referenceIds"), "reference", f"{base}/referenceIds")

    for collection_name in ("risks",):
        for index, risk in enumerate(_items(data, collection_name)):
            base = f"/{collection_name}/{index}"
            require_many(risk.get("relatedModuleIds"), "module", f"{base}/relatedModuleIds")
            require_many(risk.get("relatedRequirementIds"), "requirement", f"{base}/relatedRequirementIds")
            require(risk.get("stageId"), "stage", f"{base}/stageId", optional=True)
            require_many(risk.get("relatedReleaseIds"), "release", f"{base}/relatedReleaseIds")
            require_many(risk.get("referenceIds"), "reference", f"{base}/referenceIds")

    for index, acceptance in enumerate(_items(data, "acceptanceCriteria")):
        base = f"/acceptanceCriteria/{index}"
        scope_type = SCOPE_TYPE_MAP.get(acceptance.get("scopeType"))
        if scope_type:
            require(acceptance.get("scopeId"), scope_type, f"{base}/scopeId")
        require_many(acceptance.get("evidenceReferenceIds"), "reference", f"{base}/evidenceReferenceIds")
        require(acceptance.get("reviewId"), "review", f"{base}/reviewId", optional=True)

    for index, gate in enumerate(_items(data, "gates")):
        base = f"/gates/{index}"
        scope_type = SCOPE_TYPE_MAP.get(gate.get("scopeType"))
        if scope_type:
            require(gate.get("scopeId"), scope_type, f"{base}/scopeId")
        require_many(gate.get("evidenceReferenceIds"), "reference", f"{base}/evidenceReferenceIds")
        require_many(gate.get("acceptanceCriteriaIds"), "acceptance", f"{base}/acceptanceCriteriaIds")
        require_many(gate.get("referenceIds"), "reference", f"{base}/referenceIds")
        require(gate.get("reviewId"), "review", f"{base}/reviewId", optional=True)

    for index, reference in enumerate(_items(data, "references")):
        require_entity_refs(reference.get("relatedEntities"), f"/references/{index}/relatedEntities")

    for index, release in enumerate(_items(data, "releases")):
        base = f"/releases/{index}"
        require(release.get("architectureVersionId"), "architecture_version", f"{base}/architectureVersionId")
        require(release.get("stageId"), "stage", f"{base}/stageId")
        require_many(release.get("moduleIds"), "module", f"{base}/moduleIds")
        require_many(release.get("deploymentIds"), "deployment", f"{base}/deploymentIds")
        require_many(release.get("referenceIds"), "reference", f"{base}/referenceIds")

    for index, resource in enumerate(_items(data, "resources")):
        base = f"/resources/{index}"
        require_many(resource.get("usedByModuleIds"), "module", f"{base}/usedByModuleIds")
        require_many(resource.get("usedByDeploymentIds"), "deployment", f"{base}/usedByDeploymentIds")
        require_many(resource.get("referenceIds"), "reference", f"{base}/referenceIds")

    for index, deployment in enumerate(_items(data, "deployments")):
        base = f"/deployments/{index}"
        require(deployment.get("releaseId"), "release", f"{base}/releaseId")
        require(deployment.get("architectureVersionId"), "architecture_version", f"{base}/architectureVersionId")
        require_many(deployment.get("resourceIds"), "resource", f"{base}/resourceIds")
        require_many(deployment.get("externalResourceIds"), "resource", f"{base}/externalResourceIds")
        require_many(deployment.get("configReferenceIds"), "reference", f"{base}/configReferenceIds")
        for module_index, module_deployment in enumerate(deployment.get("moduleDeployments", [])):
            module_base = f"{base}/moduleDeployments/{module_index}"
            require(module_deployment.get("moduleId"), "module", f"{module_base}/moduleId")
            require_many(module_deployment.get("resourceIds"), "resource", f"{module_base}/resourceIds")

    for index, review in enumerate(_items(data, "reviews")):
        base = f"/reviews/{index}"
        require_entity_refs(review.get("subjectRefs"), f"{base}/subjectRefs")
        require_many(review.get("decisionIds"), "decision", f"{base}/decisionIds")
        require_many(review.get("changeIds"), "change", f"{base}/changeIds")

    for index, change in enumerate(_items(data, "changes")):
        base = f"/changes/{index}"
        require_entity_refs(change.get("entityRefs"), f"{base}/entityRefs")
        require(change.get("reviewId"), "review", f"{base}/reviewId", optional=True)
        require(change.get("architectureVersionId"), "architecture_version", f"{base}/architectureVersionId", optional=True)
        require_many(change.get("referenceIds"), "reference", f"{base}/referenceIds")

    focus_ids = set(registry.by_type.get("next_focus", {}))
    for index, batch in enumerate(_items(data, "updateBatches")):
        base = f"/updateBatches/{index}"
        require(batch.get("reviewId"), "review", f"{base}/reviewId")
        require_many(batch.get("changeIds"), "change", f"{base}/changeIds")
        for option_index, option_id in enumerate(batch.get("nextFocusOptionIds", [])):
            if option_id not in focus_ids:
                report.error(
                    "BROKEN_GUIDANCE_OPTION",
                    f"未知的下一步焦点选项：{option_id!r}。",
                    f"{base}/nextFocusOptionIds/{option_index}",
                )
        require(batch.get("projectStageBefore"), "stage", f"{base}/projectStageBefore")
        require(batch.get("projectStageAfter"), "stage", f"{base}/projectStageAfter")
        for item_index, item in enumerate(batch.get("attentionItems", [])):
            require_entity_refs(item.get("relatedEntities"), f"{base}/attentionItems/{item_index}/relatedEntities")

    if data.get("schemaVersion") == "0.2":
        policy = data.get("observationPolicy", {})
        if not isinstance(policy, dict):
            report.error("INVALID_OBSERVATION_POLICY", "observationPolicy 必须是对象。", "/observationPolicy")
        else:
            try:
                from observation_policy import policy_hash

                expected_policy_hash = policy_hash(policy)
            except (TypeError, ValueError):
                expected_policy_hash = None
            if policy.get("policyHash") != expected_policy_hash:
                report.error(
                    "OBSERVATION_POLICY_HASH_MISMATCH",
                    "Continuous Observation Policy Hash 与策略内容不一致。",
                    "/observationPolicy/policyHash",
                )
        binding = data.get("sourceBinding", {})
        require(
            binding.get("lastObservationBatchId"),
            "observation_batch",
            "/sourceBinding/lastObservationBatchId",
            optional=True,
        )
        for index, provenance in enumerate(_items(data, "factProvenance")):
            require(
                provenance.get("observationBatchId"),
                "observation_batch",
                f"/factProvenance/{index}/observationBatchId",
            )
        for index, snapshot in enumerate(_items(data, "currentArchitectureSnapshots")):
            base = f"/currentArchitectureSnapshots/{index}"
            require_many(snapshot.get("moduleIds"), "module", f"{base}/moduleIds")
            require_many(snapshot.get("connectionIds"), "connection", f"{base}/connectionIds")
            require_many(snapshot.get("provenanceIds"), "fact_provenance", f"{base}/provenanceIds")
        for index, assessment in enumerate(_items(data, "standardAssessments")):
            base = f"/standardAssessments/{index}"
            for result_index, result in enumerate(assessment.get("results", [])):
                require_entity_refs(
                    result.get("relatedEntities"),
                    f"{base}/results/{result_index}/relatedEntities",
                )
        for index, batch in enumerate(_items(data, "observationBatches")):
            base = f"/observationBatches/{index}"
            require_many(batch.get("standardAssessmentIds"), "standard_assessment", f"{base}/standardAssessmentIds")
            require_many(batch.get("provenanceIds"), "fact_provenance", f"{base}/provenanceIds")
            if batch.get("revisionTo") != batch.get("revisionFrom", -1) + 1:
                report.error(
                    "INVALID_OBSERVATION_REVISION",
                    "Observation Batch 必须精确增加一个 Revision。",
                    f"{base}/revisionTo",
                )

    guidance = data.get("guidance", {})
    for field_name in ("recommendedOptionId", "selectedOptionId"):
        option_id = guidance.get(field_name)
        if option_id is not None and option_id not in focus_ids:
            report.error(
                "BROKEN_GUIDANCE_OPTION",
                f"未知的下一步焦点选项：{option_id!r}。",
                f"/guidance/{field_name}",
            )
    for option_index, option in enumerate(guidance.get("options", [])):
        require_entity_refs(option.get("relatedEntities"), f"/guidance/options/{option_index}/relatedEntities")
    for option_index, record in enumerate(
        guidance.get("extensions", {}).get("historicalOptions", [])
        if isinstance(guidance.get("extensions"), dict)
        else []
    ):
        if not isinstance(record, dict) or record.get("lifecycle") != "historical" or record.get("active") is not False:
            report.error(
                "INVALID_GUIDANCE_HISTORY",
                "历史下一步焦点记录必须标记为 historical 且 inactive。",
                f"/guidance/extensions/historicalOptions/{option_index}",
            )
            continue
        option = record.get("option", {})
        require_entity_refs(
            option.get("relatedEntities") if isinstance(option, dict) else [],
            f"/guidance/extensions/historicalOptions/{option_index}/option/relatedEntities",
        )


def _git_file_state(source_path: Path | None) -> str:
    """Return tracked, untracked, or unknown without mutating the repository."""

    if source_path is None:
        return "unknown"
    try:
        source = source_path.resolve()
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source.parent,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        if root_result.returncode != 0:
            return "untracked"
        root = Path(root_result.stdout.strip()).resolve()
        relative = source.relative_to(root)
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative.as_posix()],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        if tracked.returncode == 0:
            return "tracked"
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--", relative.as_posix()],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        return "tracked" if staged.stdout.strip() else "untracked"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown"


def validate_rules(
    data: dict[str, Any],
    registry: EntityRegistry,
    report: ValidationReport,
    base_dir: Path,
    source_path: Path | None = None,
) -> None:
    reviews = registry.by_type.get("review", {})

    def review_approved(review_id: str | None) -> bool:
        review = reviews.get(review_id or "")
        return bool(review and review.get("status") in {"approved", "waived"})

    def related(*pairs: tuple[str, Any]) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, str]] = []
        for entity_type, entity_id in pairs:
            if not isinstance(entity_id, str) or not entity_id:
                continue
            key = (entity_type, entity_id)
            if key not in seen:
                seen.add(key)
                result.append({"type": entity_type, "id": entity_id})
        return result

    # R1 Architecture Gap
    for requirement in _items(data, "requirements"):
        if (
            requirement.get("definitionStatus") == "confirmed"
            and not requirement.get("moduleIds")
            and requirement.get("fulfillmentStatus") != "not_applicable"
        ):
            report.high(
                "ARCHITECTURE_GAP",
                f"已确认需求 {requirement.get('id')} 没有模块覆盖。",
                related_entities=related(("requirement", requirement.get("id"))),
            )

    # R2 Scope Drift
    for module in _architecture_items(data, "modules"):
        if not module.get("requirementIds") and not str(module.get("rationale", "")).strip():
            report.warning(
                "SCOPE_DRIFT",
                f"模块 {module.get('id')} 既没有需求映射，也没有设计理由。",
                related_entities=related(("module", module.get("id"))),
            )
    for work_item in _items(data, "workItems"):
        if not work_item.get("stageId") and not work_item.get("moduleIds") and not work_item.get("requirementIds"):
            report.warning(
                "SCOPE_DRIFT",
                f"工作项 {work_item.get('id')} 未关联阶段、模块或需求。",
                related_entities=related(("work_item", work_item.get("id"))),
            )
        current_stage = registry.by_type.get("stage", {}).get(
            data.get("project", {}).get("currentStageId", ""), {}
        )
        work_stage = registry.by_type.get("stage", {}).get(
            work_item.get("stageId", ""), {}
        )
        if (
            work_item.get("status") in {"ready", "in_progress", "blocked", "review"}
            and isinstance(current_stage.get("order"), int)
            and isinstance(work_stage.get("order"), int)
            and work_stage["order"] > current_stage["order"]
        ):
            report.warning(
                "SCOPE_DRIFT",
                f"活跃工作项 {work_item.get('id')} 被分配到未来阶段 {work_stage.get('id')}，而不是当前阶段 {current_stage.get('id')}。",
                related_entities=related(
                    ("work_item", work_item.get("id")),
                    ("stage", work_stage.get("id")),
                    ("stage", current_stage.get("id")),
                ),
            )

    # R3 Implementation Drift and R7 Deployment Drift
    releases = registry.by_type.get("release", {})
    modules = registry.by_type.get("module", {})
    architecture = data.get("architecture", {})
    current_arch_id = architecture.get("currentVersionId")
    active_release_ids: set[str] = set()
    for deployment in _items(data, "deployments"):
        release = releases.get(deployment.get("releaseId", ""), {})
        deployed_modules = {
            item.get("moduleId") for item in deployment.get("moduleDeployments", [])
        }
        if deployment.get("status") == "active":
            active_release_ids.add(deployment.get("releaseId"))
            if deployment.get("architectureVersionId") != release.get("architectureVersionId"):
                report.high(
                    "DEPLOYMENT_DRIFT",
                    f"活跃部署 {deployment.get('id')} 的架构与其发布不一致。",
                    related_entities=related(
                        ("deployment", deployment.get("id")),
                        ("release", release.get("id")),
                        ("architecture_version", deployment.get("architectureVersionId")),
                    ),
                )
            if deployment.get("releaseId") == data.get("project", {}).get("currentReleaseId") and deployment.get("architectureVersionId") != current_arch_id:
                report.high(
                    "DEPLOYMENT_DRIFT",
                    f"当前部署 {deployment.get('id')} 未使用当前架构。",
                    related_entities=related(
                        ("deployment", deployment.get("id")),
                        ("architecture_version", current_arch_id),
                    ),
                )
            missing_from_release = deployed_modules - set(release.get("moduleIds", []))
            if missing_from_release:
                report.high(
                    "IMPLEMENTATION_DRIFT",
                    f"部署 {deployment.get('id')} 运行了发布范围外的模块：{sorted(missing_from_release)}。",
                    related_entities=related(
                        ("deployment", deployment.get("id")),
                        ("release", release.get("id")),
                        *(("module", item) for item in sorted(missing_from_release)),
                    ),
                )
            required_release_modules = {
                module_id
                for module_id in release.get("moduleIds", [])
                if modules.get(module_id, {}).get("category") != "external"
            }
            missing_from_deployment = required_release_modules - deployed_modules
            if missing_from_deployment:
                report.high(
                    "DEPLOYMENT_DRIFT",
                    f"活跃部署 {deployment.get('id')} 缺少发布声明的非外部模块：{sorted(missing_from_deployment)}。",
                    related_entities=related(
                        ("deployment", deployment.get("id")),
                        ("release", release.get("id")),
                        *(("module", item) for item in sorted(missing_from_deployment)),
                    ),
                )
            for module_id in deployed_modules:
                module = modules.get(module_id, {})
                if module.get("architectureScope") in {"target", "historical"}:
                    report.high(
                        "IMPLEMENTATION_DRIFT",
                        f"活跃部署 {deployment.get('id')} 正在运行非当前模块 {module_id}。",
                        related_entities=related(
                            ("deployment", deployment.get("id")),
                            ("module", module_id),
                        ),
                    )

        declared_resources = set(deployment.get("resourceIds", [])) | set(
            deployment.get("externalResourceIds", [])
        )
        nested_resources = {
            resource_id
            for item in deployment.get("moduleDeployments", [])
            for resource_id in item.get("resourceIds", [])
        }
        undeclared = nested_resources - declared_resources
        if undeclared:
            report.high(
                "DEPLOYMENT_DRIFT",
                f"部署 {deployment.get('id')} 使用了未声明资源：{sorted(undeclared)}。",
                related_entities=related(
                    ("deployment", deployment.get("id")),
                    *(("resource", item) for item in sorted(undeclared)),
                ),
            )

    deployed_without_active = {
        release_id
        for release_id, release in releases.items()
        if release.get("status") == "deployed"
        and release_id not in active_release_ids
    }
    for release_id in sorted(deployed_without_active):
        report.high(
            "DEPLOYMENT_DRIFT",
            f"已部署发布 {release_id} 没有活跃部署。",
            related_entities=related(("release", release_id)),
        )

    current_release = data.get("project", {}).get("currentReleaseId")
    if (
        current_release
        and current_release not in active_release_ids
        and current_release not in deployed_without_active
    ):
        report.warning(
            "DEPLOYMENT_DRIFT",
            f"当前发布 {current_release} 没有活跃部署。",
            related_entities=related(("release", current_release)),
        )

    # R4 Review Gap. Exploration is informational until it reaches a reviewed
    # architecture, the current Release, or an active Deployment.
    module_transition_states = {
        transition.get("subjectId"): transition.get("state")
        for transition in _architecture_items(data, "transitions")
        if transition.get("subjectType") == "module"
    }
    for version in _architecture_items(data, "versions"):
        if version.get("status") in {"review_pending", "accepted"} and not review_approved(version.get("reviewId")):
            report.warning(
                "REVIEW_GAP",
                f"架构 {version.get('id')} 状态为 {version.get('status')}，但没有已批准评审。",
                related_entities=related(
                    ("architecture_version", version.get("id")),
                    ("review", version.get("reviewId")),
                ),
            )
    current_release_modules = set(
        releases.get(data.get("project", {}).get("currentReleaseId", ""), {}).get(
            "moduleIds", []
        )
    )
    active_deployment_modules = {
        item.get("moduleId")
        for deployment in _items(data, "deployments")
        if deployment.get("status") == "active"
        for item in deployment.get("moduleDeployments", [])
        if isinstance(item, dict)
    }
    for module in _architecture_items(data, "modules"):
        status = module.get("status", {})
        implemented = status.get("implementationMaturity") in {"functional", "stable"}
        migrating = module_transition_states.get(module.get("id")) in {
            "in_progress",
            "migrating",
            "completed",
        }
        design = status.get("designMaturity")
        requires_review = design in {"review_ready", "confirmed"} or implemented or migrating
        approved = any(
            review_approved(review_id) for review_id in module.get("reviewIds", [])
        )
        if not requires_review or approved:
            continue
        module_id = module.get("id")
        controls_runtime = (
            module_id in current_release_modules
            or module_id in active_deployment_modules
            or (module.get("architectureScope") in {"current", "both"} and design in {"review_ready", "confirmed"})
        )
        if design == "experimental" and implemented and not controls_runtime:
            report.info(
                "REVIEW_GAP",
                f"模块 {module_id} 是已实现但尚未确认设计的实验模块。",
                related_entities=related(("module", module_id)),
            )
        elif controls_runtime or design == "confirmed" or migrating:
            report.high(
                "REVIEW_GAP",
                f"模块 {module_id} 已确认、属于当前范围、已发布、已部署或正在迁移，但没有已批准评审。",
                related_entities=related(("module", module_id)),
            )
        else:
            report.warning(
                "REVIEW_GAP",
                f"模块 {module_id} 已可进入设计评审。",
                related_entities=related(("module", module_id)),
            )
    for decision in _items(data, "decisions"):
        if decision.get("status") == "review_pending" and not review_approved(decision.get("reviewId")):
            report.warning(
                "REVIEW_GAP",
                f"决策 {decision.get('id')} 正在等待评审。",
                related_entities=related(
                    ("decision", decision.get("id")),
                    ("review", decision.get("reviewId")),
                ),
            )
    for acceptance in _items(data, "acceptanceCriteria"):
        if acceptance.get("status") == "review_pending" and not review_approved(acceptance.get("reviewId")):
            report.warning(
                "REVIEW_GAP",
                f"验收标准 {acceptance.get('id')} 正在等待评审。",
                related_entities=related(
                    ("acceptance", acceptance.get("id")),
                    ("review", acceptance.get("reviewId")),
                ),
            )
    for change in _items(data, "changes"):
        if change.get("impactLevel") in {"module", "architecture", "deployment", "project"} and not review_approved(change.get("reviewId")):
            report.warning(
                "REVIEW_GAP",
                f"{change.get('impactLevel')} 级变更 {change.get('id')} 缺少已批准评审。",
                related_entities=related(
                    ("change", change.get("id")),
                    ("review", change.get("reviewId")),
                ),
            )

    # R5 Verification Gap
    gates = registry.by_type.get("gate", {})
    acceptance = registry.by_type.get("acceptance", {})
    for module in _architecture_items(data, "modules"):
        status = module.get("status", {})
        if status.get("implementationMaturity") not in {"functional", "stable"}:
            continue
        reasons: list[str] = []
        if status.get("verificationStatus") not in {"passed", "waived"}:
            reasons.append(f"模块验证状态为 {status.get('verificationStatus')}")
        module_acceptance = [
            acceptance[item_id]
            for item_id in module.get("acceptanceCriteriaIds", [])
            if item_id in acceptance
        ]
        if not module_acceptance:
            reasons.append("未映射验收标准")
        else:
            if any(item.get("status") != "accepted" for item in module_acceptance):
                reasons.append("已映射验收标准尚未接受")
            if any(item.get("verificationStatus") not in {"passed", "waived"} for item in module_acceptance):
                reasons.append("验收证据不完整")
        if any(
            item.get("verificationStatus") == "passed"
            and not item.get("evidenceReferenceIds")
            for item in module_acceptance
        ):
            reasons.append("已通过验收标准没有证据")
        module_gates = [
            gates[item_id] for item_id in module.get("gateIds", []) if item_id in gates
        ]
        required_gates = [item for item in module_gates if item.get("required") is True]
        if not required_gates:
            reasons.append("未映射必需门禁")
        else:
            incomplete_required = [
                item.get("id")
                for item in required_gates
                if item.get("status")
                in {"not_configured", "ready", "running", "failed"}
            ]
            if incomplete_required:
                reasons.append(
                    f"必需门禁尚未完成：{sorted(incomplete_required)}"
                )
        if any(
            item.get("required") is True
            and item.get("status") == "passed"
            and not item.get("evidenceReferenceIds")
            for item in required_gates
        ):
            reasons.append("已通过门禁没有证据")
        for item in module_gates:
            if item.get("required") is False and item.get("status") == "failed":
                report.info(
                    "OPTIONAL_GATE_FAILED",
                    f"可选门禁 {item.get('id')} 失败，但不会阻断模块 {module.get('id')} 的验证。",
                    related_entities=related(
                        ("module", module.get("id")),
                        ("gate", item.get("id")),
                    ),
                )
        if reasons:
            message = f"模块 {module.get('id')}：" + "；".join(dict.fromkeys(reasons)) + "。"
            verification_entities = related(
                ("module", module.get("id")),
                *(("acceptance", item.get("id")) for item in module_acceptance),
                *(("gate", item.get("id")) for item in module_gates),
            )
            if (
                status.get("implementationMaturity") == "stable"
                or module.get("id") in current_release_modules
                or module.get("id") in active_deployment_modules
            ):
                report.high(
                    "VERIFICATION_GAP",
                    message,
                    related_entities=verification_entities,
                )
            else:
                report.warning(
                    "VERIFICATION_GAP",
                    message,
                    related_entities=verification_entities,
                )

    # R6 Baseline Drift
    for baseline in _architecture_items(data, "baselines"):
        if baseline.get("divergence") == "high":
            report.warning(
                "BASELINE_DRIFT",
                f"基线 {baseline.get('id')} 的偏离程度为 high。",
                related_entities=related(("baseline", baseline.get("id"))),
            )
        if not str(baseline.get("upgradeStrategy", "")).strip():
            report.warning(
                "BASELINE_DRIFT",
                f"基线 {baseline.get('id')} 缺少升级策略。",
                related_entities=related(("baseline", baseline.get("id"))),
            )
    for module in _architecture_items(data, "modules"):
        source = module.get("source", {})
        if source.get("origin") == "oss" and source.get("changeType") == "modified":
            if not str(source.get("rationale", "")).strip():
                report.warning(
                    "BASELINE_DRIFT",
                    f"OSS 模块 {module.get('id')} 被修改但没有记录理由。",
                    related_entities=related(("module", module.get("id"))),
                )
            if not source.get("changedAreas"):
                report.warning(
                    "BASELINE_DRIFT",
                    f"OSS 模块 {module.get('id')} 被修改但没有记录变更区域。",
                    related_entities=related(("module", module.get("id"))),
                )

    # R8 Transition Risk
    if architecture.get("targetVersionId") and architecture.get("targetVersionId") != current_arch_id:
        transitions = _architecture_items(data, "transitions")
        transition_subjects = {
            (item.get("subjectType"), item.get("subjectId")) for item in transitions
        }
        for module in _architecture_items(data, "modules"):
            differs = module.get("architectureScope") in {"current", "target"} or (
                isinstance(module.get("targetDesign"), dict)
                and module.get("targetDesign") != module.get("currentDesign")
            )
            if differs and ("module", module.get("id")) not in transition_subjects:
                report.warning(
                    "TRANSITION_RISK",
                    f"模块 {module.get('id')} 在当前与目标架构间存在差异，但没有模块迁移记录。",
                    related_entities=related(("module", module.get("id"))),
                )
        for connection in _architecture_items(data, "connections"):
            has_transition = bool(connection.get("transitionId")) or (
                "connection",
                connection.get("id"),
            ) in transition_subjects
            if (
                connection.get("architectureScope") in {"current", "target"}
                and not has_transition
            ):
                report.warning(
                    "TRANSITION_RISK",
                    f"连接 {connection.get('id')} 在当前与目标架构间存在差异，但没有迁移记录。",
                    related_entities=related(
                        ("connection", connection.get("id")),
                        ("module", connection.get("fromModuleId")),
                        ("module", connection.get("toModuleId")),
                    ),
                )
        for transition in transitions:
            if transition.get("state") == "blocked":
                report.high(
                    "TRANSITION_RISK",
                    f"迁移 {transition.get('id')} 处于阻塞状态。",
                    related_entities=related(
                        ("transition", transition.get("id")),
                        (transition.get("subjectType", "transition"), transition.get("subjectId")),
                    ),
                )

    # R9 Resource Risk and credential warnings
    has_embedded_credentials = False
    for resource in _items(data, "resources"):
        resource_id = resource.get("id")
        credentials = resource.get("access", {}).get("credentials", {})
        mode = credentials.get("mode")
        if mode == "embedded":
            has_embedded_credentials = True
            report.warning(
                "EMBEDDED_SECRET_PRESENT",
                f"资源 {resource_id} 包含内嵌凭据；遮罩不等于加密。",
                related_entities=related(("resource", resource_id)),
            )
            if resource.get("environment") == "production":
                report.warning(
                    "EMBEDDED_SECRET_IN_PRODUCTION",
                    f"生产资源 {resource_id} 包含内嵌凭据。",
                    severity="high",
                    related_entities=related(("resource", resource_id)),
                )
        if mode == "external_file":
            secret_path = credentials.get("path", "")
            resolved = (base_dir / secret_path).resolve() if secret_path else None
            if not resolved or not resolved.exists():
                report.warning(
                    "RESOURCE_RISK",
                    f"资源 {resource_id} 的外部凭据文件缺失：{secret_path!r}。",
                    related_entities=related(("resource", resource_id)),
                )
        if mode == "external_store" and (
            not str(credentials.get("provider", "")).strip()
            or not str(credentials.get("reference", "")).strip()
            or not credentials.get("keys")
        ):
            report.warning(
                "RESOURCE_RISK",
                f"资源 {resource_id} 的外部凭据存储缺少提供方、引用或键列表。",
                related_entities=related(("resource", resource_id)),
            )
        if resource.get("type") in {"external_api", "mcp_server"}:
            if not resource.get("usedByModuleIds"):
                report.warning(
                    "RESOURCE_RISK",
                    f"外部资源 {resource_id} 没有依赖模块。",
                    related_entities=related(("resource", resource_id)),
                )
            if not str(resource.get("notes", "")).strip():
                report.warning(
                    "RESOURCE_RISK",
                    f"外部资源 {resource_id} 没有记录用途或使用说明。",
                    related_entities=related(("resource", resource_id)),
                )
        if resource.get("environment") == "unknown":
            report.warning(
                "RESOURCE_RISK",
                f"资源 {resource_id} 的环境未知。",
                related_entities=related(("resource", resource_id)),
            )
        if resource.get("status") == "active" and not resource.get("usedByDeploymentIds"):
            report.warning(
                "RESOURCE_RISK",
                f"活跃资源 {resource_id} 未关联部署。",
                related_entities=related(("resource", resource_id)),
            )

    if has_embedded_credentials:
        embedded_resources = related(
            *(("resource", item.get("id")) for item in _items(data, "resources")
              if item.get("access", {}).get("credentials", {}).get("mode") == "embedded"),
            ("project", data.get("project", {}).get("id")),
        )
        git_state = _git_file_state(source_path)
        if git_state == "tracked":
            report.high(
                "GIT_SECRET_RISK",
                "此 Panorama 包含内嵌凭据，并已被 Git 跟踪或暂存。分享前请改用 *.local.html 私有副本或外部凭据模式。",
                related_entities=embedded_resources,
            )
        elif git_state == "unknown":
            report.warning(
                "GIT_SECRET_RISK",
                "此 Panorama 包含内嵌凭据；无法确定其 Git 跟踪或暂存状态。",
                related_entities=embedded_resources,
            )

    # R10 Stage Entry / Exit Gap
    current_stage_for_entry = registry.by_type.get("stage", {}).get(
        data.get("project", {}).get("currentStageId", ""), {}
    )
    entry_ids = current_stage_for_entry.get("entryAcceptanceIds", [])
    require_entry = bool(
        current_stage_for_entry.get("extensions", {}).get("requireEntryAcceptance")
        if isinstance(current_stage_for_entry.get("extensions"), dict)
        else False
    )
    if require_entry and not entry_ids:
        report.high(
            "STAGE_ENTRY_GAP",
            f"当前阶段 {current_stage_for_entry.get('id')} 要求进入验收，但没有定义进入条件。",
            related_entities=related(("stage", current_stage_for_entry.get("id"))),
        )
    incomplete_entry = [
        item_id
        for item_id in entry_ids
        if registry.by_type.get("acceptance", {})
        .get(item_id, {})
        .get("verificationStatus")
        not in {"passed", "waived"}
    ]
    if incomplete_entry:
        report.warning(
            "STAGE_ENTRY_GAP",
            f"当前阶段 {current_stage_for_entry.get('id')} 的进入验收尚未完成：{incomplete_entry}。",
            related_entities=related(
                ("stage", current_stage_for_entry.get("id")),
                *(("acceptance", item) for item in incomplete_entry),
            ),
        )

    for stage in _items(data, "stages"):
        if stage.get("status") != "completed":
            continue
        exit_ids = stage.get("exitAcceptanceIds", [])
        if not exit_ids:
            report.high(
                "STAGE_EXIT_GAP",
                f"已完成阶段 {stage.get('id')} 没有退出验收。",
                related_entities=related(("stage", stage.get("id"))),
            )
            continue
        incomplete = [
            item_id
            for item_id in exit_ids
            if registry.by_type.get("acceptance", {}).get(item_id, {}).get("verificationStatus")
            not in {"passed", "waived"}
        ]
        if incomplete:
            report.high(
                "STAGE_EXIT_GAP",
                f"已完成阶段 {stage.get('id')} 的退出验收尚未完成：{incomplete}。",
                related_entities=related(
                    ("stage", stage.get("id")),
                    *(("acceptance", item) for item in incomplete),
                ),
            )

    current_stage = registry.by_type.get("stage", {}).get(
        data.get("project", {}).get("currentStageId", ""), {}
    )
    current_order = current_stage.get("order")
    future_stages = [
        stage
        for stage in _items(data, "stages")
        if isinstance(current_order, int)
        and isinstance(stage.get("order"), int)
        and stage.get("order") > current_order
        and stage.get("status") == "not_started"
    ]
    exit_ids = current_stage.get("exitAcceptanceIds", [])
    exit_ready = bool(exit_ids) and all(
        registry.by_type.get("acceptance", {})
        .get(item_id, {})
        .get("verificationStatus")
        in {"passed", "waived"}
        for item_id in exit_ids
    )
    if future_stages and exit_ready:
        blocked_core_modules = [
            module_id
            for module_id in current_stage.get("moduleIds", [])
            if modules.get(module_id, {}).get("category") == "core"
            and modules.get(module_id, {})
            .get("status", {})
            .get("verificationStatus")
            not in {"passed", "waived"}
        ]
        if blocked_core_modules:
            report.high(
                "STAGE_EXIT_GAP",
                f"当前阶段 {current_stage.get('id')} 已通过退出验收，但核心模块仍有验证阻塞：{sorted(blocked_core_modules)}。",
                related_entities=related(
                    ("stage", current_stage.get("id")),
                    *(("module", item) for item in sorted(blocked_core_modules)),
                ),
            )

    # Filesystem Reference warnings are intentionally warnings: browsers cannot
    # reliably verify every local path and missing docs must not block rendering.
    for reference in _items(data, "references"):
        if reference.get("locationType") not in {"relative_path", "absolute_path"}:
            continue
        location = reference.get("location", "")
        candidate = Path(location)
        resolved = candidate if candidate.is_absolute() else base_dir / candidate
        if not location or not resolved.exists():
            report.warning(
                "MISSING_REFERENCE_PATH",
                f"参考资料 {reference.get('id')} 的路径不可用：{location!r}。",
            )


def validate_data(
    data: dict[str, Any],
    schema_path: str | Path | None = None,
    *,
    base_dir: str | Path = ".",
    source_path: str | Path | None = None,
) -> ValidationReport:
    report = ValidationReport()
    schema_version = str(data.get("schemaVersion", ""))
    template_version = str(data.get("meta", {}).get("templateVersion", ""))
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        report.error(
            "UNSUPPORTED_SCHEMA_VERSION",
            f"Validator 支持 {sorted(SUPPORTED_SCHEMA_VERSIONS)}，实际为 {schema_version!r}。",
            "/schemaVersion",
        )
        return report
    if template_version not in SUPPORTED_TEMPLATE_VERSIONS:
        report.error(
            "UNSUPPORTED_TEMPLATE_VERSION",
            f"Validator 支持 {sorted(SUPPORTED_TEMPLATE_VERSIONS)}，实际为 {template_version!r}。",
            "/meta/templateVersion",
        )
        return report
    try:
        selected_schema = schema_for_data(data, schema_path)
    except ValueError as exc:
        report.error("UNSUPPORTED_SCHEMA_VERSION", str(exc), "/schemaVersion")
        return report
    validate_schema(data, selected_schema, report)
    if report.errors:
        return report
    registry = build_registry(data, report)
    validate_cross_references(data, registry, report)
    validate_rules(
        data,
        registry,
        report,
        Path(base_dir),
        Path(source_path) if source_path is not None else None,
    )
    if not report.errors:
        report.info(
            "VALID",
            f"校验完成，共有 {len(report.warnings)} 个警告。",
        )
    return report


def load_panorama(path: str | Path) -> tuple[dict[str, Any], Path]:
    source = Path(path)
    if not source.is_file():
        raise ValidationRuntimeError(f"输入文件不存在：{source}")
    try:
        if source.suffix.lower() in {".html", ".htm"}:
            return extract_data(source), source.parent
        with source.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, PanoramaIOError) as exc:
        raise ValidationRuntimeError(f"无法读取 {source}：{exc}") from exc
    if not isinstance(data, dict):
        raise ValidationRuntimeError("Panorama 输入必须是 JSON 对象。")
    return data, source.parent


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="校验 Panorama Schema、跨实体引用和工程规则。"
    )
    parser.add_argument("input", type=Path, help="Panorama JSON 或 Single HTML")
    parser.add_argument("--schema", type=Path, default=None, help="覆盖自动选择的 Schema")
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结构化校验报告。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data, base_dir = load_panorama(args.input)
        report = validate_data(
            data,
            args.schema,
            base_dir=base_dir,
            source_path=args.input,
        )
    except ValidationRuntimeError as exc:
        print(f"错误 FILE：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for issue in report.issues:
            print(issue.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
