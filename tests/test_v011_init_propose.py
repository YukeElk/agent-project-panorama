from __future__ import annotations

from copy import deepcopy
import json

from apply_patch import apply_update_package
from init_panorama import main as init_main
from new_project_data import build_minimal_project
from panorama_io import extract_data, replace_data
from propose_update import build_proposal
from validate_panorama import validate_data


def test_minimal_project_skeleton_is_schema_valid(schema_path, tmp_path):
    data = build_minimal_project(
        "PRJ-MIN", "Minimal Project", "Understand the project", "Clarify requirements", ["First requirement"]
    )
    report = validate_data(data, schema_path, base_dir=tmp_path)
    assert report.errors == []
    assert data["architecture"]["modules"] == []
    assert len(data["guidance"]["options"]) == 2


def test_init_rejects_invalid_input_without_output(
    tmp_path, template_path, schema_path, reference_data
):
    invalid = deepcopy(reference_data)
    del invalid["project"]["name"]
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "invalid.html"
    assert init_main([
        "--template", str(template_path), "--data", str(source), "--output", str(output), "--schema", str(schema_path)
    ]) == 1
    assert not output.exists()


def test_init_validates_generated_html(
    tmp_path, template_path, schema_path, reference_data
):
    source = tmp_path / "valid.json"
    source.write_text(json.dumps(reference_data, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "valid.html"
    assert init_main([
        "--template", str(template_path), "--data", str(source), "--output", str(output), "--schema", str(schema_path)
    ]) == 0
    assert extract_data(output) == reference_data


def test_proposal_contains_hash_facts_findings_and_attention(
    tmp_path, template_path, schema_path, reference_data
):
    html = tmp_path / "proposal.html"
    replace_data(template_path, reference_data, html)
    requirement_index = next(
        index for index, item in enumerate(reference_data["requirements"])
        if item["definitionStatus"] == "confirmed" and item["moduleIds"]
    )
    candidate = {
        "operations": [{"op": "replace", "path": f"/requirements/{requirement_index}/moduleIds", "value": []}],
        "summary": "Remove stale requirement coverage", "changeLevel": "module",
    }
    result = build_proposal(reference_data, candidate, schema_path, base_dir=tmp_path, source_path=html)
    proposal = result["proposal"]
    assert proposal["proposalHash"] == proposal["approval"]["proposalHash"]
    assert proposal["approval"]["status"] == "pending"
    assert proposal["reviewDraft"]["status"] == "pending"
    assert proposal["reviewDraft"]["reviewedBy"] == ""
    assert proposal["reviewDraft"]["reviewedAt"] is None
    assert result["facts"]["operationCount"] == 1
    assert any(item["code"] == "ARCHITECTURE_GAP" for item in result["validation"]["findings"])
    assert any(item["type"] == "architecture_gap" for item in proposal["updateBatchDraft"]["attentionItems"])


def test_exact_proposal_can_be_user_approved_and_applied(
    tmp_path, template_path, schema_path, reference_data
):
    html = tmp_path / "proposal-apply.html"
    replace_data(template_path, reference_data, html)
    candidate = {
        "operations": [{"op": "replace", "path": "/intent/currentFocus", "value": "Proposal lifecycle"}],
        "summary": "Update current focus", "changeLevel": "local",
    }
    result = build_proposal(reference_data, candidate, schema_path, base_dir=tmp_path, source_path=html)
    package = result["proposal"]
    package["approval"] = {
        "status": "approved", "approvedBy": "user", "approvedAt": "2026-08-08T12:00:00Z",
        "proposalHash": package["proposalHash"],
    }
    _, updated, _ = apply_update_package(html, package, schema_path)
    assert updated["intent"]["currentFocus"] == "Proposal lifecycle"
    assert updated["reviews"][-1]["status"] == "approved"
    assert updated["reviews"][-1]["reviewedBy"] == "user"
    assert updated["reviews"][-1]["reviewedAt"] == "2026-08-08T12:00:00Z"
    assert updated["updateBatches"][-1]["attentionItems"] == package["updateBatchDraft"]["attentionItems"]
