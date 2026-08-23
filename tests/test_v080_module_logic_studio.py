from __future__ import annotations

from copy import deepcopy

import pytest

from panorama_io import compute_data_hash
from studio_session import (
    StudioSessionError,
    candidate_semantic_diff,
    candidate_to_panorama,
    create_session,
    layout_hash,
    semantic_hash,
    validate_session,
)
from validate_panorama import validate_data


def _candidate(session: dict) -> dict:
    return next(
        item
        for item in session["candidates"]
        if item["candidateId"] == session["activeCandidateId"]
    )


def _target_design(root_module_id: str, external_module_id: str, connection_id: str) -> dict:
    return {
        "id": "MLD-STUDIO-ROUNDTRIP",
        "moduleId": root_module_id,
        "architectureScope": "target",
        "name": "request_pipeline",
        "displayName": "请求处理内部逻辑",
        "summary": "接收并规范化外部请求。",
        "nodes": [
            {
                "id": "MLN-STUDIO-ROUNDTRIP-IN",
                "name": "normalize_request",
                "displayName": "规范化请求",
                "type": "preprocess",
                "purpose": "统一输入。",
                "rationale": "稳定下游契约。",
                "implementationSummary": "验证输入并补齐上下文。",
                "loopExitCondition": None,
                "requirementIds": [],
                "decisionIds": [],
                "riskIds": [],
                "referenceIds": [],
                "extensions": {"preserve": True},
            }
        ],
        "edges": [],
        "boundaryPorts": [
            {
                "id": "MLP-STUDIO-ROUNDTRIP-IN",
                "name": "request_input",
                "direction": "input",
                "internalNodeId": "MLN-STUDIO-ROUNDTRIP-IN",
                "externalEntityRef": {"type": "module", "id": external_module_id},
                "bindingKind": "connection",
                "bindingId": connection_id,
                "dataSummary": "项目请求",
                "protocol": "HTTPS",
                "referenceIds": [],
                "extensions": {},
            }
        ],
        "requirementIds": [],
        "decisionIds": [],
        "riskIds": [],
        "acceptanceCriteriaIds": [],
        "gateIds": [],
        "referenceIds": [],
        "extensions": {"preserveDesign": True},
    }


def _root_binding(reference_data: dict) -> tuple[str, str, str]:
    target_modules = {
        item["id"]
        for item in reference_data["architecture"]["modules"]
        if item["architectureScope"] in {"target", "both"}
    }
    connection = next(
        item
        for item in reference_data["architecture"]["connections"]
        if item["architectureScope"] in {"target", "both"}
        and item["fromModuleId"] in target_modules
        and item["toModuleId"] in target_modules
    )
    return connection["toModuleId"], connection["fromModuleId"], connection["id"]


def _draft_canvas(root_id: str, external_id: str, binding_id: str) -> dict:
    return {
        "canvasId": "CANVAS-DRAFT-MODULE-LOGIC",
        "rootModuleId": root_id,
        "architectureScope": "target",
        "currentObservationBinding": None,
        "baseTargetDesignId": None,
        "name": "draft_request_pipeline",
        "displayName": "请求处理目标逻辑",
        "summary": "验证请求后交给模块内部处理。",
        "requirementIds": [],
        "decisionIds": [],
        "riskIds": [],
        "acceptanceCriteriaIds": [],
        "gateIds": [],
        "referenceIds": [],
        "nodes": [
            {
                "nodeId": "DRAFT-LOGIC-INPUT",
                "entityRef": None,
                "name": "validate_request",
                "displayName": "校验请求",
                "type": "validation",
                "purpose": "拒绝无效输入。",
                "rationale": "避免错误进入核心处理。",
                "implementationSummary": "检查契约、权限和必要上下文。",
                "loopExitCondition": None,
                "requirementIds": [],
                "decisionIds": [],
                "riskIds": [],
                "referenceIds": [],
                "isDraft": True,
                "x": 140,
                "y": 90,
                "extensions": {},
            }
        ],
        "edges": [],
        "boundaryPorts": [
            {
                "portId": "DRAFT-PORT-IN",
                "entityRef": None,
                "name": "request_input",
                "direction": "input",
                "internalNodeId": "DRAFT-LOGIC-INPUT",
                "externalEntityRef": {"type": "module", "id": external_id},
                "bindingKind": "connection",
                "bindingId": binding_id,
                "dataSummary": "项目请求",
                "protocol": "HTTPS",
                "referenceIds": [],
                "isDraft": True,
                "x": 40,
                "y": 90,
                "extensions": {},
            }
        ],
        "externalEntityRefs": [{"type": "module", "id": external_id}],
        "createdAt": "2026-08-23T08:00:00Z",
        "updatedAt": "2026-08-23T08:00:00Z",
        "extensions": {},
    }


