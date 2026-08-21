from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema

from panorama_io import compute_data_hash
from panorama_view_ir import (
    MODEL_SCHEMA,
    VIEW_SCHEMA,
    PanoramaViewIRError,
    compile_deployment_runtime_view_ir,
    compile_model_ir,
    compile_module_view_ir,
    compute_view_layout_hash,
    compute_view_semantic_hash,
    load_and_compile_model_ir,
    validate_model_ir,
    validate_view_ir,
)


def _reference(project_root: Path) -> dict:
    return json.loads(
        (project_root / "examples" / "reference-project.v0.2.json").read_text(
            encoding="utf-8"
        )
    )


def test_v060_model_and_view_schemas_are_meta_valid(project_root):
    for schema_path in (MODEL_SCHEMA, VIEW_SCHEMA):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)


def test_v060_core_compiles_to_bound_model_ir(project_root):
    data = _reference(project_root)
    model = compile_model_ir(data)

    assert validate_model_ir(model) == []
    assert model["projectBinding"] == {
        "projectId": "PRJ-MARW",
        "projectName": "Multi-Agent Research Workspace",
        "panoramaSchemaVersion": "0.2",
        "revision": 3,
        "dataHash": compute_data_hash(data),
    }
    assert len(model["entities"]) == 20
    assert len(model["relations"]) == 37
    module_entities = [item for item in model["entities"] if item["kind"] == "module"]
    communication_relations = [
        item for item in model["relations"] if item["kind"] == "communication"
    ]
    assert {entity["id"] for entity in module_entities} == {
        module["id"] for module in data["architecture"]["modules"]
    }
    assert {relation["id"] for relation in communication_relations} == {
        connection["id"] for connection in data["architecture"]["connections"]
    }
    assert {entity["kind"] for entity in model["entities"]} == {
        "module",
        "release",
        "deployment",
        "resource",
        "data_store",
    }
    assert sum(item["kind"] == "deployment" for item in model["relations"]) == 30
    assert model["eventBinding"]["status"] == "not_provided"
    assert model["sourceBinding"]["currentness"] == "recorded_as_of"
    assert model["informationGaps"] == [
        "event_checkpoint_not_provided",
        "source_currentness_not_verified",
    ]
    for item in model["layers"] + model["entities"] + model["relations"]:
        assert item["evidencePins"]
        for pin in item["evidencePins"]:
            assert pin["kind"] == "json_pointer"
            assert pin["ref"].startswith(
                ("/architecture/", "/releases/", "/deployments/", "/resources/")
            )
            assert pin["revision"] == model["projectBinding"]["dataHash"]
            assert pin["freshness"] == "recorded_as_of"


def test_v060_deployment_projection_is_bounded_and_secret_free(project_root):
    model = compile_model_ir(_reference(project_root))

    for entity in model["entities"]:
        if entity["kind"] in {"deployment", "resource", "data_store"}:
            assert entity["authority"] == "declared"
            assert "access" not in entity["attributes"]
            assert "credentials" not in entity["attributes"]
    for relation in model["relations"]:
        if relation["kind"] == "deployment":
            assert relation["semantics"]["order"] is None
            assert relation["attributes"]["runtimeObserved"] is False


def test_v060_deployment_views_preserve_scope_and_environment(project_root):
    model = compile_model_ir(_reference(project_root))
    expected = {
        "current": (13, 12, {"development", "shared"}),
        "transition": (10, 9, {"development", "shared"}),
        "target": (10, 9, {"production", "shared"}),
        "historical": (0, 0, set()),
    }
    for scope, (node_count, edge_count, environments) in expected.items():
        view = compile_deployment_runtime_view_ir(
            model, architecture_scopes=[scope]
        )
        assert validate_view_ir(view, model) == []
        assert len(view["nodes"]) == node_count
        assert len(view["edges"]) == edge_count
        assert {item["groupRef"] for item in view["groups"]} == environments
        assert all(edge["kind"] == "deployment" for edge in view["edges"])

    filtered = compile_deployment_runtime_view_ir(
        model,
        architecture_scopes=["current"],
        environments=["development"],
    )
    assert filtered["filters"]["environments"] == ["development"]
    assert {item["groupRef"] for item in filtered["groups"]} == {
        "development",
        "shared",
    }
    assert "deployment_runtime_observation_not_verified" in filtered[
        "informationGaps"
    ]


