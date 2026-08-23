"""Deterministic, evidence-bound guided explanations for Panorama views."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

import event_store
from panorama_io import compute_canonical_hash, compute_data_hash
from panorama_view_ir import PanoramaViewIRError, validate_model_ir, validate_view_ir
from panorama_view_set import validate_view_set
from validate_panorama import validate_data


ROOT = Path(__file__).resolve().parents[1]
EXPLAIN_PACK_SCHEMA = ROOT / "schema" / "panorama-explain-pack.schema.v0.1.json"
EXPLAIN_PACK_COMPILER = {"id": "panorama-explain-pack-compiler", "version": "0.3.0"}
MAX_PACK_BYTES = 1024 * 1024

PROFILE_CAVEATS = {
    "module": "只解释正式 Module 与 communication；布局邻近不代表依赖或所有权。",
    "module_logic": "只解释当前 Root Module 的内部逻辑、边界端口与外部引用；外部卡片不代表已展开其他模块内部实现。",
    "dependency_dataflow": "静态 dependency/data_flow 不等于 Runtime Call，未投影关系不能由画布位置补全。",
    "deployment_runtime": "Declared Deployment 不等于 Observed Runtime；只有精确 provenance 才能标为 observed。",
    "sequence": "该顺序来自 Engineering Event Action，不得描述为业务运行调用。",
    "lifecycle": "只解释 Adapter 明确提供的 before/after；Outcome 本身不能生成状态迁移。",
    "evolution_risk": "Risk emphasis 不是 Formal Finding、因果证明或 blast radius。",
}

DISPLAY_LABELS = {
    "module": "模块",
    "source_element": "源码元素",
    "resource": "资源",
    "deployment": "部署",
    "release": "发布",
    "data_store": "数据存储",
    "observed": "已观察",
    "declared": "已声明",
    "derived": "派生",
    "inferred": "推断",
    "unknown": "未知",
    "current": "当前",
    "target": "目标",
    "transition": "迁移",
    "historical": "历史",
    "confirmed": "已确认",
    "reviewable": "可评审",
    "draft": "草稿",
    "functional": "功能可用",
    "stable": "稳定",
    "partial": "部分通过",
    "passed": "已通过",
    "failed": "失败",
    "development": "开发",
    "dev": "开发",
    "test": "测试",
    "staging": "预发布",
    "production": "生产",
    "inactive": "未运行",
    "verification": "验证",
    "architecture": "架构",
    "scope": "范围",
    "implementation": "实现",
    "security": "安全",
    "baseline": "基线",
    "other": "其他",
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
    "open": "待处理",
    "monitoring": "监控中",
    "mitigating": "缓解中",
    "accepted": "已接受",
    "closed": "已关闭",
    "approved": "已批准",
    "proposed": "已提议",
    "rejected": "已拒绝",
    "superseded": "已替代",
    "planned": "已计划",
    "ready": "就绪",
    "in_progress": "进行中",
    "blocked": "阻塞",
    "review": "评审中",
    "completed": "已完成",
    "cancelled": "已取消",
    "reversible": "可逆",
    "partially_reversible": "部分可逆",
    "irreversible": "不可逆",
}


def _display(value: Any) -> str:
    raw = str(value)
    label = DISPLAY_LABELS.get(raw)
    return f"{label}（{raw}）" if label else raw

CORE_COLLECTIONS = (
    ("module", ("architecture", "modules")),
    ("connection", ("architecture", "connections")),
    ("business_flow", ("businessFlows",)),
    ("requirement", ("requirements",)),
    ("work_item", ("workItems",)),
    ("decision", ("decisions",)),
    ("risk", ("risks",)),
    ("acceptance", ("acceptanceCriteria",)),
    ("gate", ("gates",)),
    ("review", ("reviews",)),
    ("reference", ("references",)),
    ("transition", ("architecture", "transitions")),
)

FACT_FIELDS = {
    "module": (
        "name", "purpose", "rationale", "architectureScope", "category", "codePath",
    ),
    "connection": (
        "name", "fromModuleId", "toModuleId", "architectureScope", "flowDirection",
        "protocol", "communicationMode", "dataSummary", "authSummary",
        "reliabilitySummary", "rationale",
    ),
    "business_flow": (
        "name", "summary", "architectureScope", "factStatus", "status", "isDefault",
        "trigger", "outcome",
    ),
    "requirement": (
        "type", "priority", "definitionStatus", "fulfillmentStatus", "summary",
    ),
    "work_item": (
        "name", "type", "status", "stageId", "purpose", "problem",
        "expectedImpact", "actualImpact", "startedAt", "dueAt", "completedAt",
    ),
    "decision": (
        "question", "trigger", "context", "impactLevel", "recommendedOptionId",
        "selectedOptionId", "selectionRationale", "reviewId",
    ),
    "risk": (
        "category", "severity", "likelihood", "impact", "status", "description",
        "mitigation", "contingency",
    ),
    "acceptance": (
        "category", "description", "expectedEvidence", "status", "verificationStatus",
        "reviewId",
    ),
    "gate": (
        "gateType", "required", "status", "automation", "summary", "resultSummary",
        "reviewId",
    ),
    "transition": (
        "subjectType", "subjectId", "fromVersionId", "toVersionId", "state",
        "summary", "reason",
    ),
    "review": ("subjectType", "subjectId", "status", "summary", "decision"),
    "reference": ("type", "title", "summary", "sensitivity"),
}

RELATED_ID_FIELDS = (
    "relatedRequirementIds", "relatedModuleIds", "relatedReleaseIds", "moduleIds",
    "acceptanceCriteriaIds", "sourceReferenceIds", "decisionIds", "riskIds",
    "referenceIds", "evidenceReferenceIds", "workItemIds", "blockerDecisionIds",
    "blockerRiskIds",
)
SINGLE_RELATED_ID_FIELDS = ("reviewId", "scopeId", "subjectId", "stageId")


class PanoramaExplainPackError(RuntimeError):
    """The explanation input or output violates a binding or safety invariant."""


def _schema_errors(value: dict[str, Any]) -> list[str]:
    schema = json.loads(EXPLAIN_PACK_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(value),
        key=lambda issue: (tuple(str(part) for part in issue.absolute_path), issue.message),
    )
    return [
        "/" + "/".join(str(part) for part in issue.absolute_path) + f": {issue.message}"
        for issue in errors
    ]


def compute_explain_pack_semantic_hash(pack: dict[str, Any]) -> str:
    value = deepcopy(pack)
    value.pop("integrity", None)
    return compute_canonical_hash(value)


def _project_binding(panorama: dict[str, Any]) -> dict[str, Any]:
    return {
        "projectId": panorama["project"]["id"],
        "panoramaSchemaVersion": panorama["schemaVersion"],
        "revision": panorama["meta"]["revision"],
        "dataHash": compute_data_hash(panorama),
    }


def _model_binding(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "modelId": model["modelId"],
        "semanticHash": model["integrity"]["semanticHash"],
        "asOf": model["asOf"]["value"],
    }


def _view_set_binding(view_set: dict[str, Any]) -> dict[str, Any]:
    return {
        "viewSetId": view_set["viewSetId"],
        "semanticHash": view_set["integrity"]["semanticHash"],
        "defaultChapterId": view_set["defaultChapterId"],
        "chapterCount": len(view_set["chapters"]),
    }


def _view_binding(chapter: dict[str, Any]) -> dict[str, Any]:
    binding = {
        "chapterId": chapter["chapterId"],
        "viewId": chapter["viewId"],
        "profile": chapter["profile"],
        "architectureScope": chapter["architectureScope"],
        "semanticHash": chapter["semanticHash"],
        "layoutHash": chapter["layoutHash"],
    }
    if chapter.get("rootModuleId"):
        binding["rootModuleId"] = chapter["rootModuleId"]
    return binding


def _at_path(value: dict[str, Any], path: tuple[str, ...]) -> list[dict[str, Any]]:
    current: Any = value
    for part in path:
        if not isinstance(current, dict):
            return []
        current = current.get(part)
    if not isinstance(current, list):
        return []
    return [item for item in current if isinstance(item, dict)]


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)[:2048]
    if isinstance(value, list) and all(isinstance(item, (str, int, float, bool)) for item in value):
        return "、".join(str(item) for item in value)[:2048]
    return None


def _core_ref_id(core_type: str, entity_id: str, pointer: str, digest: str) -> str:
    identity = compute_canonical_hash(
        {"type": core_type, "id": entity_id, "jsonPointer": pointer, "digest": digest}
    )
    return f"CORE-{identity[:20].upper()}"


def _core_references(
    panorama: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    model_ids = {item["id"] for item in model["entities"]}
    raw: list[tuple[str, dict[str, Any], str]] = []
    known_core_ids: set[str] = set()
    for core_type, path in CORE_COLLECTIONS:
        for index, item in enumerate(_at_path(panorama, path)):
            entity_id = item.get("id")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            pointer = "/" + "/".join((*path, str(index)))
            raw.append((core_type, item, pointer))
            known_core_ids.add(entity_id)

    references: list[dict[str, Any]] = []
    id_to_ref: dict[str, str] = {}
    for core_type, item, pointer in raw:
        entity_id = item["id"]
        digest = compute_canonical_hash(item)
        core_ref_id = _core_ref_id(core_type, entity_id, pointer, digest)
        id_to_ref[entity_id] = core_ref_id
        related_ids: set[str] = set()
        for field in RELATED_ID_FIELDS:
            values = item.get(field, [])
            if isinstance(values, list):
                related_ids.update(value for value in values if isinstance(value, str))
        for field in SINGLE_RELATED_ID_FIELDS:
            value = item.get(field)
            if isinstance(value, str):
                related_ids.add(value)
        for architecture_ref in item.get("relatedArchitectureRefs", []):
            if isinstance(architecture_ref, dict) and isinstance(architecture_ref.get("id"), str):
                related_ids.add(architecture_ref["id"])
        if core_type == "business_flow":
            for step in item.get("steps", [])[:64]:
                if not isinstance(step, dict):
                    continue
                for field in ("moduleId", "connectionId"):
                    value = step.get(field)
                    if isinstance(value, str):
                        related_ids.add(value)
                for field in ("riskIds", "decisionIds", "referenceIds"):
                    values = step.get(field, [])
                    if isinstance(values, list):
                        related_ids.update(value for value in values if isinstance(value, str))
        facts = []
        for field in FACT_FIELDS.get(core_type, ()):
            value = _string_value(item.get(field))
            if value is not None and value != "":
                facts.append({"label": field, "value": value})
        if core_type == "decision":
            for option in item.get("options", [])[:12]:
                if not isinstance(option, dict) or not isinstance(option.get("id"), str):
                    continue
                option_parts = [
                    str(part)
                    for part in (option.get("title"), option.get("summary"))
                    if isinstance(part, str) and part
                ]
                for label, field in (
                    ("收益", "benefits"),
                    ("代价", "drawbacks"),
                    ("影响", "impacts"),
                    ("风险", "risks"),
                ):
                    values = option.get(field, [])
                    if isinstance(values, list) and values:
                        option_parts.append(
                            f"{label}：" + "；".join(str(value) for value in values[:8])
                        )
                option_parts.append(
                    f"可逆性：{_display(option.get('reversibility', 'unknown'))}"
                )
                option_parts.append(
                    f"预计成本：{_display(option.get('estimatedCost', 'unknown'))}"
                )
                option_text = " · ".join(option_parts)
                facts.append(
                    {"label": f"option:{option['id']}", "value": option_text[:2048]}
                )
        summary = next(
            (
                str(item[key])
                for key in ("summary", "question", "description", "context", "reason")
                if isinstance(item.get(key), str) and item[key]
            ),
            "",
        )[:2048]
        status = next(
            (
                str(item[key])
                for key in ("status", "state", "definitionStatus", "verificationStatus")
                if item.get(key) is not None
            ),
            None,
        )
        evidence_ids: set[str] = set()
        for field in ("evidenceReferenceIds", "referenceIds", "sourceReferenceIds"):
            values = item.get(field, [])
            if isinstance(values, list):
                evidence_ids.update(value for value in values if isinstance(value, str))
        references.append(
            {
                "coreRefId": core_ref_id,
                "type": core_type,
                "id": entity_id,
                "jsonPointer": pointer,
                "digest": digest,
                "label": str(item.get("title") or item.get("name") or entity_id)[:512],
                "summary": summary,
                "status": status[:128] if isinstance(status, str) else None,
                "relatedCoreIds": sorted(related_ids & known_core_ids),
                "relatedModelEntityIds": sorted(related_ids & model_ids),
                "evidenceReferenceIds": sorted(evidence_ids),
                "facts": facts[:32],
            }
        )
    references.sort(key=lambda item: (item["type"], item["id"]))
    return references, id_to_ref


def _claim(
    key: str,
    kind: str,
    text: str,
    *,
    node_ids: Iterable[str] = (),
    edge_ids: Iterable[str] = (),
    model_entity_ids: Iterable[str] = (),
    model_relation_ids: Iterable[str] = (),
    core_ref_ids: Iterable[str] = (),
    evidence_pin_ids: Iterable[str] = (),
    gap: str | None = None,
) -> dict[str, Any]:
    semantics = {
        "kind": kind,
        "text": text[:2048],
        "nodeIds": sorted(set(node_ids))[:64],
        "edgeIds": sorted(set(edge_ids))[:64],
        "modelEntityIds": sorted(set(model_entity_ids))[:64],
        "modelRelationIds": sorted(set(model_relation_ids))[:64],
        "coreRefIds": sorted(set(core_ref_ids))[:64],
        "evidencePinIds": sorted(set(evidence_pin_ids))[:64],
        "gap": gap[:1024] if isinstance(gap, str) else None,
    }
    identity = compute_canonical_hash({"key": key, **semantics})
    return {"claimId": f"CLAIM-{identity[:20].upper()}", **semantics}


def _step(
    story_key: str,
    order: int,
    title: str,
    summary: str,
    claims: list[dict[str, Any]],
    *,
    focus_node_ids: Iterable[str] = (),
    focus_edge_ids: Iterable[str] = (),
    core_ref_ids: Iterable[str] = (),
    evidence_pin_ids: Iterable[str] = (),
    information_gaps: Iterable[str] = (),
    caveats: Iterable[str] = (),
) -> dict[str, Any]:
    identity = compute_canonical_hash(
        {"story": story_key, "order": order, "title": title, "claims": claims}
    )
    return {
        "stepId": f"STEP-{identity[:20].upper()}",
        "order": order,
        "title": title[:512],
        "summary": summary[:2048],
        "claimBlocks": claims,
        "focusNodeIds": sorted(set(focus_node_ids))[:64],
        "focusEdgeIds": sorted(set(focus_edge_ids))[:64],
        "coreRefIds": sorted(set(core_ref_ids))[:64],
        "evidencePinIds": sorted(set(evidence_pin_ids))[:64],
        "informationGaps": sorted(set(information_gaps))[:64],
        "caveats": sorted(set(caveats))[:32],
    }


def _story(
    key: str,
    kind: str,
    title: str,
    summary: str,
    binding: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    core_ref_ids: Iterable[str] = (),
) -> dict[str, Any]:
    story_id = f"STORY-{compute_canonical_hash({'key': key, 'binding': binding})[:20].upper()}"
    return {
        "storyId": story_id,
        "kind": kind,
        "title": title[:512],
        "summary": summary[:2048],
        "viewBinding": binding,
        "coreRefIds": sorted(set(core_ref_ids))[:256],
        "defaultStepId": steps[0]["stepId"],
        "steps": steps,
    }


def _brief_items(items: Iterable[Any], *, limit: int = 4) -> str:
    values = [str(item).strip() for item in items if item is not None and str(item).strip()]
    if not values:
        return "未记录"
    suffix = f"；另有 {len(values) - limit} 项" if len(values) > limit else ""
    return "；".join(values[:limit]) + suffix


def _module_design_chain(
    module: dict[str, Any],
    panorama: dict[str, Any],
    view: dict[str, Any],
    id_to_ref: dict[str, str],
) -> tuple[str, list[str], list[str], list[str]]:
    requirements = {item["id"]: item for item in panorama.get("requirements", [])}
    decisions = {item["id"]: item for item in panorama.get("decisions", [])}
    risks = {item["id"]: item for item in panorama.get("risks", [])}
    layers = {
        item["id"]: item
        for item in panorama.get("architecture", {}).get("layers", [])
    }
    connections = panorama.get("architecture", {}).get("connections", [])
    current = module.get("currentDesign") if isinstance(module.get("currentDesign"), dict) else {}
    target = module.get("targetDesign") if isinstance(module.get("targetDesign"), dict) else {}
    design = target or current
    requirement_text = _brief_items(
        f"{requirements[item].get('title', item)}：{requirements[item].get('summary', '')}"
        for item in module.get("requirementIds", [])
        if item in requirements
    )
    reason_parts = [module.get("rationale", "")]
    source = module.get("source") if isinstance(module.get("source"), dict) else {}
    if source.get("rationale") and source.get("rationale") not in reason_parts:
        reason_parts.append(source["rationale"])
    for decision_id in module.get("decisionIds", [])[:3]:
        decision = decisions.get(decision_id)
        if decision:
            reason_parts.append(
                f"决策 {decision_id}：{decision.get('question') or decision.get('context') or decision.get('summary', '')}"
            )
    function_parts = [module.get("purpose", "")]
    if design.get("responsibilities"):
        function_parts.append("职责：" + _brief_items(design["responsibilities"]))
    if design.get("nonResponsibilities"):
        function_parts.append("边界=" + _brief_items(design["nonResponsibilities"]))
    layer = layers.get(module.get("targetLayerId") or module.get("layerId"), {})
    role_parts = [
        f"位于 {layer.get('displayName') or layer.get('name', module.get('layerId', '未记录层级'))}",
        f"状态所有权={design.get('stateOwnership') or '未记录'}",
        f"部署角色={design.get('deploymentRole') or '未记录'}",
    ]
    edge_by_relation = {
        item.get("relationRef", {}).get("id"): item
        for item in view.get("edges", [])
        if isinstance(item.get("relationRef"), dict)
    }
    interaction_parts: list[str] = []
    edge_ids: list[str] = []
    relation_ids: list[str] = []
    for connection in connections:
        if module["id"] not in {connection.get("fromModuleId"), connection.get("toModuleId")}:
            continue
        direction = (
            f"向 {connection.get('toModuleId')} 输出"
            if connection.get("fromModuleId") == module["id"]
            else f"从 {connection.get('fromModuleId')} 输入"
        )
        interaction_parts.append(
            f"{direction}：{connection.get('protocol') or '协议未记录'} / "
            f"{connection.get('communicationMode') or '模式未记录'} / "
            f"{connection.get('dataSummary') or '数据未记录'}"
        )
        edge = edge_by_relation.get(connection.get("id"))
        if edge:
            edge_ids.append(edge["id"])
            relation_ids.append(connection["id"])
    technology_text = _brief_items(
        f"{item.get('name', '未命名技术')}（{item.get('role', '用途未记录')} / {item.get('status', '状态未记录')}）"
        for item in design.get("technologies", [])
        if isinstance(item, dict)
    )
    risk_text = _brief_items(
        f"{item}：{risks[item].get('description') or risks[item].get('impact', '')}"
        for item in module.get("riskIds", [])
        if item in risks
    )
    text = "\n".join(
        (
            "设计需求｜" + requirement_text,
            "设计原因｜" + _brief_items(reason_parts),
            "实现功能｜" + _brief_items(function_parts),
            "系统角色｜" + "；".join(role_parts),
            "交互方式｜" + _brief_items(interaction_parts, limit=6),
            "技术选型｜" + technology_text,
            "关联风险｜" + risk_text,
        )
    )
    core_ids = {
        id_to_ref[item]
        for item in (
            [module["id"]]
            + module.get("requirementIds", [])
            + module.get("decisionIds", [])
            + module.get("riskIds", [])
        )
        if item in id_to_ref
    }
    core_ids.update(
        id_to_ref[item["id"]]
        for item in connections
        if item.get("id") in id_to_ref
        and module["id"] in {item.get("fromModuleId"), item.get("toModuleId")}
    )
    return text, sorted(core_ids), sorted(set(edge_ids)), sorted(set(relation_ids))


def _view_story(
    chapter: dict[str, Any],
    view: dict[str, Any],
    model: dict[str, Any],
    panorama: dict[str, Any],
    id_to_ref: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    key = f"view:{view['viewId']}"
    nodes = view["nodes"]
    edges = view["edges"]
    node_ids = [item["id"] for item in nodes]
    edge_ids = [item["id"] for item in edges]
    entity_ids = [item["entityRef"]["id"] for item in nodes]
    relation_ids = [item["relationRef"]["id"] for item in edges]
    evidence_ids = sorted(
        {
            pin
            for item in [*nodes, *edges]
            for pin in item.get("evidencePinIds", [])
        }
    )
    generated_gaps: list[str] = []
    steps = [
        _step(
            key,
            0,
            "如何阅读这个视图",
            view["description"],
            [
                _claim(
                    f"{key}:navigation",
                    "navigation",
                    f"本讲解只沿用 {chapter['label']} 中已经绑定的实体、关系与证据。",
                )
            ],
            caveats=[PROFILE_CAVEATS[view["profile"]]],
        )
    ]
    entity_by_id = {item["id"]: item for item in model["entities"]}
    module_by_id = {
        item["id"]: item
        for item in panorama.get("architecture", {}).get("modules", [])
    }
    if nodes:
        counts: dict[str, int] = {}
        for entity_id in entity_ids:
            kind = _display(entity_by_id[entity_id]["kind"])
            counts[kind] = counts.get(kind, 0) + 1
        count_text = "、".join(f"{kind} {count}" for kind, count in sorted(counts.items()))
        steps.append(
            _step(
                key,
                len(steps),
                "整图概览、范围与边界",
                f"当前章节投影 {len(nodes)} 个节点和 {len(edges)} 条关系；先理解整体，再逐项查看节点。",
                [
                    _claim(
                        f"{key}:nodes",
                        "bound_fact",
                        f"该 View IR 包含 {count_text}，架构范围为 {_display(chapter['architectureScope'])}。",
                        node_ids=node_ids,
                        model_entity_ids=entity_ids,
                        evidence_pin_ids=evidence_ids,
                    )
                ],
                focus_node_ids=node_ids,
                evidence_pin_ids=evidence_ids,
                caveats=[PROFILE_CAVEATS[view["profile"]]],
            )
        )
        reserved_steps = 1 + int(bool(evidence_ids)) + int(bool(view.get("informationGaps", [])))
        node_step_budget = max(1, 16 - len(steps) - reserved_steps)
        chunk_size = max(1, (len(nodes) + node_step_budget - 1) // node_step_budget)
        for start in range(0, len(nodes), chunk_size):
            node_chunk = nodes[start : start + chunk_size]
            claims: list[dict[str, Any]] = []
            chunk_evidence: set[str] = set()
            for node in node_chunk:
                entity = entity_by_id[node["entityRef"]["id"]]
                attributes = entity.get("attributes", {})
                status_parts = []
                for label, field in (
                    ("设计", "designMaturity"),
                    ("实现", "implementationMaturity"),
                    ("验证", "verificationStatus"),
                    ("运行", "runtimeStatus"),
                ):
                    if attributes.get(field) not in (None, ""):
                        status_parts.append(f"{label}={_display(attributes[field])}")
                scopes = (
                    "、".join(
                        _display(scope) for scope in entity.get("architectureScopes", [])
                    )
                    or "未记录"
                )
                purpose = (entity.get("purpose") or "未记录职责说明").rstrip(
                    "。.!！?？；; "
                )
                node_evidence = node.get("evidencePinIds", [])
                chunk_evidence.update(node_evidence)
                claim_core_refs: list[str] = []
                claim_edge_ids: list[str] = []
                claim_relation_ids: list[str] = []
                module = module_by_id.get(entity["id"])
                if entity["kind"] == "module" and module is not None:
                    chain, claim_core_refs, claim_edge_ids, claim_relation_ids = _module_design_chain(
                        module, panorama, view, id_to_ref
                    )
                    detail = (
                        f"{entity.get('displayLabel') or entity['name']}（{entity.get('technicalLabel') or entity['name']} · {entity['id']}）｜事实状态：{_display(entity['factStatus'])} / "
                        f"{_display(entity['authority'])}；架构范围：{scopes}。\n{chain}"
                    )
                else:
                    detail = (
                        f"{entity.get('displayLabel') or entity['name']}（{entity.get('technicalLabel') or entity['name']} · {entity['id']}）属于 {_display(entity['kind'])}。"
                        f"职责：{purpose}。事实状态：{_display(entity['factStatus'])} / {_display(entity['authority'])}；"
                        f"架构范围：{scopes}。"
                    )
                    if status_parts:
                        detail += "当前状态：" + "；".join(status_parts) + "。"
                    if attributes.get("codePath"):
                        detail += f"代码位置：{attributes['codePath']}。"
                claims.append(
                    _claim(
                        f"{key}:node:{node['id']}",
                        "bound_fact",
                        detail,
                        node_ids=[node["id"]],
                        edge_ids=claim_edge_ids,
                        model_entity_ids=[entity["id"]],
                        model_relation_ids=claim_relation_ids,
                        core_ref_ids=claim_core_refs,
                        evidence_pin_ids=node_evidence,
                    )
                )
            title = (
                f"节点详解：{entity_by_id[node_chunk[0]['entityRef']['id']].get('displayLabel') or entity_by_id[node_chunk[0]['entityRef']['id']]['name']}"
                if len(node_chunk) == 1
                else f"节点详解 {start + 1}–{start + len(node_chunk)}"
            )
            steps.append(
                _step(
                    key,
                    len(steps),
                    title,
                    "逐节点沿设计需求、设计原因、实现功能、系统角色、交互方式、技术选型与风险形成完整逻辑链。",
                    claims,
                    focus_node_ids=[node["id"] for node in node_chunk],
                    evidence_pin_ids=chunk_evidence,
                    caveats=[PROFILE_CAVEATS[view["profile"]]],
                )
            )
    else:
        gap = f"view_contains_no_nodes:{view['viewId']}"
        generated_gaps.append(gap)
        steps.append(
            _step(
                key,
                len(steps),
                "当前没有可讲解节点",
                "空视图不能被解释为项目中不存在对应事实。",
                [
                    _claim(
                        f"{key}:empty",
                        "information_gap",
                        "当前 View IR 没有投影节点；这表示输入或范围内没有可显示的绑定对象。",
                        gap=gap,
                    )
                ],
                information_gaps=[gap],
                caveats=[PROFILE_CAVEATS[view["profile"]]],
            )
        )
    if edges:
        relation_by_id = {item["id"]: item for item in model["relations"]}
        kinds: dict[str, int] = {}
        for relation_id in relation_ids:
            kind = relation_by_id[relation_id]["kind"]
            kinds[kind] = kinds.get(kind, 0) + 1
        kind_text = "、".join(f"{kind} {count}" for kind, count in sorted(kinds.items()))
        relation_claims: list[dict[str, Any]] = []
        for edge in edges[:16]:
            relation = relation_by_id[edge["relationRef"]["id"]]
            semantics = relation.get("semantics", {})
            detail_parts = [
                f"{relation['fromEntityId']} → {relation['toEntityId']}",
                f"关系类型={relation['kind']}",
            ]
            for label, field in (
                ("协议", "protocol"),
                ("模式", "mode"),
                ("数据", "dataSummary"),
                ("顺序", "order"),
            ):
                if semantics.get(field) not in (None, ""):
                    detail_parts.append(f"{label}={semantics[field]}")
            relation_claims.append(
                _claim(
                    f"{key}:edge:{edge['id']}",
                    "bound_fact",
                    f"{edge['label']}：" + "；".join(detail_parts) + "。",
                    edge_ids=[edge["id"]],
                    model_relation_ids=[relation["id"]],
                    evidence_pin_ids=edge.get("evidencePinIds", []),
                )
            )
        if len(edges) > 16:
            generated_gaps.append(f"view_relation_details_truncated:{view['viewId']}:{len(edges)}")
        steps.append(
            _step(
                key,
                len(steps),
                "关键关系与阅读路径",
                f"当前章节投影 {len(edges)} 条关系，关系类型为 {kind_text}；以下逐条解释方向、端点与已有语义。",
                relation_claims,
                focus_edge_ids=edge_ids,
                evidence_pin_ids=evidence_ids,
                caveats=[PROFILE_CAVEATS[view["profile"]]],
            )
        )
    else:
        steps.append(
            _step(
                key,
                len(steps),
                "关系阅读边界",
                "当前章节没有投影关系。",
                [
                    _claim(
                        f"{key}:no-edges",
                        "navigation",
                        "不要根据节点相邻、同组或连线缺失推断未记录关系。",
                    )
                ],
                caveats=[PROFILE_CAVEATS[view["profile"]]],
            )
        )
    if evidence_ids:
        steps.append(
            _step(
                key,
                len(steps),
                "证据与时间边界",
                f"当前章节引用 {len(evidence_ids)} 个 Evidence Pin。",
                [
                    _claim(
                        f"{key}:evidence",
                        "bound_fact",
                        "这些节点与关系的可见断言由列出的 Evidence Pin 支撑；离线状态不自动证明 Source 仍然新鲜。",
                        node_ids=node_ids,
                        edge_ids=edge_ids,
                        evidence_pin_ids=evidence_ids,
                    )
                ],
                focus_node_ids=node_ids,
                focus_edge_ids=edge_ids,
                evidence_pin_ids=evidence_ids,
            )
        )
    gaps = list(view.get("informationGaps", []))
    if gaps:
        visible_gaps = gaps[:16]
        if len(gaps) > 16:
            generated_gaps.append(f"view_information_gaps_truncated:{view['viewId']}:{len(gaps)}")
        steps.append(
            _step(
                key,
                len(steps),
                "未知项与限制",
                "这些缺口必须保留，不能由讲解文本自动补齐。",
                [
                    _claim(
                        f"{key}:gap:{index}",
                        "information_gap",
                        gap,
                        gap=gap,
                    )
                    for index, gap in enumerate(visible_gaps)
                ],
                information_gaps=visible_gaps,
                caveats=[PROFILE_CAVEATS[view["profile"]]],
            )
        )
    return (
        _story(
            key,
            "view",
            f"{chapter['label']} · 逐步讲解",
            view["description"],
            _view_binding(chapter),
            steps,
        ),
        generated_gaps,
    )


def _module_logic_stories(
    chapter: dict[str, Any],
    view: dict[str, Any],
    model: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compile child-canvas explanations without expanding external modules."""

    entity_by_id = {item["id"]: item for item in model["entities"]}
    internal_nodes = [
        item for item in view["nodes"] if item["kind"] == "module_logic_node"
    ]
    ports = [item for item in view["nodes"] if item["kind"] == "boundary_port"]
    external_nodes = [
        item
        for item in view["nodes"]
        if item["entityRef"]["id"] in set(view.get("extensions", {}).get("externalEntityIds", []))
    ]
    binding = _view_binding(chapter)
    generated_gaps: list[str] = []
    stories: list[dict[str, Any]] = []

    overview_key = f"module-logic-overview:{view['viewId']}"
    overview_evidence = sorted(
        {
            pin
            for item in [*view["nodes"], *view["edges"]]
            for pin in item.get("evidencePinIds", [])
        }
    )
    overview_steps = [
        _step(
            overview_key,
            0,
            "这张模块子画布说明什么",
            view["description"],
            [
                _claim(
                    f"{overview_key}:scope",
                    "bound_fact",
                    f"当前只展开模块 {view['rootModuleId']}：内部逻辑 {len(internal_nodes)} 个、边界端口 {len(ports)} 个、外部引用 {len(external_nodes)} 个。",
                    node_ids=[item["id"] for item in view["nodes"]],
                    model_entity_ids=[item["entityRef"]["id"] for item in view["nodes"]],
                    evidence_pin_ids=overview_evidence,
                )
            ],
            focus_node_ids=[item["id"] for item in view["nodes"]],
            evidence_pin_ids=overview_evidence,
            caveats=[PROFILE_CAVEATS["module_logic"]],
        ),
        _step(
            overview_key,
            1,
            "入口、出口与主处理路径",
            "先沿边界端口进入内部节点，再沿已绑定逻辑边阅读；不能根据位置补造执行顺序。",
            [
                _claim(
                    f"{overview_key}:path",
                    "bound_fact",
                    f"当前 View 明确投影 {len(view['edges'])} 条内部或边界关系；关系方向来自 View IR，而不是画布布局。",
                    node_ids=[item["id"] for item in [*ports, *internal_nodes]],
                    edge_ids=[item["id"] for item in view["edges"]],
                    model_entity_ids=[item["entityRef"]["id"] for item in [*ports, *internal_nodes]],
                    model_relation_ids=[item["relationRef"]["id"] for item in view["edges"]],
                    evidence_pin_ids=overview_evidence,
                )
            ],
            focus_node_ids=[item["id"] for item in [*ports, *internal_nodes]],
            focus_edge_ids=[item["id"] for item in view["edges"]],
            evidence_pin_ids=overview_evidence,
        ),
        _step(
            overview_key,
            2,
            "外部交互边界",
            "外部模块与资源只显示引用身份、端口、协议和数据绑定，不展开对方内部逻辑。",
            [
                _claim(
                    f"{overview_key}:boundary",
                    "bound_fact",
                    f"当前边界包含 {len(ports)} 个端口和 {len(external_nodes)} 个外部引用。",
                    node_ids=[item["id"] for item in [*ports, *external_nodes]],
                    model_entity_ids=[item["entityRef"]["id"] for item in [*ports, *external_nodes]],
                    evidence_pin_ids=overview_evidence,
                )
            ],
            focus_node_ids=[item["id"] for item in [*ports, *external_nodes]],
            evidence_pin_ids=overview_evidence,
            caveats=["双击外部引用可以导航到对方自己的子画布，但当前场景不会复制其内部节点。"],
        ),
        _step(
            overview_key,
            3,
            "证据、新鲜度与信息缺口",
            "Current 内部逻辑是有界源码归纳；Target 内部逻辑是正式设计。两者都必须保留来源与未知项。",
            [
                _claim(
                    f"{overview_key}:evidence",
                    "bound_fact",
                    f"当前子画布绑定 {len(overview_evidence)} 个 Evidence Pin，并保留 {len(view.get('informationGaps', []))} 个信息缺口。",
                    node_ids=[item["id"] for item in view["nodes"]],
                    edge_ids=[item["id"] for item in view["edges"]],
                    evidence_pin_ids=overview_evidence,
                )
            ],
            focus_node_ids=[item["id"] for item in view["nodes"]],
            focus_edge_ids=[item["id"] for item in view["edges"]],
            evidence_pin_ids=overview_evidence,
            information_gaps=view.get("informationGaps", []),
        ),
    ]
    stories.append(
        _story(
            overview_key,
            "module_logic_overview",
            f"模块整体 · {view['title']}",
            "按范围、内部路径、外部边界和证据逐步理解当前模块。",
            binding,
            overview_steps,
        )
    )

    if internal_nodes:
        node_key = f"module-logic-nodes:{view['viewId']}"
        node_steps = [
            _step(
                node_key,
                0,
                "内部逻辑节点阅读方式",
                "每个节点沿设计需求、设计原因、实现逻辑、系统角色、交互和证据形成完整链。",
                [_claim(f"{node_key}:intro", "navigation", "节点名称是业务实现逻辑摘要；函数、类和文件只作为 Evidence，不直接成为画布节点。")],
                caveats=[PROFILE_CAVEATS["module_logic"]],
            )
        ]
        for node in internal_nodes[:15]:
            entity = entity_by_id[node["entityRef"]["id"]]
            attributes = entity.get("attributes", {})
            incoming = [edge for edge in view["edges"] if edge["toNodeId"] == node["id"]]
            outgoing = [edge for edge in view["edges"] if edge["fromNodeId"] == node["id"]]
            interactions = _brief_items(
                [f"输入：{edge['label']}" for edge in incoming]
                + [f"输出：{edge['label']}" for edge in outgoing],
                limit=8,
            )
            detail = "\n".join(
                (
                    "设计需求｜" + (entity.get("purpose") or "未记录"),
                    "设计原因｜" + (attributes.get("rationale") or "未记录"),
                    "实现逻辑｜" + (attributes.get("implementationSummary") or "未记录"),
                    f"系统角色｜{attributes.get('logicType', 'unknown')}，位于 {view['rootModuleId']} 内部",
                    "交互方式｜" + interactions,
                    "技术与证据｜" + _brief_items(node.get("evidencePinIds", [])),
                    "循环退出｜" + (attributes.get("loopExitCondition") or "不适用或未记录"),
                )
            )
            node_steps.append(
                _step(
                    node_key,
                    len(node_steps),
                    f"节点 · {entity.get('displayLabel') or entity['name']}",
                    entity.get("purpose") or "未记录节点用途。",
                    [
                        _claim(
                            f"{node_key}:{node['id']}",
                            "bound_fact",
                            detail,
                            node_ids=[node["id"]],
                            edge_ids=[edge["id"] for edge in [*incoming, *outgoing]],
                            model_entity_ids=[entity["id"]],
                            model_relation_ids=[edge["relationRef"]["id"] for edge in [*incoming, *outgoing]],
                            evidence_pin_ids=node.get("evidencePinIds", []),
                        )
                    ],
                    focus_node_ids=[node["id"]],
                    focus_edge_ids=[edge["id"] for edge in [*incoming, *outgoing]],
                    evidence_pin_ids=node.get("evidencePinIds", []),
                )
            )
        if len(internal_nodes) > 15:
            generated_gaps.append(f"module_logic_node_story_truncated:{view['viewId']}:{len(internal_nodes)}")
        stories.append(
            _story(
                node_key,
                "module_logic_node",
                f"节点逻辑 · {view['title']}",
                "逐节点解释业务实现逻辑，不下沉到函数级源码。",
                binding,
                node_steps,
            )
        )

    if ports:
        port_key = f"module-logic-boundary:{view['viewId']}"
        port_steps = []
        for port in ports[:16]:
            entity = entity_by_id[port["entityRef"]["id"]]
            attributes = entity.get("attributes", {})
            port_edges = [
                edge for edge in view["edges"] if port["id"] in {edge["fromNodeId"], edge["toNodeId"]}
            ]
            detail = (
                f"方向={attributes.get('portDirection', 'unknown')}；协议={attributes.get('protocol') or '未记录'}；"
                f"数据={attributes.get('dataSummary') or entity.get('purpose') or '未记录'}；"
                f"外部对象={attributes.get('externalEntityId') or '未记录'}；"
                f"绑定={attributes.get('bindingKind', 'unknown')}:{attributes.get('bindingId') or '未记录'}。"
            )
            port_steps.append(
                _step(
                    port_key,
                    len(port_steps),
                    f"边界交互 · {entity.get('displayLabel') or entity['name']}",
                    "解释外部对象、端口方向、数据、协议与正式绑定。",
                    [
                        _claim(
                            f"{port_key}:{port['id']}",
                            "bound_fact",
                            detail,
                            node_ids=[port["id"]],
                            edge_ids=[edge["id"] for edge in port_edges],
                            model_entity_ids=[entity["id"]],
                            model_relation_ids=[edge["relationRef"]["id"] for edge in port_edges],
                            evidence_pin_ids=port.get("evidencePinIds", []),
                        )
                    ],
                    focus_node_ids=[port["id"]],
                    focus_edge_ids=[edge["id"] for edge in port_edges],
                    evidence_pin_ids=port.get("evidencePinIds", []),
                    caveats=["外部引用卡只证明边界绑定，不证明对方模块的内部实现。"],
                )
            )
        stories.append(
            _story(
                port_key,
                "boundary_interaction",
                f"外部交互 · {view['title']}",
                "沿边界端口解释模块间数据交互。",
                binding,
                port_steps,
            )
        )
    return stories, generated_gaps


