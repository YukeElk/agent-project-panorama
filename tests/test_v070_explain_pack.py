from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from panorama_explain_pack import (
    build_explain_pack,
    compute_explain_pack_semantic_hash,
    validate_explain_pack,
)
from panorama_view_set import build_view_set
from render_codex_explainer import (
    build_fragment_payload,
    render_fragment,
    render_markdown,
)
from panorama_view_ir import PanoramaViewIRError
from panorama_view_ir import (
    compile_dependency_dataflow_view_ir,
    compile_deployment_runtime_view_ir,
    compile_evolution_risk_view_ir,
    compile_lifecycle_view_ir,
    compile_model_ir,
    compile_module_view_ir,
    compile_sequence_view_ir,
)
from render_panorama_views import build_bundle, render_html
from verified_delivery import prepare_delivery
from test_v060_multi_view_renderer import _model_and_views, _reference


def _compiled(project_root: Path, tmp_path: Path):
    panorama = _reference(project_root)
    model, views = _model_and_views(project_root, tmp_path)
    view_set = build_view_set(model, views)
    pack = build_explain_pack(panorama, model, views, view_set)
    return panorama, model, views, view_set, pack


def test_v070_explain_pack_covers_six_views_and_governance_stories(
    project_root, tmp_path
):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)

    assert pack["formatVersion"] == "panorama-explain-pack.v0.1"
    assert pack["explainPackId"].startswith("PEP-")
    assert validate_explain_pack(pack, panorama, model, views, view_set) == []
    assert len([story for story in pack["stories"] if story["kind"] == "view"]) == 6
    assert {story["viewBinding"]["profile"] for story in pack["stories"] if story["kind"] == "view"} == {
        "module",
        "dependency_dataflow",
        "deployment_runtime",
        "sequence",
        "lifecycle",
        "evolution_risk",
    }
    kinds = {story["kind"] for story in pack["stories"]}
    assert {"decision", "risk", "evidence_freshness", "verification_trace"} <= kinds
    for story in pack["stories"]:
        for step in story["steps"]:
            for claim in step["claimBlocks"]:
                if claim["kind"] == "bound_fact":
                    assert any(
                        claim[field]
                        for field in (
                            "nodeIds",
                            "edgeIds",
                            "modelEntityIds",
                            "modelRelationIds",
                            "coreRefIds",
                            "evidencePinIds",
                        )
                    )


def test_v070_governance_only_case_preserves_decision_and_empty_event_boundaries(
    project_root
):
    panorama = _reference(project_root)
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

    assert any(story["kind"] == "decision" for story in pack["stories"])
    assert any(story["kind"] == "verification_trace" for story in pack["stories"])
    sequence_story = next(
        story
        for story in pack["stories"]
        if story["kind"] == "view" and story["viewBinding"]["profile"] == "sequence"
    )
    assert any(
        claim["kind"] in {"information_gap", "navigation"}
        for step in sequence_story["steps"]
        for claim in step["claimBlocks"]
    )


def test_v070_event_checkpoint_case_explains_recorded_sequence_without_runtime_claim(
    project_root, tmp_path
):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)
    sequence_view = next(view for view in views if view["profile"] == "sequence")
    lifecycle_view = next(view for view in views if view["profile"] == "lifecycle")
    sequence_story = next(
        story
        for story in pack["stories"]
        if story["kind"] == "view" and story["viewBinding"]["profile"] == "sequence"
    )

    assert sequence_view["edges"]
    assert lifecycle_view["edges"]
    assert all(
        "Runtime Call" not in claim["text"]
        for step in sequence_story["steps"]
        for claim in step["claimBlocks"]
    )
    assert any(
        "Engineering Event Action" in caveat
        for step in sequence_story["steps"]
        for caveat in step["caveats"]
    )


