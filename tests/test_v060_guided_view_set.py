from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from panorama_view_ir import PanoramaViewIRError, compile_module_view_ir
from panorama_view_set import build_view_set, validate_view_set
from render_panorama_views import build_bundle, render_html
from test_v060_multi_view_renderer import _model_and_views


def _guided(project_root: Path, tmp_path: Path):
    model, views = _model_and_views(project_root, tmp_path)
    target = compile_module_view_ir(model, architecture_scopes=["target"])
    return model, [*views, target]


def test_v060_view_set_guides_current_and_target_without_owning_topology(
    project_root, tmp_path
):
    model, views = _guided(project_root, tmp_path)
    view_set = build_view_set(model, views)

    assert view_set["formatVersion"] == "panorama-view-set.v0.1"
    assert view_set["viewSetId"].startswith("PVS-")
    assert len(view_set["chapters"]) == 7
    assert [(item["profile"], item["architectureScope"]) for item in view_set["chapters"][:2]] == [
        ("module", "current"),
        ("module", "target"),
    ]
    assert [item["label"] for item in view_set["chapters"][:2]] == [
        "模块 · 当前",
        "模块 · 目标",
    ]
    assert set(view_set["chapters"][0]) == {
        "chapterId",
        "viewId",
        "order",
        "profile",
        "architectureScope",
        "label",
        "semanticHash",
        "layoutHash",
    }
    assert validate_view_set(view_set, model, views) == []

    bundle = build_bundle(model, views, view_set=view_set)
    assert bundle["viewSet"] == view_set
    assert [view["viewId"] for view in bundle["views"]] == [
        chapter["viewId"] for chapter in view_set["chapters"]
    ]
    html = render_html(bundle)
    assert "引导式视图章节" in html
    assert "viewSet.defaultChapterId" in html
    assert "data-chapter=" in html
    assert 'id="qa-diagnostics"' in html


def test_v060_view_set_fails_closed_on_binding_order_and_hash_tamper(
    project_root, tmp_path
):
    model, views = _guided(project_root, tmp_path)
    view_set = build_view_set(model, views)

    binding_tamper = deepcopy(view_set)
    binding_tamper["chapters"][0]["semanticHash"] = "f" * 64
    assert any("semanticHash" in item for item in validate_view_set(binding_tamper, model, views))

    order_tamper = deepcopy(view_set)
    order_tamper["chapters"][0]["order"] = 2
    assert any("order 必须" in item for item in validate_view_set(order_tamper, model, views))

    hash_tamper = deepcopy(view_set)
    hash_tamper["description"] = "tampered"
    with pytest.raises(PanoramaViewIRError, match="View Set 无效"):
        build_bundle(model, views, view_set=hash_tamper)


def test_v060_view_set_rejects_duplicate_or_multi_scope_view(project_root, tmp_path):
    model, views = _guided(project_root, tmp_path)
    with pytest.raises(PanoramaViewIRError, match="View IR 重复"):
        build_view_set(model, [views[0], deepcopy(views[0])])

    multi_scope = deepcopy(views[0])
    multi_scope["filters"]["architectureScopes"] = ["current", "target"]
    from panorama_view_ir import compute_view_semantic_hash

    multi_scope["integrity"]["semanticHash"] = compute_view_semantic_hash(multi_scope)
    with pytest.raises(PanoramaViewIRError, match="单 scope"):
        build_view_set(model, [multi_scope])

    duplicate_variant = deepcopy(views[0])
    duplicate_variant["viewId"] = "VIEW-" + "A" * 24
    duplicate_variant["title"] = "alternate current module"
    duplicate_variant["integrity"]["semanticHash"] = compute_view_semantic_hash(
        duplicate_variant
    )
    with pytest.raises(PanoramaViewIRError, match="Guided Variant 重复"):
        build_view_set(model, [views[0], duplicate_variant])


def test_v060_view_set_cli_writes_sidecar(project_root, tmp_path):
    model, views = _guided(project_root, tmp_path)
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    view_paths = []
    for index, view in enumerate(views):
        path = tmp_path / f"view-{index}.json"
        path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
        view_paths.append(path)
    output = tmp_path / "view-set.json"
    args = [
        sys.executable,
        str(project_root / "scripts" / "compile_panorama_view_set.py"),
        str(model_path),
    ]
    for path in view_paths:
        args.extend(["--view", str(path)])
    args.extend(["--output", str(output)])
    run = subprocess.run(
        args,
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["chapterCount"] == 7
    assert json.loads(output.read_text(encoding="utf-8"))["viewSetId"] == report["viewSetId"]
