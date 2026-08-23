from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from module_logic_observation import materialize_observation
from panorama_view_ir import (
    PanoramaViewIRError,
    compile_model_ir,
    compile_module_logic_view_ir,
    compile_module_view_ir,
    compute_view_semantic_hash,
    validate_model_ir,
    validate_view_ir,
)
from panorama_view_set import build_view_set
from test_v080_module_logic_contract import (
    _observation_candidate,
    _with_target_design,
)


def test_v080_formal_target_logic_compiles_into_shared_model(reference_data):
    panorama = _with_target_design(reference_data)
    model = compile_model_ir(panorama)

    assert validate_model_ir(model) == []
    assert model["moduleLogicBindings"] == [
        {
            "source": "formal_design",
            "moduleId": "MOD-ORCHESTRATOR",
            "architectureScope": "target",
            "designId": "MLD-ORCHESTRATOR-TARGET",
            "designDigest": model["moduleLogicBindings"][0]["designDigest"],
        }
    ]
    logic_entities = [
        item
        for item in model["entities"]
        if item["kind"] in {"module_logic_node", "boundary_port"}
    ]
    assert {item["id"] for item in logic_entities} == {
        "LOGIC-TARGET-INPUT",
        "LOGIC-TARGET-OUTPUT",
        "LOGIC-TARGET-PORT-IN",
    }
    assert all(
        item["attributes"]["rootModuleId"] == "MOD-ORCHESTRATOR"
        for item in logic_entities
    )
    assert {
        item["kind"]
        for item in model["relations"]
        if item.get("attributes", {}).get("designId")
        == "MLD-ORCHESTRATOR-TARGET"
    } == {"logic_flow", "port_binding"}


def test_v080_target_child_view_binds_parent_and_keeps_external_compact(
    reference_data,
):
    model = compile_model_ir(_with_target_design(reference_data))
    parent = compile_module_view_ir(model, architecture_scopes=["target"])
    child = compile_module_logic_view_ir(
        model,
        parent_view=parent,
        root_module_id="MOD-ORCHESTRATOR",
        architecture_scopes=["target"],
    )

    assert validate_view_ir(child, model, parent_view=parent) == []
    assert child["profile"] == "module_logic"
    assert child["rootModuleId"] == "MOD-ORCHESTRATOR"
    assert child["parentViewBinding"] == {
        "viewId": parent["viewId"],
        "semanticHash": parent["integrity"]["semanticHash"],
    }
    assert child["moduleLogicBinding"]["source"] == "formal_design"
    assert child["extensions"]["navigation"] == {
        "enterGesture": "double_click_module",
        "backTargetViewId": parent["viewId"],
    }
    assert child["extensions"]["externalEntityIds"] == ["MOD-CONSOLE"]
    projected = {item["entityRef"]["id"] for item in child["nodes"]}
    assert projected == {
        "LOGIC-TARGET-INPUT",
        "LOGIC-TARGET-OUTPUT",
        "LOGIC-TARGET-PORT-IN",
        "MOD-CONSOLE",
    }
    with pytest.raises(PanoramaViewIRError, match="暂不支持 profile"):
        build_view_set(model, [parent, child])


def test_v080_current_child_view_uses_only_current_complete_observation(
    reference_data,
):
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

    assert validate_model_ir(model) == []
    assert validate_view_ir(child, model, parent_view=parent) == []
    assert child["moduleLogicBinding"] == {
        "source": "current_observation",
        "moduleId": "MOD-ORCHESTRATOR",
        "observationId": observation["observationId"],
        "semanticHash": observation["integrity"]["semanticHash"],
        "coverage": "complete",
        "sourceCoverage": "complete",
        "currentness": "current",
    }
    assert child["extensions"]["externalEntityIds"] == ["MOD-CONSOLE"]
    internal = [
        item
        for item in model["entities"]
        if item.get("attributes", {}).get("observationId")
        == observation["observationId"]
    ]
    assert {item["kind"] for item in internal} == {
        "module_logic_node",
        "boundary_port",
    }
    assert all(item["authority"] == "inferred" for item in internal)


def test_v080_partial_recorded_as_of_observation_is_visible_with_explicit_gaps(
    reference_data,
):
    candidate = _observation_candidate(reference_data)
    candidate["sourceBinding"]["coverage"] = "partial"
    candidate["sourceBinding"]["currentness"] = "recorded_as_of"
    candidate["coverage"]["status"] = "partial"
    candidate["informationGaps"] = ["module_scope_partial"]
    observation = materialize_observation(candidate)

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

    assert child["moduleLogicBinding"]["coverage"] == "partial"
    assert child["moduleLogicBinding"]["currentness"] == "recorded_as_of"
    assert "module_scope_partial" in child["informationGaps"]
    assert (
        "module_logic_coverage_partial:MOD-ORCHESTRATOR"
        in child["informationGaps"]
    )
    assert "局部、按时点记录" in child["description"]


