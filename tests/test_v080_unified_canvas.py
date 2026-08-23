from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from panorama_explain_pack import build_explain_pack, validate_explain_pack
from panorama_view_ir import (
    compile_dependency_dataflow_view_ir,
    compile_deployment_runtime_view_ir,
    compile_evolution_risk_view_ir,
    compile_lifecycle_view_ir,
    compile_model_ir,
    compile_module_view_ir,
    compile_sequence_view_ir,
)
from panorama_view_set import build_view_set
from render_panorama_views import build_bundle, render_html
from studio_session import candidate_to_panorama, create_session, semantic_hash, validate_session
from test_v060_multi_view_renderer import _reference
from validate_panorama import validate_data


def _business_flow() -> dict:
    return {
        "id": "FLOW-DEVELOPER-REQUEST",
        "name": "开发者请求处理",
        "summary": "从控制台提交请求，经编排与业务 Agent 到当前向量访问层。",
        "architectureScope": "current",
        "factStatus": "declared",
        "status": "active",
        "isDefault": True,
        "trigger": "开发者在控制台提交项目任务。",
        "outcome": "当前向量访问层返回处理结果。",
        "steps": [
            {
                "id": "FLOWSTEP-REQUEST-01",
                "order": 0,
                "type": "trigger",
                "moduleId": "MOD-CONSOLE",
                "connectionId": None,
                "action": "控制台接收开发者请求",
                "dataSummary": "任务与项目上下文",
                "result": "形成待编排请求",
                "riskIds": [],
                "decisionIds": [],
                "referenceIds": [],
                "extensions": {},
            },
            {
                "id": "FLOWSTEP-REQUEST-02",
                "order": 1,
                "type": "data_transfer",
                "moduleId": "MOD-ORCHESTRATOR",
                "connectionId": "CONN-CONSOLE-ORCH",
                "action": "编排器接收并拆分请求",
                "dataSummary": "用户请求、项目上下文和操作指令",
                "result": "形成业务 Agent 调用",
                "riskIds": [],
                "decisionIds": [],
                "referenceIds": ["REF-INTERFACE"],
                "extensions": {},
            },
            {
                "id": "FLOWSTEP-REQUEST-03",
                "order": 2,
                "type": "processing",
                "moduleId": "MOD-BUSINESS-AGENT",
                "connectionId": "CONN-ORCH-AGENT",
                "action": "业务 Agent 执行任务并发起检索",
                "dataSummary": "Agent 输入和运行上下文",
                "result": "形成向量检索请求",
                "riskIds": [],
                "decisionIds": [],
                "referenceIds": [],
                "extensions": {},
            },
            {
                "id": "FLOWSTEP-REQUEST-04",
                "order": 3,
                "type": "result",
                "moduleId": "MOD-DIRECT-VECTOR",
                "connectionId": "CONN-AGENT-DIRECT",
                "action": "当前向量访问层返回检索结果",
                "dataSummary": "检索查询与向量结果",
                "result": "业务任务获得当前检索结果",
                "riskIds": [],
                "decisionIds": [],
                "referenceIds": [],
                "extensions": {},
            },
        ],
        "riskIds": [],
        "decisionIds": [],
        "referenceIds": ["REF-INTERFACE"],
        "extensions": {},
    }


def _compiled(project_root: Path):
    panorama = deepcopy(_reference(project_root))
    panorama["businessFlows"] = [_business_flow()]
    model = compile_model_ir(panorama)
    views = [
        compile_module_view_ir(model),
        compile_dependency_dataflow_view_ir(model),
        compile_deployment_runtime_view_ir(model),
        compile_sequence_view_ir(model),
        compile_lifecycle_view_ir(model),
        compile_evolution_risk_view_ir(model),
    ]
    view_set = build_view_set(model, views)
    pack = build_explain_pack(panorama, model, views, view_set)
    return panorama, model, views, view_set, pack


def test_v080_reference_projects_publish_reproducible_default_business_flow(project_root):
    for name in ("reference-project.v0.1.json", "reference-project.v0.2.json"):
        panorama = json.loads(
            (project_root / "examples" / name).read_text(encoding="utf-8")
        )
        assert validate_data(panorama).errors == []
        assert len(panorama["businessFlows"]) == 1
        flow = panorama["businessFlows"][0]
        assert flow["id"] == "FLOW-DEVELOPER-REQUEST"
        assert flow["isDefault"] is True
        assert [step["moduleId"] for step in flow["steps"]] == [
            "MOD-CONSOLE",
            "MOD-ORCHESTRATOR",
            "MOD-BUSINESS-AGENT",
            "MOD-RETRIEVAL",
            "MOD-WORKSPACE",
            "MOD-VECTORSTORE",
        ]
        assert all(step["connectionId"] for step in flow["steps"][1:])


