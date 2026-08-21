from __future__ import annotations

import json
from pathlib import Path

import pytest

import approval_policy
import verified_delivery_policy_adapter as adapter
from test_v060_verified_delivery import _evidence, _fixture
from verified_delivery import VerifiedDeliveryError, prepare_delivery


AT = "2026-08-21T16:00:00Z"
POLICY_ID = "POLICY-VERIFIED-DELIVERY"


def _policy_semantics(project_root: Path, project_id: str, schema_version: str):
    value = json.loads((project_root / "examples" / "approval-policy.verification-receipt.v0.1.json").read_text(encoding="utf-8"))
    value.pop("approvalBinding")
    value.update(
        {
            "policyId": POLICY_ID,
            "operation": "verified_delivery.promote",
            "effectClass": "artifact_promotion",
            "projectBinding": {"projectId": project_id, "panoramaSchemaVersions": [schema_version]},
            "validity": {
                "effectiveAt": "2026-08-21T15:00:00Z",
                "expiresAt": "2026-08-22T15:00:00Z",
                "maxUses": 10,
                "sourceBindingRule": "not_applicable",
            },
            "scope": {
                "governanceProtectionProfile": "panorama-governance-v0.5",
                "allowedPanoramaPathTemplates": [],
                "additionalForbiddenPathTemplates": [],
                "artifactRoots": [".panorama-work/verified-delivery/v0.1"],
                "producerBindings": [],
                "commandBindings": [],
                "receiptRules": None,
                "recoveryRules": None,
                "deliveryRules": {
                    "requiredMachineChecks": ["semantic", "schema", "geometry", "browser", "privacy"],
                    "allowPendingVisualReview": True,
                    "rejectedVisualReviewBlocks": True,
                    "atomicLastGoodReplace": True,
                    "coreMutationAllowed": False,
                    "externalPublicationAllowed": False,
                },
            },
        }
    )
    return value


def _workspace(project_root: Path, tmp_path: Path):
    model, views = _fixture(project_root, tmp_path)
    root = tmp_path / "subject"
    root.mkdir()
    receipt, _ = prepare_delivery(
        root,
        model,
        views,
        browser_evidence=_evidence(model, views),
        generated_at="2026-08-21T15:30:01Z",
    )
    store = root / ".panorama-work" / "approval-policy-store" / "v0.1"
    semantics = _policy_semantics(
        project_root,
        receipt["projectBinding"]["projectId"],
        receipt["projectBinding"]["panoramaSchemaVersion"],
    )
    prepared = approval_policy.prepare_policy(semantics, prepared_at="2026-08-21T15:00:00Z")
    approval = approval_policy.record_policy_approval(
        prepared,
        confirmed_policy_hash=prepared["policyHash"],
        approved_by="test-user",
        approval_recorded_at="2026-08-21T15:00:01Z",
    )
    active = approval_policy.materialize_policy(prepared, approval)
    approval_policy.install_policy(store, active, installed_at="2026-08-21T15:00:02Z")
    return root, receipt, store


def _crash_at(expected: str):
    def inject(point: str):
        if point == expected:
            raise adapter.InjectedAdapterCrash(point)

    return inject


def test_v060_policy_promotion_is_atomic_audited_and_then_zero_use_no_op(project_root, tmp_path):
    root, receipt, store = _workspace(project_root, tmp_path)

    result = adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at=AT)

    last_good = root / ".panorama-work" / "verified-delivery" / "v0.1" / "last-good" / "index.html"
    assert result["status"] == "finalized"
    assert result["result"]["status"] == "succeeded"
    assert last_good.read_bytes() == (root / ".panorama-work" / "verified-delivery" / "v0.1" / receipt["artifact"]["ref"]).read_bytes()
    assert adapter.validate_transactions(root) == {
        "formatVersion": "panorama-verified-delivery-promotion-store-validation.v0.1",
        "valid": True,
        "transactionCount": 1,
        "errors": [],
    }
    state = approval_policy.inspect_policy_state(store, POLICY_ID)
    assert state["ledger"]["nextUseNumber"] == 2
    assert state["ledger"]["pending"] is None

    no_op = adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at="2026-08-21T16:01:00Z")
    assert no_op["status"] == "no_op"
    assert approval_policy.inspect_policy_state(store, POLICY_ID)["ledger"]["nextUseNumber"] == 2


