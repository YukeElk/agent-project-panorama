from __future__ import annotations

from copy import deepcopy

from panorama_view_ir import compile_model_ir, compile_module_view_ir, validate_view_ir
from studio_session import candidate_to_panorama, create_session, validate_session
from validate_panorama import validate_data


PROFILE_FORMAT = "panorama-layering-profile.v0.1"
SEMANTICS_FORMAT = "panorama-layer-semantics.v0.1"
ASSIGNMENTS_FORMAT = "panorama-layer-assignments.v0.1"


def _reviewed_layering(data: dict) -> dict:
    result = deepcopy(data)
    architecture = result["architecture"]
    architecture.setdefault("extensions", {})["layeringProfile"] = {
        "formatVersion": PROFILE_FORMAT,
        "primaryViewpoint": "logical_capability",
        "secondaryViewpoints": ["runtime", "deployment", "source"],
        "assignmentPolicy": "single_primary_evidence_bound",
        "ambiguityPolicy": "unassigned",
        "containerPolicy": "layer_or_module_logic",
        "status": "reviewed",
    }
    for layer in architecture["layers"]:
        layer.setdefault("extensions", {})["layerSemantics"] = {
            "formatVersion": SEMANTICS_FORMAT,
            "boundaryType": "capability",
            "rationale": f"{layer['id']} 由责任、接口与演进边界共同确定。",
            "inScope": ["承担本层正式责任的模块"],
            "outOfScope": ["仅属于模块内部的实现步骤"],
            "evidenceReferenceIds": ["REF-ARCH"],
            "confidence": "high",
        }
    for module in architecture["modules"]:
        assignments = {"formatVersion": ASSIGNMENTS_FORMAT}
        if module["architectureScope"] in {"current", "both"}:
            assignments["current"] = _assignment(module["layerId"])
        if module["architectureScope"] in {"target", "both"}:
            assignments["target"] = _assignment(
                module.get("targetLayerId") or module["layerId"]
            )
        if module["architectureScope"] == "historical":
            assignments["historical"] = _assignment(module["layerId"])
        module.setdefault("extensions", {})["layerAssignments"] = assignments
    return result


def _assignment(layer_id: str) -> dict:
    return {
        "layerId": layer_id,
        "basis": "responsibility",
        "rationale": "责任、接口、状态所有权与独立演进证据均指向该层。",
        "evidenceReferenceIds": ["REF-ARCH"],
        "confidence": "high",
        "alternativeLayerIds": [],
    }


def _candidate(session: dict) -> dict:
    return next(
        item
        for item in session["candidates"]
        if item["candidateId"] == session["activeCandidateId"]
    )


def test_reviewed_layering_is_evidence_bound_not_name_inferred(reference_data: dict):
    data = _reviewed_layering(reference_data)
    modules = {item["id"]: item for item in data["architecture"]["modules"]}
    modules["MOD-CONSOLE"]["name"] = "BFF Access Boundary"
    modules["MOD-ORCHESTRATOR"]["name"] = "DeepSeek Harness Runtime"

    report = validate_data(data)

    assert report.errors == []
    model = compile_model_ir(data)
    projected = {item["id"]: item for item in model["entities"]}
    assert projected["MOD-CONSOLE"]["layerBindings"]["current"] == "LAYER-INTERACTION"
    assert projected["MOD-ORCHESTRATOR"]["layerBindings"]["current"] == "LAYER-ORCHESTRATION"


def test_reviewed_layering_rejects_binding_assignment_mismatch(reference_data: dict):
    data = _reviewed_layering(reference_data)
    module = data["architecture"]["modules"][0]
    module["extensions"]["layerAssignments"]["current"]["layerId"] = "LAYER-DATA"

    codes = {issue.code for issue in validate_data(data).errors}

    assert "LAYER_ASSIGNMENT_MISMATCH" in codes


