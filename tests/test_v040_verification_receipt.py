from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from apply_patch import apply_operations, compose_updated_data
from import_verification_receipt import (
    MAX_RECEIPT_BYTES,
    PREVIEW_FORMAT,
    VerificationReceiptError,
    build_receipt_preview,
    build_receipt_proposal,
    compute_receipt_hash,
    load_receipt,
    validate_receipt,
)
from panorama_io import compute_data_hash


@pytest.fixture
def v02_data(project_root: Path) -> dict:
    return json.loads(
        (project_root / "examples" / "reference-project.v0.2.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture
def receipt(project_root: Path) -> dict:
    return json.loads(
        (project_root / "examples" / "verification-receipt.v0.1.json").read_text(
            encoding="utf-8"
        )
    )


def rehash(value: dict) -> dict:
    value["producer"]["receiptHash"] = compute_receipt_hash(value)
    return value


def test_receipt_schema_is_valid_and_example_hash_is_exact(
    project_root: Path, receipt: dict
):
    schema = json.loads(
        (project_root / "schema" / "verification-receipt.schema.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    assert receipt["producer"]["receiptHash"] == compute_receipt_hash(receipt)
    assert receipt["isolation"] == {
        "networkIsolation": "not_enforced",
        "filesystemIsolation": "not_enforced",
        "processTreeIsolation": "not_executed",
    }


def test_preview_maps_reference_evidence_and_provenance_without_governance_state(
    project_root: Path, v02_data: dict, receipt: dict
):
    preview = build_receipt_preview(
        v02_data, receipt, project_root=project_root
    )
    assert preview["format"] == PREVIEW_FORMAT
    assert preview["status"] == "ready"
    assert preview["experimental"] is True
    assert preview["mapping"]["gateIds"] == ["GATE-WS-CONC"]
    assert preview["mapping"]["acceptanceCriteriaIds"] == [
        "ACC-WS-CONSISTENCY"
    ]
    assert preview["mapping"]["changesGovernanceStatus"] is False
    assert preview["boundary"] == {
        "executesTests": False,
        "setsGatePassed": False,
        "acceptsAcceptance": False,
        "createsApproval": False,
        "externalFindingsAreFormal": False,
    }
    assert preview["isolation"]["networkIsolation"] == "not_enforced"
    assert preview["isolation"]["processTreeIsolation"] == "not_executed"
    assert preview["redaction"]["truncated"] is True
    assert preview["validation"]["valid"] is True
    assert preview["validation"]["counts"]["errors"] == 0
    assert preview["validation"]["counts"]["warnings"] > 0

    current_gate = next(item for item in v02_data["gates"] if item["id"] == "GATE-WS-CONC")
    current_acceptance = next(
        item
        for item in v02_data["acceptanceCriteria"]
        if item["id"] == "ACC-WS-CONSISTENCY"
    )
    candidate = apply_operations(v02_data, preview["mapping"]["operations"])
    mapped_gate = next(item for item in candidate["gates"] if item["id"] == "GATE-WS-CONC")
    mapped_acceptance = next(
        item
        for item in candidate["acceptanceCriteria"]
        if item["id"] == "ACC-WS-CONSISTENCY"
    )
    assert mapped_gate["status"] == current_gate["status"]
    assert mapped_acceptance["status"] == current_acceptance["status"]
    assert mapped_acceptance["verificationStatus"] == current_acceptance["verificationStatus"]
    assert preview["referenceId"] in mapped_gate["evidenceReferenceIds"]
    assert preview["referenceId"] in mapped_acceptance["evidenceReferenceIds"]
    assert candidate["factProvenance"][-1]["authority"] == "declared"
    assert candidate["observationBatches"][-1]["extensions"]["externalFindingsAreFormal"] is False


def test_proposal_is_normal_pending_update_and_preserves_gate_acceptance_status(
    project_root: Path, v02_data: dict, receipt: dict
):
    preview = build_receipt_preview(v02_data, receipt, project_root=project_root)
    wrapper = build_receipt_proposal(v02_data, preview, project_root=project_root)
    package = wrapper["proposal"]
    assert wrapper["validation"]["valid"] is True
    assert package["approval"]["status"] == "pending"
    receipt_binding = package["reviewDraft"]["extensions"][
        "verificationReceiptBinding"
    ]
    assert receipt_binding == package["updateBatchDraft"]["extensions"][
        "verificationReceiptBinding"
    ]
    assert receipt_binding["receiptHash"] == preview["receiptHash"]
    updated = compose_updated_data(v02_data, package)
    before_gate = next(item for item in v02_data["gates"] if item["id"] == "GATE-WS-CONC")
    after_gate = next(item for item in updated["gates"] if item["id"] == "GATE-WS-CONC")
    before_acceptance = next(
        item for item in v02_data["acceptanceCriteria"] if item["id"] == "ACC-WS-CONSISTENCY"
    )
    after_acceptance = next(
        item for item in updated["acceptanceCriteria"] if item["id"] == "ACC-WS-CONSISTENCY"
    )
    assert after_gate["status"] == before_gate["status"]
    assert after_acceptance["status"] == before_acceptance["status"]
    assert after_acceptance["verificationStatus"] == before_acceptance["verificationStatus"]


def test_exact_duplicate_is_deduplicated_by_receipt_hash(
    project_root: Path, v02_data: dict, receipt: dict
):
    first = build_receipt_preview(v02_data, receipt, project_root=project_root)
    imported = apply_operations(v02_data, first["mapping"]["operations"])
    imported["meta"]["revision"] += 1
    duplicate = build_receipt_preview(imported, receipt, project_root=project_root)
    assert duplicate["status"] == "duplicate"
    assert duplicate["mapping"]["operationCount"] == 0


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda value: value["producer"].update(receiptHash="f" * 64), "Receipt Hash"),
        (lambda value: value["projectBinding"].update(projectId="PRJ-OTHER"), "projectId"),
        (lambda value: value["projectBinding"].update(panoramaRevision=99), "Revision"),
        (
            lambda value: value["evidence"][0].update(locationType="relative_path", location="../secret.log"),
            "逃逸",
        ),
        (
            lambda value: value["extensions"].update(authorization="Bearer abcdefghijklmnopqrstuv"),
            "敏感",
        ),
    ],
)
def test_invalid_hash_binding_escape_and_secret_are_rejected(
    project_root: Path,
    v02_data: dict,
    receipt: dict,
    mutator,
    message: str,
):
    mutator(receipt)
    if message != "Receipt Hash":
        rehash(receipt)
    with pytest.raises(VerificationReceiptError, match=message):
        build_receipt_preview(v02_data, receipt, project_root=project_root)


def test_receipt_does_not_accept_unknown_mapping_or_evidence(
    project_root: Path, v02_data: dict, receipt: dict
):
    receipt["verifications"][0]["gateIds"] = ["GATE-UNKNOWN"]
    rehash(receipt)
    with pytest.raises(VerificationReceiptError, match="未知 Gate"):
        build_receipt_preview(v02_data, receipt, project_root=project_root)

    receipt["verifications"][0]["gateIds"] = ["GATE-WS-CONC"]
    receipt["verifications"][0]["evidenceIds"] = ["EVID-UNKNOWN"]
    rehash(receipt)
    with pytest.raises(VerificationReceiptError, match="未知 Evidence"):
        validate_receipt(receipt, project_root)


def test_receipt_file_over_one_mib_is_rejected(tmp_path: Path):
    receipt_path = tmp_path / "oversized-receipt.json"
    receipt_path.write_bytes(b"{" + (b" " * MAX_RECEIPT_BYTES) + b"}")
    with pytest.raises(VerificationReceiptError, match="超过 1 MiB"):
        load_receipt(receipt_path)


def test_formal_source_binding_mismatch_is_rejected(
    project_root: Path, v02_data: dict, receipt: dict
):
    v02_data["sourceBinding"] = {
        "mode": "git",
        "gitHead": "a" * 40,
        "gitBranch": "release/v0.4",
        "sourceSnapshotHash": "b" * 64,
        "observedAt": "2026-08-14T00:00:00Z",
        "lastObservationBatchId": None,
        "extensions": {},
    }
    receipt["projectBinding"].update(
        panoramaDataHash=compute_data_hash(v02_data),
        gitHead="c" * 40,
        sourceSnapshotHash="b" * 64,
    )
    rehash(receipt)
    with pytest.raises(VerificationReceiptError, match="gitHead"):
        build_receipt_preview(v02_data, receipt, project_root=project_root)


def test_relative_evidence_symlink_is_rejected(
    project_root: Path, tmp_path: Path, v02_data: dict, receipt: dict
):
    evidence_dir = project_root / ".panorama-work" / "receipt-test"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    link = evidence_dir / "outside-evidence.txt"
    target = tmp_path / "outside-evidence.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前环境不能创建符号链接：{exc}")
    try:
        receipt["evidence"][0].update(
            locationType="relative_path",
            location=".panorama-work/receipt-test/outside-evidence.txt",
        )
        rehash(receipt)
        with pytest.raises(VerificationReceiptError, match="符号链接|越出 project root"):
            build_receipt_preview(v02_data, receipt, project_root=project_root)
    finally:
        link.unlink(missing_ok=True)


def test_same_receipt_id_with_new_semantics_creates_a_new_timeline_entry(
    project_root: Path, v02_data: dict, receipt: dict
):
    first = build_receipt_preview(v02_data, receipt, project_root=project_root)
    imported = apply_operations(v02_data, first["mapping"]["operations"])
    imported["meta"]["revision"] += 1

    receipt["verifications"][0]["summary"] = "同一外部运行的补充语义版本。"
    receipt["projectBinding"].update(
        panoramaRevision=imported["meta"]["revision"],
        panoramaDataHash=compute_data_hash(imported),
    )
    rehash(receipt)
    second = build_receipt_preview(imported, receipt, project_root=project_root)

    assert second["status"] == "ready"
    assert second["receiptId"] == first["receiptId"]
    assert second["receiptHash"] != first["receiptHash"]
    assert second["referenceId"] != first["referenceId"]