def _chapter_for_model_ids(
    chapters: list[dict[str, Any]], views_by_id: dict[str, dict[str, Any]], model_ids: set[str]
) -> dict[str, Any]:
    for chapter in chapters:
        view = views_by_id[chapter["viewId"]]
        visible_ids = {node["entityRef"]["id"] for node in view["nodes"]}
        if chapter.get("profile") == "module_logic":
            visible_ids &= set(view.get("extensions", {}).get("internalEntityIds", []))
        if visible_ids & model_ids:
            return chapter
    return next(
        (chapter for chapter in chapters if chapter.get("profile") != "module_logic"),
        chapters[0],
    )


def _progress_story(
    ref: dict[str, Any], chapter: dict[str, Any], view: dict[str, Any], id_to_ref: dict[str, str]
) -> dict[str, Any]:
    key = f"progress:{ref['id']}"
    facts = _facts(ref)
    focus = _focus_for_ids(view, set(ref["relatedModelEntityIds"]))
    related_refs = [id_to_ref[item] for item in ref["relatedCoreIds"] if item in id_to_ref]
    core_refs = [ref["coreRefId"], *related_refs]
    status = ref.get("status") or facts.get("status") or "unknown"
    purpose = facts.get("purpose") or ref.get("summary") or "未记录"
    problem = facts.get("problem") or "未记录"
    expected = facts.get("expectedImpact") or "未记录"
    actual = facts.get("actualImpact") or "尚未记录"
    steps = [
        _step(
            key, 0, "当前开发工作与状态", f"{ref['label']}；状态={_display(status)}。",
            [_claim(f"{key}:status", "bound_fact", f"工作项 {ref['label']} 当前状态为 {_display(status)}；目的：{purpose}", node_ids=focus, model_entity_ids=ref["relatedModelEntityIds"], core_ref_ids=[ref["coreRefId"]])],
            focus_node_ids=focus, core_ref_ids=[ref["coreRefId"]],
        ),
        _step(
            key, 1, "AI/开发当前位于哪里", "只按正式架构引用定位到 Layer、Module 或内部 Logic Node。",
            [_claim(f"{key}:location", "bound_fact", f"当前工作精确绑定 {len(focus)} 个本场景节点；未绑定对象不会按名称猜测。", node_ids=focus, model_entity_ids=ref["relatedModelEntityIds"], core_ref_ids=core_refs)],
            focus_node_ids=focus, core_ref_ids=core_refs,
        ),
        _step(
            key, 2, "为什么做、预期与实际结果", f"问题：{problem}；预期：{expected}；实际：{actual}。",
            [_claim(f"{key}:outcome", "bound_fact", f"问题｜{problem}\n预期结果｜{expected}\n实际结果｜{actual}", node_ids=focus, core_ref_ids=[ref["coreRefId"]])],
            focus_node_ids=focus, core_ref_ids=[ref["coreRefId"]],
        ),
        _step(
            key, 3, "实现、验证、批准与部署边界", "工作项状态不能自动提升为验证、批准或部署事实。",
            [_claim(f"{key}:boundary", "navigation", "实现进度来自 WorkItem；验证看 Acceptance/Gate，批准看 Approval，部署看 Release/Deployment。缺少对应证据时保持未知。")],
            focus_node_ids=focus, core_ref_ids=core_refs,
            caveats=["代码存在、事件发生或 WorkItem completed 都不能单独证明已验证、已批准或已部署。"],
        ),
    ]
    return _story(key, "progress", f"进度 · {ref['label']}", purpose, _view_binding(chapter), steps, core_ref_ids=core_refs)


