from __future__ import annotations

from copy import deepcopy
import json

import pytest

from apply_patch import ApplyPatchError, apply_update_package
from init_panorama import main as init_main
from panorama_io import compute_presentation_hash, extract_data, replace_data
from propose_update import build_proposal
from read_panorama import main as inspect_main
from validate_panorama import validate_data


def _write_data(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _init_args(template, source, output, schema, *extra):
    return [
        "--template",
        str(template),
        "--data",
        str(source),
        "--output",
        str(output),
        "--schema",
        str(schema),
        *extra,
    ]


def test_init_refuses_silent_overwrite_and_preserves_existing(
    tmp_path, template_path, schema_path, reference_data
):
    source = tmp_path / "data.json"
    output = tmp_path / "project.html"
    _write_data(source, reference_data)
    output.write_text("existing bytes", encoding="utf-8")

    assert init_main(_init_args(template_path, source, output, schema_path)) == 1
    assert output.read_text(encoding="utf-8") == "existing bytes"
    assert list(tmp_path.glob("project.backup-*.html")) == []


def test_init_explicit_overwrite_creates_backup_and_atomically_replaces(
    tmp_path, template_path, schema_path, reference_data
):
    source = tmp_path / "data.json"
    output = tmp_path / "project.html"
    _write_data(source, reference_data)
    output.write_text("original bytes", encoding="utf-8")

    assert init_main(
        _init_args(
            template_path,
            source,
            output,
            schema_path,
            "--overwrite-existing",
        )
    ) == 0
    backups = list(tmp_path.glob("project.backup-*.html"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "original bytes"
    assert extract_data(output) == reference_data
    assert list(tmp_path.glob(".project.html.*.staged")) == []


def test_init_invalid_overwrite_keeps_original_and_creates_no_backup(
    tmp_path, template_path, schema_path, reference_data
):
    invalid = deepcopy(reference_data)
    del invalid["project"]["name"]
    source = tmp_path / "invalid.json"
    output = tmp_path / "project.html"
    _write_data(source, invalid)
    output.write_text("original bytes", encoding="utf-8")

    assert init_main(
        _init_args(
            template_path,
            source,
            output,
            schema_path,
            "--overwrite-existing",
        )
    ) == 1
    assert output.read_text(encoding="utf-8") == "original bytes"
    assert list(tmp_path.glob("project.backup-*.html")) == []


def test_init_rejects_output_equal_to_template_even_with_overwrite(
    tmp_path, template_path, schema_path, reference_data
):
    local_template = tmp_path / "template.html"
    original = template_path.read_text(encoding="utf-8")
    local_template.write_text(original, encoding="utf-8")
    source = tmp_path / "data.json"
    _write_data(source, reference_data)

    assert init_main(
        _init_args(
            local_template,
            source,
            local_template,
            schema_path,
            "--overwrite-existing",
        )
    ) == 1
    assert local_template.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("template.backup-*.html")) == []


def test_review_draft_stays_pending_until_approval_is_materialized(
    tmp_path, template_path, schema_path, reference_data
):
    html = tmp_path / "review.html"
    replace_data(template_path, reference_data, html)
    result = build_proposal(
        reference_data,
        {
            "operations": [
                {
                    "op": "replace",
                    "path": "/intent/currentFocus",
                    "value": "审批后落盘",
                }
            ],
            "summary": "验证真实审批审计",
            "changeLevel": "local",
        },
        schema_path,
        base_dir=tmp_path,
        source_path=html,
    )
    package = result["proposal"]
    proposal_hash = package["proposalHash"]
    assert package["reviewDraft"]["status"] == "pending"
    assert package["reviewDraft"]["reviewedBy"] == ""
    assert package["reviewDraft"]["reviewedAt"] is None

    package["approval"] = {
        "status": "approved",
        "approvedBy": "Alice",
        "approvedAt": "2026-08-08T13:00:00Z",
        "proposalHash": proposal_hash,
    }
    _, updated, _ = apply_update_package(html, package, schema_path)
    review = updated["reviews"][-1]
    assert review["status"] == "approved"
    assert review["reviewedBy"] == "Alice"
    assert review["reviewedAt"] == "2026-08-08T13:00:00Z"
    assert package["reviewDraft"]["status"] == "pending"
    assert package["proposalHash"] == proposal_hash


def test_supplied_review_draft_cannot_preapprove_a_proposal(
    tmp_path, template_path, schema_path, reference_data
):
    html = tmp_path / "supplied-review.html"
    replace_data(template_path, reference_data, html)
    existing_review = deepcopy(reference_data["reviews"][0])
    existing_review["id"] = "REV-SUPPLIED-HARDENING"
    existing_review["status"] = "approved"
    existing_review["reviewedBy"] = "Proposal Generator"
    existing_review["reviewedAt"] = "2026-08-08T10:00:00Z"
    package = build_proposal(
        reference_data,
        {
            "operations": [],
            "summary": "禁止预批准",
            "reviewDraft": existing_review,
        },
        schema_path,
        base_dir=tmp_path,
        source_path=html,
    )["proposal"]

    assert package["reviewDraft"]["status"] == "pending"
    assert package["reviewDraft"]["reviewedBy"] == ""
    assert package["reviewDraft"]["reviewedAt"] is None


def test_tampering_review_draft_after_approval_is_rejected(
    tmp_path, template_path, schema_path, reference_data
):
    html = tmp_path / "tamper.html"
    replace_data(template_path, reference_data, html)
    package = build_proposal(
        reference_data,
        {"operations": [], "summary": "不可篡改"},
        schema_path,
        base_dir=tmp_path,
        source_path=html,
    )["proposal"]
    package["approval"] = {
        "status": "approved",
        "approvedBy": "Alice",
        "approvedAt": "2026-08-08T13:00:00Z",
        "proposalHash": package["proposalHash"],
    }
    package["reviewDraft"]["summary"] = "tampered"

    with pytest.raises(ApplyPatchError, match="Hash"):
        apply_update_package(html, package, schema_path)


def _proposal(reference_data, schema_path, tmp_path, operation):
    return build_proposal(
        reference_data,
        {"operations": [operation], "summary": "受影响实体检测"},
        schema_path,
        base_dir=tmp_path,
    )["proposal"]


def test_affected_entities_detect_new_module_and_connection(
    reference_data, schema_path, tmp_path
):
    module = deepcopy(reference_data["architecture"]["modules"][0])
    module["id"] = "MOD-NEW-HARDENING"
    module["name"] = "Hardening Module"
    module_package = _proposal(
        reference_data,
        schema_path,
        tmp_path,
        {"op": "add", "path": "/architecture/modules/-", "value": module},
    )
    expected_module = {"type": "module", "id": module["id"]}
    assert expected_module in module_package["changeRecords"][0]["entityRefs"]
    assert expected_module in module_package["reviewDraft"]["subjectRefs"]

    connection = deepcopy(reference_data["architecture"]["connections"][0])
    connection["id"] = "CONN-NEW-HARDENING"
    connection["name"] = "Hardening Connection"
    connection_package = _proposal(
        reference_data,
        schema_path,
        tmp_path,
        {
            "op": "add",
            "path": "/architecture/connections/-",
            "value": connection,
        },
    )
    expected_connection = {"type": "connection", "id": connection["id"]}
    assert expected_connection in connection_package["changeRecords"][0]["entityRefs"]


@pytest.mark.parametrize("op", ["remove", "replace"])
def test_affected_entities_detect_removed_and_modified_module(
    op, reference_data, schema_path, tmp_path
):
    module = reference_data["architecture"]["modules"][0]
    operation = {"op": op, "path": "/architecture/modules/0"}
    if op == "replace":
        replacement = deepcopy(module)
        replacement["name"] = "Modified module"
        operation["value"] = replacement
    package = _proposal(reference_data, schema_path, tmp_path, operation)
    assert {"type": "module", "id": module["id"]} in package["reviewDraft"]["subjectRefs"]


def test_affected_entities_ignore_id_substrings_in_free_text(
    reference_data, schema_path, tmp_path
):
    module_id = reference_data["architecture"]["modules"][0]["id"]
    package = _proposal(
        reference_data,
        schema_path,
        tmp_path,
        {
            "op": "replace",
            "path": "/intent/currentFocus",
            "value": f"这里仅在说明文字中提到 {module_id}",
        },
    )
    assert package["reviewDraft"]["subjectRefs"] == [
        {"type": "project", "id": reference_data["project"]["id"]}
    ]


def test_findings_and_attention_carry_related_entities(
    reference_data, schema_path, tmp_path
):
    candidate = deepcopy(reference_data)
    requirement = next(
        item
        for item in candidate["requirements"]
        if item["definitionStatus"] == "confirmed" and item["moduleIds"]
    )
    requirement["moduleIds"] = []
    report = validate_data(candidate, schema_path, base_dir=tmp_path)
    finding = next(item for item in report.issues if item.code == "ARCHITECTURE_GAP")
    expected = {"type": "requirement", "id": requirement["id"]}
    assert expected in finding.related_entities
    assert expected in finding.to_dict()["relatedEntities"]

    package = build_proposal(
        reference_data,
        candidate,
        schema_path,
        base_dir=tmp_path,
    )["proposal"]
    attention = next(
        item
        for item in package["updateBatchDraft"]["attentionItems"]
        if item["type"] == "architecture_gap"
    )
    assert expected in attention["relatedEntities"]


def test_core_rule_finding_entities_cover_module_deployment_and_resource(
    reference_data, schema_path, tmp_path
):
    verification_data = deepcopy(reference_data)
    module = verification_data["architecture"]["modules"][0]
    module["acceptanceCriteriaIds"] = []
    module["gateIds"] = []
    verification = validate_data(verification_data, schema_path, base_dir=tmp_path)
    verification_finding = next(
        item
        for item in verification.issues
        if item.code == "VERIFICATION_GAP"
        and {"type": "module", "id": module["id"]} in item.related_entities
    )
    assert verification_finding.severity == "high"

    deployment_data = deepcopy(reference_data)
    deployment = next(
        item for item in deployment_data["deployments"] if item["status"] == "active"
    )
    release = next(
        item for item in deployment_data["releases"] if item["id"] == deployment["releaseId"]
    )
    deployment["architectureVersionId"] = next(
        item["id"]
        for item in deployment_data["architecture"]["versions"]
        if item["id"] != release["architectureVersionId"]
    )
    deployment_report = validate_data(deployment_data, schema_path, base_dir=tmp_path)
    deployment_ref = {"type": "deployment", "id": deployment["id"]}
    assert any(
        item.code == "DEPLOYMENT_DRIFT"
        and deployment_ref in item.related_entities
        for item in deployment_report.issues
    )

    resource_report = validate_data(reference_data, schema_path, base_dir=tmp_path)
    resource_finding = next(
        item for item in resource_report.issues if item.code == "RESOURCE_RISK"
    )
    resource_ids = {item["id"] for item in reference_data["resources"]}
    assert any(
        item.get("type") == "resource" and item.get("id") in resource_ids
        for item in resource_finding.related_entities
    )


def test_complete_hardening_lifecycle_with_new_module_and_finding(
    tmp_path, template_path, schema_path, reference_data
):
    source = tmp_path / "reference.json"
    html = tmp_path / "lifecycle.html"
    _write_data(source, reference_data)

    # INIT -> INSPECT
    assert init_main(_init_args(template_path, source, html, schema_path)) == 0
    assert inspect_main([str(html)]) == 0
    presentation_hash = compute_presentation_hash(html.read_text(encoding="utf-8"))

    module = deepcopy(reference_data["architecture"]["modules"][0])
    module.update({"id": "MOD-E2E-HARDENING", "name": "E2E Hardening Module"})
    module["architectureScope"] = "target"
    module["reviewIds"] = []
    module["acceptanceCriteriaIds"] = []
    module["gateIds"] = []
    module["workItemIds"] = []
    candidate = {
        "operations": [
            {"op": "add", "path": "/architecture/modules/-", "value": module}
        ],
        "summary": "Add E2E hardening module",
        "changeLevel": "module",
    }

    # PROPOSE -> approval pending -> simulated user approval -> APPLY
    result = build_proposal(
        extract_data(html),
        candidate,
        schema_path,
        base_dir=tmp_path,
        source_path=html,
    )
    package = result["proposal"]
    new_ref = {"type": "module", "id": module["id"]}
    assert package["approval"]["status"] == "pending"
    assert new_ref in result["facts"]["affectedEntities"]
    assert any(
        new_ref in item["relatedEntities"]
        for item in package["updateBatchDraft"]["attentionItems"]
    )
    package["approval"] = {
        "status": "approved",
        "approvedBy": "E2E Reviewer",
        "approvedAt": "2026-08-08T14:00:00Z",
        "proposalHash": package["proposalHash"],
    }
    _, updated, _ = apply_update_package(html, package, schema_path)

    # VALIDATE -> INSPECT
    final_report = validate_data(updated, schema_path, base_dir=tmp_path, source_path=html)
    assert final_report.errors == []
    assert any(
        item.code in {"REVIEW_GAP", "VERIFICATION_GAP"}
        and new_ref in item.related_entities
        for item in final_report.issues
    )
    assert inspect_main([str(html)]) == 0
    assert compute_presentation_hash(html.read_text(encoding="utf-8")) == presentation_hash
    assert updated["reviews"][-1]["reviewedBy"] == "E2E Reviewer"