def test_v070_explain_pack_is_deterministic_and_layout_bound(project_root, tmp_path):
    panorama, model, views, view_set, first = _compiled(project_root, tmp_path)
    assert build_explain_pack(panorama, model, views, view_set) == first

    changed_view_set = deepcopy(view_set)
    changed_view_set["chapters"][0]["layoutHash"] = "f" * 64
    assert any(
        "viewBinding" in error or "View Set" in error
        for error in validate_explain_pack(first, panorama, model, views, changed_view_set)
    )


def test_v070_explain_pack_rejects_content_and_binding_tamper(project_root, tmp_path):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)

    content_tamper = deepcopy(pack)
    content_tamper["stories"][0]["title"] = "tampered"
    assert any("semanticHash" in error for error in validate_explain_pack(content_tamper, panorama, model, views, view_set))

    repaired_hash = deepcopy(content_tamper)
    repaired_hash["integrity"]["semanticHash"] = compute_explain_pack_semantic_hash(repaired_hash)
    assert any("explainPackId" in error for error in validate_explain_pack(repaired_hash, panorama, model, views, view_set))

    core_tamper = deepcopy(pack)
    core_tamper["coreReferences"][0]["digest"] = "f" * 64
    core_tamper["integrity"]["semanticHash"] = compute_explain_pack_semantic_hash(core_tamper)
    assert any("digest" in error for error in validate_explain_pack(core_tamper, panorama, model, views, view_set))

    changed_panorama = deepcopy(panorama)
    changed_panorama["meta"]["revision"] += 1
    assert any("projectBinding" in error for error in validate_explain_pack(pack, changed_panorama, model, views, view_set))


def test_v070_explanations_recompile_when_project_facts_progress(project_root):
    panorama = _reference(project_root)
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
    previous = build_explain_pack(panorama, model, views, view_set)

    progressed = deepcopy(panorama)
    progressed["meta"]["revision"] += 1
    progressed["risks"][0]["impact"] = "新的项目事实：若不处理，将阻塞下一阶段验收。"
    next_model = compile_model_ir(progressed)
    next_views = [
        compile_module_view_ir(next_model),
        compile_dependency_dataflow_view_ir(next_model),
        compile_deployment_runtime_view_ir(next_model),
        compile_sequence_view_ir(next_model),
        compile_lifecycle_view_ir(next_model),
        compile_evolution_risk_view_ir(next_model),
    ]
    next_view_set = build_view_set(next_model, next_views)
    current = build_explain_pack(progressed, next_model, next_views, next_view_set)

    assert previous["explainPackId"] != current["explainPackId"]
    assert any(
        "阻塞下一阶段验收" in claim["text"]
        for story in current["stories"]
        if story["kind"] == "risk"
        for step in story["steps"]
        for claim in step["claimBlocks"]
    )
    assert any(
        "projectBinding" in error
        for error in validate_explain_pack(previous, progressed, next_model, next_views, next_view_set)
    )


def test_v070_explain_pack_cli_writes_sidecar_and_refuses_overwrite(
    project_root, tmp_path
):
    panorama, model, views, view_set, _ = _compiled(project_root, tmp_path)
    panorama_path = tmp_path / "panorama.json"
    model_path = tmp_path / "model.json"
    view_set_path = tmp_path / "view-set.json"
    panorama_path.write_text(json.dumps(panorama, ensure_ascii=False), encoding="utf-8")
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    view_set_path.write_text(json.dumps(view_set, ensure_ascii=False), encoding="utf-8")
    view_paths = []
    for index, view in enumerate(views):
        path = tmp_path / f"view-{index}.json"
        path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
        view_paths.append(path)
    output = tmp_path / "explain-pack.json"
    args = [
        sys.executable,
        str(project_root / "scripts" / "compile_panorama_explain_pack.py"),
        str(panorama_path),
        str(model_path),
        "--view-set",
        str(view_set_path),
        "--output",
        str(output),
    ]
    for path in view_paths:
        args.extend(["--view", str(path)])
    first = subprocess.run(
        args, cwd=project_root, text=True, encoding="utf-8", capture_output=True, check=False
    )
    assert first.returncode == 0, first.stderr
    assert output.exists()
    assert json.loads(first.stdout)["status"] == "compiled"
    second = subprocess.run(
        args, cwd=project_root, text=True, encoding="utf-8", capture_output=True, check=False
    )
    assert second.returncode == 2
    assert "已存在" in second.stderr


