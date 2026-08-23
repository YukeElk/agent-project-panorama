from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import pytest

from materialize_module_logic_observation import (
    ModuleLogicMaterializationError,
    materialize_candidate,
)
from prepare_module_logic_analysis import (
    ModuleLogicAnalysisError,
    compute_manifest_semantic_hash,
    prepare_manifest,
    validate_manifest,
)
from reconcile_module_logic_observation import reconcile
from source_topology import extract_source_topology


T0 = "2026-08-23T08:00:00Z"
T1 = "2026-08-23T08:05:00Z"


def _project(root: Path) -> None:
    source = root / "src" / "orchestrator"
    source.mkdir(parents=True)
    (source / "router.py").write_text(
        "def normalize_request(value):\n"
        "    return {'task': value}\n\n"
        "def dispatch_task(task, agent):\n"
        "    return agent.run(task)\n",
        encoding="utf-8",
    )
    (source / "policy.py").write_text(
        "def allowed(task):\n    return bool(task.get('task'))\n",
        encoding="utf-8",
    )


def _source_observation(root: Path, project_id: str, observed_at: str = T0) -> dict:
    return extract_source_topology(
        root, project_id=project_id, observed_at=observed_at
    )["observation"]


def _candidate(manifest: dict) -> dict:
    first = manifest["files"][0]
    evidence_id = "EVIDENCE-MODULE-LOGIC-PIPELINE"
    return {
        "filesReadPaths": [item["path"] for item in manifest["files"]],
        "evidencePins": [
            {
                "evidenceId": evidence_id,
                "kind": "source_location",
                "ref": first["path"],
                "lineStart": 1,
                "lineEnd": 5,
                "digest": first["sha256"],
                "accessClass": "PROJECT_OPERATIONAL_METADATA",
                "freshness": "current",
            }
        ],
        "nodes": [
            {
                "id": "LOGIC-PIPELINE-NORMALIZE",
                "name": "normalize_request",
                "displayName": "规范化请求",
                "type": "preprocess",
                "purpose": "把原始请求转换为统一任务结构。",
                "rationale": "稳定下游 Agent 的输入契约。",
                "implementationSummary": "将输入包装为带 task 字段的结构。",
                "loopExitCondition": None,
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "high",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            },
            {
                "id": "LOGIC-PIPELINE-DISPATCH",
                "name": "dispatch_task",
                "displayName": "分发任务",
                "type": "agent_call",
                "purpose": "把规范化任务交给业务 Agent。",
                "rationale": "分离编排与业务执行。",
                "implementationSummary": "调用注入 Agent 的 run 入口。",
                "loopExitCondition": None,
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "high",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            },
        ],
        "edges": [
            {
                "id": "LOGIC-PIPELINE-EDGE-01",
                "fromNodeId": "LOGIC-PIPELINE-NORMALIZE",
                "toNodeId": "LOGIC-PIPELINE-DISPATCH",
                "kind": "flow",
                "condition": "",
                "dataSummary": "规范化任务",
                "order": 0,
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "high",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            }
        ],
        "boundaryPorts": [
            {
                "id": "LOGIC-PIPELINE-PORT-IN",
                "name": "console_request",
                "direction": "input",
                "internalNodeId": "LOGIC-PIPELINE-NORMALIZE",
                "externalReferenceId": "LOGIC-PIPELINE-EXT-CONSOLE",
                "bindingKind": "connection",
                "bindingId": "CONN-CONSOLE-ORCH",
                "dataSummary": "用户请求",
                "protocol": "HTTPS",
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "high",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            }
        ],
        "externalReferences": [
            {
                "id": "LOGIC-PIPELINE-EXT-CONSOLE",
                "entityRef": {"type": "module", "id": "MOD-CONSOLE"},
                "label": "项目控制台",
                "purpose": "提供外部请求。",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            }
        ],
        "unresolved": [],
        "transformationLoss": [],
        "informationGaps": [],
        "extensions": {},
    }


@pytest.fixture
def pipeline_inputs(tmp_path: Path, reference_data: dict):
    _project(tmp_path)
    source_observation = _source_observation(
        tmp_path, reference_data["project"]["id"]
    )
    manifest = prepare_manifest(
        reference_data,
        source_observation,
        module_id="MOD-ORCHESTRATOR",
        prepared_at=T0,
    )
    return tmp_path, reference_data, source_observation, manifest


