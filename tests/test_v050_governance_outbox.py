from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

import apply_patch as patch_module
import governance_outbox as outbox_module
from apply_patch import apply_update_package, compute_proposal_hash
from event_store import EventStoreError, validate_store
from governance_outbox import (
    GovernanceOutboxError,
    GovernanceRecoveryRequired,
    prepare_governance_outbox,
    resolve_governance_outbox,
    validate_outbox,
    validate_outbox_store,
)
from panorama_io import compute_data_hash, extract_data, replace_data


def _html(tmp_path: Path, template_path: Path, data: dict) -> Path:
    path = tmp_path / "governed-panorama.html"
    replace_data(template_path, data, path)
    return path


def _package(data: dict, *, focus: str = "Governed Outbox update") -> dict:
    package = {
        "baseRevision": data["meta"]["revision"],
        "baseDataHash": compute_data_hash(data),
        "operations": [
            {"op": "replace", "path": "/intent/currentFocus", "value": focus}
        ],
        "changeRecords": [],
        "reviewDraft": {},
        "updateBatchDraft": {},
        "guidanceDraft": {},
    }
    package["proposalHash"] = compute_proposal_hash(package)
    package["approval"] = {
        "status": "approved",
        "approvedBy": "outbox-test-user",
        "approvedAt": "2026-08-20T10:00:00Z",
        "proposalHash": package["proposalHash"],
    }
    return package


def test_opt_in_governance_apply_finalizes_event_and_outbox(
    tmp_path, template_path, reference_data, schema_path
):
    html = _html(tmp_path, template_path, reference_data)
    event_store = tmp_path / "event-store"
    outbox_store = tmp_path / "outbox-store"

    _, updated, _ = apply_update_package(
        html,
        _package(reference_data),
        schema_path,
        event_store_path=event_store,
        outbox_store=outbox_store,
    )

    assert extract_data(html) == updated
    outbox_report = validate_outbox_store(
        outbox_store, event_store_path=event_store
    )
    assert outbox_report["valid"] is True
    assert outbox_report["counts"]["finalized"] == 1
    assert outbox_report["recoveryRequired"] is False
    event_report = validate_store(event_store)
    assert event_report["valid"] is True
    assert event_report["eventCount"] == 1
    event_path = next((event_store / "events").glob("*.json"))
    event = json.loads(event_path.read_text(encoding="utf-8"))
    transition = event["extensions"]["panoramaProjection"][
        "lifecycleTransitions"
    ][0]
    assert transition["fromState"] == "approved_candidate"
    assert transition["toState"] == "applied"
    assert transition["subjectId"] == _package(reference_data)["proposalHash"]
    outbox = next(outbox_store.glob("*.json")).read_text(encoding="utf-8")
    assert _package(reference_data)["operations"][0]["value"] not in outbox


def test_finalized_outbox_requires_exact_durable_event_binding(
    tmp_path, template_path, reference_data, schema_path
):
    html = _html(tmp_path, template_path, reference_data)
    event_store = tmp_path / "event-store"
    outbox_store = tmp_path / "outbox-store"
    apply_update_package(
        html,
        _package(reference_data),
        schema_path,
        event_store_path=event_store,
        outbox_store=outbox_store,
    )
    event_path = next((event_store / "events").glob("*.json"))
    event_path.unlink()
    report = validate_outbox_store(outbox_store, event_store_path=event_store)
    assert report["valid"] is False
    assert report["recoveryRequired"] is True
    assert any("durable Event" in item for item in report["errors"])


def test_event_failure_keeps_applied_panorama_pending_and_blocks_next_write(
    monkeypatch, tmp_path, template_path, reference_data, schema_path
):
    html = _html(tmp_path, template_path, reference_data)
    event_store = tmp_path / "event-store"
    outbox_store = tmp_path / "outbox-store"
    package = _package(reference_data, focus="First committed state")
    real_record = outbox_module.event_store.record_request

    def fail_event(*args, **kwargs):
        raise EventStoreError("injected recorder failure")

    monkeypatch.setattr(outbox_module.event_store, "record_request", fail_event)
    with pytest.raises(GovernanceRecoveryRequired, match="Event"):
        apply_update_package(
            html,
            package,
            schema_path,
            event_store_path=event_store,
            outbox_store=outbox_store,
        )

    committed = extract_data(html)
    assert committed["intent"]["currentFocus"] == "First committed state"
    report = validate_outbox_store(outbox_store)
    assert report["valid"] is True
    assert report["counts"]["pending"] == 1
    assert report["recoveryRequired"] is True

    with pytest.raises(GovernanceRecoveryRequired, match="pending"):
        apply_update_package(
            html,
            _package(committed, focus="Must remain blocked"),
            schema_path,
            event_store_path=event_store,
            outbox_store=outbox_store,
        )

    monkeypatch.setattr(outbox_module.event_store, "record_request", real_record)
    outbox_path = next(outbox_store.glob("*.json"))
    recovered = resolve_governance_outbox(
        outbox_path,
        panorama_path=html,
        event_store_path=event_store,
        resolved_at="2026-08-20T10:00:02Z",
    )
    assert recovered["status"] == "finalized"
    assert validate_outbox_store(outbox_store)["recoveryRequired"] is False
    assert validate_store(event_store)["eventCount"] == 1