def test_formal_target_logic_round_trips_through_candidate(reference_data: dict):
    current = deepcopy(reference_data)
    root_id, external_id, connection_id = _root_binding(current)
    formal = _target_design(root_id, external_id, connection_id)
    current["architecture"]["moduleLogicDesigns"] = [formal]

    session = create_session(current)
    canvas = _candidate(session)["moduleLogicCanvases"][0]

    assert canvas["baseTargetDesignId"] == formal["id"]
    assert canvas["nodes"][0]["entityRef"] == {
        "type": "logic_node",
        "id": formal["nodes"][0]["id"],
    }
    assert validate_session(session) == []
    materialized = candidate_to_panorama(current, session, session["activeCandidateId"])
    assert materialized == current
    assert compute_data_hash(materialized) == compute_data_hash(current)


def test_module_logic_layout_is_not_semantic_or_formal_data(reference_data: dict):
    current = deepcopy(reference_data)
    root_id, external_id, connection_id = _root_binding(current)
    current["architecture"]["moduleLogicDesigns"] = [
        _target_design(root_id, external_id, connection_id)
    ]
    session = create_session(current)
    candidate = _candidate(session)
    before = deepcopy(candidate)
    semantic_before = semantic_hash(candidate)
    layout_before = layout_hash(candidate)
    candidate["moduleLogicCanvases"][0]["nodes"][0]["x"] += 75

    assert semantic_hash(candidate) == semantic_before
    assert layout_hash(candidate) != layout_before
    assert candidate_semantic_diff(before, candidate)["hasChanges"] is False
    assert candidate_to_panorama(current, session, candidate["candidateId"]) == current


def test_new_module_logic_design_gets_deterministic_formal_ids(reference_data: dict):
    current = deepcopy(reference_data)
    root_id, external_id, connection_id = _root_binding(current)
    session = create_session(current)
    candidate = _candidate(session)
    candidate["moduleLogicCanvases"] = [
        _draft_canvas(root_id, external_id, connection_id)
    ]

    first = candidate_to_panorama(current, session, candidate["candidateId"])
    second = candidate_to_panorama(current, session, candidate["candidateId"])
    assert first == second
    design = first["architecture"]["moduleLogicDesigns"][0]
    assert design["id"].startswith("MLD-STUDIO-")
    assert design["nodes"][0]["id"].startswith("MLN-STUDIO-")
    assert design["boundaryPorts"][0]["id"].startswith("MLP-STUDIO-")
    assert design["boundaryPorts"][0]["bindingId"] == connection_id
    assert validate_data(first).errors == []


def test_candidate_edge_id_can_bind_cross_boundary_port(reference_data: dict):
    current = deepcopy(reference_data)
    root_id, external_id, connection_id = _root_binding(current)
    session = create_session(current)
    candidate = _candidate(session)
    candidate_edge = next(
        edge
        for edge in candidate["edges"]
        if edge["entityRef"]["id"] == connection_id
    )
    canvas = _draft_canvas(root_id, external_id, candidate_edge["edgeId"])
    candidate["moduleLogicCanvases"] = [canvas]

    materialized = candidate_to_panorama(current, session, candidate["candidateId"])
    port = materialized["architecture"]["moduleLogicDesigns"][0]["boundaryPorts"][0]
    assert port["bindingId"] == connection_id
    assert validate_data(materialized).errors == []