def test_v060_shared_entities_keep_stable_node_ids_across_views(project_root):
    model = compile_model_ir(_reference(project_root))
    module = compile_module_view_ir(model)
    deployment = compile_deployment_runtime_view_ir(model)
    module_ids = {
        node["entityRef"]["id"]: node["id"] for node in module["nodes"]
    }
    deployment_ids = {
        node["entityRef"]["id"]: node["id"] for node in deployment["nodes"]
    }
    shared = set(module_ids) & set(deployment_ids)
    assert shared
    assert all(module_ids[item] == deployment_ids[item] for item in shared)


def test_v060_model_and_view_compilation_are_deterministic(project_root):
    data = _reference(project_root)
    first_model = compile_model_ir(data)
    second_model = compile_model_ir(deepcopy(data))
    assert first_model == second_model

    first_view = compile_module_view_ir(first_model)
    second_view = compile_module_view_ir(second_model)
    assert first_view == second_view


def test_v060_json_and_single_html_compile_to_same_model(project_root):
    json_model = load_and_compile_model_ir(
        project_root / "examples" / "reference-project.v0.2.json"
    )
    html_model = load_and_compile_model_ir(
        project_root / "examples" / "reference-project.v0.2.html"
    )
    assert json_model == html_model


def test_v060_module_view_filters_current_and_target_without_inventing_edges(
    project_root,
):
    model = compile_model_ir(_reference(project_root))
    current = compile_module_view_ir(model, architecture_scopes=["current"])
    target = compile_module_view_ir(model, architecture_scopes=["target"])

    assert validate_view_ir(current, model) == []
    assert validate_view_ir(target, model) == []
    assert len(current["nodes"]) == 7
    assert len(current["edges"]) == 6
    assert len(target["nodes"]) == 7
    assert len(target["edges"]) == 6
    assert {node["entityRef"]["id"] for node in current["nodes"]} - {
        node["entityRef"]["id"] for node in target["nodes"]
    } == {"MOD-DIRECT-VECTOR"}
    assert {node["entityRef"]["id"] for node in target["nodes"]} - {
        node["entityRef"]["id"] for node in current["nodes"]
    } == {"MOD-METADATA"}
    assert {node["entityRef"]["id"] for node in current["nodes"]} <= {
        entity["id"] for entity in model["entities"]
    }
    assert {edge["relationRef"]["id"] for edge in current["edges"]} <= {
        relation["id"] for relation in model["relations"]
    }
    assert all(edge["order"] is None for edge in current["edges"])
    assert current["viewType"] == "architecture"
    assert current["profile"] == "module"


def test_v060_layout_is_separate_from_view_semantics(project_root):
    model = compile_model_ir(_reference(project_root))
    view = compile_module_view_ir(model)
    semantic_hash = view["integrity"]["semanticHash"]
    first_node = view["nodes"][0]["id"]

    moved = deepcopy(view)
    moved["layout"] = {
        "strategy": "authored",
        "positions": [{"nodeId": first_node, "x": 120, "y": 80}],
    }
    moved["integrity"]["layoutHash"] = compute_view_layout_hash(moved)

    assert compute_view_semantic_hash(moved) == semantic_hash
    assert moved["integrity"]["layoutHash"] != view["integrity"]["layoutHash"]
    assert validate_view_ir(moved, model) == []


def test_v060_target_view_uses_target_layer_binding(project_root):
    data = _reference(project_root)
    module = next(
        item for item in data["architecture"]["modules"] if item["id"] == "MOD-CONSOLE"
    )
    module["targetLayerId"] = "LAYER-DATA"
    model = compile_model_ir(data)
    projected = next(
        item for item in model["entities"] if item["id"] == "MOD-CONSOLE"
    )
    assert projected["layerBindings"] == {
        "current": "LAYER-INTERACTION",
        "target": "LAYER-DATA",
        "historical": None,
    }

    target = compile_module_view_ir(model, architecture_scopes=["target"])
    node = next(
        item for item in target["nodes"] if item["entityRef"]["id"] == "MOD-CONSOLE"
    )
    group = next(item for item in target["groups"] if item["id"] == node["groupId"])
    assert group["layerId"] == "LAYER-DATA"