def _focus_for_ids(view: dict[str, Any], model_ids: set[str]) -> list[str]:
    return sorted(
        node["id"] for node in view["nodes"] if node["entityRef"]["id"] in model_ids
    )[:64]


def _facts(ref: dict[str, Any]) -> dict[str, str]:
    return {item["label"]: item["value"] for item in ref["facts"]}


def _decision_story(
    ref: dict[str, Any], chapter: dict[str, Any], view: dict[str, Any], id_to_ref: dict[str, str]
) -> dict[str, Any]:
    key = f"decision:{ref['id']}"
    facts = _facts(ref)
    focus = _focus_for_ids(view, set(ref["relatedModelEntityIds"]))
    related_refs = [id_to_ref[item] for item in ref["relatedCoreIds"] if item in id_to_ref]
    core_refs = [ref["coreRefId"], *related_refs]
    steps = [
        _step(
            key, 0, "决策问题与状态", ref["summary"],
            [_claim(f"{key}:question", "bound_fact", f"{ref['label']}；状态为 {_display(ref['status'] or 'unknown')}。", core_ref_ids=[ref["coreRefId"]])],
            focus_node_ids=focus, core_ref_ids=[ref["coreRefId"]],
        )
    ]
    context_parts = [facts.get(name) for name in ("trigger", "context") if facts.get(name)]
    if context_parts:
        why_claims = [
            _claim(
                f"{key}:context",
                "bound_fact",
                "；".join(context_parts),
                core_ref_ids=[ref["coreRefId"]],
            )
        ]
        why_gaps: list[str] = []
    else:
        gap = f"decision_rationale_not_recorded:{ref['id']}"
        why_claims = [
            _claim(
                f"{key}:context-gap",
                "information_gap",
                "当前 Decision 没有记录 trigger/context，不能从标题推断为什么现在必须决策。",
                gap=gap,
            )
        ]
        why_gaps = [gap]
    steps.append(
        _step(
            key,
            len(steps),
            "为什么现在需要这个决策",
            "；".join(context_parts) if context_parts else "当前成因信息不完整。",
            why_claims,
            focus_node_ids=focus,
            core_ref_ids=[ref["coreRefId"]] if context_parts else [],
            information_gaps=why_gaps,
        )
    )
    option_facts = [item for item in ref["facts"] if item["label"].startswith("option:")]
    if option_facts:
        steps.append(
            _step(
                key, len(steps), "候选选项及其细节", f"已记录 {len(option_facts)} 个选项。",
                [_claim(f"{key}:options", "bound_fact", "；".join(f"{item['label'][7:]}：{item['value']}" for item in option_facts), core_ref_ids=[ref["coreRefId"]])],
                core_ref_ids=[ref["coreRefId"]],
            )
        )
        steps.append(
            _step(
                key,
                len(steps),
                "不同选择可能导致什么结果",
                "结果只来自各 Option 已记录的收益、代价、影响、风险、可逆性和预计成本。",
                [
                    _claim(
                        f"{key}:outcomes",
                        "bound_fact",
                        "；".join(
                            f"{item['label'][7:]}：{item['value']}" for item in option_facts
                        ),
                        core_ref_ids=[ref["coreRefId"]],
                    )
                ],
                core_ref_ids=[ref["coreRefId"]],
            )
        )
    choice = "；".join(
        f"{label}={facts[label]}"
        for label in ("recommendedOptionId", "selectedOptionId", "selectionRationale")
        if facts.get(label)
    )
    if choice:
        steps.append(
            _step(
                key, len(steps), "推荐与已选择项", choice,
                [_claim(f"{key}:choice", "bound_fact", choice, core_ref_ids=[ref["coreRefId"]])],
                core_ref_ids=[ref["coreRefId"]],
            )
        )
    if len(core_refs) > 1 or focus:
        steps.append(
            _step(
                key, len(steps), "影响与关联", "只展示正式 ID 关系，不从文本猜测影响。",
                [_claim(f"{key}:related", "bound_fact", f"该决策绑定 {len(focus)} 个当前 View 节点和 {len(related_refs)} 个治理实体。", node_ids=focus, model_entity_ids=ref["relatedModelEntityIds"], core_ref_ids=core_refs)],
                focus_node_ids=focus, core_ref_ids=core_refs,
            )
        )
    return _story(key, "decision", f"决策 · {ref['label']}", ref["summary"], _view_binding(chapter), steps, core_ref_ids=core_refs)