def test_apply_failure_reconciles_exact_base_as_abandoned(
    monkeypatch, tmp_path, template_path, reference_data, schema_path
):
    html = _html(tmp_path, template_path, reference_data)
    before = html.read_bytes()
    event_store = tmp_path / "event-store"
    outbox_store = tmp_path / "outbox-store"
    real_atomic = patch_module.atomic_write

    def fail_source_write(path, text):
        if Path(path) == html:
            raise OSError("injected source commit failure")
        return real_atomic(path, text)

    monkeypatch.setattr(patch_module, "atomic_write", fail_source_write)
    with pytest.raises(OSError, match="injected"):
        apply_update_package(
            html,
            _package(reference_data),
            schema_path,
            event_store_path=event_store,
            outbox_store=outbox_store,
        )
    assert html.read_bytes() == before
    report = validate_outbox_store(outbox_store)
    assert report["valid"] is True
    assert report["counts"]["abandoned"] == 1
    assert report["recoveryRequired"] is False
    assert not event_store.exists()


def test_finalize_write_failure_recovers_idempotent_event(
    monkeypatch, tmp_path, template_path, reference_data, schema_path
):
    html = _html(tmp_path, template_path, reference_data)
    event_store = tmp_path / "event-store"
    outbox_store = tmp_path / "outbox-store"
    real_write = outbox_module._write_json

    def fail_final_outbox(path, value, *, create_only=False):
        if not create_only and value.get("status") == "finalized":
            raise OSError("injected outbox finalize failure")
        return real_write(path, value, create_only=create_only)

    monkeypatch.setattr(outbox_module, "_write_json", fail_final_outbox)
    with pytest.raises(OSError, match="injected"):
        apply_update_package(
            html,
            _package(reference_data),
            schema_path,
            event_store_path=event_store,
            outbox_store=outbox_store,
        )
    assert validate_store(event_store)["eventCount"] == 1
    assert validate_outbox_store(outbox_store)["counts"]["pending"] == 1

    monkeypatch.setattr(outbox_module, "_write_json", real_write)
    outbox_path = next(outbox_store.glob("*.json"))
    resolved = resolve_governance_outbox(
        outbox_path,
        panorama_path=html,
        event_store_path=event_store,
        resolved_at="2026-08-20T10:00:03Z",
    )
    assert resolved["status"] == "finalized"
    assert validate_store(event_store)["eventCount"] == 1


def test_recovery_conflict_is_persisted_and_fail_closed(
    tmp_path, template_path, reference_data, schema_path
):
    html = _html(tmp_path, template_path, reference_data)
    event_store = tmp_path / "event-store"
    outbox_store = tmp_path / "outbox-store"
    package = _package(reference_data)
    from apply_patch import compose_updated_data
    from governance_outbox import prepare_governance_outbox

    expected = compose_updated_data(reference_data, package)
    outbox_path = prepare_governance_outbox(
        outbox_store,
        current=reference_data,
        updated=expected,
        proposal_hash=package["proposalHash"],
        created_at="2026-08-20T10:00:00Z",
    )
    conflicting = deepcopy(reference_data)
    conflicting["meta"]["revision"] += 8
    conflicting["intent"]["currentFocus"] = "Unrelated conflicting state"
    replace_data(html, conflicting, html)
    result = resolve_governance_outbox(
        outbox_path,
        panorama_path=html,
        event_store_path=event_store,
        resolved_at="2026-08-20T10:00:04Z",
    )
    assert result["status"] == "conflict"
    assert validate_outbox_store(outbox_store)["recoveryRequired"] is True
    assert not event_store.exists()


def test_outbox_state_hash_rejects_forged_terminal_transition(
    tmp_path, reference_data
):
    updated = deepcopy(reference_data)
    updated["meta"]["revision"] += 1
    updated["intent"]["currentFocus"] = "Expected state"
    path = prepare_governance_outbox(
        tmp_path / "outbox-store",
        current=reference_data,
        updated=updated,
        proposal_hash="f" * 64,
        created_at="2026-08-20T10:00:00Z",
    )
    forged = json.loads(path.read_text(encoding="utf-8"))
    forged["status"] = "abandoned"
    forged["updatedAt"] = "2026-08-20T10:00:01Z"
    forged["observedResult"] = {
        "revision": reference_data["meta"]["revision"],
        "dataHash": compute_data_hash(reference_data),
        "observedAt": "2026-08-20T10:00:01Z",
    }
    forged["resolution"] = {
        "eventId": None,
        "eventHash": None,
        "reason": "Forged terminal state without runtime reconciliation.",
    }
    errors = validate_outbox(forged)
    assert any("STATE_HASH" in item for item in errors)


def test_outbox_json_depth_is_bounded(tmp_path):
    nested = {}
    cursor = nested
    for _ in range(25):
        cursor["child"] = {}
        cursor = cursor["child"]
    with pytest.raises(GovernanceOutboxError, match="嵌套深度"):
        outbox_module._write_json(tmp_path / "too-deep.json", nested)