def test_v060_post_effect_crash_resumes_without_replacing_again(project_root, tmp_path):
    root, receipt, store = _workspace(project_root, tmp_path)
    with pytest.raises(adapter.InjectedAdapterCrash, match="after_effect"):
        adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at=AT, fault_injector=_crash_at("after_effect"))

    last_good = root / ".panorama-work" / "verified-delivery" / "v0.1" / "last-good" / "index.html"
    before_resume = last_good.read_bytes()
    state = approval_policy.inspect_policy_state(store, POLICY_ID)
    assert state["ledger"]["pending"]["useNumber"] == 1

    result = adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at="2026-08-21T16:01:00Z")
    assert result["status"] == "finalized"
    assert last_good.read_bytes() == before_resume
    assert approval_policy.inspect_policy_state(store, POLICY_ID)["ledger"]["pending"] is None


def test_v060_allocated_crash_then_third_state_fails_closed_and_preserves_bytes(project_root, tmp_path):
    root, receipt, store = _workspace(project_root, tmp_path)
    with pytest.raises(adapter.InjectedAdapterCrash, match="after_allocated"):
        adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at=AT, fault_injector=_crash_at("after_allocated"))

    last_good = root / ".panorama-work" / "verified-delivery" / "v0.1" / "last-good" / "index.html"
    last_good.parent.mkdir(parents=True, exist_ok=True)
    last_good.write_text("third-state", encoding="utf-8")
    with pytest.raises(adapter.VerifiedDeliveryPolicyConflictError, match="第三状态"):
        adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at="2026-08-21T16:01:00Z")
    assert last_good.read_text(encoding="utf-8") == "third-state"
    assert approval_policy.inspect_policy_state(store, POLICY_ID)["ledger"]["pending"] is None


def test_v060_tampered_candidate_blocks_before_use_and_preserves_last_good(project_root, tmp_path):
    root, receipt, store = _workspace(project_root, tmp_path)
    last_good = root / ".panorama-work" / "verified-delivery" / "v0.1" / "last-good" / "index.html"
    last_good.parent.mkdir(parents=True, exist_ok=True)
    last_good.write_text("known-good", encoding="utf-8")
    candidate = root / ".panorama-work" / "verified-delivery" / "v0.1" / receipt["artifact"]["ref"]
    candidate.write_text("tampered", encoding="utf-8")

    with pytest.raises(adapter.VerifiedDeliveryPolicyAdapterError, match="不匹配"):
        adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at=AT)
    assert last_good.read_text(encoding="utf-8") == "known-good"
    assert approval_policy.inspect_policy_state(store, POLICY_ID)["ledger"]["nextUseNumber"] == 1


def test_v060_candidate_drift_after_use_allocation_is_failed_without_external_effect(project_root, tmp_path):
    root, receipt, store = _workspace(project_root, tmp_path)
    with pytest.raises(adapter.InjectedAdapterCrash, match="after_allocated"):
        adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at=AT, fault_injector=_crash_at("after_allocated"))
    candidate = root / ".panorama-work" / "verified-delivery" / "v0.1" / receipt["artifact"]["ref"]
    candidate.write_text("drifted-after-allocation", encoding="utf-8")

    with pytest.raises(adapter.VerifiedDeliveryPolicyAdapterError, match="不匹配"):
        adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at="2026-08-21T16:01:00Z")
    last_good = root / ".panorama-work" / "verified-delivery" / "v0.1" / "last-good" / "index.html"
    assert not last_good.exists()
    state = approval_policy.inspect_policy_state(store, POLICY_ID)
    assert state["ledger"]["pending"] is None
    execution = approval_policy.read_execution_receipt(store, POLICY_ID, 1)
    assert execution["execution"]["status"] == "failed"
    assert execution["effect"]["externalEffectObserved"] is False


def test_v060_transaction_hash_tamper_is_detected(project_root, tmp_path):
    root, receipt, _ = _workspace(project_root, tmp_path)
    with pytest.raises(adapter.InjectedAdapterCrash):
        adapter.execute(root, receipt["deliveryId"], POLICY_ID, executed_at=AT, fault_injector=_crash_at("after_prepared"))
    transaction = next((root / ".panorama-work" / "approval-policy-operations" / "v0.1" / "verified-delivery-promote").glob("VDP-*.json"))
    value = json.loads(transaction.read_text(encoding="utf-8"))
    value["result"]["summary"] = "tampered"
    transaction.write_text(json.dumps(value), encoding="utf-8")
    report = adapter.validate_transactions(root)
    assert report["valid"] is False
    assert "State Hash" in report["errors"][0]