def test_v070_codex_fragment_is_offline_bounded_and_uses_same_pack(
    project_root, tmp_path
):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)
    payload = build_fragment_payload(pack, views)
    fragment = render_fragment(pack, views)
    markdown = render_markdown(pack)

    assert payload["explainPackId"] == pack["explainPackId"]
    assert payload["explainPackSemanticHash"] == pack["integrity"]["semanticHash"]
    assert len(payload["stories"]) == len(pack["stories"])
    assert "<!doctype" not in fragment.lower()
    assert "<html" not in fragment.lower()
    assert "<head" not in fragment.lower()
    assert "<body" not in fragment.lower()
    assert "fetch(" not in fragment.lower()
    assert "xmlhttprequest" not in fragment.lower()
    assert "websocket" not in fragment.lower()
    assert fragment.count(f'id="panorama-explain-{pack["explainPackId"].lower()}"') == 1
    assert len(fragment.encode("utf-8")) < 1024 * 1024
    assert "ResizeObserver" in fragment
    assert 'aria-live="polite"' in fragment
    assert "上一步" in fragment and "下一步" in fragment
    assert pack["explainPackId"] in markdown
    assert all(story["title"] in markdown for story in pack["stories"])


def test_v070_codex_fragment_cli_requires_external_output_and_writes_fallback(
    project_root, tmp_path
):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)
    inputs = {
        "panorama": panorama,
        "model": model,
        "view-set": view_set,
        "explain-pack": pack,
    }
    paths = {}
    for name, value in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    view_paths = []
    for index, view in enumerate(views):
        path = tmp_path / f"fragment-view-{index}.json"
        path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
        view_paths.append(path)
    output = tmp_path / "guided-explainer.html"
    fallback = tmp_path / "guided-explainer.md"
    args = [
        sys.executable,
        str(project_root / "scripts" / "render_codex_explainer.py"),
        str(paths["panorama"]),
        str(paths["model"]),
        "--view-set",
        str(paths["view-set"]),
        "--explain-pack",
        str(paths["explain-pack"]),
        "--output",
        str(output),
        "--markdown-output",
        str(fallback),
    ]
    for path in view_paths:
        args.extend(["--view", str(path)])
    run = subprocess.run(
        args, cwd=project_root, text=True, encoding="utf-8", capture_output=True, check=False
    )
    assert run.returncode == 0, run.stderr
    assert output.exists() and fallback.exists()
    result = json.loads(run.stdout)
    assert result["explainPackId"] == pack["explainPackId"]
    assert result["bytes"] < 1024 * 1024

    refused_output = project_root / ".panorama-work" / "forbidden-fragment.html"
    refused_args = args.copy()
    refused_args[refused_args.index(str(output))] = str(refused_output)
    refused_args.append("--overwrite")
    refused = subprocess.run(
        refused_args, cwd=project_root, text=True, encoding="utf-8", capture_output=True, check=False
    )
    assert refused.returncode == 2
    assert "仓库之外" in refused.stderr


def test_v070_explain_pack_is_consumed_by_v080_native_canvas(project_root, tmp_path):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)
    bundle = build_bundle(
        model,
        views,
        view_set=view_set,
        explain_pack=pack,
        panorama=panorama,
    )
    html = render_html(bundle, panorama=panorama)

    assert bundle["formatVersion"] == "panorama-multi-view-bundle.v0.2"
    assert bundle["renderer"]["version"] == "0.4.0"
    assert bundle["explainPack"] == pack
    assert 'data-view="control"' in html
    assert 'data-view="system"' in html
    assert 'data-view="evolution"' in html
    assert "架构设计 Studio" in html
    assert f'"explainPackId":"{pack["explainPackId"]}"' in html
    assert 'id="panorama-guided-v08"' in html
    assert "panorama-guided-v08" in html
    assert "逐步讲解" in html and "业务流程演示" in html
    assert 'aria-live="polite"' in html
    assert "单击画布节点" in html
    assert "不需要按回车" in html
    assert "待决策项" in html and "风险" in html
    assert "Observed / bound" not in html
    assert "<iframe" not in html
    assert "data:text/html;base64" not in html
    assert "connect-src 'none'" in html


