from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from apply_patch import (
    ApplyPatchError,
    apply_update_package,
    compute_proposal_hash,
    inject_studio_approval,
    main as apply_main,
    unwrap_proposal_artifact,
)
from panorama_io import extract_data, replace_data
from propose_update import build_proposal
from studio_approval import (
    StudioApprovalError,
    main as approval_main,
    record_studio_approval,
)


RECORDER_TIME = "2026-08-12T09:30:00Z"


def _proposal_wrapper(
    reference_data: dict,
    schema_path: Path,
    tmp_path: Path,
    template_path: Path,
) -> tuple[Path, dict]:
    html = tmp_path / "studio-approval.html"
    replace_data(template_path, reference_data, html)
    candidate = {
        "operations": [
            {
                "op": "replace",
                "path": "/intent/currentFocus",
                "value": "Apply approved Architecture Studio proposal",
            }
        ],
        "summary": "Architecture Studio proposal",
        "changeLevel": "local",
        "createdAt": "2026-08-12T09:00:00Z",
    }
    return html, build_proposal(
        reference_data,
        candidate,
        schema_path,
        base_dir=tmp_path,
        source_path=html,
    )


def test_wrapper_unwrap_is_strict_and_rejects_mixed_shape(
    reference_data, schema_path, tmp_path, template_path
):
    _, wrapper = _proposal_wrapper(
        reference_data, schema_path, tmp_path, template_path
    )

    assert unwrap_proposal_artifact(wrapper) == wrapper["proposal"]
    assert unwrap_proposal_artifact(wrapper["proposal"]) == wrapper["proposal"]

    mixed = deepcopy(wrapper)
    mixed["baseRevision"] = wrapper["proposal"]["baseRevision"]
    with pytest.raises(ApplyPatchError, match="混合"):
        unwrap_proposal_artifact(mixed)

    malformed = deepcopy(wrapper)
    malformed["unexpected"] = True
    with pytest.raises(ApplyPatchError, match="严格合同"):
        unwrap_proposal_artifact(malformed)


def test_recording_requires_exact_hash_and_uses_recorder_clock(
    reference_data, schema_path, tmp_path, template_path
):
    _, wrapper = _proposal_wrapper(
        reference_data, schema_path, tmp_path, template_path
    )
    proposal_hash = wrapper["proposal"]["proposalHash"]
    source_binding = {
        "projectId": reference_data["project"]["id"],
        "schemaVersion": reference_data["schemaVersion"],
    }

    approval = record_studio_approval(
        wrapper,
        approved_hash=proposal_hash,
        approved_by=" Architecture Reviewer ",
        recorded_at=RECORDER_TIME,
        source_binding=source_binding,
    )

    assert approval == {
        "approvalVersion": "studio-update-approval.v0.1",
        "status": "approved",
        "proposalHash": proposal_hash,
        "approvedBy": "Architecture Reviewer",
        "approvalRecordedAt": RECORDER_TIME,
        "approvalMethod": "explicit_hash_confirmation",
        "approvalTimeSource": "approval_recorder_clock",
        "sourceBinding": {
            "projectId": reference_data["project"]["id"],
            "schemaVersion": reference_data["schemaVersion"],
            "baseRevision": wrapper["proposal"]["baseRevision"],
            "baseDataHash": wrapper["proposal"]["baseDataHash"],
        },
    }
    with pytest.raises(StudioApprovalError, match="explicit approved hash"):
        record_studio_approval(
            wrapper,
            approved_hash="0" * 64,
            approved_by="Architecture Reviewer",
            recorded_at=RECORDER_TIME,
        )


def test_approval_artifact_is_write_once(
    reference_data, schema_path, tmp_path, template_path
):
    _, wrapper = _proposal_wrapper(
        reference_data, schema_path, tmp_path, template_path
    )
    proposal_path = tmp_path / "pending-update.json"
    approval_path = tmp_path / "studio-approval.json"
    proposal_path.write_text(
        json.dumps(wrapper, ensure_ascii=False), encoding="utf-8"
    )
    argv = [
        str(proposal_path),
        "--approved-hash",
        wrapper["proposal"]["proposalHash"],
        "--approved-by",
        "Architecture Reviewer",
        "--output",
        str(approval_path),
    ]

    assert approval_main(argv) == 0
    first = approval_path.read_bytes()
    assert approval_main(argv) == 2
    assert approval_path.read_bytes() == first


