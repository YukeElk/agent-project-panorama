from __future__ import annotations

import pytest

from module_logic_observation import materialize_observation
from panorama_explain_pack import build_explain_pack, validate_explain_pack
from panorama_view_ir import (
    PanoramaViewIRError,
    compile_model_ir,
    compile_module_logic_view_ir,
    compile_module_view_ir,
)
from panorama_view_set import build_view_set
from render_panorama_views import build_bundle, render_html
from test_v080_module_logic_contract import _observation_candidate, _with_target_design
from verified_delivery import prepare_delivery


def _integrated_parent_child(reference_data):
    observation = materialize_observation(_observation_candidate(reference_data))
    model = compile_model_ir(
        reference_data,
        module_logic_observations=[observation],
    )
    parent = compile_module_view_ir(model, architecture_scopes=["current"])
    child = compile_module_logic_view_ir(
        model,
        parent_view=parent,
        root_module_id="MOD-ORCHESTRATOR",
        architecture_scopes=["current"],
    )
    view_set = build_view_set(model, [parent])
    explain_pack = build_explain_pack(
        reference_data,
        model,
        [parent],
        view_set,
        child_views=[child],
    )
    bundle = build_bundle(
        model,
        [parent],
        child_views=[child],
        view_set=view_set,
        explain_pack=explain_pack,
        panorama=reference_data,
    )
    return model, parent, child, bundle


def test_v080_bundle_keeps_child_out_of_top_level_view_set(reference_data):
    _, parent, child, bundle = _integrated_parent_child(reference_data)

    assert [item["viewId"] for item in bundle["views"]] == [parent["viewId"]]
    assert [item["viewId"] for item in bundle["childViews"]] == [child["viewId"]]
    assert [item["viewId"] for item in bundle["viewSet"]["chapters"]] == [
        parent["viewId"]
    ]
    assert bundle["childViews"][0]["parentViewBinding"]["viewId"] == parent["viewId"]


def test_v080_renderer_exposes_one_world_parent_child_navigation(reference_data):
    _, _, child, bundle = _integrated_parent_child(reference_data)
    html = render_html(bundle, panorama=reference_data)

    assert child["viewId"] in html
    assert html.count('data-canvas-world="one"') == 1
    assert 'data-canvas-runtime="unified"' in html
    assert "class CanvasSceneAdapter" in html
    assert "class CameraController" in html
    assert "class NavigationStack" in html
    assert 'data-semantic-enter="double-click"' in html
    assert "this.store.enter(child.viewId)" in html
    assert "this.store.back()" in html
    assert "overflow:hidden;touch-action:none;cursor:grab" in html
    assert ".canvas-wrap" not in html.split('id="panorama-guided-v08"', 1)[1]


def test_v080_bundle_rejects_child_with_parent_not_in_bundle(reference_data):
    model, _, child, _ = _integrated_parent_child(reference_data)
    unrelated_parent = compile_module_view_ir(
        model, architecture_scopes=["target"]
    )
    with pytest.raises(PanoramaViewIRError, match="缺少同 Bundle 父 View"):
        build_bundle(model, [unrelated_parent], child_views=[child])


def test_v080_camera_and_navigation_are_viewer_state_only(project_root):
    source = (project_root / "templates" / "panorama-guided-host.html").read_text(
        encoding="utf-8"
    )
    assert "cameras:this.cameras" in source
    assert "navigation:this.navigation.items" in source
    assert "semanticHash" not in source.split("class CameraController", 1)[1].split(
        "class PanoramaUnifiedCanvas", 1
    )[0]


def test_v080_child_canvas_has_overview_node_and_boundary_stories(reference_data):
    model, parent, child, bundle = _integrated_parent_child(reference_data)
    pack = bundle["explainPack"]

    assert validate_explain_pack(
        pack,
        reference_data,
        model,
        [parent],
        bundle["viewSet"],
        child_views=[child],
    ) == []
    child_stories = [
        story
        for story in pack["stories"]
        if story["viewBinding"]["viewId"] == child["viewId"]
    ]
    assert {story["kind"] for story in child_stories} >= {
        "module_logic_overview",
        "module_logic_node",
        "boundary_interaction",
    }
    text = "\n".join(
        claim["text"]
        for story in child_stories
        for step in story["steps"]
        for claim in step["claimBlocks"]
    )
    for heading in ("设计需求｜", "设计原因｜", "实现逻辑｜", "系统角色｜", "交互方式｜"):
        assert heading in text
    assert "外部对象=" in text and "绑定=" in text


def test_v080_verified_delivery_keeps_hash_bound_child_canvas(
    reference_data, tmp_path
):
    model, parent, child, bundle = _integrated_parent_child(reference_data)
    project = tmp_path / "project"
    project.mkdir()

    receipt, created = prepare_delivery(
        project,
        model,
        [parent],
        child_views=[child],
        view_set=bundle["viewSet"],
        explain_pack=bundle["explainPack"],
        panorama=reference_data,
        generated_at="2026-08-23T11:30:00Z",
    )

    candidate = (
        project
        / ".panorama-work"
        / "verified-delivery"
        / "v0.1"
        / receipt["artifact"]["ref"]
    )
    html = candidate.read_text(encoding="utf-8")
    assert created is True
    assert receipt["bundleBinding"]["bundleHash"] == bundle["bundleHash"]
    assert child["viewId"] in html
    assert '"childViews":[' in html
    assert receipt["gates"]["browser"]["status"] == "not_provided"


def test_v080_progress_risk_and_decision_bind_exact_target_logic_nodes(reference_data):
    panorama = _with_target_design(reference_data)
    panorama["decisions"][0]["relatedArchitectureRefs"] = [
        {"type": "logic_node", "id": "LOGIC-TARGET-INPUT"}
    ]
    model = compile_model_ir(panorama)
    parent = compile_module_view_ir(model, architecture_scopes=["target"])
    child = compile_module_logic_view_ir(
        model,
        parent_view=parent,
        root_module_id="MOD-ORCHESTRATOR",
        architecture_scopes=["target"],
    )
    view_set = build_view_set(model, [parent])
    pack = build_explain_pack(
        panorama, model, [parent], view_set, child_views=[child]
    )
    child_kinds = {
        story["kind"]
        for story in pack["stories"]
        if story["viewBinding"]["viewId"] == child["viewId"]
    }
    assert {"progress", "risk", "decision"} <= child_kinds
    assert validate_explain_pack(
        pack,
        panorama,
        model,
        [parent],
        view_set,
        child_views=[child],
    ) == []
