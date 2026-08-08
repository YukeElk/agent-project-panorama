"""Schema, cross-reference, and engineering-rule validation for Panorama data."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from panorama_io import PanoramaIOError, extract_data


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
}

SCOPE_TYPE_MAP = {
    "project": "project",
    "stage": "stage",
    "module": "module",
    "requirement": "requirement",
    "release": "release",
    "deployment": "deployment",
}


class ValidationRuntimeError(RuntimeError):
    """An input, marker, schema, or dependency failure (CLI exit code 2)."""


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    code: str
    message: str
    path: str = ""

    def render(self) -> str:
        location = f" [{self.path}]" if self.path else ""
        return f"{self.level} {self.code}{location}: {self.message}"


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, level: str, code: str, message: str, path: str = "") -> None:
        self.issues.append(ValidationIssue(level, code, message, path))

    def error(self, code: str, message: str, path: str = "") -> None:
        self.add("ERROR", code, message, path)

    def warning(self, code: str, message: str, path: str = "") -> None:
        self.add("WARNING", code, message, path)

    def info(self, code: str, message: str, path: str = "") -> None:
        self.add("INFO", code, message, path)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "WARNING"]


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
                    f"{entity_id} is used by both {all_ids[entity_id]} and {entity_type}.",
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
            "jsonschema is required for Draft 2020-12 validation. "
            "Install it with: python -m pip install jsonschema"
        ) from exc

    path = Path(schema_path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationRuntimeError(f"Cannot read schema {path}: {exc}") from exc

    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        report.error("SCHEMA_INVALID", exc.message, _path(exc.path))
        return

    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
        report.error("SCHEMA", error.message, _path(error.absolute_path))


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
                f"Expected existing {entity_type} id, got {entity_id!r}.",
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
                    f"Unsupported entityRef type {entity_type!r}.",
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
            f"Exactly one Stage must be current; found {len(current_stages)}.",
            "/stages",
        )
    elif current_stages[0].get("id") != project.get("currentStageId"):
        report.error(
            "CURRENT_STAGE_MISMATCH",
            "project.currentStageId does not match the Stage marked current.",
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
                    f"Data-flow transition subject {subject_id!r} has no resolvable entity.",
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
                    f"{option_id!r} is not an option of decision {decision.get('id')}.",
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
                    f"Unknown Next Focus option {option_id!r}.",
                    f"{base}/nextFocusOptionIds/{option_index}",
                )
        require(batch.get("projectStageBefore"), "stage", f"{base}/projectStageBefore")
        require(batch.get("projectStageAfter"), "stage", f"{base}/projectStageAfter")
        for item_index, item in enumerate(batch.get("attentionItems", [])):
            require_entity_refs(item.get("relatedEntities"), f"{base}/attentionItems/{item_index}/relatedEntities")

    guidance = data.get("guidance", {})
    for field_name in ("recommendedOptionId", "selectedOptionId"):
        option_id = guidance.get(field_name)
        if option_id is not None and option_id not in focus_ids:
            report.error(
                "BROKEN_GUIDANCE_OPTION",
                f"Unknown Next Focus option {option_id!r}.",
                f"/guidance/{field_name}",
            )
    for option_index, option in enumerate(guidance.get("options", [])):
        require_entity_refs(option.get("relatedEntities"), f"/guidance/options/{option_index}/relatedEntities")


def validate_rules(
    data: dict[str, Any],
    registry: EntityRegistry,
    report: ValidationReport,
    base_dir: Path,
) -> None:
    reviews = registry.by_type.get("review", {})

    def review_approved(review_id: str | None) -> bool:
        review = reviews.get(review_id or "")
        return bool(review and review.get("status") in {"approved", "waived"})

    # R1 Architecture Gap
    for requirement in _items(data, "requirements"):
        if (
            requirement.get("definitionStatus") == "confirmed"
            and not requirement.get("moduleIds")
            and requirement.get("fulfillmentStatus") != "not_applicable"
        ):
            report.warning(
                "ARCHITECTURE_GAP",
                f"Confirmed requirement {requirement.get('id')} has no Module coverage.",
            )

    # R2 Scope Drift
    for module in _architecture_items(data, "modules"):
        if not module.get("requirementIds") and not str(module.get("rationale", "")).strip():
            report.warning(
                "SCOPE_DRIFT",
                f"Module {module.get('id')} has neither Requirement mapping nor rationale.",
            )
    for work_item in _items(data, "workItems"):
        if not work_item.get("stageId") and not work_item.get("moduleIds") and not work_item.get("requirementIds"):
            report.warning(
                "SCOPE_DRIFT",
                f"Work Item {work_item.get('id')} is unrelated to Stage, Module, and Requirement.",
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
                f"Active Work Item {work_item.get('id')} is assigned to future Stage {work_stage.get('id')} instead of Current Stage {current_stage.get('id')}.",
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
                report.warning(
                    "DEPLOYMENT_DRIFT",
                    f"Active deployment {deployment.get('id')} architecture does not match its Release.",
                )
            if deployment.get("releaseId") == data.get("project", {}).get("currentReleaseId") and deployment.get("architectureVersionId") != current_arch_id:
                report.warning(
                    "DEPLOYMENT_DRIFT",
                    f"Current deployment {deployment.get('id')} does not use Current Architecture.",
                )
            missing_from_release = deployed_modules - set(release.get("moduleIds", []))
            if missing_from_release:
                report.warning(
                    "IMPLEMENTATION_DRIFT",
                    f"Deployment {deployment.get('id')} runs Modules outside Release scope: {sorted(missing_from_release)}.",
                )
            required_release_modules = {
                module_id
                for module_id in release.get("moduleIds", [])
                if modules.get(module_id, {}).get("category") != "external"
            }
            missing_from_deployment = required_release_modules - deployed_modules
            if missing_from_deployment:
                report.warning(
                    "DEPLOYMENT_DRIFT",
                    f"Active deployment {deployment.get('id')} is missing non-external Release Modules: {sorted(missing_from_deployment)}.",
                )
            for module_id in deployed_modules:
                module = modules.get(module_id, {})
                if module.get("architectureScope") in {"target", "historical"}:
                    report.warning(
                        "IMPLEMENTATION_DRIFT",
                        f"Active deployment {deployment.get('id')} runs non-current Module {module_id}.",
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
            report.warning(
                "DEPLOYMENT_DRIFT",
                f"Deployment {deployment.get('id')} uses undeclared Resources: {sorted(undeclared)}.",
            )

    deployed_without_active = {
        release_id
        for release_id, release in releases.items()
        if release.get("status") == "deployed"
        and release_id not in active_release_ids
    }
    for release_id in sorted(deployed_without_active):
        report.warning(
            "DEPLOYMENT_DRIFT",
            f"Deployed Release {release_id} has no active Deployment.",
        )

    current_release = data.get("project", {}).get("currentReleaseId")
    if (
        current_release
        and current_release not in active_release_ids
        and current_release not in deployed_without_active
    ):
        report.warning(
            "DEPLOYMENT_DRIFT",
            f"Current Release {current_release} has no active Deployment.",
        )

    # R4 Review Gap
    module_transition_states = {
        transition.get("subjectId"): transition.get("state")
        for transition in _architecture_items(data, "transitions")
        if transition.get("subjectType") == "module"
    }
    for version in _architecture_items(data, "versions"):
        if version.get("status") in {"review_pending", "accepted"} and not review_approved(version.get("reviewId")):
            report.warning(
                "REVIEW_GAP",
                f"Architecture {version.get('id')} is {version.get('status')} without an approved review.",
            )
    for module in _architecture_items(data, "modules"):
        status = module.get("status", {})
        implemented = status.get("implementationMaturity") in {"functional", "stable"}
        migrating = module_transition_states.get(module.get("id")) in {
            "in_progress",
            "migrating",
            "completed",
        }
        requires_review = (
            status.get("designMaturity") == "review_ready" or implemented or migrating
        )
        if requires_review and not any(
            review_approved(review_id) for review_id in module.get("reviewIds", [])
        ):
            report.warning(
                "REVIEW_GAP",
                f"Module {module.get('id')} is implemented, migrating, or review_ready without an approved Module/Architecture review.",
            )
    for decision in _items(data, "decisions"):
        if decision.get("status") == "review_pending" and not review_approved(decision.get("reviewId")):
            report.warning("REVIEW_GAP", f"Decision {decision.get('id')} is awaiting review.")
    for acceptance in _items(data, "acceptanceCriteria"):
        if acceptance.get("status") == "review_pending" and not review_approved(acceptance.get("reviewId")):
            report.warning(
                "REVIEW_GAP",
                f"Acceptance {acceptance.get('id')} is awaiting review.",
            )
    for change in _items(data, "changes"):
        if change.get("impactLevel") in {"module", "architecture", "deployment", "project"} and not review_approved(change.get("reviewId")):
            report.warning(
                "REVIEW_GAP",
                f"{change.get('impactLevel').title()} Change {change.get('id')} lacks approved review.",
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
            reasons.append(f"module verification is {status.get('verificationStatus')}")
        module_acceptance = [
            acceptance[item_id]
            for item_id in module.get("acceptanceCriteriaIds", [])
            if item_id in acceptance
        ]
        if not module_acceptance:
            reasons.append("no Acceptance is mapped")
        else:
            if any(item.get("status") != "accepted" for item in module_acceptance):
                reasons.append("mapped Acceptance is not accepted")
            if any(item.get("verificationStatus") not in {"passed", "waived"} for item in module_acceptance):
                reasons.append("Acceptance evidence is incomplete")
        if any(
            item.get("verificationStatus") == "passed"
            and not item.get("evidenceReferenceIds")
            for item in module_acceptance
        ):
            reasons.append("passed Acceptance has no evidence")
        module_gates = [
            gates[item_id] for item_id in module.get("gateIds", []) if item_id in gates
        ]
        if not module_gates:
            reasons.append("no Gate is mapped")
        elif any(item.get("status") in {"not_configured", "ready", "running", "failed"} for item in module_gates):
            reasons.append("required Gate is incomplete")
        if any(
            item.get("status") == "passed" and not item.get("evidenceReferenceIds")
            for item in module_gates
        ):
            reasons.append("passed Gate has no evidence")
        if reasons:
            report.warning(
                "VERIFICATION_GAP",
                f"Module {module.get('id')}: " + "; ".join(dict.fromkeys(reasons)) + ".",
            )

    # R6 Baseline Drift
    for baseline in _architecture_items(data, "baselines"):
        if baseline.get("divergence") == "high":
            report.warning(
                "BASELINE_DRIFT",
                f"Baseline {baseline.get('id')} divergence is high.",
            )
        if not str(baseline.get("upgradeStrategy", "")).strip():
            report.warning(
                "BASELINE_DRIFT",
                f"Baseline {baseline.get('id')} lacks an Upgrade Strategy.",
            )
    for module in _architecture_items(data, "modules"):
        source = module.get("source", {})
        if source.get("origin") == "oss" and source.get("changeType") == "modified":
            if not str(source.get("rationale", "")).strip():
                report.warning(
                    "BASELINE_DRIFT",
                    f"OSS Module {module.get('id')} is modified without rationale.",
                )
            if not source.get("changedAreas"):
                report.warning(
                    "BASELINE_DRIFT",
                    f"OSS Module {module.get('id')} is modified without changed areas.",
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
                    f"Module {module.get('id')} differs between Current and Target but has no Module Transition.",
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
                    f"Connection {connection.get('id')} differs between Current and Target but has no Transition.",
                )
        for transition in transitions:
            if transition.get("state") == "blocked":
                report.warning(
                    "TRANSITION_RISK",
                    f"Transition {transition.get('id')} is blocked.",
                )

    # R9 Resource Risk and credential warnings
    for resource in _items(data, "resources"):
        resource_id = resource.get("id")
        credentials = resource.get("access", {}).get("credentials", {})
        mode = credentials.get("mode")
        if mode == "embedded":
            report.warning(
                "EMBEDDED_SECRET_PRESENT",
                f"Resource {resource_id} contains Embedded credentials; masking is not encryption.",
            )
            if resource.get("environment") == "production":
                report.warning(
                    "EMBEDDED_SECRET_IN_PRODUCTION",
                    f"Production Resource {resource_id} contains Embedded credentials.",
                )
        if mode == "external_file":
            secret_path = credentials.get("path", "")
            resolved = (base_dir / secret_path).resolve() if secret_path else None
            if not resolved or not resolved.exists():
                report.warning(
                    "RESOURCE_RISK",
                    f"External credential file for Resource {resource_id} is missing: {secret_path!r}.",
                )
        if mode == "external_store" and (
            not str(credentials.get("provider", "")).strip()
            or not str(credentials.get("reference", "")).strip()
            or not credentials.get("keys")
        ):
            report.warning(
                "RESOURCE_RISK",
                f"External credential store for Resource {resource_id} has an incomplete provider, reference, or key list.",
            )
        if resource.get("type") in {"external_api", "mcp_server"}:
            if not resource.get("usedByModuleIds"):
                report.warning(
                    "RESOURCE_RISK",
                    f"External Resource {resource_id} has no dependent Module.",
                )
            if not str(resource.get("notes", "")).strip():
                report.warning(
                    "RESOURCE_RISK",
                    f"External Resource {resource_id} has no recorded purpose or usage note.",
                )
        if resource.get("environment") == "unknown":
            report.warning(
                "RESOURCE_RISK", f"Resource {resource_id} has unknown environment."
            )
        if resource.get("status") == "active" and not resource.get("usedByDeploymentIds"):
            report.warning(
                "RESOURCE_RISK", f"Active Resource {resource_id} is not tied to a Deployment."
            )

    # R10 Stage Exit Gap
    for stage in _items(data, "stages"):
        if stage.get("status") != "completed":
            continue
        exit_ids = stage.get("exitAcceptanceIds", [])
        if not exit_ids:
            report.warning(
                "STAGE_EXIT_GAP",
                f"Completed Stage {stage.get('id')} has no Exit Acceptance.",
            )
            continue
        incomplete = [
            item_id
            for item_id in exit_ids
            if registry.by_type.get("acceptance", {}).get(item_id, {}).get("verificationStatus")
            not in {"passed", "waived"}
        ]
        if incomplete:
            report.warning(
                "STAGE_EXIT_GAP",
                f"Completed Stage {stage.get('id')} has incomplete Exit Acceptance: {incomplete}.",
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
            report.warning(
                "STAGE_EXIT_GAP",
                f"Current Stage {current_stage.get('id')} has passed Exit Acceptance but core Modules still have Verification blockers: {sorted(blocked_core_modules)}.",
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
                f"Reference {reference.get('id')} path is not available: {location!r}.",
            )


def validate_data(
    data: dict[str, Any],
    schema_path: str | Path,
    *,
    base_dir: str | Path = ".",
) -> ValidationReport:
    report = ValidationReport()
    validate_schema(data, schema_path, report)
    if report.errors:
        return report
    registry = build_registry(data, report)
    validate_cross_references(data, registry, report)
    validate_rules(data, registry, report, Path(base_dir))
    if not report.errors:
        report.info(
            "VALID",
            f"Validation completed with {len(report.warnings)} warning(s).",
        )
    return report


def load_panorama(path: str | Path) -> tuple[dict[str, Any], Path]:
    source = Path(path)
    if not source.is_file():
        raise ValidationRuntimeError(f"Input file does not exist: {source}")
    try:
        if source.suffix.lower() in {".html", ".htm"}:
            return extract_data(source), source.parent
        with source.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, PanoramaIOError) as exc:
        raise ValidationRuntimeError(f"Cannot read {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationRuntimeError("Panorama input must be a JSON object.")
    return data, source.parent


def build_parser() -> argparse.ArgumentParser:
    default_schema = Path(__file__).resolve().parents[1] / "schema" / "panorama.schema.v0.1.json"
    parser = argparse.ArgumentParser(
        description="Validate Panorama Schema, cross-references, and engineering rules."
    )
    parser.add_argument("input", type=Path, help="Panorama JSON or Single HTML")
    parser.add_argument("--schema", type=Path, default=default_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data, base_dir = load_panorama(args.input)
        report = validate_data(data, args.schema, base_dir=base_dir)
    except ValidationRuntimeError as exc:
        print(f"ERROR FILE: {exc}", file=sys.stderr)
        return 2
    for issue in report.issues:
        print(issue.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