def test_v070_view_stories_cover_whole_view_and_every_node_in_detail(
    project_root, tmp_path
):
    _, _, views, _, pack = _compiled(project_root, tmp_path)
    view_by_id = {view["viewId"]: view for view in views}
    for story in [item for item in pack["stories"] if item["kind"] == "view"]:
        titles = [step["title"] for step in story["steps"]]
        assert "整图概览、范围与边界" in titles or "当前没有可讲解节点" in titles
        view = view_by_id[story["viewBinding"]["viewId"]]
        if not view["nodes"]:
            continue
        detail_steps = [
            step for step in story["steps"] if step["title"].startswith("节点详解")
        ]
        assert detail_steps
        covered = {
            node_id for step in detail_steps for node_id in step["focusNodeIds"]
        }
        assert covered == {node["id"] for node in view["nodes"]}
        detail_text = " ".join(
            claim["text"] for step in detail_steps for claim in step["claimBlocks"]
        )
        assert "职责：" in detail_text and "事实状态：" in detail_text


def test_v070_risk_and_decision_stories_explain_causes_actions_and_outcomes(
    project_root, tmp_path
):
    _, _, _, _, pack = _compiled(project_root, tmp_path)
    risk_stories = [story for story in pack["stories"] if story["kind"] == "risk"]
    decision_stories = [
        story for story in pack["stories"] if story["kind"] == "decision"
    ]
    assert risk_stories and decision_stories
    for story in risk_stories:
        titles = {step["title"] for step in story["steps"]}
        assert {
            "为什么会有这个风险",
            "可能影响与导致的结果",
            "应对措施",
        }.issubset(titles)
    for story in decision_stories:
        titles = {step["title"] for step in story["steps"]}
        assert "为什么现在需要这个决策" in titles
        assert "不同选择可能导致什么结果" in titles
        option_text = " ".join(
            claim["text"]
            for step in story["steps"]
            if step["title"] in {"候选选项及其细节", "不同选择可能导致什么结果"}
            for claim in step["claimBlocks"]
        )
        assert "可逆性：" in option_text and "预计成本：" in option_text


def test_v070_native_dialog_fails_closed_without_core_or_on_pack_tamper(
    project_root, tmp_path
):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)
    with pytest.raises(PanoramaViewIRError, match="Panorama Core"):
        build_bundle(model, views, view_set=view_set, explain_pack=pack)

    tampered = deepcopy(pack)
    tampered["stories"][0]["summary"] = "tampered"
    with pytest.raises(PanoramaViewIRError, match="Explain Pack 无效"):
        build_bundle(
            model,
            views,
            view_set=view_set,
            explain_pack=tampered,
            panorama=panorama,
        )


def test_v070_verified_delivery_transitively_binds_explain_pack(
    project_root, tmp_path
):
    panorama, model, views, view_set, pack = _compiled(project_root, tmp_path)
    delivery_root = tmp_path / "delivery-project"
    delivery_root.mkdir()
    receipt, created = prepare_delivery(
        delivery_root,
        model,
        views,
        view_set=view_set,
        explain_pack=pack,
        panorama=panorama,
        generated_at="2026-08-22T10:00:00Z",
    )
    candidate = (
        delivery_root
        / ".panorama-work"
        / "verified-delivery"
        / "v0.1"
        / receipt["artifact"]["ref"]
    )
    html = candidate.read_text(encoding="utf-8")

    assert created is True
    assert pack["explainPackId"] in html
    assert receipt["bundleBinding"]["renderer"]["version"] == "0.4.0"
    assert receipt["gates"]["visualReview"] == "pending"
    assert "browser_evidence_not_provided" in receipt["informationGaps"]