def test_external_approval_detects_tampering_and_dual_approval(
    reference_data, schema_path, tmp_path, template_path
):
    _, wrapper = _proposal_wrapper(
        reference_data, schema_path, tmp_path, template_path
    )
    approval = record_studio_approval(
        wrapper,
        approved_hash=wrapper["proposal"]["proposalHash"],
        approved_by="Architecture Reviewer",
        recorded_at=RECORDER_TIME,
    )
    tampered = deepcopy(wrapper["proposal"])
    tampered["operations"][0]["value"] = "post-approval tampering"
    with pytest.raises(ApplyPatchError, match="完全一致"):
        inject_studio_approval(tampered, approval)

    inline = deepcopy(wrapper["proposal"])
    inline["approval"] = {
        "status": "approved",
        "approvedBy": "another reviewer",
        "approvedAt": RECORDER_TIME,
        "proposalHash": inline["proposalHash"],
    }
    with pytest.raises(ApplyPatchError, match="同时使用"):
        inject_studio_approval(inline, approval)


def test_apply_external_approval_keeps_wrapper_pending_and_checks_binding(
    reference_data, schema_path, tmp_path, template_path
):
    html, wrapper = _proposal_wrapper(
        reference_data, schema_path, tmp_path, template_path
    )
    package = unwrap_proposal_artifact(wrapper)
    source_binding = {
        "projectId": reference_data["project"]["id"],
        "schemaVersion": reference_data["schemaVersion"],
    }
    approval = record_studio_approval(
        wrapper,
        approved_hash=compute_proposal_hash(package),
        approved_by="Architecture Reviewer",
        recorded_at=RECORDER_TIME,
        source_binding=source_binding,
    )

    _, updated, _ = apply_update_package(
        html, package, schema_path, approval
    )

    assert package["approval"]["status"] == "pending"
    assert wrapper["proposal"]["approval"]["status"] == "pending"
    assert updated["intent"]["currentFocus"] == (
        "Apply approved Architecture Studio proposal"
    )
    assert updated["reviews"][-1]["reviewedBy"] == "Architecture Reviewer"
    assert updated["reviews"][-1]["reviewedAt"] == RECORDER_TIME
    assert extract_data(html)["meta"]["revision"] == (
        reference_data["meta"]["revision"] + 1
    )

    second_html = tmp_path / "binding-mismatch.html"
    replace_data(template_path, reference_data, second_html)
    wrong_binding = deepcopy(approval)
    wrong_binding["sourceBinding"]["projectId"] = "PRJ-WRONG"
    with pytest.raises(ApplyPatchError, match="sourceBinding.projectId"):
        apply_update_package(second_html, package, schema_path, wrong_binding)


def test_apply_cli_accepts_proposer_wrapper_with_approval_adapter(
    reference_data, schema_path, tmp_path, template_path
):
    html, wrapper = _proposal_wrapper(
        reference_data, schema_path, tmp_path, template_path
    )
    approval = record_studio_approval(
        wrapper,
        approved_hash=wrapper["proposal"]["proposalHash"],
        approved_by="CLI Reviewer",
        recorded_at=RECORDER_TIME,
    )
    proposal_path = tmp_path / "cli-pending-update.json"
    approval_path = tmp_path / "cli-studio-approval.json"
    proposal_path.write_text(
        json.dumps(wrapper, ensure_ascii=False), encoding="utf-8"
    )
    approval_path.write_text(
        json.dumps(approval, ensure_ascii=False), encoding="utf-8"
    )

    assert apply_main(
        [
            str(html),
            str(proposal_path),
            "--approval",
            str(approval_path),
            "--schema",
            str(schema_path),
        ]
    ) == 0
    updated = extract_data(html)
    assert updated["reviews"][-1]["reviewedBy"] == "CLI Reviewer"
    assert wrapper["proposal"]["approval"]["status"] == "pending"
