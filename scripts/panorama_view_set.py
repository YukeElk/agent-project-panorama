"""Deterministic guided navigation over validated single-scope View IRs.

The View Set owns only chapter order and labels. Topology, layout, facts, and
evidence remain exclusively owned by the bound View IR and Model IR.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from panorama_io import compute_canonical_hash
from panorama_view_ir import PanoramaViewIRError, validate_model_ir, validate_view_ir


ROOT = Path(__file__).resolve().parents[1]
VIEW_SET_SCHEMA = ROOT / "schema" / "panorama-view-set.schema.v0.1.json"
VIEW_SET_COMPILER = {"id": "panorama-guided-view-set-compiler", "version": "0.1.0"}
PROFILE_ORDER = {
    "module": 0,
    "dependency_dataflow": 1,
    "deployment_runtime": 2,
    "sequence": 3,
    "lifecycle": 4,
    "evolution_risk": 5,
}
SCOPE_ORDER = {"current": 0, "target": 1, "transition": 2, "historical": 3}
PROFILE_LABELS = {
    "module": "模块",
    "dependency_dataflow": "依赖 / 数据流",
    "deployment_runtime": "部署",
    "sequence": "时序",
    "lifecycle": "生命周期",
    "evolution_risk": "演进 / 风险",
}
SCOPE_LABELS = {
    "current": "当前",
    "target": "目标",
    "transition": "迁移中",
    "historical": "历史",
}


def _schema_errors(value: dict[str, Any]) -> list[str]:
    schema = json.loads(VIEW_SET_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(value),
        key=lambda issue: (tuple(str(part) for part in issue.absolute_path), issue.message),
    )
    return [
        "/" + "/".join(str(part) for part in issue.absolute_path) + f": {issue.message}"
        for issue in errors
    ]


def _model_binding(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "projectId": model["projectBinding"]["projectId"],
        "panoramaDataHash": model["projectBinding"]["dataHash"],
        "asOf": model["asOf"]["value"],
    }


def compute_view_set_semantic_hash(view_set: dict[str, Any]) -> str:
    value = deepcopy(view_set)
    value.pop("integrity", None)
    return compute_canonical_hash(value)


def _single_scope(view: dict[str, Any]) -> str:
    scopes = view.get("filters", {}).get("architectureScopes", [])
    if len(scopes) != 1:
        raise PanoramaViewIRError(
            f"Guided View Set 只接受单 scope View IR：{view.get('viewId', '?')}"
        )
    return scopes[0]


def _ordered_views(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        views,
        key=lambda view: (
            PROFILE_ORDER.get(view["profile"], 99),
            SCOPE_ORDER.get(_single_scope(view), 99),
            view["viewId"],
        ),
    )


def build_view_set(model: dict[str, Any], views: list[dict[str, Any]]) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:20]))
    if not views:
        raise PanoramaViewIRError("Guided View Set 至少需要一个 View IR。")
    if len(views) > 24:
        raise PanoramaViewIRError("Guided View Set 最多允许 24 个章节。")
    view_ids: set[str] = set()
    variant_keys: set[tuple[str, str]] = set()
    for view in views:
        errors = validate_view_ir(view, model)
        if errors:
            raise PanoramaViewIRError(
                f"View {view.get('viewId', '?')} 无效：" + "; ".join(errors[:20])
            )
        if view["viewId"] in view_ids:
            raise PanoramaViewIRError(f"View IR 重复：{view['viewId']}")
        view_ids.add(view["viewId"])
        if view["profile"] not in PROFILE_LABELS:
            raise PanoramaViewIRError(
                f"Guided View Set 暂不支持 profile：{view['profile']}"
            )
        variant_key = (view["profile"], _single_scope(view))
        if variant_key in variant_keys:
            raise PanoramaViewIRError(
                f"Guided Variant 重复：{variant_key[0]} / {variant_key[1]}"
            )
        variant_keys.add(variant_key)

    ordered = _ordered_views(views)
    chapters = []
    for order, view in enumerate(ordered):
        scope = _single_scope(view)
        chapter_hash = compute_canonical_hash({"viewId": view["viewId"]})
        chapters.append(
            {
                "chapterId": f"CHAPTER-{chapter_hash[:20].upper()}",
                "viewId": view["viewId"],
                "order": order,
                "profile": view["profile"],
                "architectureScope": scope,
                "label": f"{PROFILE_LABELS[view['profile']]} · {SCOPE_LABELS[scope]}",
                "semanticHash": view["integrity"]["semanticHash"],
                "layoutHash": view["integrity"]["layoutHash"],
            }
        )
    preferred = next(
        (
            chapter
            for chapter in chapters
            if chapter["profile"] == "module"
            and chapter["architectureScope"] == "current"
        ),
        chapters[0],
    )
    semantics = {
        "formatVersion": "panorama-view-set.v0.1",
        "generatedAt": model["compiledAt"],
        "compiler": VIEW_SET_COMPILER,
        "modelBinding": _model_binding(model),
        "title": f"{model['projectBinding']['projectName']} 引导式架构视图集",
        "description": "只组织已验证的单 scope View IR；不拥有或改写拓扑、布局、事实与证据。",
        "defaultChapterId": preferred["chapterId"],
        "chapters": chapters,
    }
    identity = compute_canonical_hash(semantics)
    view_set = {
        **semantics,
        "viewSetId": f"PVS-{identity[:24].upper()}",
        "integrity": {
            "hashAlgorithm": "sha256",
            "semanticHash": "0" * 64,
            "semanticHashScope": "view_set_without_integrity",
        },
    }
    view_set["integrity"]["semanticHash"] = compute_view_set_semantic_hash(view_set)
    errors = validate_view_set(view_set, model, ordered)
    if errors:
        raise PanoramaViewIRError("View Set 编译结果无效：" + "; ".join(errors[:20]))
    return view_set


def validate_view_set(
    view_set: dict[str, Any], model: dict[str, Any], views: list[dict[str, Any]]
) -> list[str]:
    errors = _schema_errors(view_set)
    if errors:
        return errors
    if view_set["integrity"]["semanticHash"] != compute_view_set_semantic_hash(view_set):
        errors.append("/integrity/semanticHash: 与 View Set 语义内容不匹配")
    identity_semantics = deepcopy(view_set)
    identity_semantics.pop("viewSetId", None)
    identity_semantics.pop("integrity", None)
    expected_id = f"PVS-{compute_canonical_hash(identity_semantics)[:24].upper()}"
    if view_set["viewSetId"] != expected_id:
        errors.append("/viewSetId: 与 View Set 身份语义不匹配")
    if view_set["modelBinding"] != _model_binding(model):
        errors.append("/modelBinding: 与输入 Model IR 不匹配")

    view_by_id = {view.get("viewId"): view for view in views}
    if len(view_by_id) != len(views):
        errors.append("/chapters: 输入 View IR 存在重复 viewId")
    chapter_ids = [chapter["chapterId"] for chapter in view_set["chapters"]]
    chapter_view_ids = [chapter["viewId"] for chapter in view_set["chapters"]]
    orders = [chapter["order"] for chapter in view_set["chapters"]]
    variants = [
        (chapter["profile"], chapter["architectureScope"])
        for chapter in view_set["chapters"]
    ]
    if len(chapter_ids) != len(set(chapter_ids)):
        errors.append("/chapters: 存在重复 chapterId")
    if len(chapter_view_ids) != len(set(chapter_view_ids)):
        errors.append("/chapters: 同一 View IR 不能重复成为章节")
    if orders != list(range(len(orders))):
        errors.append("/chapters: order 必须从 0 连续递增且与数组顺序一致")
    if len(variants) != len(set(variants)):
        errors.append("/chapters: 同一 profile/scope Guided Variant 不能重复")
    if set(chapter_view_ids) != set(view_by_id):
        errors.append("/chapters: 必须精确覆盖输入 View IR 集合")
    if view_set["defaultChapterId"] not in set(chapter_ids):
        errors.append("/defaultChapterId: 未绑定现有章节")

    for chapter in view_set["chapters"]:
        view = view_by_id.get(chapter["viewId"])
        if view is None:
            continue
        try:
            scope = _single_scope(view)
        except PanoramaViewIRError as exc:
            errors.append(f"/chapters/{chapter['chapterId']}: {exc}")
            continue
        expected = {
            "profile": view["profile"],
            "architectureScope": scope,
            "semanticHash": view["integrity"]["semanticHash"],
            "layoutHash": view["integrity"]["layoutHash"],
        }
        expected_chapter_id = (
            "CHAPTER-"
            + compute_canonical_hash({"viewId": chapter["viewId"]})[:20].upper()
        )
        if chapter["chapterId"] != expected_chapter_id:
            errors.append(
                f"/chapters/{chapter['chapterId']}/chapterId: 与绑定 View IR 不匹配"
            )
        for field, value in expected.items():
            if chapter[field] != value:
                errors.append(
                    f"/chapters/{chapter['chapterId']}/{field}: 与绑定 View IR 不匹配"
                )
    return errors