def test_unbound_or_stale_module_logic_candidate_cannot_formalize(reference_data: dict):
    root_id, external_id, connection_id = _root_binding(reference_data)
    session = create_session(reference_data)
    candidate = _candidate(session)
    canvas = _draft_canvas(root_id, external_id, connection_id)
    canvas["boundaryPorts"][0]["bindingKind"] = "unbound_candidate"
    canvas["boundaryPorts"][0]["bindingId"] = None
    candidate["moduleLogicCanvases"] = [canvas]

    with pytest.raises(StudioSessionError, match="not bound"):
        candidate_to_panorama(reference_data, session, candidate["candidateId"])

    canvas["boundaryPorts"] = []
    canvas["externalEntityRefs"] = []
    canvas["currentObservationBinding"] = {
        "observationId": "MLO-" + "A" * 24,
        "semanticHash": "b" * 64,
        "currentness": "stale",
    }
    assert any(
        item["code"] == "CLIENT_LOGIC_OBSERVATION_STALE"
        for item in validate_session(session)
    )
    with pytest.raises(StudioSessionError, match="stale Current observation"):
        candidate_to_panorama(reference_data, session, candidate["candidateId"])


def test_module_logic_client_validator_rejects_invalid_loop(reference_data: dict):
    root_id, external_id, connection_id = _root_binding(reference_data)
    session = create_session(reference_data)
    candidate = _candidate(session)
    canvas = _draft_canvas(root_id, external_id, connection_id)
    canvas["nodes"][0]["type"] = "loop"
    canvas["nodes"][0]["loopExitCondition"] = ""
    candidate["moduleLogicCanvases"] = [canvas]

    codes = {item["code"] for item in validate_session(session)}
    assert "CLIENT_LOGIC_LOOP_EXIT" in codes
    assert "CLIENT_LOGIC_LOOP_BACK_MISSING" in codes


def test_unified_runtime_owns_read_and_edit_canvas(project_root):
    host = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    template = (project_root / "templates" / "panorama.html").read_text(
        encoding="utf-8"
    )

    assert 'data-canvas-mode="${editing?"edit":"read"}"' in host
    assert 'mode: "edit", session: session, onAction: handleUnifiedStudioAction' in template
    assert 'data-canvas-runtime="unified"' in host
    assert 'data-canvas-world="one"' in host
    assert "renderLegacyStudio(); return;" in template
    assert "else if (state.systemView === \"logical\" && state.studioMode === \"studio\") renderStudio();" in template


def test_unified_editor_tracks_module_logic_semantics_not_camera(project_root):
    host = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    template = (project_root / "templates" / "panorama.html").read_text(
        encoding="utf-8"
    )

    assert "moduleLogicCanvases: arr(candidate.moduleLogicCanvases).map" in template
    assert "delete copy.camera; delete copy.viewerState" in template
    assert 'emitEditor("layout-move"' in host
    assert 'unifiedStudioCommit("layout.move"' in template
    assert 'unifiedStudioCommit("module_logic.node.add"' in template
    assert 'unifiedStudioCommit("module_logic.port.add"' in template
    assert 'bindingKind: binding ? "connection" : "unbound_candidate"' in template


def test_unified_editor_keeps_edges_attached_and_snaps_modules_to_layers(project_root):
    host = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    template = (project_root / "templates" / "panorama.html").read_text(
        encoding="utf-8"
    )

    assert 'data-edge-from="${esc(edge.fromNodeId)}"' in host
    assert 'data-edge-to="${esc(edge.toNodeId)}"' in host
    assert "this.refreshEditorEdges()" in host
    assert "this.editorLayerAt(nextY+drag.button.offsetHeight/2)" in host
    assert "layerId:dropLayer&&dropLayer.id||null" in host
    assert 'unifiedStudioCommit("node.layer.snap"' in template
    assert 'layoutItem.layerId = nextLayerId' in template


def test_unified_editor_exposes_full_existing_module_design_fields(project_root):
    host = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    template = (project_root / "templates" / "panorama.html").read_text(
        encoding="utf-8"
    )

    assert "已有模块可修改完整 Target 设计" in host
    for field in (
        "responsibilities",
        "nonResponsibilities",
        "stateOwnership",
        "dataHandled",
        "interfaceSummary",
        "deploymentRole",
        "technologies",
    ):
        assert f'addModuleArea(' in host
        assert f'"{field}"' in host
    assert 'valueType:field.dataset.editorValueType||"string"' in host
    assert 'detail.valueType === "string-list"' in template
    assert 'detail.valueType === "technology-list"' in template