def test_v080_business_flow_requires_authored_contiguous_directional_path(project_root):
    panorama = deepcopy(_reference(project_root))
    panorama["businessFlows"] = [_business_flow()]
    assert validate_data(panorama).errors == []

    broken_order = deepcopy(panorama)
    broken_order["businessFlows"][0]["steps"][2]["order"] = 4
    assert any(
        item.code == "BUSINESS_FLOW_ORDER_GAP"
        for item in validate_data(broken_order).errors
    )

    broken_connection = deepcopy(panorama)
    broken_connection["businessFlows"][0]["steps"][2]["connectionId"] = "CONN-AGENT-DIRECT"
    assert any(
        item.code == "BUSINESS_FLOW_CONNECTION_MISMATCH"
        for item in validate_data(broken_connection).errors
    )


def test_v080_explain_pack_projects_exact_finite_playback_scenario(project_root):
    panorama, model, views, view_set, pack = _compiled(project_root)
    assert validate_explain_pack(pack, panorama, model, views, view_set) == []
    assert len(pack["playbackScenarios"]) == 1
    scenario = pack["playbackScenarios"][0]
    assert scenario["businessFlowId"] == "FLOW-DEVELOPER-REQUEST"
    assert scenario["isDefault"] is True
    assert [step["order"] for step in scenario["steps"]] == [0, 1, 2, 3]
    assert all(step["nodeId"] for step in scenario["steps"])
    assert scenario["steps"][0]["edgeId"] is None
    assert all(step["edgeId"] for step in scenario["steps"][1:])
    assert all(step["coreRefIds"] for step in scenario["steps"])


def test_v080_module_explanation_forms_complete_design_logic_chain(project_root):
    _, _, _, _, pack = _compiled(project_root)
    module_story = next(
        story
        for story in pack["stories"]
        if story["kind"] == "view" and story["viewBinding"]["profile"] == "module"
    )
    text = "\n".join(
        claim["text"]
        for step in module_story["steps"]
        for claim in step["claimBlocks"]
    )
    for heading in (
        "设计需求｜",
        "设计原因｜",
        "实现功能｜",
        "系统角色｜",
        "交互方式｜",
        "技术选型｜",
        "关联风险｜",
    ):
        assert heading in text


def test_v080_integrated_renderer_uses_same_page_canvas_and_inline_story(project_root):
    panorama, model, views, view_set, pack = _compiled(project_root)
    bundle = build_bundle(
        model,
        views,
        view_set=view_set,
        explain_pack=pack,
        panorama=panorama,
    )
    html = render_html(bundle, panorama=panorama)
    assert bundle["renderer"]["version"] == "0.4.0"
    assert "panorama-guided-v08" in html
    assert "阅读模式" in html
    assert 'data-studio-mode="explain"' not in html
    assert 'data-canvas-runtime="unified"' in html
    assert 'data-canvas-world="one"' in html
    assert "业务流程演示" in html
    assert "播放" in html and "暂停" in html and "重新开始" in html
    assert "prefers-reduced-motion" in html
    assert "逐步讲解" in html and "项目架构主画布" in html
    assert "<iframe" not in html
    assert "data:text/html;base64" not in html
    assert "showModal" not in html.split("panorama-guided-v08", 1)[1]


def test_v080_same_canvas_exposes_progress_risk_decision_and_playback_lenses(project_root):
    source = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    assert '[["explain","讲解"],["detail","节点"],["progress","进度"],["risk_decision","风险/决策"],["playback","流程演示"]]' in source
    assert 'lens==="progress"' in source
    assert 'lens==="risk_decision"' in source
    assert "this.store.viewId=scenario.viewBinding.viewId" in source


def test_v080_reference_uses_chinese_primary_labels_and_keeps_technical_identity(project_root):
    panorama = deepcopy(_reference(project_root))
    assert all(layer.get("displayName") for layer in panorama["architecture"]["layers"])
    assert all(module.get("displayName") for module in panorama["architecture"]["modules"])
    model = compile_model_ir(panorama)
    modules = [entity for entity in model["entities"] if entity["kind"] == "module"]
    assert all(entity["displayLabel"] != entity["technicalLabel"] for entity in modules)
    assert not [gap for gap in model["informationGaps"] if gap.startswith("display_name_missing:")]
    view = compile_module_view_ir(model, architecture_scopes=["current"])
    assert {node["label"] for node in view["nodes"]} >= {"项目控制台", "编排器", "业务智能体"}
    source = (project_root / "templates" / "panorama-guided-host.html").read_text(encoding="utf-8")
    assert "enhanceLocalizedScanning" in source
    assert "entity.technicalLabel" in source