def test_v080_stale_or_unknown_observation_fails_closed(reference_data):
    for currentness in ("stale", "unknown"):
        candidate = _observation_candidate(reference_data)
        candidate["sourceBinding"]["coverage"] = "partial"
        candidate["sourceBinding"]["currentness"] = currentness
        candidate["coverage"]["status"] = "partial"
        candidate["informationGaps"] = ["module_scope_not_current"]
        observation = materialize_observation(candidate)

        with pytest.raises(PanoramaViewIRError, match="current/recorded_as_of"):
            compile_model_ir(
                reference_data,
                module_logic_observations=[observation],
            )


def test_v080_child_view_rejects_wrong_parent_scope_and_tamper(reference_data):
    model = compile_model_ir(_with_target_design(reference_data))
    current_parent = compile_module_view_ir(model, architecture_scopes=["current"])
    with pytest.raises(PanoramaViewIRError, match="Scope"):
        compile_module_logic_view_ir(
            model,
            parent_view=current_parent,
            root_module_id="MOD-ORCHESTRATOR",
            architecture_scopes=["target"],
        )

    target_parent = compile_module_view_ir(model, architecture_scopes=["target"])
    child = compile_module_logic_view_ir(
        model,
        parent_view=target_parent,
        root_module_id="MOD-ORCHESTRATOR",
        architecture_scopes=["target"],
    )
    tampered = deepcopy(child)
    tampered["parentViewBinding"]["semanticHash"] = "0" * 64
    tampered["integrity"]["semanticHash"] = compute_view_semantic_hash(tampered)
    assert any(
        "parentViewBinding" in item
        for item in validate_view_ir(tampered, model, parent_view=target_parent)
    )


def test_v080_display_names_drive_canvas_labels_without_losing_technical_names(
    reference_data,
):
    panorama = deepcopy(reference_data)
    panorama["architecture"]["layers"][0]["displayName"] = "交互层"
    module = next(
        item
        for item in panorama["architecture"]["modules"]
        if item["id"] == "MOD-CONSOLE"
    )
    module["displayName"] = "项目控制台"
    model = compile_model_ir(panorama)
    view = compile_module_view_ir(model)

    model_module = next(item for item in model["entities"] if item["id"] == "MOD-CONSOLE")
    view_node = next(item for item in view["nodes"] if item["entityRef"]["id"] == "MOD-CONSOLE")
    group = next(item for item in view["groups"] if item["layerId"] == module["layerId"])
    assert model_module["name"] == module["name"]
    assert model_module["technicalLabel"] == module["name"]
    assert model_module["displayLabel"] == "项目控制台"
    assert view_node["label"] == "项目控制台"
    assert group["label"] == "交互层"


def test_v080_module_logic_cli_builds_parent_child_chain(
    project_root: Path,
    reference_data,
    tmp_path: Path,
):
    observation = materialize_observation(_observation_candidate(reference_data))
    panorama_path = tmp_path / "panorama.json"
    observation_path = tmp_path / "module-logic.json"
    model_path = tmp_path / "model.json"
    parent_path = tmp_path / "parent.json"
    child_path = tmp_path / "child.json"
    panorama_path.write_text(
        json.dumps(reference_data, ensure_ascii=False), encoding="utf-8"
    )
    observation_path.write_text(
        json.dumps(observation, ensure_ascii=False), encoding="utf-8"
    )
    commands = [
        [
            sys.executable,
            str(project_root / "scripts" / "compile_panorama_model_ir.py"),
            str(panorama_path),
            "--module-logic-observation",
            str(observation_path),
            "--output",
            str(model_path),
        ],
        [
            sys.executable,
            str(project_root / "scripts" / "compile_panorama_view_ir.py"),
            str(model_path),
            "--profile",
            "module",
            "--scope",
            "current",
            "--output",
            str(parent_path),
        ],
        [
            sys.executable,
            str(project_root / "scripts" / "compile_panorama_view_ir.py"),
            str(model_path),
            "--profile",
            "module_logic",
            "--scope",
            "current",
            "--root-module",
            "MOD-ORCHESTRATOR",
            "--parent-view",
            str(parent_path),
            "--output",
            str(child_path),
        ],
    ]
    for command in commands:
        result = subprocess.run(
            command,
            cwd=project_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    child = json.loads(child_path.read_text(encoding="utf-8"))
    assert child["profile"] == "module_logic"
    assert child["extensions"]["navigation"]["enterGesture"] == "double_click_module"