def test_unified_editor_preserves_layer_hierarchy_and_governance_closure(project_root):
    host = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    template = (project_root / "templates" / "panorama.html").read_text(
        encoding="utf-8"
    )

    assert 'parentTitle.textContent="父层级 / 上级容器"' in host
    assert 'parentSelect.dataset.editorLayerField="parentLayerId"' in host
    assert 'detail.field === "parentLayerId" ? (detail.value || null)' in template
    assert 'showToast("父层级不能形成循环")' in template
    assert 'add("approve","精确批准"' in host
    assert 'add("apply","Apply"' in host
    assert 'detail.command === "approve"' in template
    assert 'detail.command === "apply"' in template
    assert 'governance: {' in template
    assert 'canApply: online && studioBridgeFeature("apply")' in template


def test_studio_roundtrip_preserves_authored_chinese_module_display_name(reference_data: dict):
    panorama = deepcopy(reference_data)
    module = panorama["architecture"]["modules"][0]
    module["displayName"] = "项目控制台"
    session = create_session(panorama)
    candidate = _candidate(session)
    node = next(item for item in candidate["nodes"] if item["entityRef"]["id"] == module["id"])
    assert node["displayName"] == "项目控制台"
    node["displayName"] = "开发者项目控制台"
    materialized = candidate_to_panorama(panorama, session, candidate["candidateId"])
    updated = next(item for item in materialized["architecture"]["modules"] if item["id"] == module["id"])
    assert updated["name"] == "Project Console"
    assert updated["displayName"] == "开发者项目控制台"


def test_existing_module_full_design_is_editable_and_materializes(reference_data: dict):
    panorama = deepcopy(reference_data)
    session = create_session(panorama)
    candidate = _candidate(session)
    module = panorama["architecture"]["modules"][0]
    node = next(
        item for item in candidate["nodes"] if item["entityRef"]["id"] == module["id"]
    )
    baseline = deepcopy(candidate)

    node["displayName"] = "可编辑项目控制台"
    node["purpose"] = "承载项目级开发与审批入口。"
    node["responsibilities"] = ["提交任务", "展示开发进度"]
    node["nonResponsibilities"] = ["不直接执行生产发布"]
    node["stateOwnership"] = "只持有交互会话状态"
    node["dataHandled"] = ["用户指令", "审批结果"]
    node["interfaceSummary"] = ["HTTPS JSON"]
    node["deploymentRole"] = "开发环境入口"
    node["technologies"] = [
        {
            "name": "Vanilla Web UI",
            "role": "交互控制台",
            "decisionId": None,
            "status": "target",
        }
    ]

    diff = candidate_semantic_diff(baseline, candidate)
    modified = next(
        item for item in diff["nodes"]["modified"] if module["id"] in item["identity"]
    )
    assert "displayName" in {item["field"] for item in modified["fields"]}
    materialized = candidate_to_panorama(
        panorama, session, candidate["candidateId"]
    )
    updated = next(
        item for item in materialized["architecture"]["modules"] if item["id"] == module["id"]
    )
    assert updated["displayName"] == "可编辑项目控制台"
    assert updated["purpose"] == "承载项目级开发与审批入口。"
    assert updated["targetDesign"]["responsibilities"] == ["提交任务", "展示开发进度"]
    assert updated["targetDesign"]["nonResponsibilities"] == ["不直接执行生产发布"]
    assert updated["targetDesign"]["stateOwnership"] == "只持有交互会话状态"
    assert updated["targetDesign"]["dataHandled"] == ["用户指令", "审批结果"]
    assert updated["targetDesign"]["interfaceSummary"] == ["HTTPS JSON"]
    assert updated["targetDesign"]["deploymentRole"] == "开发环境入口"
    assert updated["targetDesign"]["technologies"][0]["name"] == "Vanilla Web UI"