def _risk_story(
    ref: dict[str, Any], chapter: dict[str, Any], view: dict[str, Any], id_to_ref: dict[str, str]
) -> dict[str, Any]:
    key = f"risk:{ref['id']}"
    facts = _facts(ref)
    focus = _focus_for_ids(view, set(ref["relatedModelEntityIds"]))
    related_refs = [id_to_ref[item] for item in ref["relatedCoreIds"] if item in id_to_ref]
    core_refs = [ref["coreRefId"], *related_refs]

    cause = facts.get("description") or ref["summary"]
    if cause:
        cause_claims = [
            _claim(
                f"{key}:cause",
                "bound_fact",
                cause,
                node_ids=focus,
                model_entity_ids=ref["relatedModelEntityIds"],
                core_ref_ids=[ref["coreRefId"]],
            )
        ]
        cause_gaps: list[str] = []
    else:
        gap = f"risk_cause_not_recorded:{ref['id']}"
        cause_claims = [
            _claim(
                f"{key}:cause-gap",
                "information_gap",
                "当前 Risk 没有记录 description，不能从严重度或关联对象推断成因。",
                gap=gap,
            )
        ]
        cause_gaps = [gap]

    impact = facts.get("impact")
    impact_gap = f"risk_impact_not_recorded:{ref['id']}"
    mitigation = facts.get("mitigation")
    mitigation_gap = f"risk_mitigation_not_recorded:{ref['id']}"
    steps = [
        _step(
            key,
            0,
            "风险是什么与当前状态",
            f"{ref['label']}；类别={_display(facts.get('category', 'unknown'))}；严重度={_display(facts.get('severity', 'unknown'))}；状态={_display(ref['status'] or 'unknown')}。",
            [
                _claim(
                    f"{key}:identity",
                    "bound_fact",
                    f"风险 {ref['label']} 当前状态为 {_display(ref['status'] or 'unknown')}，严重度为 {_display(facts.get('severity', 'unknown'))}。",
                    core_ref_ids=[ref["coreRefId"]],
                )
            ],
            focus_node_ids=focus,
            core_ref_ids=[ref["coreRefId"]],
        ),
        _step(
            key,
            1,
            "为什么会有这个风险",
            cause or "当前成因信息不完整。",
            cause_claims,
            focus_node_ids=focus,
            core_ref_ids=[ref["coreRefId"]] if cause else [],
            information_gaps=cause_gaps,
        ),
        _step(
            key,
            2,
            "可能影响与导致的结果",
            impact or "当前影响信息不完整。",
            [
                _claim(
                    f"{key}:impact" if impact else f"{key}:impact-gap",
                    "bound_fact" if impact else "information_gap",
                    impact or "当前 Risk 没有记录 impact，不能推断可能结果。",
                    core_ref_ids=[ref["coreRefId"]] if impact else [],
                    gap=None if impact else impact_gap,
                )
            ],
            focus_node_ids=focus,
            core_ref_ids=[ref["coreRefId"]] if impact else [],
            information_gaps=[] if impact else [impact_gap],
        ),
        _step(
            key,
            3,
            "应对措施",
            mitigation or "当前应对措施信息不完整。",
            [
                _claim(
                    f"{key}:mitigation" if mitigation else f"{key}:mitigation-gap",
                    "bound_fact" if mitigation else "information_gap",
                    mitigation or "当前 Risk 没有记录 mitigation，不能自动生成应对方案。",
                    core_ref_ids=[ref["coreRefId"]] if mitigation else [],
                    gap=None if mitigation else mitigation_gap,
                )
            ],
            focus_node_ids=focus,
            core_ref_ids=[ref["coreRefId"]] if mitigation else [],
            information_gaps=[] if mitigation else [mitigation_gap],
        ),
        _step(
            key,
            4,
            "关联对象与后续追踪",
            "只展示正式 ID 关联；是否已经缓解仍以 Risk status、验证证据和后续事件为准。",
            [
                _claim(
                    f"{key}:related",
                    "bound_fact",
                    f"该风险绑定 {len(focus)} 个当前 View 节点和 {len(related_refs)} 个治理实体。",
                    node_ids=focus,
                    model_entity_ids=ref["relatedModelEntityIds"],
                    core_ref_ids=core_refs,
                )
            ],
            focus_node_ids=focus,
            core_ref_ids=core_refs,
            caveats=["风险状态变化必须来自更新后的 Core/Event 证据，讲解文本不能自行关闭风险。"],
        ),
    ]
    return _story(
        key,
        "risk",
        f"风险 · {ref['label']}",
        ref["summary"],
        _view_binding(chapter),
        steps,
        core_ref_ids=core_refs,
    )


