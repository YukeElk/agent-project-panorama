"""Read-only, hash-bound Engineering Event checkpoint projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from event_store import MAX_EVENT_BYTES, validate_event, validate_store
from panorama_io import compute_canonical_hash


class EventProjectionError(ValueError):
    """Raised when an Event Store cannot form an exact projection checkpoint."""


def validate_event_checkpoint(checkpoint: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "formatVersion",
        "checkpointId",
        "checkpointHash",
        "projectId",
        "streamId",
        "epoch",
        "asOfSequence",
        "tailEventId",
        "tailEventHash",
        "asOf",
        "eventBindings",
        "events",
    }
    if set(checkpoint) != required:
        return ["Event Checkpoint 字段不匹配。"]
    events = checkpoint.get("events")
    if not isinstance(events, list) or not events:
        return ["Event Checkpoint events 必须是非空数组。"]
    for event in events:
        if not isinstance(event, dict):
            errors.append("Event Checkpoint 包含非 object Event。")
            continue
        event_errors = validate_event(event)
        errors.extend(f"{event.get('eventId')}: {item}" for item in event_errors)
    if errors:
        return errors
    ordered = sorted(
        events, key=lambda item: item.get("stream", {}).get("sequence", 0)
    )
    if events != ordered:
        errors.append("Event Checkpoint events 未按 sequence 排序。")
    bindings = [
        {
            "eventId": event.get("eventId"),
            "eventHash": event.get("integrity", {}).get("eventHash"),
            "sequence": event.get("stream", {}).get("sequence"),
        }
        for event in events
    ]
    if checkpoint.get("eventBindings") != bindings:
        errors.append("Event Checkpoint eventBindings 与 events 不匹配。")
    tail = events[-1]
    semantics = {
        "projectId": checkpoint.get("projectId"),
        "streamId": checkpoint.get("streamId"),
        "epoch": checkpoint.get("epoch"),
        "asOfSequence": checkpoint.get("asOfSequence"),
        "tailEventId": checkpoint.get("tailEventId"),
        "tailEventHash": checkpoint.get("tailEventHash"),
        "eventBindings": bindings,
    }
    expected_hash = compute_canonical_hash(semantics)
    if checkpoint.get("checkpointHash") != expected_hash:
        errors.append("Event Checkpoint Hash 不匹配。")
    if checkpoint.get("checkpointId") != f"EVCP-{expected_hash[:24].upper()}":
        errors.append("Event Checkpoint ID 不匹配。")
    if checkpoint.get("asOfSequence") != tail.get("stream", {}).get("sequence"):
        errors.append("Event Checkpoint Tail Sequence 不匹配。")
    if checkpoint.get("tailEventId") != tail.get("eventId"):
        errors.append("Event Checkpoint Tail Event ID 不匹配。")
    if checkpoint.get("tailEventHash") != tail.get("integrity", {}).get("eventHash"):
        errors.append("Event Checkpoint Tail Event Hash 不匹配。")
    if checkpoint.get("asOf") != tail.get("recordedAt"):
        errors.append("Event Checkpoint asOf 不匹配。")
    if any(
        event.get("projectBinding", {}).get("projectId")
        != checkpoint.get("projectId")
        for event in events
    ):
        errors.append("Event Checkpoint Project Binding 不一致。")
    return errors


def _read_event(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise EventProjectionError(f"Event 文件不允许是符号链接：{path.name}")
    try:
        if path.stat().st_size > MAX_EVENT_BYTES:
            raise EventProjectionError(f"Event 文件超过大小上限：{path.name}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventProjectionError(f"Event 文件无效 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise EventProjectionError(f"Event 文件必须是 JSON object：{path.name}")
    errors = validate_event(value)
    if errors:
        raise EventProjectionError(
            f"Event {path.name} 无效：" + "；".join(errors[:20])
        )
    if path.name != f"{value['eventId']}.json":
        raise EventProjectionError(f"Event 文件名与 eventId 不匹配：{path.name}")
    return value


def load_event_checkpoint(store: Path, *, project_id: str) -> dict[str, Any]:
    """Load a current, fully validated Event chain into an in-memory checkpoint.

    The checkpoint hash covers an ordered index of immutable Event IDs and
    hashes. Full Event records are carried only as verified compiler input.
    """

    store = Path(store)
    report = validate_store(store)
    if not report["valid"] or report["headStatus"] != "current":
        details = "；".join(report["errors"][:20]) or report["headStatus"]
        raise EventProjectionError("Event Store 不能形成 current checkpoint：" + details)
    head = report["head"]
    if not isinstance(head, dict) or head.get("projectId") != project_id:
        raise EventProjectionError("Event Store projectId 与 Panorama Core 不匹配。")
    if report["eventCount"] < 1:
        raise EventProjectionError("空 Event Store 不能形成 Sequence checkpoint。")

    events_dir = store / "events"
    events = [_read_event(path) for path in events_dir.glob("*.json")]
    events.sort(key=lambda item: item["stream"]["sequence"])
    if len(events) != report["eventCount"]:
        raise EventProjectionError("Event Store 计数在读取期间发生变化。")
    bindings = [
        {
            "eventId": event["eventId"],
            "eventHash": event["integrity"]["eventHash"],
            "sequence": event["stream"]["sequence"],
        }
        for event in events
    ]
    semantics = {
        "projectId": project_id,
        "streamId": head["streamId"],
        "epoch": head["epoch"],
        "asOfSequence": head["sequence"],
        "tailEventId": head["eventId"],
        "tailEventHash": head["eventHash"],
        "eventBindings": bindings,
    }
    checkpoint_hash = compute_canonical_hash(semantics)
    checkpoint = {
        "formatVersion": "panorama-event-checkpoint.v0.1",
        "checkpointId": f"EVCP-{checkpoint_hash[:24].upper()}",
        "checkpointHash": checkpoint_hash,
        "projectId": project_id,
        "streamId": head["streamId"],
        "epoch": head["epoch"],
        "asOfSequence": head["sequence"],
        "tailEventId": head["eventId"],
        "tailEventHash": head["eventHash"],
        "asOf": events[-1]["recordedAt"],
        "eventBindings": bindings,
        "events": events,
    }
    errors = validate_event_checkpoint(checkpoint)
    if errors:
        raise EventProjectionError("Event Checkpoint 无效：" + "；".join(errors[:20]))
    return checkpoint
