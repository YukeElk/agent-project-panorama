from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

import approval_policy
import event_store
import event_head_policy_adapter as adapter
from panorama_io import compute_data_hash
from recover_event_head_with_policy import main as recover_main


AT = "2026-08-21T08:00:00Z"
POLICY_ID = "POLICY-HEAD-RECOVERY"


def _policy_semantics(project_root: Path, project_id: str, *, max_uses: int = 10):
    value = json.loads(
        (project_root / "examples" / "approval-policy.verification-receipt.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    value.pop("approvalBinding")
    value.update(
        {
            "policyId": POLICY_ID,
            "operation": "event_head.recover",
            "effectClass": "deterministic_recovery",
            "projectBinding": {
                "projectId": project_id,
                "panoramaSchemaVersions": ["0.1"],
            },
            "validity": {
                "effectiveAt": "2026-08-21T07:00:00Z",
                "expiresAt": "2026-08-22T07:00:00Z",
                "maxUses": max_uses,
                "sourceBindingRule": "not_applicable",
            },
            "scope": {
                "governanceProtectionProfile": "panorama-governance-v0.5",
                "allowedPanoramaPathTemplates": [],
                "additionalForbiddenPathTemplates": [],
                "artifactRoots": [".panorama-work/event-store/v0.1"],
                "producerBindings": [],
                "commandBindings": [],
                "receiptRules": None,
                "recoveryRules": {
                    "allowedHeadStates": ["head_missing", "head_behind"],
                    "requireSchemaValidChain": True,
                    "requireHashValidChain": True,
                    "requireContiguousSequence": True,
                    "requireUniqueTail": True,
                    "requireExactProjectStreamEpochBinding": True,
                    "eventMutationAllowed": False,
                },
                "deliveryRules": None,
            },
        }
    )
    return value


def _request(project_root: Path, panorama: dict, *, key: str = "1" * 64):
    request = json.loads(
        (project_root / "examples" / "engineering-event-request.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    data_hash = compute_data_hash(panorama)
    request["idempotencyKey"] = key
    request["projectBinding"] = {
        "projectId": panorama["project"]["id"],
        "panoramaSchemaVersion": panorama["schemaVersion"],
        "baseRevision": panorama["meta"]["revision"],
        "resultRevision": panorama["meta"]["revision"],
        "baseDataHash": data_hash,
        "resultDataHash": data_hash,
    }
    request["sourceBinding"] = {
        "mode": "not_applicable",
        "gitHead": None,
        "sourceSnapshotHash": None,
        "sourceContentDigest": None,
        "coverage": "not_applicable",
    }
    request["evidenceBindings"] = []
    return request


def _workspace(tmp_path: Path, project_root: Path, *, two_events: bool = False):
    root = tmp_path / "subject-project"
    root.mkdir()
    panorama_path = root / "panorama.json"
    shutil.copyfile(project_root / "examples" / "reference-project.v0.1.json", panorama_path)
    panorama = json.loads(panorama_path.read_text(encoding="utf-8"))
    event_path = root / ".panorama-work" / "event-store" / "v0.1"
    policy_path = root / ".panorama-work" / "approval-policy-store" / "v0.1"
    first, _ = event_store.record_request(
        event_path,
        _request(project_root, panorama),
        recorded_at="2026-08-21T07:30:00Z",
        project_root=root,
    )
    events = [first]
    if two_events:
        second_request = _request(project_root, panorama, key="2" * 64)
        second_request["eventType"] = "artifact.invalidated"
        second, _ = event_store.record_request(
            event_path,
            second_request,
            recorded_at="2026-08-21T07:31:00Z",
            project_root=root,
        )
        events.append(second)
    prepared = approval_policy.prepare_policy(
        _policy_semantics(project_root, panorama["project"]["id"]),
        prepared_at="2026-08-21T07:00:00Z",
    )
    approval = approval_policy.record_policy_approval(
        prepared,
        confirmed_policy_hash=prepared["policyHash"],
        approved_by="test-user",
        approval_recorded_at="2026-08-21T07:00:01Z",
    )
    active = approval_policy.materialize_policy(prepared, approval)
    approval_policy.install_policy(
        policy_path, active, installed_at="2026-08-21T07:00:02Z"
    )
    return {
        "root": root,
        "panoramaPath": panorama_path,
        "panorama": panorama,
        "eventStore": event_path,
        "policyStore": policy_path,
        "events": events,
        "policy": active,
    }


def _crash_at(expected: str):
    def inject(point: str):
        if point == expected:
            raise adapter.InjectedAdapterCrash(point)

    return inject


def test_v051_current_preview_and_execute_are_zero_use_no_op(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)

    result = adapter.preview(
        workspace["root"], workspace["panoramaPath"], POLICY_ID, previewed_at=AT
    )
    executed = adapter.execute(
        workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
    )

    assert result["status"] == "no_recovery_needed"
    assert executed["status"] == "no_recovery_needed"
    state = approval_policy.inspect_policy_state(workspace["policyStore"], POLICY_ID)
    assert state["ledger"]["nextUseNumber"] == 1
    assert state["ledger"]["pending"] is None
    assert adapter.validate_transactions(workspace["root"])["transactionCount"] == 0


def test_v051_missing_head_execute_binds_transaction_event_receipt_and_ledger(
    tmp_path, project_root
):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()

    preview = adapter.preview(
        workspace["root"], workspace["panoramaPath"], POLICY_ID, previewed_at=AT
    )
    result = adapter.execute(
        workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
    )

    assert preview["status"] == "recoverable"
    assert preview["headInspection"]["status"] == "head_missing"
    assert result["status"] == "finalized"
    assert result["policyBinding"]["useNumber"] == 1
    assert result["observedHead"]["eventId"] == workspace["events"][-1]["eventId"]
    assert result["finalHead"]["eventId"] == result["eventBinding"]["eventId"]
    assert result["receiptBinding"]["receiptId"].startswith("PEX-")
    assert event_store.validate_store(workspace["eventStore"])["valid"] is True
    assert approval_policy.validate_policy_store(workspace["policyStore"])["valid"] is True
    transaction_report = adapter.validate_transactions(workspace["root"])
    assert transaction_report["valid"] is True
    assert transaction_report["counts"]["finalized"] == 1
    receipt = approval_policy.read_execution_receipt(workspace["policyStore"], POLICY_ID, 1)
    assert receipt is not None
    assert receipt["execution"]["outputHash"] == result["outputHash"]
    assert receipt["eventBinding"] == result["eventBinding"]
    audit = event_store._read_json(
        workspace["eventStore"] / "events" / f"{result['eventBinding']['eventId']}.json"
    )
    assert audit["sourceBinding"]["mode"] == "not_applicable"
    assert audit["sourceBinding"]["coverage"] == "not_applicable"


def test_v051_exact_behind_head_is_recoverable(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root, two_events=True)
    head_path = workspace["eventStore"] / "stream-head.json"
    head = json.loads(head_path.read_text(encoding="utf-8"))
    first = workspace["events"][0]
    head.update(
        {
            "sequence": 1,
            "eventId": first["eventId"],
            "eventHash": first["integrity"]["eventHash"],
        }
    )
    head_path.write_text(json.dumps(head, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = adapter.execute(
        workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
    )

    assert result["status"] == "finalized"
    assert result["before"]["status"] == "head_behind"
    assert result["observedHead"]["eventId"] == workspace["events"][1]["eventId"]


def test_v051_mismatch_is_blocked_without_allocating_use(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    head_path = workspace["eventStore"] / "stream-head.json"
    head = json.loads(head_path.read_text(encoding="utf-8"))
    head["eventHash"] = "f" * 64
    head_path.write_text(json.dumps(head, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = adapter.preview(
        workspace["root"], workspace["panoramaPath"], POLICY_ID, previewed_at=AT
    )
    with pytest.raises(adapter.EventHeadPolicyConflictError, match="不可"):
        adapter.execute(
            workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
        )

    assert result["status"] == "blocked"
    state = approval_policy.inspect_policy_state(workspace["policyStore"], POLICY_ID)
    assert state["ledger"]["nextUseNumber"] == 1
    assert adapter.validate_transactions(workspace["root"])["transactionCount"] == 0


def test_v051_crash_after_allocation_resumes_same_use(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()

    with pytest.raises(adapter.InjectedAdapterCrash, match="after_allocated"):
        adapter.execute(
            workspace["root"],
            workspace["panoramaPath"],
            POLICY_ID,
            executed_at=AT,
            fault_injector=_crash_at("after_allocated"),
        )
    report = adapter.validate_transactions(workspace["root"])
    assert report["counts"]["allocated"] == 1
    transaction_path = next(
        (workspace["root"] / adapter.OPERATIONS_REF).glob("EHR-*.json")
    )
    transaction_id = json.loads(transaction_path.read_text(encoding="utf-8"))["transactionId"]

    result = adapter.resume(
        workspace["root"],
        workspace["panoramaPath"],
        POLICY_ID,
        transaction_id,
        resumed_at="2026-08-21T08:00:01Z",
    )

    assert result["status"] == "finalized"
    assert result["policyBinding"]["useNumber"] == 1
    state = approval_policy.inspect_policy_state(workspace["policyStore"], POLICY_ID)
    assert state["ledger"]["nextUseNumber"] == 2
    assert state["ledger"]["pending"] is None


def test_v051_crash_after_effect_resumes_without_second_recovery(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()

    with pytest.raises(adapter.InjectedAdapterCrash, match="after_effect"):
        adapter.execute(
            workspace["root"],
            workspace["panoramaPath"],
            POLICY_ID,
            executed_at=AT,
            fault_injector=_crash_at("after_effect"),
        )
    transaction_path = next(
        (workspace["root"] / adapter.OPERATIONS_REF).glob("EHR-*.json")
    )
    pending_tx = json.loads(transaction_path.read_text(encoding="utf-8"))
    observed_hash = pending_tx["outputHash"]

    # A normal execute retry discovers the non-terminal transaction before it
    # interprets the now-current Head as a zero-use no-op.
    result = adapter.execute(
        workspace["root"],
        workspace["panoramaPath"],
        POLICY_ID,
        executed_at="2026-08-21T08:00:01Z",
    )

    assert result["status"] == "finalized"
    assert result["outputHash"] == observed_hash
    assert result["policyBinding"]["useNumber"] == 1
    assert event_store.validate_store(workspace["eventStore"])["eventCount"] == 2


def test_v051_durable_receipt_ledger_failure_uses_core_recovery(
    tmp_path, project_root, monkeypatch
):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()
    original_write_ledger = approval_policy._write_ledger

    def fail_final_ledger(path, ledger):
        if ledger["pending"] is None and ledger["previousReceiptHash"] is not None:
            raise OSError("simulated ledger publication failure")
        return original_write_ledger(path, ledger)

    monkeypatch.setattr(approval_policy, "_write_ledger", fail_final_ledger)
    with pytest.raises(OSError, match="ledger publication"):
        adapter.execute(
            workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
        )
    monkeypatch.setattr(approval_policy, "_write_ledger", original_write_ledger)
    transaction_path = next(
        (workspace["root"] / adapter.OPERATIONS_REF).glob("EHR-*.json")
    )
    pending_tx = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert approval_policy.read_execution_receipt(workspace["policyStore"], POLICY_ID, 1)

    result = adapter.resume(
        workspace["root"],
        workspace["panoramaPath"],
        POLICY_ID,
        pending_tx["transactionId"],
        resumed_at="2026-08-21T08:00:01Z",
    )

    assert result["status"] == "finalized"
    assert approval_policy.validate_policy_store(workspace["policyStore"])["valid"] is True


def test_v051_durable_audit_event_head_failure_is_reconciled(
    tmp_path, project_root, monkeypatch
):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()
    original_atomic_write = event_store.atomic_write
    head_writes = 0

    def fail_audit_head(path, text):
        nonlocal head_writes
        if path.name == "stream-head.json":
            head_writes += 1
            if head_writes == 2:
                raise OSError("simulated audit head publication failure")
        return original_atomic_write(path, text)

    monkeypatch.setattr(event_store, "atomic_write", fail_audit_head)
    with pytest.raises(event_store.EventStoreError, match="recovery-required"):
        adapter.execute(
            workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
        )
    monkeypatch.setattr(event_store, "atomic_write", original_atomic_write)
    assert event_store.inspect_head_state(workspace["eventStore"])["status"] == "head_behind"
    transaction_path = next(
        (workspace["root"] / adapter.OPERATIONS_REF).glob("EHR-*.json")
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))

    result = adapter.resume(
        workspace["root"],
        workspace["panoramaPath"],
        POLICY_ID,
        transaction["transactionId"],
        resumed_at="2026-08-21T08:00:01Z",
    )

    assert result["status"] == "finalized"
    assert event_store.validate_store(workspace["eventStore"])["valid"] is True
    assert approval_policy.validate_policy_store(workspace["policyStore"])["valid"] is True
    assert event_store.validate_store(workspace["eventStore"])["eventCount"] == 2


def test_v051_finalize_transaction_failure_reconciles_completed_core(
    tmp_path, project_root, monkeypatch
):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()
    original_atomic_write = adapter.atomic_write
    transaction_writes = 0

    def fail_finalize(path, text):
        nonlocal transaction_writes
        if path.suffix == ".json" and path.parent.name == "event-head-recover":
            transaction_writes += 1
            if transaction_writes == 4:
                raise OSError("simulated transaction finalize failure")
        return original_atomic_write(path, text)

    monkeypatch.setattr(adapter, "atomic_write", fail_finalize)
    with pytest.raises(OSError, match="transaction finalize"):
        adapter.execute(
            workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
        )
    monkeypatch.setattr(adapter, "atomic_write", original_atomic_write)
    assert approval_policy.read_execution_receipt(workspace["policyStore"], POLICY_ID, 1)
    transaction_path = next(
        (workspace["root"] / adapter.OPERATIONS_REF).glob("EHR-*.json")
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert transaction["status"] == "effect_observed"

    result = adapter.resume(
        workspace["root"],
        workspace["panoramaPath"],
        POLICY_ID,
        transaction["transactionId"],
        resumed_at="2026-08-21T08:00:01Z",
    )

    assert result["status"] == "finalized"
    assert result["policyBinding"]["useNumber"] == 1
    assert event_store.validate_store(workspace["eventStore"])["eventCount"] == 2


def test_v051_transaction_hash_tamper_blocks_resume(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()
    with pytest.raises(adapter.InjectedAdapterCrash):
        adapter.execute(
            workspace["root"],
            workspace["panoramaPath"],
            POLICY_ID,
            executed_at=AT,
            fault_injector=_crash_at("after_allocated"),
        )
    transaction_path = next(
        (workspace["root"] / adapter.OPERATIONS_REF).glob("EHR-*.json")
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    transaction["inputHash"] = "f" * 64
    transaction_path.write_text(
        json.dumps(transaction, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(adapter.EventHeadPolicyConflictError, match="State Hash"):
        adapter.resume(
            workspace["root"],
            workspace["panoramaPath"],
            POLICY_ID,
            transaction["transactionId"],
            resumed_at="2026-08-21T08:00:01Z",
        )


def test_v051_policy_scope_drift_fails_before_use(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()
    policy_path = workspace["policyStore"] / "policies" / f"{POLICY_ID}.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["scope"]["artifactRoots"] = [".panorama-work"]
    policy["approvalBinding"]["policyHash"] = approval_policy.compute_policy_hash(policy)
    policy_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(approval_policy.ApprovalPolicyConflictError):
        adapter.preview(
            workspace["root"], workspace["panoramaPath"], POLICY_ID, previewed_at=AT
        )

    # The ledger remains bound to the original approved hash and no Use is allocated.
    ledger = event_store._read_json(
        workspace["policyStore"] / "ledgers" / f"{POLICY_ID}.json"
    )
    assert ledger["nextUseNumber"] == 1
    assert ledger["pending"] is None


def test_v051_expired_policy_is_rejected_before_transaction(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()

    with pytest.raises(adapter.EventHeadPolicyConflictError) as captured:
        adapter.execute(
            workspace["root"],
            workspace["panoramaPath"],
            POLICY_ID,
            executed_at="2026-08-22T07:00:00Z",
        )

    assert captured.value.code == "POLICY_INACTIVE"
    state = approval_policy.inspect_policy_state(workspace["policyStore"], POLICY_ID)
    assert state["ledger"]["nextUseNumber"] == 1
    assert adapter.validate_transactions(workspace["root"])["transactionCount"] == 0


def test_v051_revoked_policy_is_rejected_before_transaction(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    (workspace["eventStore"] / "stream-head.json").unlink()
    approval_policy.revoke_policy(
        workspace["policyStore"],
        POLICY_ID,
        policy_hash=workspace["policy"]["approvalBinding"]["policyHash"],
        revoked_by="test-user",
        revoked_at="2026-08-21T07:59:00Z",
    )

    with pytest.raises(adapter.EventHeadPolicyConflictError) as captured:
        adapter.execute(
            workspace["root"], workspace["panoramaPath"], POLICY_ID, executed_at=AT
        )

    assert captured.value.code == "POLICY_REVOKED"
    assert adapter.validate_transactions(workspace["root"])["transactionCount"] == 0


def test_v051_cli_preview_execute_and_validate(
    tmp_path, project_root, monkeypatch, capsys
):
    workspace = _workspace(tmp_path, project_root)
    monkeypatch.setattr(adapter, "_now", lambda: AT)
    common = [
        "--project-root",
        str(workspace["root"]),
        "--panorama",
        str(workspace["panoramaPath"]),
        "--policy-id",
        POLICY_ID,
        "--json",
    ]

    assert recover_main(["preview", *common]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "no_recovery_needed"
    (workspace["eventStore"] / "stream-head.json").unlink()
    assert recover_main(["execute", *common]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "finalized"
    assert (
        recover_main(
            ["validate", "--project-root", str(workspace["root"]), "--json"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_v051_path_escape_and_policy_id_traversal_are_rejected(tmp_path, project_root):
    workspace = _workspace(tmp_path, project_root)
    outside = tmp_path / "outside-panorama.json"
    shutil.copyfile(workspace["panoramaPath"], outside)

    with pytest.raises(adapter.EventHeadPolicyAdapterError) as escaped:
        adapter.preview(
            workspace["root"], outside, POLICY_ID, previewed_at=AT
        )
    assert escaped.value.code == "PATH_ESCAPE"

    with pytest.raises(adapter.EventHeadPolicyAdapterError) as traversal:
        adapter.preview(
            workspace["root"],
            workspace["panoramaPath"],
            "../POLICY",
            previewed_at=AT,
        )
    assert traversal.value.code == "POLICY_ID_INVALID"