def _transition_story(
    ref: dict[str, Any], chapter: dict[str, Any], view: dict[str, Any], id_to_ref: dict[str, str]
) -> dict[str, Any]:
    key = f"transition:{ref['id']}"
    facts = _facts(ref)
    focus = _focus_for_ids(view, set(ref["relatedModelEntityIds"]))
    related_refs = [id_to_ref[item] for item in ref["relatedCoreIds"] if item in id_to_ref]
    summary = "；".join(
        f"{name}={facts[name]}"
        for name in ("fromVersionId", "toVersionId", "state", "summary", "reason")
        if facts.get(name)
    )
    claims = [_claim(f"{key}:state", "bound_fact", summary or ref["label"], node_ids=focus, model_entity_ids=ref["relatedModelEntityIds"], core_ref_ids=[ref["coreRefId"], *related_refs])]
    steps = [
        _step(
            key, 0, "迁移对象与状态", summary or ref["summary"], claims,
            focus_node_ids=focus, core_ref_ids=[ref["coreRefId"], *related_refs],
            caveats=["Transition 只表达正式记录的 from/to/state，不推断完成度或日期。"],
        )
    ]
    return _story(key, "transition", f"迁移 · {ref['label']}", ref["summary"], _view_binding(chapter), steps, core_ref_ids=[ref["coreRefId"], *related_refs])


