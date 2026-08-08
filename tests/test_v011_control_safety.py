from __future__ import annotations

from copy import deepcopy
import json

import pytest

from apply_patch import (
    ApplyPatchError,
    apply_update_package,
    compute_proposal_hash,
)
from panorama_io import compute_data_hash, replace_data
from read_panorama import main as read_main, redact_embedded_secrets
from validate_panorama import main as validate_main, validate_data


def signed_package(data, **overrides):
    package = {
        "baseRevision": data["meta"]["revision"],
        "baseDataHash": compute_data_hash(data),
        "operations": [],
        "changeRecords": [],
        "reviewDraft": {},
        "updateBatchDraft": {},
        "guidanceDraft": {},
    }
    package.update(overrides)
    package["proposalHash"] = compute_proposal_hash(package)
    package["approval"] = {
        "status": "approved",
        "approvedBy": "user",
        "approvedAt": "2026-08-08T12:00:00Z",
        "proposalHash": package["proposalHash"],
    }
    return package


def make_html(tmp_path, template_path, reference_data):
    target = tmp_path / "project-panorama.html"
    replace_data(template_path, reference_data, target)
    return target


@pytest.mark.parametrize("approval", [None, {"status": "pending"}])
def test_unapproved_package_is_rejected(
    approval, tmp_path, template_path, reference_data, schema_path
):
    html = make_html(tmp_path, template_path, reference_data)
    package = signed_package(reference_data)
    if approval is None:
        package.pop("approval")
    else:
        package["approval"] = approval
    with pytest.raises(ApplyPatchError, match="approval"):
        apply_update_package(html, package, schema_path)


@pytest.mark.parametrize("field", ["operations", "guidanceDraft"])
def test_approved_package_mutation_invalidates_hash(
    field, tmp_path, template_path, reference_data, schema_path
):
    html = make_html(tmp_path, template_path, reference_data)
    package = signed_package(reference_data)
    package[field] = [{"op": "replace", "path": "/intent/currentFocus", "value": "x"}] if field == "operations" else deepcopy(reference_data["guidance"])
    with pytest.raises(ApplyPatchError, match="hash"):
        apply_update_package(html, package, schema_path)


def test_valid_approved_package_applies(
    tmp_path, template_path, reference_data, schema_path
):
    html = make_html(tmp_path, template_path, reference_data)
    package = signed_package(
        reference_data,
        operations=[
            {
                "op": "replace",
                "path": "/intent/currentFocus",
                "value": "Approved exact proposal",
            }
        ],
    )
    _, updated, _ = apply_update_package(html, package, schema_path)
    assert updated["intent"]["currentFocus"] == "Approved exact proposal"


def test_default_json_read_redacts_embedded_credentials(
    reference_path, reference_data, capsys
):
    secret_values = set(
        next(
            item
            for item in reference_data["resources"]
            if item["access"]["credentials"]["mode"] == "embedded"
        )["access"]["credentials"]["fields"].values()
    )
    assert read_main([str(reference_path), "--json"]) == 0
    output = capsys.readouterr().out
    assert "***REDACTED***" in output
    assert not any(value in output for value in secret_values)


def test_redaction_does_not_resolve_external_credentials(reference_data):
    redacted = redact_embedded_secrets(reference_data)
    for resource in redacted["resources"]:
        credentials = resource["access"]["credentials"]
        if credentials["mode"] in {"external_file", "external_store"}:
            assert "fields" not in credentials


def test_unsafe_json_read_is_explicit(reference_path, reference_data, capsys):
    secret = next(
        iter(
            next(
                item
                for item in reference_data["resources"]
                if item["access"]["credentials"]["mode"] == "embedded"
            )["access"]["credentials"]["fields"].values()
        )
    )
    assert read_main([str(reference_path), "--json", "--unsafe-include-secrets"]) == 0
    captured = capsys.readouterr()
    assert secret in captured.out
    assert "WARNING: Embedded secrets are being emitted in plaintext." in captured.err


def test_validator_json_output_is_structured(reference_path, capsys):
    assert validate_main([str(reference_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["findings"]
    assert {"level", "severity", "code", "message", "path"} <= set(
        payload["findings"][0]
    )


def test_required_and_optional_gate_semantics(
    reference_data, reference_path, schema_path
):
    broken = deepcopy(reference_data)
    required = next(item for item in broken["gates"] if item["required"])
    required["status"] = "ready"
    optional = broken["gates"][-1]
    optional["required"] = False
    optional["status"] = "ready"
    report = validate_data(broken, schema_path, base_dir=reference_path.parent)
    verification = [item.message for item in report.warnings if item.code == "VERIFICATION_GAP"]
    assert any(required["id"] in message or "required Gate" in message for message in verification)
    assert not any(optional["id"] in message for message in verification)


def test_optional_failed_gate_is_separate_finding(
    reference_data, reference_path, schema_path
):
    broken = deepcopy(reference_data)
    optional = broken["gates"][-1]
    optional["required"] = False
    optional["status"] = "failed"
    report = validate_data(broken, schema_path, base_dir=reference_path.parent)
    assert any(item.code == "OPTIONAL_GATE_FAILED" for item in report.issues)


def test_review_gap_severity_is_contextual(reference_data, reference_path, schema_path):
    candidate = deepcopy(reference_data)
    module = next(
        item
        for item in candidate["architecture"]["modules"]
        if item["id"] == "MOD-METADATA"
    )
    module["reviewIds"] = []
    module["status"]["designMaturity"] = "experimental"
    module["status"]["implementationMaturity"] = "functional"
    module["architectureScope"] = "target"
    report = validate_data(candidate, schema_path, base_dir=reference_path.parent)
    finding = next(item for item in report.issues if item.code == "REVIEW_GAP" and module["id"] in item.message)
    assert finding.level == "INFO"
    assert finding.severity == "info"


def test_stage_entry_gap_checks_current_stage(reference_data, reference_path, schema_path):
    candidate = deepcopy(reference_data)
    current = next(item for item in candidate["stages"] if item["status"] == "current")
    current["entryAcceptanceIds"] = ["ACC-STG-MVP"]
    acceptance = next(item for item in candidate["acceptanceCriteria"] if item["id"] == "ACC-STG-MVP")
    acceptance["verificationStatus"] = "failed"
    report = validate_data(candidate, schema_path, base_dir=reference_path.parent)
    assert any(item.code == "STAGE_ENTRY_GAP" for item in report.warnings)
