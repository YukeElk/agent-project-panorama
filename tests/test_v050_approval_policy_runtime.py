from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def _scripts_on_path(project_root, monkeypatch):
    monkeypatch.syspath_prepend(str(project_root / "scripts"))


def _semantics(project_root: Path, *, policy_id: str = "POLICY-TEST", max_uses: int = 4):
    value = json.loads(
        (project_root / "examples" / "approval-policy.verification-receipt.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    value.pop("approvalBinding")
    value["policyId"] = policy_id
    value["validity"] = {
        "effectiveAt": "2026-08-20T08:00:00Z",
        "expiresAt": "2026-09-20T08:00:00Z",
        "maxUses": max_uses,
        "sourceBindingRule": "revalidate_current_each_use",
    }
    return value


def _active(project_root: Path, *, policy_id: str = "POLICY-TEST", max_uses: int = 4):
    from approval_policy import materialize_policy, prepare_policy, record_policy_approval

    prepared = prepare_policy(
        _semantics(project_root, policy_id=policy_id, max_uses=max_uses),
        prepared_at="2026-08-20T08:00:00Z",
    )
    approval = record_policy_approval(
        prepared,
        confirmed_policy_hash=prepared["policyHash"],
        approved_by="test-user",
        approval_recorded_at="2026-08-20T08:00:01Z",
    )
    return materialize_policy(prepared, approval), prepared, approval


def _project_binding():
    return {
        "projectId": "REFERENCE-PROJECT",
        "panoramaSchemaVersion": "0.2",
        "baseRevision": 3,
        "baseDataHash": "a" * 64,
        "sourceBindingHash": "c" * 64,
    }


def _result_binding():
    return {
        "projectId": "REFERENCE-PROJECT",
        "panoramaSchemaVersion": "0.2",
        "resultRevision": 4,
        "resultDataHash": "d" * 64,
    }


def _producer(active):
    return copy.deepcopy(active["scope"]["producerBindings"][0])


def test_prepare_approve_materialize_exact_hash_and_tamper(project_root):
    from approval_policy import (
        ApprovalPolicyConflictError,
        ApprovalPolicyError,
        compute_policy_hash,
        materialize_policy,
        prepare_policy,
        record_policy_approval,
        validate_active_policy,
    )

    active, prepared, approval = _active(project_root)
    assert active["approvalBinding"]["policyHash"] == compute_policy_hash(active)
    assert active["approvalBinding"]["approvalTimeSource"] == "approval_recorder_clock"
    validate_active_policy(active)

    rejected = copy.deepcopy(prepared)
    rejected["approvalState"] = "rejected"
    with pytest.raises(ApprovalPolicyError, match="awaiting_user_approval"):
        record_policy_approval(
            rejected,
            confirmed_policy_hash=prepared["policyHash"],
            approved_by="test-user",
        )
    invalid_validation = copy.deepcopy(prepared)
    invalid_validation["validation"]["valid"] = False
    with pytest.raises(ApprovalPolicyError, match="validation"):
        record_policy_approval(
            invalid_validation,
            confirmed_policy_hash=prepared["policyHash"],
            approved_by="test-user",
        )
    forged_clock = copy.deepcopy(approval)
    forged_clock["approvalTimeSource"] = "forged_agent_clock"
    with pytest.raises(ApprovalPolicyError, match="approvalTimeSource"):
        materialize_policy(prepared, forged_clock)

    tampered = copy.deepcopy(prepared)
    tampered["policy"]["validity"]["maxUses"] += 1
    with pytest.raises(ApprovalPolicyConflictError):
        record_policy_approval(
            tampered,
            confirmed_policy_hash=prepared["policyHash"],
            approved_by="test-user",
            approval_recorded_at="2026-08-20T08:00:02Z",
        )
    approval["policyHash"] = "f" * 64
    with pytest.raises(ApprovalPolicyConflictError):
        materialize_policy(prepared, approval)


def test_policy_json_depth_is_bounded(project_root):
    from approval_policy import ApprovalPolicyError, prepare_policy

    semantics = _semantics(project_root)
    nested = {}
    cursor = nested
    for _ in range(25):
        cursor["child"] = {}
        cursor = cursor["child"]
    semantics["extensions"] = nested
    with pytest.raises(ApprovalPolicyError, match="嵌套深度"):
        prepare_policy(semantics, prepared_at="2026-08-20T08:00:00Z")


def test_install_begin_success_event_receipt_and_chain(project_root, tmp_path):
    from approval_policy import (
        begin_execution,
        complete_execution,
        install_policy,
        validate_policy_store,
    )
    from event_store import validate_store

    active, _, _ = _active(project_root)
    policy_store = tmp_path / "policy-store"
    engineering_store = tmp_path / "event-store"
    assert install_policy(policy_store, active, installed_at="2026-08-20T08:00:02Z") is True
    assert install_policy(policy_store, active, installed_at="2026-08-20T08:00:03Z") is False

    pending = begin_execution(
        policy_store,
        active["policyId"],
        input_hash="1" * 64,
        project_binding=_project_binding(),
        source_binding_status="matched",
        panorama_paths=["/references/-"],
        artifact_refs=[".panorama-work/verification-receipts/receipt.json"],
        binding=_producer(active),
        started_at="2026-08-20T09:00:00Z",
    )
    assert pending["useNumber"] == 1
    receipt, created = complete_execution(
        policy_store,
        active["policyId"],
        use_number=1,
        status="succeeded",
        result_project_binding=_result_binding(),
        output_hash="2" * 64,
        panorama_paths=["/references/-"],
        artifact_refs=[".panorama-work/verification-receipts/receipt.json"],
        formal_validation="passed",
        summary="Receipt imported after formal validation.",
        event_store_path=engineering_store,
        project_root=tmp_path,
        completed_at="2026-08-20T09:00:01Z",
    )
    assert created is True
    assert receipt["execution"]["status"] == "succeeded"
    assert receipt["eventBinding"]["eventId"].startswith("EVT-")
    assert validate_store(engineering_store)["valid"] is True
    report = validate_policy_store(policy_store)
    assert report["valid"] is True
    assert report["receiptCount"] == 1
    assert report["recoveryRequired"] is False


def test_pending_blocks_and_failure_consumes_use_with_truthful_effect(project_root, tmp_path):
    from approval_policy import (
        ApprovalPolicyConflictError,
        begin_execution,
        complete_execution,
        install_policy,
        validate_policy_store,
    )

    active, _, _ = _active(project_root)
    store = tmp_path / "policy-store"
    install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    begin_execution(
        store,
        active["policyId"],
        input_hash="3" * 64,
        project_binding=_project_binding(),
        source_binding_status="matched",
        panorama_paths=["/references/-"],
        binding=_producer(active),
        started_at="2026-08-20T09:00:00Z",
    )
    with pytest.raises(ApprovalPolicyConflictError, match="pending"):
        begin_execution(
            store,
            active["policyId"],
            input_hash="4" * 64,
            project_binding=_project_binding(),
            source_binding_status="matched",
            panorama_paths=["/references/-"],
            binding=_producer(active),
            started_at="2026-08-20T09:00:01Z",
        )
    receipt, _ = complete_execution(
        store,
        active["policyId"],
        use_number=1,
        status="failed",
        result_project_binding=_result_binding(),
        output_hash=None,
        panorama_paths=["/references/-"],
        formal_validation="failed",
        external_effect_observed=True,
        summary="Producer failed after an external side effect was observed.",
        completed_at="2026-08-20T09:00:02Z",
    )
    assert receipt["execution"]["status"] == "failed"
    assert receipt["effect"]["externalEffectObserved"] is True
    assert receipt["eventBinding"] is None
    assert validate_policy_store(store)["valid"] is True
    pending = begin_execution(
        store,
        active["policyId"],
        input_hash="5" * 64,
        project_binding=_project_binding(),
        source_binding_status="matched",
        panorama_paths=["/references/-"],
        binding=_producer(active),
        started_at="2026-08-20T09:00:03Z",
    )
    assert pending["useNumber"] == 2


def test_protected_path_untrusted_binding_and_source_conflict_fail_closed(project_root, tmp_path):
    from approval_policy import (
        ApprovalPolicyConflictError,
        begin_execution,
        install_policy,
        materialize_policy,
        prepare_policy,
        protected_pointer_matches,
        record_policy_approval,
    )

    assert protected_pointer_matches("/intent", "/intent/currentFocus")
    assert protected_pointer_matches(
        "/resources/*/access", "/resources/0/access/credentials/mode"
    )

    active, _, _ = _active(project_root)
    store = tmp_path / "policy-store"
    install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    cases = [
        {"panorama_paths": ["/gates/G-1/status"], "binding": _producer(active), "source_binding_status": "matched"},
        {"panorama_paths": ["/references/-"], "binding": {**_producer(active), "producerVersion": "9.9.9"}, "source_binding_status": "matched"},
        {"panorama_paths": ["/references/-"], "binding": _producer(active), "source_binding_status": "mismatch"},
    ]
    for index, case in enumerate(cases):
        with pytest.raises(ApprovalPolicyConflictError):
            begin_execution(
                store,
                active["policyId"],
                input_hash=f"{index + 6:x}" * 64,
                project_binding=_project_binding(),
                started_at="2026-08-20T09:00:00Z",
                **case,
            )
    ledger = json.loads((store / "ledgers" / f"{active['policyId']}.json").read_text(encoding="utf-8"))
    assert ledger["nextUseNumber"] == 1
    assert ledger["pending"] is None

    # Even an exact allowlist entry cannot override a governance-protected root.
    semantics = _semantics(project_root, policy_id="POLICY-PROTECTED-DESCENDANT")
    semantics["scope"]["allowedPanoramaPathTemplates"].append("/intent/currentFocus")
    prepared = prepare_policy(semantics, prepared_at="2026-08-20T08:00:00Z")
    approval = record_policy_approval(
        prepared,
        confirmed_policy_hash=prepared["policyHash"],
        approved_by="test-user",
        approval_recorded_at="2026-08-20T08:00:01Z",
    )
    protected_policy = materialize_policy(prepared, approval)
    protected_store = tmp_path / "protected-descendant-store"
    install_policy(protected_store, protected_policy, installed_at="2026-08-20T08:00:02Z")
    with pytest.raises(ApprovalPolicyConflictError, match="治理保护"):
        begin_execution(
            protected_store,
            protected_policy["policyId"],
            input_hash="a" * 64,
            project_binding=_project_binding(),
            source_binding_status="matched",
            panorama_paths=["/intent/currentFocus"],
            binding=_producer(protected_policy),
            started_at="2026-08-20T09:00:00Z",
        )


def test_required_formal_validation_rejects_not_applicable_success(project_root, tmp_path):
    from approval_policy import begin_execution, complete_execution, install_policy

    active, _, _ = _active(project_root, policy_id="POLICY-FORMAL-VALIDATION")
    store = tmp_path / "policy-store"
    install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    begin_execution(
        store,
        active["policyId"],
        input_hash="b" * 64,
        project_binding=_project_binding(),
        source_binding_status="matched",
        panorama_paths=["/references/-"],
        binding=_producer(active),
        started_at="2026-08-20T09:00:00Z",
    )
    receipt, _ = complete_execution(
        store,
        active["policyId"],
        use_number=1,
        status="succeeded",
        result_project_binding=_result_binding(),
        output_hash="c" * 64,
        panorama_paths=["/references/-"],
        formal_validation="not_applicable",
        completed_at="2026-08-20T09:00:01Z",
    )
    assert receipt["execution"]["status"] == "failed"
    assert receipt["validation"]["formalValidation"] == "not_applicable"
    assert receipt["eventBinding"] is None


def test_missing_event_keeps_success_pending_for_explicit_completion(project_root, tmp_path):
    from approval_policy import ApprovalPolicyError, begin_execution, complete_execution, install_policy, validate_policy_store

    active, _, _ = _active(project_root)
    store = tmp_path / "policy-store"
    install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    begin_execution(
        store,
        active["policyId"],
        input_hash="9" * 64,
        project_binding=_project_binding(),
        source_binding_status="matched",
        panorama_paths=["/references/-"],
        binding=_producer(active),
        started_at="2026-08-20T09:00:00Z",
    )
    with pytest.raises(ApprovalPolicyError, match="Engineering Event"):
        complete_execution(
            store,
            active["policyId"],
            use_number=1,
            status="succeeded",
            result_project_binding=_result_binding(),
            output_hash="a" * 64,
            panorama_paths=["/references/-"],
            formal_validation="passed",
            completed_at="2026-08-20T09:00:01Z",
        )
    report = validate_policy_store(store)
    assert report["valid"] is True
    assert report["recoveryRequired"] is True


def test_durable_receipt_with_ledger_write_failure_is_recoverable(project_root, tmp_path, monkeypatch):
    import approval_policy
    from approval_policy import (
        begin_execution,
        complete_execution,
        install_policy,
        recover_pending,
        validate_policy_store,
    )

    active, _, _ = _active(project_root)
    store = tmp_path / "policy-store"
    event_store = tmp_path / "event-store"
    install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    begin_execution(
        store,
        active["policyId"],
        input_hash="e" * 64,
        project_binding=_project_binding(),
        source_binding_status="matched",
        panorama_paths=["/references/-"],
        binding=_producer(active),
        started_at="2026-08-20T09:00:00Z",
    )
    original = approval_policy._write_ledger

    def fail_final_ledger(path, ledger):
        if ledger["pending"] is None and ledger["previousReceiptHash"] is not None:
            raise OSError("injected final ledger failure")
        return original(path, ledger)

    monkeypatch.setattr(approval_policy, "_write_ledger", fail_final_ledger)
    with pytest.raises(OSError, match="injected"):
        complete_execution(
            store,
            active["policyId"],
            use_number=1,
            status="succeeded",
            result_project_binding=_result_binding(),
            output_hash="f" * 64,
            panorama_paths=["/references/-"],
            formal_validation="passed",
            summary="Durable receipt before injected ledger failure.",
            event_store_path=event_store,
            completed_at="2026-08-20T09:00:01Z",
        )
    report = validate_policy_store(store)
    assert report["valid"] is True
    assert report["recoveryRequired"] is True
    assert report["receiptCount"] == 1

    monkeypatch.setattr(approval_policy, "_write_ledger", original)
    recovered = recover_pending(
        store, active["policyId"], recovered_at="2026-08-20T09:00:02Z"
    )
    assert recovered["recovered"] is True
    assert validate_policy_store(store)["recoveryRequired"] is False


def test_revocation_and_superseding_activation(project_root, tmp_path):
    from approval_policy import (
        ApprovalPolicyConflictError,
        begin_execution,
        install_policy,
        revoke_policy,
    )

    old, _, _ = _active(project_root, policy_id="POLICY-OLD")
    store = tmp_path / "policy-store"
    install_policy(store, old, installed_at="2026-08-20T08:00:02Z")
    revocation, created = revoke_policy(
        store,
        old["policyId"],
        policy_hash=old["approvalBinding"]["policyHash"],
        revoked_by="test-user",
        revoked_at="2026-08-20T08:10:00Z",
    )
    assert created and revocation["revocationMethod"] == "explicit_policy_reference"
    with pytest.raises(ApprovalPolicyConflictError, match="撤销"):
        begin_execution(
            store,
            old["policyId"],
            input_hash="b" * 64,
            project_binding=_project_binding(),
            source_binding_status="matched",
            panorama_paths=["/references/-"],
            binding=_producer(old),
            started_at="2026-08-20T09:00:00Z",
        )

    # A clean store proves superseding approval revokes the old policy before activation.
    store2 = tmp_path / "supersede-store"
    install_policy(store2, old, installed_at="2026-08-20T08:00:02Z")
    semantics = _semantics(project_root, policy_id="POLICY-NEW")
    semantics["supersedesPolicyId"] = old["policyId"]
    from approval_policy import materialize_policy, prepare_policy, record_policy_approval

    prepared = prepare_policy(semantics, prepared_at="2026-08-20T08:15:00Z")
    approval = record_policy_approval(
        prepared,
        confirmed_policy_hash=prepared["policyHash"],
        approved_by="test-user",
        approval_recorded_at="2026-08-20T08:15:01Z",
    )
    new = materialize_policy(prepared, approval)
    install_policy(store2, new, installed_at="2026-08-20T08:15:02Z")
    stored_revocation = json.loads(
        (store2 / "revocations" / "POLICY-OLD.json").read_text(encoding="utf-8")
    )
    assert stored_revocation["revocationMethod"] == "superseding_policy_approval"


def test_install_recovers_only_valid_initial_orphan_ledger(
    project_root, tmp_path, monkeypatch
):
    import approval_policy
    from approval_policy import install_policy, validate_policy_store

    active, _, _ = _active(project_root, policy_id="POLICY-INSTALL-RECOVERY")
    store = tmp_path / "policy-store"
    original_write = approval_policy._write_json

    def fail_activation_marker(path, value):
        if Path(path).parent.name == "policies":
            raise OSError("injected activation marker failure")
        return original_write(path, value)

    monkeypatch.setattr(approval_policy, "_write_json", fail_activation_marker)
    with pytest.raises(OSError, match="injected"):
        install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    ledger_path = store / "ledgers" / f"{active['policyId']}.json"
    assert ledger_path.exists()
    assert not (store / "policies" / f"{active['policyId']}.json").exists()

    monkeypatch.setattr(approval_policy, "_write_json", original_write)
    assert install_policy(store, active, installed_at="2026-08-20T08:00:03Z") is True
    report = validate_policy_store(store)
    assert report["valid"] is True
    assert report["recoveryRequired"] is False


def test_materialize_publishes_before_activation_and_exact_retry_recovers(
    project_root, tmp_path, monkeypatch
):
    import materialize_approval_policy as materialize_module
    from approval_policy import validate_policy_store

    _, prepared, approval = _active(project_root, policy_id="POLICY-CLI-RECOVERY")
    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    active_path = tmp_path / "active.json"
    store = tmp_path / "policy-store"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    original_install = materialize_module.install_policy

    def fail_install(*args, **kwargs):
        raise OSError("injected activation failure")

    monkeypatch.setattr(materialize_module, "install_policy", fail_install)
    argv = [
        str(prepared_path),
        str(approval_path),
        "--output",
        str(active_path),
        "--store",
        str(store),
    ]
    assert materialize_module.main(argv) == 2
    assert active_path.exists()
    assert not (store / "policies").exists()

    monkeypatch.setattr(materialize_module, "install_policy", original_install)
    assert materialize_module.main(argv) == 0
    assert validate_policy_store(store)["valid"] is True


def test_store_validation_cli_is_nonzero_when_recovery_is_required(
    project_root, tmp_path
):
    import validate_approval_policy_store as validator_cli
    from approval_policy import begin_execution, install_policy

    active, _, _ = _active(project_root, policy_id="POLICY-PENDING-CLI")
    store = tmp_path / "policy-store"
    install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    begin_execution(
        store,
        active["policyId"],
        input_hash="d" * 64,
        project_binding=_project_binding(),
        source_binding_status="matched",
        panorama_paths=["/references/-"],
        binding=_producer(active),
        started_at="2026-08-20T09:00:00Z",
    )
    assert validator_cli.main([str(store), "--json"]) == 1


def test_policy_store_tamper_detection_and_cli_lifecycle(project_root, tmp_path):
    from approval_policy import install_policy, validate_policy_store

    active, _, _ = _active(project_root)
    store = tmp_path / "policy-store"
    install_policy(store, active, installed_at="2026-08-20T08:00:02Z")
    ledger_path = store / "ledgers" / f"{active['policyId']}.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["nextUseNumber"] = 99
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    report = validate_policy_store(store)
    assert report["valid"] is False
    assert any("Ledger Hash" in item for item in report["errors"])

    semantics_path = tmp_path / "semantics.json"
    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    active_path = tmp_path / "active.json"
    semantics_path.write_text(
        json.dumps(_semantics(project_root, policy_id="POLICY-CLI")), encoding="utf-8"
    )
    scripts = project_root / "scripts"
    prepare = subprocess.run(
        [sys.executable, str(scripts / "prepare_approval_policy.py"), str(semantics_path), "--output", str(prepared_path)],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert prepare.returncode == 0, prepare.stderr
    policy_hash = json.loads(prepared_path.read_text(encoding="utf-8"))["policyHash"]
    approve = subprocess.run(
        [sys.executable, str(scripts / "record_approval_policy_approval.py"), str(prepared_path), "--approved-hash", policy_hash, "--approved-by", "cli-user", "--output", str(approval_path)],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert approve.returncode == 0, approve.stderr
    materialize = subprocess.run(
        [sys.executable, str(scripts / "materialize_approval_policy.py"), str(prepared_path), str(approval_path), "--output", str(active_path)],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert materialize.returncode == 0, materialize.stderr
    assert json.loads(active_path.read_text(encoding="utf-8"))["policyId"] == "POLICY-CLI"
