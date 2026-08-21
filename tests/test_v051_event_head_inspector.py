from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import time
import tracemalloc

import pytest

from event_store import (
    EventStoreError,
    inspect_head_state,
    record_request,
    recover_store_head,
    validate_store,
)


def _request(project_root):
    return json.loads(
        (project_root / "examples" / "engineering-event-request.v0.1.json").read_text(
            encoding="utf-8"
        )
    )


def _two_event_store(tmp_path, project_root):
    store = tmp_path / "event-store" / "v0.1"
    first, _ = record_request(
        store, _request(project_root), recorded_at="2026-08-21T00:00:01Z"
    )
    second_request = deepcopy(_request(project_root))
    second_request["idempotencyKey"] = "f" * 64
    second_request["eventType"] = "artifact.invalidated"
    second, _ = record_request(
        store, second_request, recorded_at="2026-08-21T00:00:02Z"
    )
    return store, first, second


def _write_head(store, **changes):
    path = store / "stream-head.json"
    head = json.loads(path.read_text(encoding="utf-8"))
    head.update(changes)
    path.write_text(json.dumps(head, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_v051_inspector_classifies_current_missing_and_exact_behind(tmp_path, project_root):
    store, first, second = _two_event_store(tmp_path, project_root)

    current = inspect_head_state(store)
    assert current["status"] == "current"
    assert current["recoverable"] is False
    assert current["expectedTail"]["eventId"] == second["eventId"]

    _write_head(
        store,
        sequence=first["stream"]["sequence"],
        eventId=first["eventId"],
        eventHash=first["integrity"]["eventHash"],
    )
    behind = inspect_head_state(store)
    assert behind["status"] == "head_behind"
    assert behind["recoverable"] is True
    recovered = recover_store_head(store, recovered_at="2026-08-21T00:00:03Z")
    assert recovered["eventId"] == second["eventId"]
    assert validate_store(store)["valid"] is True

    (store / "stream-head.json").unlink()
    missing = inspect_head_state(store)
    assert missing["status"] == "head_missing"
    assert missing["recoverable"] is True


@pytest.mark.parametrize(
    ("changes", "expected_status"),
    [
        ({"sequence": 3, "eventId": None, "eventHash": None}, "head_ahead"),
        ({"eventHash": "0" * 64}, "head_mismatch"),
        ({"projectId": "OTHER"}, "head_mismatch"),
        ({"streamId": "project-OTHER"}, "head_mismatch"),
        ({"epoch": 2}, "head_mismatch"),
    ],
)
def test_v051_inspector_rejects_ahead_or_invented_head(
    tmp_path, project_root, changes, expected_status
):
    store, _, _ = _two_event_store(tmp_path, project_root)
    _write_head(store, **changes)

    report = inspect_head_state(store)

    assert report["status"] == expected_status
    assert report["recoverable"] is False
    with pytest.raises(EventStoreError, match="不满足确定性恢复条件"):
        recover_store_head(store)


def test_v051_inspector_rejects_invalid_head_and_tampered_chain(tmp_path, project_root):
    store, _, second = _two_event_store(tmp_path, project_root)
    head_path = store / "stream-head.json"
    head_path.write_text("not-json", encoding="utf-8")
    assert inspect_head_state(store)["status"] == "head_invalid"
    with pytest.raises(EventStoreError, match="不满足确定性恢复条件"):
        recover_store_head(store)

    head_path.unlink()
    event_path = store / "events" / f"{second['eventId']}.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["outcome"]["summary"] = "tampered"
    event_path.write_text(
        json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = inspect_head_state(store)
    assert report["status"] == "chain_invalid"
    assert report["recoverable"] is False


def test_v051_inspector_treats_redaction_epoch_as_ambiguous(tmp_path, project_root):
    store, _, _ = _two_event_store(tmp_path, project_root)
    redaction = store / "redactions" / "RED-1.json"
    redaction.write_text("{}\n", encoding="utf-8")

    report = inspect_head_state(store)

    assert report["status"] == "ambiguous"
    assert report["recoverable"] is False
    assert any("REDACTION_EPOCH_AMBIGUOUS" in item for item in report["errors"])


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation requires privileges")
def test_v051_inspector_rejects_symlinked_event(tmp_path, project_root):
    store, _, second = _two_event_store(tmp_path, project_root)
    target = store / "events" / f"{second['eventId']}.json"
    outside = tmp_path / "outside.json"
    target.replace(outside)
    target.symlink_to(outside)

    report = inspect_head_state(store)

    assert report["status"] == "chain_invalid"
    assert any("EVENT_SYMLINK" in item for item in report["errors"])


def test_v051_1000_event_chain_capacity_evidence(tmp_path, project_root):
    import event_store

    store = tmp_path / "event-store" / "v0.1"
    events_dir = store / "events"
    events_dir.mkdir(parents=True)
    request = _request(project_root)
    project_id = request["projectBinding"]["projectId"]
    stream_id = f"project-{project_id}"
    previous_hash = None
    last = None
    generation_started = time.perf_counter()
    for sequence in range(1, 1001):
        item = deepcopy(request)
        item["idempotencyKey"] = hashlib.sha256(
            f"capacity-{sequence}".encode("ascii")
        ).hexdigest()
        event = event_store.build_event(
            item,
            stream_id=stream_id,
            epoch=1,
            sequence=sequence,
            previous_event_hash=previous_hash,
            recorded_at="2026-08-21T09:00:00Z",
        )
        (events_dir / f"{event['eventId']}.json").write_text(
            json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        previous_hash = event["integrity"]["eventHash"]
        last = event
    generation_seconds = time.perf_counter() - generation_started

    tracemalloc.start()
    recovery_started = time.perf_counter()
    recovered = recover_store_head(store, recovered_at="2026-08-21T09:00:01Z")
    recovery_seconds = time.perf_counter() - recovery_started
    validation_started = time.perf_counter()
    report = validate_store(store)
    validation_seconds = time.perf_counter() - validation_started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert last is not None
    assert recovered["eventId"] == last["eventId"]
    assert report["valid"] is True
    assert report["eventCount"] == 1000
    print(
        "V051_CAPACITY "
        + json.dumps(
            {
                "eventCount": 1000,
                "generationSeconds": round(generation_seconds, 3),
                "recoverySeconds": round(recovery_seconds, 3),
                "validationSeconds": round(validation_seconds, 3),
                "peakTrackedMiB": round(peak_bytes / (1024 * 1024), 3),
                "sloDeclared": False,
            },
            sort_keys=True,
        )
    )