def _evidence_story(
    default_chapter: dict[str, Any], default_view: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    key = "evidence_freshness:global"
    pins = [pin for item in [*model["entities"], *model["relations"]] for pin in item["evidencePins"]]
    pin_ids = sorted({pin["evidenceId"] for pin in pins})
    freshness: dict[str, int] = {}
    for pin in pins:
        value = pin["freshness"]
        freshness[value] = freshness.get(value, 0) + 1
    node_ids = [node["id"] for node in default_view["nodes"]]
    claims = []
    if pin_ids:
        claims.append(
            _claim(
                f"{key}:pins", "bound_fact",
                "Evidence Freshness 分布为 " + "、".join(f"{name} {count}" for name, count in sorted(freshness.items())) + "。",
                node_ids=node_ids, evidence_pin_ids=pin_ids,
            )
        )
    else:
        claims.append(_claim(f"{key}:missing", "information_gap", "Model IR 没有 Evidence Pin。", gap="model_evidence_pins_missing"))
    steps = [
        _step(
            key, 0, "证据与新鲜度", "离线讲解只报告已记录 Freshness，不验证当前 Source。",
            claims, focus_node_ids=node_ids, evidence_pin_ids=pin_ids,
            information_gaps=[] if pin_ids else ["model_evidence_pins_missing"],
            caveats=["recorded_as_of 不等于 current；unknown/not_detected 不等于 absent。"],
        )
    ]
    gaps = list(model.get("informationGaps", []))[:16]
    if gaps:
        steps.append(
            _step(
                key, 1, "模型未知项", "这些 Information Gap 不由讲解自动补齐。",
                [_claim(f"{key}:gap:{index}", "information_gap", gap, gap=gap) for index, gap in enumerate(gaps)],
                information_gaps=gaps,
            )
        )
    return _story(key, "evidence_freshness", "证据、Freshness 与未知项", "解释证据时间边界和未覆盖事实。", _view_binding(default_chapter), steps)


def _verification_story(
    refs: list[dict[str, Any]], id_to_ref: dict[str, str], chapter: dict[str, Any], view: dict[str, Any]
) -> dict[str, Any] | None:
    requirements = [item for item in refs if item["type"] == "requirement"]
    if not requirements:
        return None
    key = "verification_trace:global"
    steps = []
    story_core_refs: set[str] = set()
    for requirement in requirements[:16]:
        related = [id_to_ref[item] for item in requirement["relatedCoreIds"] if item in id_to_ref]
        core_refs = [requirement["coreRefId"], *related]
        story_core_refs.update(core_refs)
        focus = _focus_for_ids(view, set(requirement["relatedModelEntityIds"]))
        steps.append(
            _step(
                key, len(steps), requirement["label"], requirement["summary"],
                [_claim(f"{key}:{requirement['id']}", "bound_fact", f"该 Requirement 绑定 {len(focus)} 个当前 View 节点和 {len(related)} 个 Acceptance/Decision/Gate 等治理实体。", node_ids=focus, model_entity_ids=requirement["relatedModelEntityIds"], core_ref_ids=core_refs)],
                focus_node_ids=focus, core_ref_ids=core_refs,
                caveats=["只有 required Gate passed 且 Evidence 完整时才能称为 verified。"],
            )
        )
    return _story(key, "verification_trace", "Requirement 到验证闭环", "按正式 ID 关系逐项解释 Requirement、Module、Decision、Acceptance 与 Gate。", _view_binding(chapter), steps, core_ref_ids=story_core_refs)


def _playback_scenarios(
    panorama: dict[str, Any],
    chapters: list[dict[str, Any]],
    views_by_id: dict[str, dict[str, Any]],
    id_to_ref: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    scenarios: list[dict[str, Any]] = []
    generated_gaps: list[str] = []
    module_chapters = [
        chapter
        for chapter in chapters
        if views_by_id[chapter["viewId"]]["profile"] == "module"
    ]
    for flow in panorama.get("businessFlows", [])[:32]:
        if not isinstance(flow, dict) or not isinstance(flow.get("id"), str):
            continue
        chapter = next(
            (
                item
                for item in module_chapters
                if item["architectureScope"] == flow.get("architectureScope")
            ),
            module_chapters[0] if module_chapters else chapters[0],
        )
        view = views_by_id[chapter["viewId"]]
        nodes_by_entity = {
            node.get("entityRef", {}).get("id"): node
            for node in view.get("nodes", [])
            if isinstance(node.get("entityRef"), dict)
        }
        edges_by_relation = {
            edge.get("relationRef", {}).get("id"): edge
            for edge in view.get("edges", [])
            if isinstance(edge.get("relationRef"), dict)
        }
        flow_ref = id_to_ref[flow["id"]]
        scenario_gaps: list[str] = []
        if chapter["architectureScope"] != flow.get("architectureScope"):
            gap = (
                f"business_flow_scope_view_missing:{flow['id']}:"
                f"{flow.get('architectureScope')}"
            )
            scenario_gaps.append(gap)
            generated_gaps.append(gap)
        playback_steps: list[dict[str, Any]] = []
        scenario_core_refs: set[str] = {flow_ref}
        for step in flow.get("steps", [])[:64]:
            module_id = step["moduleId"]
            connection_id = step.get("connectionId")
            node = nodes_by_entity.get(module_id)
            edge = edges_by_relation.get(connection_id)
            step_core_refs = {
                id_to_ref[item]
                for item in (
                    [flow["id"], module_id]
                    + ([connection_id] if isinstance(connection_id, str) else [])
                    + step.get("riskIds", [])
                    + step.get("decisionIds", [])
                    + step.get("referenceIds", [])
                )
                if item in id_to_ref
            }
            scenario_core_refs.update(step_core_refs)
            if node is None:
                gap = f"business_flow_node_not_projected:{flow['id']}:{step['id']}:{module_id}"
                scenario_gaps.append(gap)
                generated_gaps.append(gap)
            if connection_id is not None and edge is None:
                gap = (
                    f"business_flow_edge_not_projected:{flow['id']}:"
                    f"{step['id']}:{connection_id}"
                )
                scenario_gaps.append(gap)
                generated_gaps.append(gap)
            step_semantics = {
                "sourceStepId": step["id"],
                "order": step["order"],
                "type": step["type"],
                "title": step["action"][:512],
                "detail": step["action"][:2048],
                "dataSummary": str(step.get("dataSummary", ""))[:2048],
                "result": str(step.get("result", ""))[:2048],
                "moduleId": module_id,
                "connectionId": connection_id,
                "nodeId": node["id"] if node is not None else None,
                "edgeId": edge["id"] if edge is not None else None,
                "coreRefIds": sorted(step_core_refs),
            }
            step_identity = compute_canonical_hash(
                {
                    "businessFlowId": flow["id"],
                    "viewBinding": _view_binding(chapter),
                    **step_semantics,
                }
            )
            playback_steps.append(
                {
                    "playbackStepId": f"PLAYSTEP-{step_identity[:20].upper()}",
                    **step_semantics,
                }
            )
        scenario_semantics = {
            "businessFlowId": flow["id"],
            "title": str(flow.get("name", flow["id"]))[:512],
            "summary": str(flow.get("summary", ""))[:2048],
            "architectureScope": flow["architectureScope"],
            "factStatus": flow["factStatus"],
            "status": flow["status"],
            "isDefault": bool(flow.get("isDefault")),
            "trigger": str(flow.get("trigger", ""))[:2048],
            "outcome": str(flow.get("outcome", ""))[:2048],
            "viewBinding": _view_binding(chapter),
            "coreRefIds": sorted(scenario_core_refs),
            "steps": playback_steps,
            "informationGaps": sorted(set(scenario_gaps))[:64],
        }
        scenario_identity = compute_canonical_hash(scenario_semantics)
        scenarios.append(
            {
                "scenarioId": f"SCENARIO-{scenario_identity[:20].upper()}",
                **scenario_semantics,
            }
        )
    scenarios.sort(
        key=lambda item: (
            not item["isDefault"],
            item["architectureScope"],
            item["businessFlowId"],
        )
    )
    return scenarios, generated_gaps


def build_explain_pack(
    panorama: dict[str, Any],
    model: dict[str, Any],
    views: list[dict[str, Any]],
    view_set: dict[str, Any],
    *,
    child_views: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    child_views = list(child_views or [])
    report = validate_data(panorama)
    if report.errors:
        raise PanoramaExplainPackError(
            "Panorama Core 无效：" + "; ".join(item.message for item in report.errors[:20])
        )
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaExplainPackError("Model IR 无效：" + "; ".join(model_errors[:20]))
    for view in views:
        errors = validate_view_ir(view, model)
        if errors:
            raise PanoramaExplainPackError(
                f"View {view.get('viewId', '?')} 无效：" + "; ".join(errors[:20])
            )
    parent_view_by_id = {view["viewId"]: view for view in views}
    for child in child_views:
        parent = parent_view_by_id.get(child.get("parentViewBinding", {}).get("viewId"))
        if parent is None:
            raise PanoramaExplainPackError(
                f"Module Logic View {child.get('viewId', '?')} 缺少同 Explain Pack 父 View。"
            )
        errors = validate_view_ir(child, model, parent_view=parent)
        if errors:
            raise PanoramaExplainPackError(
                f"Child View {child.get('viewId', '?')} 无效：" + "; ".join(errors[:20])
            )
    view_set_errors = validate_view_set(view_set, model, views)
    if view_set_errors:
        raise PanoramaExplainPackError("View Set 无效：" + "; ".join(view_set_errors[:20]))
    expected_project = _project_binding(panorama)
    model_project = model["projectBinding"]
    if (
        model_project["projectId"] != expected_project["projectId"]
        or model_project["panoramaSchemaVersion"] != expected_project["panoramaSchemaVersion"]
        or model_project["revision"] != expected_project["revision"]
        or model_project["dataHash"] != expected_project["dataHash"]
    ):
        raise PanoramaExplainPackError("Panorama Core 与 Model IR Project Binding 不匹配。")

    views_by_id = {view["viewId"]: view for view in [*views, *child_views]}
    chapters = view_set["chapters"]
    chapter_by_view_id = {chapter["viewId"]: chapter for chapter in chapters}
    child_chapters: list[dict[str, Any]] = []
    for child in sorted(child_views, key=lambda item: item["viewId"]):
        parent_chapter = chapter_by_view_id.get(child["parentViewBinding"]["viewId"])
        if parent_chapter is None:
            raise PanoramaExplainPackError(
                f"Module Logic View {child['viewId']} 的父 View 不在 Guided View Set。"
            )
        child_chapters.append(
            {
                "chapterId": parent_chapter["chapterId"],
                "viewId": child["viewId"],
                "profile": "module_logic",
                "architectureScope": child["filters"]["architectureScopes"][0],
                "semanticHash": child["integrity"]["semanticHash"],
                "layoutHash": child["integrity"]["layoutHash"],
                "rootModuleId": child["rootModuleId"],
                "label": child["title"],
            }
        )
    stories: list[dict[str, Any]] = []
    generated_gaps: list[str] = []
    refs, id_to_ref = _core_references(panorama, model)
    for chapter in chapters:
        story, gaps = _view_story(
            chapter,
            views_by_id[chapter["viewId"]],
            model,
            panorama,
            id_to_ref,
        )
        stories.append(story)
        generated_gaps.extend(gaps)
    for child_chapter in child_chapters:
        child_stories, gaps = _module_logic_stories(
            child_chapter,
            views_by_id[child_chapter["viewId"]],
            model,
        )
        stories.extend(child_stories)
        generated_gaps.extend(gaps)

    for ref in refs:
        if ref["type"] not in {"work_item", "decision", "risk", "transition"}:
            continue
        chapter = _chapter_for_model_ids(
            [*child_chapters, *chapters], views_by_id, set(ref["relatedModelEntityIds"])
        )
        view = views_by_id[chapter["viewId"]]
        if ref["type"] == "work_item":
            stories.append(_progress_story(ref, chapter, view, id_to_ref))
        elif ref["type"] == "decision":
            stories.append(_decision_story(ref, chapter, view, id_to_ref))
        elif ref["type"] == "risk":
            stories.append(_risk_story(ref, chapter, view, id_to_ref))
        else:
            stories.append(_transition_story(ref, chapter, view, id_to_ref))

    default_chapter = next(
        item for item in chapters if item["chapterId"] == view_set["defaultChapterId"]
    )
    default_view = views_by_id[default_chapter["viewId"]]
    stories.append(_evidence_story(default_chapter, default_view, model))
    verification = _verification_story(
        refs, id_to_ref, default_chapter, default_view
    )
    if verification is not None:
        stories.append(verification)
    if len([item for item in refs if item["type"] == "requirement"]) > 16:
        generated_gaps.append("verification_trace_requirements_truncated")
    playback_scenarios, playback_gaps = _playback_scenarios(
        panorama, chapters, views_by_id, id_to_ref
    )
    generated_gaps.extend(playback_gaps)

    semantics = {
        "formatVersion": "panorama-explain-pack.v0.1",
        "generatedAt": model["compiledAt"],
        "compiler": EXPLAIN_PACK_COMPILER,
        "projectBinding": expected_project,
        "modelBinding": _model_binding(model),
        "viewSetBinding": _view_set_binding(view_set),
        "stories": stories,
        "playbackScenarios": playback_scenarios,
        "coreReferences": refs,
        "informationGaps": sorted(
            set(model.get("informationGaps", []))
            | {gap for view in [*views, *child_views] for gap in view.get("informationGaps", [])}
            | set(generated_gaps)
        )[:1000],
        "limitations": sorted(set(PROFILE_CAVEATS.values())),
        "extensions": {},
    }
    identity = compute_canonical_hash(semantics)
    pack = {
        **semantics,
        "explainPackId": f"PEP-{identity[:24].upper()}",
        "integrity": {
            "hashAlgorithm": "sha256",
            "semanticHash": "0" * 64,
            "semanticHashScope": "explain_pack_without_integrity",
        },
    }
    pack["integrity"]["semanticHash"] = compute_explain_pack_semantic_hash(pack)
    errors = validate_explain_pack(
        pack, panorama, model, views, view_set, child_views=child_views
    )
    if errors:
        raise PanoramaExplainPackError("Explain Pack 编译结果无效：" + "; ".join(errors[:20]))
    try:
        event_store._validate_redaction(pack)
    except event_store.EventStoreError as exc:
        raise PanoramaExplainPackError(f"Explain Pack Privacy Gate 未通过：{exc}") from exc
    return pack


def _resolve_pointer(data: dict[str, Any], pointer: str) -> Any:
    current: Any = data
    for raw_part in pointer.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


def validate_explain_pack(
    pack: dict[str, Any],
    panorama: dict[str, Any],
    model: dict[str, Any],
    views: list[dict[str, Any]],
    view_set: dict[str, Any],
    *,
    child_views: list[dict[str, Any]] | None = None,
) -> list[str]:
    child_views = list(child_views or [])
    errors = _schema_errors(pack)
    if errors:
        return errors
    encoded = json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PACK_BYTES:
        errors.append(f"/: Explain Pack 超过 {MAX_PACK_BYTES} bytes")
    if pack["integrity"]["semanticHash"] != compute_explain_pack_semantic_hash(pack):
        errors.append("/integrity/semanticHash: 与 Explain Pack 语义内容不匹配")
    identity_semantics = deepcopy(pack)
    identity_semantics.pop("explainPackId", None)
    identity_semantics.pop("integrity", None)
    expected_id = f"PEP-{compute_canonical_hash(identity_semantics)[:24].upper()}"
    if pack["explainPackId"] != expected_id:
        errors.append("/explainPackId: 与 Explain Pack 身份语义不匹配")
    if pack["projectBinding"] != _project_binding(panorama):
        errors.append("/projectBinding: 与 Panorama Core 不匹配")
    if pack["modelBinding"] != _model_binding(model):
        errors.append("/modelBinding: 与 Model IR 不匹配")
    if pack["viewSetBinding"] != _view_set_binding(view_set):
        errors.append("/viewSetBinding: 与 Guided View Set 不匹配")

    view_by_id = {view["viewId"]: view for view in [*views, *child_views]}
    chapter_by_id = {item["chapterId"]: item for item in view_set["chapters"]}
    model_entity_ids = {item["id"] for item in model["entities"]}
    model_relation_ids = {item["id"] for item in model["relations"]}
    evidence_ids = {
        pin["evidenceId"]
        for item in [*model["entities"], *model["relations"], *model["layers"]]
        for pin in item["evidencePins"]
    }
    core_by_ref = {item["coreRefId"]: item for item in pack["coreReferences"]}
    if len(core_by_ref) != len(pack["coreReferences"]):
        errors.append("/coreReferences: coreRefId 必须唯一")
    for ref in pack["coreReferences"]:
        try:
            actual = _resolve_pointer(panorama, ref["jsonPointer"])
        except (KeyError, IndexError, ValueError):
            errors.append(f"/coreReferences/{ref['coreRefId']}: jsonPointer 不可解析")
            continue
        if not isinstance(actual, dict) or actual.get("id") != ref["id"]:
            errors.append(f"/coreReferences/{ref['coreRefId']}: Core ID 与 jsonPointer 不匹配")
        if compute_canonical_hash(actual) != ref["digest"]:
            errors.append(f"/coreReferences/{ref['coreRefId']}: digest 与 Core Object 不匹配")
        expected_ref_id = _core_ref_id(ref["type"], ref["id"], ref["jsonPointer"], ref["digest"])
        if ref["coreRefId"] != expected_ref_id:
            errors.append(f"/coreReferences/{ref['coreRefId']}: coreRefId 与绑定内容不匹配")
        if not set(ref["relatedModelEntityIds"]) <= model_entity_ids:
            errors.append(f"/coreReferences/{ref['coreRefId']}: relatedModelEntityIds 含未知实体")

    story_ids: set[str] = set()
    step_ids: set[str] = set()
    claim_ids: set[str] = set()
    for story in pack["stories"]:
        if story["storyId"] in story_ids:
            errors.append("/stories: storyId 必须唯一")
        story_ids.add(story["storyId"])
        binding = story["viewBinding"]
        chapter = chapter_by_id.get(binding["chapterId"])
        if binding["profile"] == "module_logic":
            view = view_by_id.get(binding["viewId"])
            if (
                chapter is None
                or view is None
                or view.get("profile") != "module_logic"
                or view.get("parentViewBinding", {}).get("viewId") != chapter.get("viewId")
            ):
                errors.append(f"/stories/{story['storyId']}/viewBinding: 与父 Chapter/Child View 不匹配")
                continue
            expected_binding = {
                "chapterId": chapter["chapterId"],
                "viewId": view["viewId"],
                "profile": "module_logic",
                "architectureScope": view["filters"]["architectureScopes"][0],
                "rootModuleId": view["rootModuleId"],
                "semanticHash": view["integrity"]["semanticHash"],
                "layoutHash": view["integrity"]["layoutHash"],
            }
            if binding != expected_binding:
                errors.append(f"/stories/{story['storyId']}/viewBinding: 与 Child View Hash/Scope 不匹配")
                continue
        else:
            if chapter is None or _view_binding(chapter) != binding:
                errors.append(f"/stories/{story['storyId']}/viewBinding: 与 View Set Chapter 不匹配")
                continue
            view = view_by_id.get(chapter["viewId"])
        if view is None:
            errors.append(f"/stories/{story['storyId']}: 绑定 View 不存在")
            continue
        node_ids = {item["id"] for item in view["nodes"]}
        edge_ids = {item["id"] for item in view["edges"]}
        if not set(story["coreRefIds"]) <= set(core_by_ref):
            errors.append(f"/stories/{story['storyId']}/coreRefIds: 含未知 Core Ref")
        orders = [step["order"] for step in story["steps"]]
        if orders != list(range(len(orders))):
            errors.append(f"/stories/{story['storyId']}/steps: order 必须连续")
        if story["defaultStepId"] not in {step["stepId"] for step in story["steps"]}:
            errors.append(f"/stories/{story['storyId']}/defaultStepId: 未绑定现有 Step")
        for step in story["steps"]:
            if step["stepId"] in step_ids:
                errors.append("/stories/*/steps: stepId 必须全局唯一")
            step_ids.add(step["stepId"])
            if not set(step["focusNodeIds"]) <= node_ids:
                errors.append(f"/stories/{story['storyId']}/steps/{step['stepId']}: focusNodeIds 越出 View")
            if not set(step["focusEdgeIds"]) <= edge_ids:
                errors.append(f"/stories/{story['storyId']}/steps/{step['stepId']}: focusEdgeIds 越出 View")
            if not set(step["coreRefIds"]) <= set(core_by_ref):
                errors.append(f"/stories/{story['storyId']}/steps/{step['stepId']}: 含未知 Core Ref")
            if not set(step["evidencePinIds"]) <= evidence_ids:
                errors.append(f"/stories/{story['storyId']}/steps/{step['stepId']}: 含未知 Evidence Pin")
            for claim in step["claimBlocks"]:
                if claim["claimId"] in claim_ids:
                    errors.append("/stories/*/steps/*/claimBlocks: claimId 必须全局唯一")
                claim_ids.add(claim["claimId"])
                if not set(claim["nodeIds"]) <= node_ids or not set(claim["edgeIds"]) <= edge_ids:
                    errors.append(f"/stories/{story['storyId']}/steps/{step['stepId']}/{claim['claimId']}: Claim 越出 View")
                if not set(claim["modelEntityIds"]) <= model_entity_ids:
                    errors.append(f"/stories/{story['storyId']}/{claim['claimId']}: 含未知 Model Entity")
                if not set(claim["modelRelationIds"]) <= model_relation_ids:
                    errors.append(f"/stories/{story['storyId']}/{claim['claimId']}: 含未知 Model Relation")
                if not set(claim["coreRefIds"]) <= set(core_by_ref):
                    errors.append(f"/stories/{story['storyId']}/{claim['claimId']}: 含未知 Core Ref")
                if not set(claim["evidencePinIds"]) <= evidence_ids:
                    errors.append(f"/stories/{story['storyId']}/{claim['claimId']}: 含未知 Evidence Pin")
    flow_by_id = {
        item["id"]: item
        for item in panorama.get("businessFlows", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    ref_by_core_id = {item["id"]: item["coreRefId"] for item in pack["coreReferences"]}
    scenario_ids: set[str] = set()
    playback_step_ids: set[str] = set()
    for scenario in pack.get("playbackScenarios", []):
        scenario_id = scenario["scenarioId"]
        if scenario_id in scenario_ids:
            errors.append("/playbackScenarios: scenarioId 必须唯一")
        scenario_ids.add(scenario_id)
        flow = flow_by_id.get(scenario["businessFlowId"])
        if flow is None:
            errors.append(f"/playbackScenarios/{scenario_id}: businessFlowId 不存在")
            continue
        chapter = chapter_by_id.get(scenario["viewBinding"]["chapterId"])
        if chapter is None or _view_binding(chapter) != scenario["viewBinding"]:
            errors.append(f"/playbackScenarios/{scenario_id}/viewBinding: 与 View Set Chapter 不匹配")
            continue
        view = view_by_id.get(chapter["viewId"])
        if view is None:
            errors.append(f"/playbackScenarios/{scenario_id}: 绑定 View 不存在")
            continue
        node_by_id = {item["id"]: item for item in view["nodes"]}
        edge_by_id = {item["id"]: item for item in view["edges"]}
        if not set(scenario["coreRefIds"]) <= set(core_by_ref):
            errors.append(f"/playbackScenarios/{scenario_id}/coreRefIds: 含未知 Core Ref")
        for field in (
            "architectureScope", "factStatus", "status", "isDefault", "trigger", "outcome"
        ):
            expected = flow.get(field)
            if isinstance(expected, str):
                expected = expected[:2048]
            if scenario[field] != expected:
                errors.append(f"/playbackScenarios/{scenario_id}/{field}: 与 Business Flow 不匹配")
        source_steps = flow.get("steps", [])
        if [item["order"] for item in scenario["steps"]] != list(range(len(scenario["steps"]))):
            errors.append(f"/playbackScenarios/{scenario_id}/steps: order 必须连续")
        if len(scenario["steps"]) != len(source_steps):
            errors.append(f"/playbackScenarios/{scenario_id}/steps: 与 Business Flow Step 数量不匹配")
        for index, playback_step in enumerate(scenario["steps"]):
            playback_step_id = playback_step["playbackStepId"]
            if playback_step_id in playback_step_ids:
                errors.append("/playbackScenarios/*/steps: playbackStepId 必须全局唯一")
            playback_step_ids.add(playback_step_id)
            if index >= len(source_steps):
                continue
            source_step = source_steps[index]
            if (
                playback_step["sourceStepId"] != source_step.get("id")
                or playback_step["moduleId"] != source_step.get("moduleId")
                or playback_step["connectionId"] != source_step.get("connectionId")
                or playback_step["order"] != source_step.get("order")
                or playback_step["type"] != source_step.get("type")
            ):
                errors.append(
                    f"/playbackScenarios/{scenario_id}/steps/{playback_step_id}: 与 Business Flow Step 不匹配"
                )
            node_id = playback_step["nodeId"]
            edge_id = playback_step["edgeId"]
            if node_id is not None:
                node = node_by_id.get(node_id)
                if node is None or node.get("entityRef", {}).get("id") != playback_step["moduleId"]:
                    errors.append(
                        f"/playbackScenarios/{scenario_id}/steps/{playback_step_id}/nodeId: 未精确锚定 moduleId"
                    )
            if edge_id is not None:
                edge = edge_by_id.get(edge_id)
                if edge is None or edge.get("relationRef", {}).get("id") != playback_step["connectionId"]:
                    errors.append(
                        f"/playbackScenarios/{scenario_id}/steps/{playback_step_id}/edgeId: 未精确锚定 connectionId"
                    )
            if not set(playback_step["coreRefIds"]) <= set(core_by_ref):
                errors.append(
                    f"/playbackScenarios/{scenario_id}/steps/{playback_step_id}/coreRefIds: 含未知 Core Ref"
                )
            expected_step_refs = {
                ref_by_core_id[item]
                for item in (
                    [flow["id"], source_step.get("moduleId")]
                    + ([source_step.get("connectionId")] if source_step.get("connectionId") else [])
                    + source_step.get("riskIds", [])
                    + source_step.get("decisionIds", [])
                    + source_step.get("referenceIds", [])
                )
                if item in ref_by_core_id
            }
            if set(playback_step["coreRefIds"]) != expected_step_refs:
                errors.append(
                    f"/playbackScenarios/{scenario_id}/steps/{playback_step_id}/coreRefIds: 与 Business Flow 引用不匹配"
                )
            step_semantics = {key: value for key, value in playback_step.items() if key != "playbackStepId"}
            expected_step_id = f"PLAYSTEP-{compute_canonical_hash({'businessFlowId': flow['id'], 'viewBinding': scenario['viewBinding'], **step_semantics})[:20].upper()}"
            if playback_step_id != expected_step_id:
                errors.append(
                    f"/playbackScenarios/{scenario_id}/steps/{playback_step_id}: playbackStepId 与绑定语义不匹配"
                )
        scenario_semantics = {key: value for key, value in scenario.items() if key != "scenarioId"}
        expected_scenario_id = f"SCENARIO-{compute_canonical_hash(scenario_semantics)[:20].upper()}"
        if scenario_id != expected_scenario_id:
            errors.append(f"/playbackScenarios/{scenario_id}: scenarioId 与绑定语义不匹配")
    return errors


__all__ = [
    "EXPLAIN_PACK_COMPILER",
    "EXPLAIN_PACK_SCHEMA",
    "MAX_PACK_BYTES",
    "PanoramaExplainPackError",
    "build_explain_pack",
    "compute_explain_pack_semantic_hash",
    "validate_explain_pack",
]