def test_reviewed_layering_requires_explainable_layers_and_modules(reference_data: dict):
    data = _reviewed_layering(reference_data)
    data["architecture"]["layers"][0]["extensions"].pop("layerSemantics")
    data["architecture"]["modules"][0]["extensions"].pop("layerAssignments")

    codes = {issue.code for issue in validate_data(data).errors}

    assert "LAYER_SEMANTICS_GAP" in codes
    assert "LAYER_ASSIGNMENT_GAP" in codes


def test_module_view_preserves_empty_parent_container(reference_data: dict):
    data = _reviewed_layering(reference_data)
    parent = {
        "id": "LAYER-EXECUTION-CONTAINER",
        "name": "Execution Container",
        "displayName": "执行容器",
        "order": 2,
        "summary": "组织编排与智能执行责任，但不直接承载功能模块。",
        "kind": "layer",
        "extensions": {
            "layerSemantics": {
                "formatVersion": SEMANTICS_FORMAT,
                "boundaryType": "system",
                "rationale": "编排与智能执行共享一个系统边界，内部仍保持不同责任层。",
                "inScope": ["编排层", "智能体层"],
                "outOfScope": ["接入边界", "数据持久化"],
                "evidenceReferenceIds": ["REF-ARCH"],
                "confidence": "high",
            }
        },
    }
    data["architecture"]["layers"].append(parent)
    for layer in data["architecture"]["layers"]:
        if layer["id"] in {"LAYER-ORCHESTRATION", "LAYER-AGENT"}:
            layer["parentLayerId"] = parent["id"]

    model = compile_model_ir(data)
    view = compile_module_view_ir(model, architecture_scopes=["current"])
    groups = {item["layerId"]: item for item in view["groups"]}

    assert validate_view_ir(view, model) == []
    assert parent["id"] in groups
    assert not any(node["groupId"] == groups[parent["id"]]["id"] for node in view["nodes"])
    assert groups["LAYER-ORCHESTRATION"]["parentGroupId"] == groups[parent["id"]]["id"]
    assert groups["LAYER-AGENT"]["parentGroupId"] == groups[parent["id"]]["id"]


def test_studio_cross_layer_edit_invalidates_then_materializes_assignment(reference_data: dict):
    data = _reviewed_layering(reference_data)
    session = create_session(data)
    candidate = _candidate(session)
    node = next(
        item for item in candidate["nodes"] if item["entityRef"]["id"] == "MOD-CONSOLE"
    )
    node["layerId"] = "LAYER-SERVICE"

    mismatch_codes = {item["code"] for item in validate_session(session)}
    assert "CLIENT_LAYER_ASSIGNMENT" in mismatch_codes

    node["layerAssignment"] = {
        "layerId": "LAYER-SERVICE",
        "basis": "security",
        "rationale": "目标设计把接入适配与服务鉴权合并为独立服务边界。",
        "evidenceReferenceIds": ["REF-ARCH"],
        "confidence": "medium",
        "alternativeLayerIds": ["LAYER-INTERACTION"],
    }
    assert validate_session(session) == []

    materialized = candidate_to_panorama(data, session, candidate["candidateId"])
    module = next(
        item for item in materialized["architecture"]["modules"]
        if item["id"] == "MOD-CONSOLE"
    )
    assert module["targetLayerId"] == "LAYER-SERVICE"
    assert module["extensions"]["layerAssignments"]["target"] == node["layerAssignment"]
    assert validate_data(materialized).errors == []


def test_renderer_never_guesses_layer_from_module_type_or_array_position(project_root):
    source = (project_root / "templates" / "panorama.html").read_text(encoding="utf-8")

    assert "function addStudioNode(kind, requestedLayerId)" in source
    assert 'layer.id === "LAYER-UNASSIGNED"' in source
    assert "Studio 不会按组件类型自动猜测归层" in source
    assert "state.studioLayers[2]" not in source
    assert "state.studioLayers[state.studioLayers.length - 1]" not in source
    assert "function studioFieldValue(node, field)" in source
