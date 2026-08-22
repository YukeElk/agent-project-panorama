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
EXPLAIN_PACK_COMPILER = {"id": "panorama-explain-pack-compiler", "version": "0.1.0"}
MAX_PACK_BYTES = 1024 * 1024

PROFILE_CAVEATS = {
    "module": "只解释正式 Module 与 communication；布局邻近不代表依赖或所有权。",
    "dependency_dataflow": "静态 dependency/data_flow 不等于 Runtime Call，未投影关系不能由画布位置补全。",
    "deployment_runtime": "Declared Deployment 不等于 Observed Runtime；只有精确 provenance 才能标为 observed。",
    "sequence": "该顺序来自 Engineering Event Action，不得描述为业务运行调用。",
    "lifecycle": "只解释 Adapter 明确提供的 before/after；Outcome 本身不能生成状态迁移。",
    "evolution_risk": "Risk emphasis 不是 Formal Finding、因果证明或 blast radius。",
}

CORE_COLLECTIONS = (
    ("requirement", ("requirements",)),
    ("decision", ("decisions",)),
    ("risk", ("risks",)),
    ("acceptance", ("acceptanceCriteria",)),
    ("gate", ("gates",)),
    ("review", ("reviews",)),
    ("reference", ("references",)),
    ("transition", ("architecture", "transitions")),
)

FACT_FIELDS = {
    "requirement": (
        "type", "priority", "definitionStatus", "fulfillmentStatus", "summary",
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
    return {
        "chapterId": chapter["chapterId"],
        "viewId": chapter["viewId"],
        "profile": chapter["profile"],
        "architectureScope": chapter["architectureScope"],
        "semanticHash": chapter["semanticHash"],
        "layoutHash": chapter["layoutHash"],
    }


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
        facts = []
        for field in FACT_FIELDS.get(core_type, ()):
            value = _string_value(item.get(field))
            if value is not None and value != "":
                facts.append({"label": field, "value": value})
        if core_type == "decision":
            for option in item.get("options", [])[:12]:
                if not isinstance(option, dict) or not isinstance(option.get("id"), str):
                    continue
                option_text = " · ".join(
                    str(part)
                    for part in (option.get("title"), option.get("summary"))
                    if isinstance(part, str) and part
                )
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


def _view_story(
    chapter: dict[str, Any], view: dict[str, Any], model: dict[str, Any]
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
    if nodes:
        counts: dict[str, int] = {}
        entity_by_id = {item["id"]: item for item in model["entities"]}
        for entity_id in entity_ids:
            kind = entity_by_id[entity_id]["kind"]
            counts[kind] = counts.get(kind, 0) + 1
        count_text = "、".join(f"{kind} {count}" for kind, count in sorted(counts.items()))
        steps.append(
            _step(
                key,
                len(steps),
                "视图范围与主要对象",
                f"当前章节投影 {len(nodes)} 个节点。",
                [
                    _claim(
                        f"{key}:nodes",
                        "bound_fact",
                        f"该 View IR 包含 {count_text}。",
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
        steps.append(
            _step(
                key,
                len(steps),
                "已绑定关系",
                f"当前章节投影 {len(edges)} 条关系。",
                [
                    _claim(
                        f"{key}:edges",
                        "bound_fact",
                        f"关系类型为 {kind_text}；方向与端点均来自绑定 Model Relation。",
                        edge_ids=edge_ids,
                        model_relation_ids=relation_ids,
                        evidence_pin_ids=evidence_ids,
                    )
                ],
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


def _chapter_for_model_ids(
    chapters: list[dict[str, Any]], views_by_id: dict[str, dict[str, Any]], model_ids: set[str]
) -> dict[str, Any]:
    for chapter in chapters:
        view = views_by_id[chapter["viewId"]]
        if any(node["entityRef"]["id"] in model_ids for node in view["nodes"]):
            return chapter
    return chapters[0]


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
            [_claim(f"{key}:question", "bound_fact", f"{ref['label']}；状态为 {ref['status'] or 'unknown'}。", core_ref_ids=[ref["coreRefId"]])],
            focus_node_ids=focus, core_ref_ids=[ref["coreRefId"]],
        )
    ]
    context_parts = [facts.get(name) for name in ("trigger", "context") if facts.get(name)]
    if context_parts:
        steps.append(
            _step(
                key, len(steps), "为什么现在决策", "；".join(context_parts),
                [_claim(f"{key}:context", "bound_fact", "；".join(context_parts), core_ref_ids=[ref["coreRefId"]])],
                focus_node_ids=focus, core_ref_ids=[ref["coreRefId"]],
            )
        )
    option_facts = [item for item in ref["facts"] if item["label"].startswith("option:")]
    if option_facts:
        steps.append(
            _step(
                key, len(steps), "候选选项", f"已记录 {len(option_facts)} 个选项。",
                [_claim(f"{key}:options", "bound_fact", "；".join(f"{item['label'][7:]}：{item['value']}" for item in option_facts), core_ref_ids=[ref["coreRefId"]])],
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


def build_explain_pack(
    panorama: dict[str, Any],
    model: dict[str, Any],
    views: list[dict[str, Any]],
    view_set: dict[str, Any],
) -> dict[str, Any]:
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

    views_by_id = {view["viewId"]: view for view in views}
    chapters = view_set["chapters"]
    stories: list[dict[str, Any]] = []
    generated_gaps: list[str] = []
    for chapter in chapters:
        story, gaps = _view_story(chapter, views_by_id[chapter["viewId"]], model)
        stories.append(story)
        generated_gaps.extend(gaps)

    refs, id_to_ref = _core_references(panorama, model)
    for ref in refs:
        if ref["type"] not in {"decision", "transition"}:
            continue
        chapter = _chapter_for_model_ids(
            chapters, views_by_id, set(ref["relatedModelEntityIds"])
        )
        view = views_by_id[chapter["viewId"]]
        if ref["type"] == "decision":
            stories.append(_decision_story(ref, chapter, view, id_to_ref))
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

    semantics = {
        "formatVersion": "panorama-explain-pack.v0.1",
        "generatedAt": model["compiledAt"],
        "compiler": EXPLAIN_PACK_COMPILER,
        "projectBinding": expected_project,
        "modelBinding": _model_binding(model),
        "viewSetBinding": _view_set_binding(view_set),
        "stories": stories,
        "coreReferences": refs,
        "informationGaps": sorted(
            set(model.get("informationGaps", []))
            | {gap for view in views for gap in view.get("informationGaps", [])}
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
    errors = validate_explain_pack(pack, panorama, model, views, view_set)
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
) -> list[str]:
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

    view_by_id = {view["viewId"]: view for view in views}
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
        chapter = chapter_by_id.get(story["viewBinding"]["chapterId"])
        if chapter is None or _view_binding(chapter) != story["viewBinding"]:
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