def test_v080_analysis_manifest_schema_is_meta_valid(project_root):
    schema = json.loads(
        (
            project_root
            / "schema"
            / "module-logic-analysis-manifest.schema.v0.1.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator.check_schema(schema)


def test_v080_prepare_manifest_is_bounded_body_free_and_hash_bound(pipeline_inputs):
    _, panorama, source_observation, manifest = pipeline_inputs
    assert validate_manifest(manifest, panorama, source_observation) == []
    assert manifest["scope"] == {
        "status": "complete",
        "filesMatched": 2,
        "filesIncluded": 2,
        "bytesIncluded": sum(item["size"] for item in manifest["files"]),
        "maxFiles": 256,
        "maxBytes": 16 * 1024 * 1024,
        "truncated": False,
    }
    rendered = json.dumps(manifest, ensure_ascii=False)
    assert "def normalize_request" not in rendered
    assert manifest["privacyBoundary"]["sourceBodiesPersisted"] is False

    tampered = deepcopy(manifest)
    tampered["files"][0]["sha256"] = "0" * 64
    assert compute_manifest_semantic_hash(tampered) != manifest["integrity"][
        "semanticHash"
    ]


def test_v080_prepare_manifest_rejects_unknown_module(reference_data, tmp_path):
    _project(tmp_path)
    source_observation = _source_observation(
        tmp_path, reference_data["project"]["id"]
    )
    with pytest.raises(ModuleLogicAnalysisError, match="Module"):
        prepare_manifest(
            reference_data,
            source_observation,
            module_id="MOD-UNKNOWN",
            prepared_at=T0,
        )


def test_v080_materialize_candidate_binds_manifest_panorama_and_source(pipeline_inputs):
    _, panorama, _, manifest = pipeline_inputs
    observation = materialize_candidate(
        panorama, manifest, _candidate(manifest), generated_at=T1
    )
    assert observation["coverage"]["status"] == "complete"
    assert observation["sourceBinding"]["currentness"] == "current"
    assert observation["extensions"]["analysisManifestBinding"] == {
        "manifestId": manifest["manifestId"],
        "semanticHash": manifest["integrity"]["semanticHash"],
    }


def test_v080_materialize_candidate_rejects_out_of_scope_evidence(pipeline_inputs):
    _, panorama, _, manifest = pipeline_inputs
    candidate = _candidate(manifest)
    candidate["evidencePins"][0]["ref"] = "src/outside.py"
    with pytest.raises(ModuleLogicMaterializationError, match="Manifest"):
        materialize_candidate(panorama, manifest, candidate, generated_at=T1)


def test_v080_materialize_candidate_rejects_persisted_prompt(pipeline_inputs):
    _, panorama, _, manifest = pipeline_inputs
    candidate = _candidate(manifest)
    candidate["extensions"]["prompt"] = "hidden source request"
    with pytest.raises(ModuleLogicMaterializationError, match="prompt"):
        materialize_candidate(panorama, manifest, candidate, generated_at=T1)


def test_v080_partial_manifest_never_materializes_current_complete(
    tmp_path: Path, reference_data: dict
):
    _project(tmp_path)
    source_observation = _source_observation(
        tmp_path, reference_data["project"]["id"]
    )
    manifest = prepare_manifest(
        reference_data,
        source_observation,
        module_id="MOD-ORCHESTRATOR",
        prepared_at=T0,
        max_files=1,
    )
    observation = materialize_candidate(
        reference_data, manifest, _candidate(manifest), generated_at=T1
    )
    assert manifest["scope"]["status"] == "partial"
    assert observation["coverage"]["status"] == "partial"
    assert observation["sourceBinding"]["currentness"] == "recorded_as_of"
    assert "module_scope_truncated_by_analysis_limits" in observation[
        "informationGaps"
    ]


def test_v080_reconcile_reports_match_then_stale_on_source_change(pipeline_inputs):
    root, panorama, source_observation, manifest = pipeline_inputs
    observation = materialize_candidate(
        panorama, manifest, _candidate(manifest), generated_at=T1
    )
    matched = reconcile(
        panorama,
        manifest,
        observation,
        source_observation,
        project_root=root,
        checked_at=T1,
    )
    assert matched["status"] == "match"
    assert matched["checks"]["sourceBodiesPersisted"] is False

    (root / "src" / "orchestrator" / "router.py").write_text(
        "def normalize_request(value):\n    return {'task': value, 'changed': True}\n",
        encoding="utf-8",
    )
    stale = reconcile(
        panorama,
        manifest,
        observation,
        source_observation,
        project_root=root,
        checked_at=T1,
    )
    assert stale["status"] == "stale"
    assert any(reason.startswith("source_file_changed:") for reason in stale["reasons"])


def test_v080_module_logic_pipeline_cli_end_to_end(
    project_root: Path, tmp_path: Path, reference_data: dict
):
    source_root = tmp_path / "project"
    _project(source_root)
    source_observation = _source_observation(
        source_root, reference_data["project"]["id"]
    )
    panorama_path = tmp_path / "panorama.json"
    source_path = tmp_path / "source-observation.json"
    manifest_path = tmp_path / "analysis-manifest.json"
    candidate_path = tmp_path / "candidate.json"
    observation_path = tmp_path / "module-logic-observation.json"
    reconciliation_path = tmp_path / "reconciliation.json"
    panorama_path.write_text(
        json.dumps(reference_data, ensure_ascii=False), encoding="utf-8"
    )
    source_path.write_text(
        json.dumps(source_observation, ensure_ascii=False), encoding="utf-8"
    )

    prepare = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "prepare_module_logic_analysis.py"),
            str(panorama_path),
            str(source_path),
            "--module-id",
            "MOD-ORCHESTRATOR",
            "--prepared-at",
            T0,
            "--output",
            str(manifest_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert prepare.returncode == 0, prepare.stderr
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate_path.write_text(
        json.dumps(_candidate(manifest), ensure_ascii=False), encoding="utf-8"
    )

    materialize = subprocess.run(
        [
            sys.executable,
            str(
                project_root
                / "scripts"
                / "materialize_module_logic_observation.py"
            ),
            str(panorama_path),
            str(manifest_path),
            str(candidate_path),
            "--generated-at",
            T1,
            "--output",
            str(observation_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert materialize.returncode == 0, materialize.stderr

    reconcile_result = subprocess.run(
        [
            sys.executable,
            str(
                project_root
                / "scripts"
                / "reconcile_module_logic_observation.py"
            ),
            str(panorama_path),
            str(manifest_path),
            str(observation_path),
            str(source_path),
            "--project-root",
            str(source_root),
            "--checked-at",
            T1,
            "--output",
            str(reconciliation_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert reconcile_result.returncode == 0, reconcile_result.stderr
    assert json.loads(reconciliation_path.read_text(encoding="utf-8"))[
        "status"
    ] == "match"
