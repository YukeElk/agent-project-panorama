from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from event_projection import load_event_checkpoint
from event_store import record_request
from panorama_view_ir import (
    PanoramaViewIRError,
    compile_dependency_dataflow_view_ir,
    compile_deployment_runtime_view_ir,
    compile_evolution_risk_view_ir,
    compile_lifecycle_view_ir,
    compile_model_ir,
    compile_module_view_ir,
    compile_sequence_view_ir,
)
from render_panorama_views import build_bundle, render_html
from source_topology import extract_source_topology


def _reference(project_root: Path) -> dict:
    return json.loads(
        (project_root / "examples" / "reference-project.v0.2.json").read_text(
            encoding="utf-8"
        )
    )


def _model_and_views(project_root: Path, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("import b\n", encoding="utf-8")
    (source / "b.py").write_text("VALUE = 1\n", encoding="utf-8")
    observation = extract_source_topology(
        source,
        project_id="PRJ-MARW",
        observed_at="2026-08-21T14:00:00Z",
    )["observation"]

    request = json.loads(
        (project_root / "examples" / "engineering-event-request.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    request["projectBinding"]["projectId"] = "PRJ-MARW"
    request["subjectRefs"] = [
        {"type": "module", "id": "MOD-CONSOLE", "relationship": "validates"}
    ]
    request["extensions"] = {
        "panoramaProjection": {
            "lifecycleTransitions": [
                {
                    "subjectType": "module",
                    "subjectId": "MOD-CONSOLE",
                    "fromState": "pending",
                    "toState": "validated",
                    "trigger": "validation.completed",
                }
            ]
        }
    }
    store = tmp_path / "event-store"
    record_request(store, request, recorded_at="2026-08-21T14:00:01Z")
    checkpoint = load_event_checkpoint(store, project_id="PRJ-MARW")
    model = compile_model_ir(
        _reference(project_root),
        source_observation=observation,
        event_checkpoint=checkpoint,
    )
    views = [
        compile_module_view_ir(model),
        compile_dependency_dataflow_view_ir(model, max_nodes=100),
        compile_deployment_runtime_view_ir(model),
        compile_sequence_view_ir(model),
        compile_lifecycle_view_ir(model),
        compile_evolution_risk_view_ir(model),
    ]
    return model, views


def test_v060_renderer_builds_bound_offline_six_view_bundle(
    project_root, tmp_path
):
    model, views = _model_and_views(project_root, tmp_path)
    bundle = build_bundle(model, views)
    html = render_html(bundle)

    assert bundle["formatVersion"] == "panorama-multi-view-bundle.v0.1"
    assert len(bundle["views"]) == 6
    assert [view["profile"] for view in bundle["views"]] == [
        "module",
        "dependency_dataflow",
        "deployment_runtime",
        "sequence",
        "lifecycle",
        "evolution_risk",
    ]
    assert bundle["bundleId"].startswith("PVB-")
    assert "connect-src 'none'" in html
    assert 'id="panorama-view-bundle"' in html
    assert "data-view=" in html or 'id="tabs"' in html
    assert "__PANORAMA_VIEW_BUNDLE__" not in html
    assert "https://" not in html
    assert "http://" not in html
    assert "Search/Focus" not in html
    assert "滚轮缩放" in html


def test_v060_renderer_rejects_tampered_or_duplicate_view(project_root, tmp_path):
    model, views = _model_and_views(project_root, tmp_path)
    tampered = deepcopy(views[0])
    tampered["title"] = "tampered"
    with pytest.raises(PanoramaViewIRError, match="View"):
        build_bundle(model, [tampered])
    with pytest.raises(PanoramaViewIRError, match="profile 重复"):
        build_bundle(model, [views[0], deepcopy(views[0])])


def test_v060_renderer_cli_and_javascript_syntax(project_root, tmp_path):
    model, views = _model_and_views(project_root, tmp_path)
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    view_paths = []
    for view in views:
        path = tmp_path / f"{view['profile']}.json"
        path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
        view_paths.append(path)
    output = tmp_path / "multi-view.html"
    args = [
        sys.executable,
        str(project_root / "scripts" / "render_panorama_views.py"),
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
    assert json.loads(run.stdout)["visualReview"] == "pending"
    assert output.exists()

    refused = subprocess.run(
        args,
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "已存在" in refused.stderr
