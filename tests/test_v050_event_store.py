from __future__ import annotations

from copy import deepcopy
import json

import pytest

import event_store

from event_store import (
    EventConflictError,
    EventStoreError,
    compute_event_hash,
    record_request,
    recover_store_head,
    validate_request_safety,
    validate_store,
)
from record_engineering_event import main as record_main
from validate_event_store import main as validate_main


def _request(project_root):
    return json.loads(
        (project_root / "examples" / "engineering-event-request.v0.1.json").read_text(
            encoding="utf-8"
        )
    )


def test_v050_record_chain_and_idempotency(tmp_path, project_root):
    store = tmp_path / "event-store" / "v0.1"
    first_request = _request(project_root)
    first, created = record_request(
        store, first_request, recorded_at="2026-08-20T02:00:01Z"
    )
    duplicate, duplicate_created = record_request(
        store, first_request, recorded_at="2026-08-20T02:00:09Z"
    )
    second_request = deepcopy(first_request)
    second_request["idempotencyKey"] = "e" * 64
    second_request["eventType"] = "artifact.invalidated"
    second_request["outcome"] = {
        "status": "failed",
        "labelStrength": "observed",
        "summary": "The candidate artifact is stale.",
    }
    second, second_created = record_request(
        store, second_request, recorded_at="2026-08-20T02:00:10Z"
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate == first
    assert second_created is True
    assert second["stream"]["sequence"] == 2
    assert second["stream"]["previousEventHash"] == first["integrity"]["eventHash"]
    report = validate_store(store)
    assert report["valid"] is True
    assert report["eventCount"] == 2


def test_v050_same_idempotency_identity_with_different_request_is_conflict(
    tmp_path, project_root
):
    store = tmp_path / "event-store" / "v0.1"
    request = _request(project_root)
    record_request(store, request, recorded_at="2026-08-20T02:00:01Z")
    changed = deepcopy(request)
    changed["payload"]["warningCount"] = 1

    with pytest.raises(EventConflictError, match="不同 Request Hash"):
        record_request(store, changed, recorded_at="2026-08-20T02:00:02Z")


def test_v050_tamper_is_detected(tmp_path, project_root):
    store = tmp_path / "event-store" / "v0.1"
    event, _ = record_request(
        store, _request(project_root), recorded_at="2026-08-20T02:00:01Z"
    )
    path = store / "events" / f"{event['eventId']}.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["outcome"]["summary"] = "tampered"
    path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = validate_store(store)
    assert report["valid"] is False
    assert any("REQUEST_HASH" in error for error in report["errors"])
    assert any("EVENT_HASH" in error for error in report["errors"])


def test_v050_head_drift_requires_explicit_recovery(tmp_path, project_root):
    store = tmp_path / "event-store" / "v0.1"
    first, _ = record_request(
        store, _request(project_root), recorded_at="2026-08-20T02:00:01Z"
    )
    second_request = deepcopy(_request(project_root))
    second_request["idempotencyKey"] = "e" * 64
    second_request["eventType"] = "artifact.invalidated"
    second, _ = record_request(
        store, second_request, recorded_at="2026-08-20T02:00:02Z"
    )
    head_path = store / "stream-head.json"
    head = json.loads(head_path.read_text(encoding="utf-8"))
    head.update(
        {
            "sequence": 1,
            "eventId": first["eventId"],
            "eventHash": first["integrity"]["eventHash"],
        }
    )
    head_path.write_text(json.dumps(head, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert validate_store(store)["valid"] is False
    with pytest.raises(EventConflictError, match="写入已停止"):
        third = deepcopy(second_request)
        third["idempotencyKey"] = "f" * 64
        record_request(store, third, recorded_at="2026-08-20T02:00:03Z")
    recovered = recover_store_head(store)
    assert recovered["eventId"] == second["eventId"]
    assert validate_store(store)["valid"] is True


def test_v050_missing_head_can_be_recovered_from_valid_chain(tmp_path, project_root):
    store = tmp_path / "event-store" / "v0.1"
    event, _ = record_request(
        store, _request(project_root), recorded_at="2026-08-20T02:00:01Z"
    )
    (store / "stream-head.json").unlink()

    assert validate_store(store)["valid"] is False
    recovered = recover_store_head(store)
    assert recovered["eventId"] == event["eventId"]
    assert validate_store(store)["valid"] is True


def test_v050_event_written_head_failure_enters_recovery_required(
    tmp_path, project_root, monkeypatch
):
    store = tmp_path / "event-store" / "v0.1"
    original_atomic_write = event_store.atomic_write
    head_writes = 0

    def fail_second_head_write(path, text):
        nonlocal head_writes
        if path.name == "stream-head.json":
            head_writes += 1
            if head_writes == 2:
                raise OSError("simulated head publication failure")
        return original_atomic_write(path, text)

    monkeypatch.setattr(event_store, "atomic_write", fail_second_head_write)
    with pytest.raises(EventStoreError, match="recovery-required"):
        record_request(
            store, _request(project_root), recorded_at="2026-08-20T02:00:01Z"
        )
    monkeypatch.setattr(event_store, "atomic_write", original_atomic_write)

    report = validate_store(store)
    assert report["valid"] is False
    assert report["eventCount"] == 1
    recovered = recover_store_head(store)
    assert recovered["sequence"] == 1
    assert validate_store(store)["valid"] is True


def test_v050_existing_lock_blocks_writer(tmp_path, project_root):
    store = tmp_path / "event-store" / "v0.1"
    store.mkdir(parents=True)
    (store / ".event-store.lock").write_text("other-writer", encoding="ascii")

    with pytest.raises(EventConflictError, match="另一个 Writer"):
        record_request(store, _request(project_root))


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda value: value["payload"].update({"api_key": "redacted"}), "敏感字段键"),
        (
            lambda value: value["outcome"].update(
                {"summary": "Authorization: Bearer abcdefghijklmnopqrstuv"}
            ),
            "敏感明文",
        ),
        (
            lambda value: value["evidenceBindings"][0].update(
                {"ref": "D:/private/report.json"}
            ),
            "绝对路径",
        ),
        (lambda value: value.update({"trainingEligibility": {"sft": True}}), "字段不匹配"),
    ],
)
def test_v050_request_safety_rejects_sensitive_or_derived_fields(
    project_root, mutate, expected
):
    request = _request(project_root)
    mutate(request)
    with pytest.raises(EventStoreError, match=expected):
        validate_request_safety(request)


def test_v050_cli_record_validate_and_recover(tmp_path, project_root, capsys):
    request_path = project_root / "examples" / "engineering-event-request.v0.1.json"
    store = tmp_path / "event-store" / "v0.1"

    assert (
        record_main(
            [
                str(request_path),
                "--store",
                str(store),
                "--recorded-at",
                "2026-08-20T02:00:01Z",
                "--json",
            ]
        )
        == 0
    )
    assert '"created": true' in capsys.readouterr().out
    assert validate_main([str(store), "--json"]) == 0
    assert '"valid": true' in capsys.readouterr().out


def test_v050_recomputed_event_hash_changes_after_semantic_change(tmp_path, project_root):
    store = tmp_path / "event-store" / "v0.1"
    event, _ = record_request(
        store, _request(project_root), recorded_at="2026-08-20T02:00:01Z"
    )
    changed = deepcopy(event)
    changed["payload"]["errorCount"] = 1

    assert compute_event_hash(changed) != event["integrity"]["eventHash"]