def test_v080_legacy_missing_display_name_is_visible_gap_not_auto_translation(project_root):
    panorama = deepcopy(_reference(project_root))
    panorama["architecture"]["modules"][0].pop("displayName")
    model = compile_model_ir(panorama)
    module = next(entity for entity in model["entities"] if entity["id"] == "MOD-CONSOLE")
    assert module["displayLabel"] == module["technicalLabel"] == "Project Console"
    assert "display_name_missing:module:MOD-CONSOLE" in model["informationGaps"]


def test_v080_guided_canvas_fits_desktop_shell_and_routes_by_facing_ports(project_root):
    source = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    assert ":host{display:block;height:calc(100vh - 171px)" in source
    assert ".layout{display:grid;" in source
    assert "height:calc(100% - 59px);min-height:0" in source
    assert ".canvas-viewport{position:relative;flex:1;min-height:0;overflow:hidden" in source
    assert ".rail-body{flex:1;min-height:0;overflow:auto" in source
    assert "class CanvasSceneAdapter" in source
    assert "class CanvasStore" in source
    assert "class CameraController" in source
    assert "class NavigationStack" in source
    assert 'data-semantic-enter="double-click"' in source
    assert "this.store.enter(child.viewId)" in source
    assert "else if(to.x>from.x)" in source
    assert "else{x1=from.x;x2=to.x+to.w" in source
    assert "Math.abs(from.x-to.x)<2" in source


def test_v080_explanations_and_playback_recompile_when_flow_progresses(project_root):
    panorama, _, _, _, previous = _compiled(project_root)
    progressed = deepcopy(panorama)
    progressed["meta"]["revision"] += 1
    progressed["businessFlows"][0]["steps"][3]["result"] = "新的事实：结果已进入验收队列"
    model = compile_model_ir(progressed)
    views = [compile_module_view_ir(model)]
    view_set = build_view_set(model, views)
    current = build_explain_pack(progressed, model, views, view_set)
    assert previous["explainPackId"] != current["explainPackId"]
    assert current["playbackScenarios"][0]["steps"][3]["result"] == "新的事实：结果已进入验收队列"


def test_v080_progress_story_separates_work_from_validation_approval_and_deployment(project_root):
    _, _, _, _, pack = _compiled(project_root)
    progress = [story for story in pack["stories"] if story["kind"] == "progress"]
    assert progress
    text = "\n".join(
        claim["text"]
        for story in progress
        for step in story["steps"]
        for claim in step["claimBlocks"]
    )
    assert "当前工作精确绑定" in text
    assert "验证看 Acceptance/Gate" in text
    assert "批准看 Approval" in text
    assert "部署看 Release/Deployment" in text


def test_v080_studio_layers_are_candidate_semantics_and_materialize_governed_hierarchy(project_root):
    panorama = deepcopy(_reference(project_root))
    session = create_session(panorama)
    candidate = session["candidates"][0]
    before = semantic_hash(candidate)
    candidate["architectureLayers"].append(
        {
            "id": "LAYER-STUDIO-CAPABILITY",
            "name": "业务能力区",
            "order": len(candidate["architectureLayers"]),
            "summary": "由 Studio 候选新增的功能层级。",
            "parentLayerId": candidate["architectureLayers"][0]["id"],
            "kind": "capability",
            "extensions": {},
            "layoutHeight": 160,
        }
    )
    assert semantic_hash(candidate) != before
    semantic_with_height = semantic_hash(candidate)
    candidate["architectureLayers"][-1]["layoutHeight"] = 220
    assert semantic_hash(candidate) == semantic_with_height
    assert not [item for item in validate_session(session) if item["level"] == "error"]
    materialized = candidate_to_panorama(panorama, session, candidate["candidateId"])
    added = next(
        layer
        for layer in materialized["architecture"]["layers"]
        if layer["id"] == "LAYER-STUDIO-CAPABILITY"
    )
    assert added["parentLayerId"] == candidate["architectureLayers"][0]["id"]
    assert "layoutHeight" not in added
    assert validate_data(materialized).errors == []


def test_v080_studio_template_exposes_layer_hierarchy_module_and_dataflow_authoring(project_root):
    source = (project_root / "templates" / "panorama.html").read_text(encoding="utf-8")
    assert "层级 / 功能区" in source
    assert "data-studio-layer-action='add'" in source
    assert "data-studio-layer-parent" in source
    assert "layer.change_parent" in source
    assert "data-studio-add-node='agent'" in source
    assert "data-studio-action='add-edge'" in source