def test_v060_model_and_view_tamper_fail_closed(project_root):
    model = compile_model_ir(_reference(project_root))
    broken_model = deepcopy(model)
    broken_model["entities"][0]["name"] = "tampered"
    assert any("semanticHash" in error for error in validate_model_ir(broken_model))

    view = compile_module_view_ir(model)
    broken_view = deepcopy(view)
    broken_view["edges"][0]["relationRef"]["id"] = "REL-UNKNOWN"
    broken_view["integrity"]["semanticHash"] = compute_view_semantic_hash(
        broken_view
    )
    errors = validate_view_ir(broken_view, model)
    assert any("未知 relationRef" in error for error in errors)

    rebound = deepcopy(view)
    rebound["nodes"][0]["label"] = "tampered"
    rebound["integrity"]["semanticHash"] = compute_view_semantic_hash(rebound)
    assert any("绑定 Entity" in error for error in validate_view_ir(rebound, model))

    false_compiler = deepcopy(view)
    false_compiler["compiler"] = {
        "id": "panorama-false-compiler",
        "version": "9.9.9",
    }
    false_compiler["integrity"]["semanticHash"] = compute_view_semantic_hash(
        false_compiler
    )
    assert any(
        "/compiler" in error for error in validate_view_ir(false_compiler, model)
    )


def test_v060_invalid_panorama_or_scope_is_rejected(project_root):
    data = _reference(project_root)
    data["architecture"]["connections"][0]["fromModuleId"] = "MOD-UNKNOWN"
    try:
        compile_model_ir(data)
    except PanoramaViewIRError as exc:
        assert "Panorama Core 无效" in str(exc)
    else:
        raise AssertionError("invalid Panorama must be rejected")

    model = compile_model_ir(_reference(project_root))
    try:
        compile_module_view_ir(model, architecture_scopes=["current", "current"])
    except PanoramaViewIRError as exc:
        assert "architecture_scopes" in str(exc)
    else:
        raise AssertionError("duplicate scope must be rejected")


def test_v060_model_and_view_cli_end_to_end(project_root, tmp_path):
    model_path = tmp_path / "reference.model-ir.json"
    view_path = tmp_path / "reference.view-ir.json"
    model_script = project_root / "scripts" / "compile_panorama_model_ir.py"
    view_script = project_root / "scripts" / "compile_panorama_view_ir.py"
    source = project_root / "examples" / "reference-project.v0.2.json"

    model_run = subprocess.run(
        [
            sys.executable,
            str(model_script),
            str(source),
            "--output",
            str(model_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert model_run.returncode == 0, model_run.stderr
    model_summary = json.loads(model_run.stdout)
    assert model_summary["status"] == "compiled"

    view_run = subprocess.run(
        [
            sys.executable,
            str(view_script),
            str(model_path),
            "--profile",
            "module",
            "--scope",
            "current",
            "--output",
            str(view_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert view_run.returncode == 0, view_run.stderr
    view_summary = json.loads(view_run.stdout)
    assert view_summary["status"] == "compiled"

    model = json.loads(model_path.read_text(encoding="utf-8"))
    view = json.loads(view_path.read_text(encoding="utf-8"))
    assert validate_model_ir(model) == []
    assert validate_view_ir(view, model) == []

    deployment_path = tmp_path / "reference.deployment.view-ir.json"
    deployment_run = subprocess.run(
        [
            sys.executable,
            str(view_script),
            str(model_path),
            "--profile",
            "deployment_runtime",
            "--scope",
            "transition",
            "--environment",
            "development",
            "--output",
            str(deployment_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert deployment_run.returncode == 0, deployment_run.stderr
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    assert deployment["profile"] == "deployment_runtime"
    assert validate_view_ir(deployment, model) == []

    incompatible = subprocess.run(
        [
            sys.executable,
            str(view_script),
            str(model_path),
            "--profile",
            "module",
            "--environment",
            "development",
            "--output",
            str(tmp_path / "invalid.view-ir.json"),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert incompatible.returncode == 2
    assert "不接受" in incompatible.stderr

    overwrite_refused = subprocess.run(
        [
            sys.executable,
            str(model_script),
            str(source),
            "--output",
            str(model_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert overwrite_refused.returncode == 2
    assert "输出已存在" in overwrite_refused.stderr
