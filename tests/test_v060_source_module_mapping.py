from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from source_module_mapping import (
    SCHEMA,
    compile_mapping_proposal,
    compute_mapping_proposal_hash,
    validate_mapping_proposal,
)
from source_topology import extract_source_topology


OBSERVED_AT = "2026-08-21T18:00:00Z"


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _observation(tmp_path: Path) -> dict:
    root = tmp_path / "mapping-project"
    _write(root / "src/main/java/com/acme/orders/OrderController.java", "package com.acme.orders;\n@Controller\nclass OrderController {}\n")
    _write(root / "src/main/java/com/acme/orders/OrderRepository.java", "package com.acme.orders;\ninterface OrderRepository {}\n")
    _write(root / "src/main/java/com/acme/App.java", "package com.acme;\nclass App {}\n")
    _write(root / "src/test/java/com/acme/orders/OrderTests.java", "package com.acme.orders;\nclass OrderTests {}\n")
    _write(root / "src/main/resources/application.properties", "database=h2\n")
    return extract_source_topology(root, project_id="PRJ-MAP", observed_at=OBSERVED_AT)["observation"]


def test_v060_mapping_schema_is_meta_valid():
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(json.loads(SCHEMA.read_text(encoding="utf-8")))


def test_v060_mapping_is_pending_evidence_bound_and_non_promoting(tmp_path):
    observation = _observation(tmp_path)
    proposal = compile_mapping_proposal(observation, generated_at=OBSERVED_AT)

    assert validate_mapping_proposal(proposal, observation) == []
    assert proposal["status"] == "pending_review"
    assert proposal["promotionPolicy"] == "exact_approved_mapping_proposal_required"
    assert all(item["reviewStatus"] == "pending" for item in proposal["candidates"])
    assert all(item["suggestedModuleId"] is None for item in proposal["candidates"])
    assert {item["groupingKey"] for item in proposal["candidates"]} == {
        "com.acme",
        "com.acme.orders",
    }
    mapped = {
        element_id
        for candidate in proposal["candidates"]
        for element_id in candidate["sourceElementIds"]
    }
    test_and_config = {
        item["id"]
        for item in observation["elements"]
        if item.get("path") and (item["path"].startswith("src/test/") or item["kind"] == "configuration")
    }
    assert test_and_config <= set(proposal["unmappedSourceElementIds"])
    assert not (test_and_config & mapped)


def test_v060_mapping_tamper_and_duplicate_fail_closed(tmp_path):
    observation = _observation(tmp_path)
    proposal = compile_mapping_proposal(observation, generated_at=OBSERVED_AT)
    tampered = deepcopy(proposal)
    tampered["candidates"][0]["name"] = "Approved Module"
    assert any("mappingProposalHash" in item for item in validate_mapping_proposal(tampered, observation))

    duplicate = deepcopy(proposal)
    duplicate["candidates"][1]["sourceElementIds"].append(
        duplicate["candidates"][0]["sourceElementIds"][0]
    )
    duplicate["candidates"][1]["sourceElementIds"].sort()
    duplicate["integrity"]["mappingProposalHash"] = compute_mapping_proposal_hash(duplicate)
    assert any("多个候选" in item for item in validate_mapping_proposal(duplicate, observation))


def test_v060_mapping_cli_writes_new_artifact(project_root, tmp_path):
    observation = _observation(tmp_path)
    observation_path = tmp_path / "source-observation.json"
    output = tmp_path / "mapping-proposal.json"
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "compile_source_module_mapping.py"),
            str(observation_path),
            "--generated-at",
            OBSERVED_AT,
            "--output",
            str(output),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "pending_review"
    rerun = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "compile_source_module_mapping.py"), str(observation_path), "--generated-at", OBSERVED_AT, "--output", str(output)],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert rerun.returncode == 2
