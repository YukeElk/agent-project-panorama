"""Project-local Engineering Event Store for the V0.5 Foundation.

The store is deliberately independent from the formal Panorama JSON.  It owns
one immutable event file per event plus a rebuildable stream-head index.  No
existing Panorama transaction calls this module yet.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterator

from jsonschema import Draft202012Validator, FormatChecker

from panorama_io import atomic_write, compute_canonical_hash


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schema" / "engineering-event.schema.v0.1.json"
EVENT_FORMAT = "panorama-engineering-event.v0.1"
REQUEST_FORMAT = "panorama-engineering-event-request.v0.1"
HEAD_FORMAT = "panorama-engineering-event-stream-head.v0.1"
MAX_EVENT_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 20

REQUEST_FIELDS = {
    "requestVersion",
    "idempotencyKey",
    "eventType",
    "occurredAt",
    "timePrecision",
    "actor",
    "projectBinding",
    "sourceBinding",
    "correlation",
    "subjectRefs",
    "authority",
    "confidence",
    "payload",
    "evidenceBindings",
    "informationGaps",
    "outcome",
    "privacy",
    "extensions",
}

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "private_key",
    "client_secret",
    "access_key",
}
SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|client[_-]?secret)"
        r"\s*[:=]\s*(?!\[?redacted\]?|removed\b)\S+"
    ),
)
WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


class EventStoreError(RuntimeError):
    """The event store or event request is invalid."""


class EventConflictError(EventStoreError):
    """An immutable identity or stream state conflicts with the request."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_NESTING_DEPTH:
        return depth
    if isinstance(value, dict):
        return max([depth] + [_json_depth(item, depth + 1) for item in value.values()])
    if isinstance(value, list):
        return max([depth] + [_json_depth(item, depth + 1) for item in value])
    return depth


def _read_json(path: Path, *, max_bytes: int = MAX_EVENT_BYTES) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EventStoreError(f"无法读取 {path}: {exc}") from exc
    if size > max_bytes:
        raise EventStoreError(f"JSON 制品超过 {max_bytes} bytes: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventStoreError(f"JSON 制品无效 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EventStoreError(f"JSON 制品必须是对象: {path}")
    if _json_depth(value) > MAX_NESTING_DEPTH:
        raise EventStoreError(f"JSON 制品嵌套超过 {MAX_NESTING_DEPTH} 层: {path.name}")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_recorded_at(value: str | None) -> str:
    candidate = value or _now()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventStoreError("recordedAt 必须是带时区的 RFC 3339 时间。") from exc
    if parsed.tzinfo is None:
        raise EventStoreError("recordedAt 必须包含时区。")
    parsed = parsed.astimezone(timezone.utc)
    timespec = "milliseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


def _validate_redaction(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in SENSITIVE_KEYS:
                raise EventStoreError(f"Event Request 包含敏感字段键：{path}.{key}")
            _validate_redaction(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_redaction(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        if WINDOWS_ABSOLUTE.search(value):
            raise EventStoreError(f"Event Request 包含本机绝对路径：{path}")
        for pattern in SENSITIVE_PATTERNS:
            if pattern.search(value):
                raise EventStoreError(f"Event Request 疑似包含敏感明文：{path}")


def _validate_relative_path(ref: str, project_root: Path | None) -> None:
    normalized = ref.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or ".." in pure.parts or WINDOWS_ABSOLUTE.search(ref):
        raise EventStoreError(f"Evidence relative_path 非法：{ref!r}")
    if project_root is None:
        return
    root = project_root.resolve(strict=True)
    candidate = root.joinpath(*pure.parts)
    if not candidate.exists():
        return
    if candidate.is_symlink():
        raise EventStoreError(f"Evidence relative_path 不允许符号链接：{ref!r}")
    try:
        candidate.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise EventStoreError(f"Evidence relative_path 越出 project root：{ref!r}") from exc


def validate_request_safety(
    request: dict[str, Any], *, project_root: Path | None = None
) -> None:
    if set(request) != REQUEST_FIELDS:
        missing = sorted(REQUEST_FIELDS - set(request))
        extra = sorted(set(request) - REQUEST_FIELDS)
        raise EventStoreError(f"Event Request 字段不匹配；missing={missing}, extra={extra}")
    if request.get("requestVersion") != REQUEST_FORMAT:
        raise EventStoreError(f"requestVersion 必须是 {REQUEST_FORMAT}")
    encoded = _canonical_bytes(request)
    if len(encoded) > MAX_EVENT_BYTES:
        raise EventStoreError(f"Event Request 超过 {MAX_EVENT_BYTES} bytes。")
    if _json_depth(request) > MAX_NESTING_DEPTH:
        raise EventStoreError(f"Event Request 嵌套超过 {MAX_NESTING_DEPTH} 层。")
    _validate_redaction(request)
    for item in request.get("evidenceBindings", []):
        if isinstance(item, dict) and item.get("kind") == "relative_path":
            _validate_relative_path(str(item.get("ref", "")), project_root)


def compute_request_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(request)).hexdigest()


def compute_event_id(request: dict[str, Any]) -> str:
    binding = request.get("projectBinding")
    project_id = binding.get("projectId") if isinstance(binding, dict) else None
    identity = {
        "projectId": project_id,
        "eventType": request.get("eventType"),
        "idempotencyKey": request.get("idempotencyKey"),
    }
    digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()[:32].upper()
    return f"EVT-{digest}"


def compute_event_hash(event: dict[str, Any]) -> str:
    value = copy.deepcopy(event)
    integrity = value.get("integrity")
    if isinstance(integrity, dict):
        integrity.pop("eventHash", None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def request_from_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "requestVersion": REQUEST_FORMAT,
        "idempotencyKey": event.get("idempotencyKey"),
        "eventType": event.get("eventType"),
        "occurredAt": event.get("occurredAt"),
        "timePrecision": event.get("timePrecision"),
        "actor": event.get("actor"),
        "projectBinding": event.get("projectBinding"),
        "sourceBinding": event.get("sourceBinding"),
        "correlation": event.get("correlation"),
        "subjectRefs": event.get("subjectRefs"),
        "authority": event.get("authority"),
        "confidence": event.get("confidence"),
        "payload": event.get("payload"),
        "evidenceBindings": event.get("evidenceBindings"),
        "informationGaps": event.get("informationGaps"),
        "outcome": event.get("outcome"),
        "privacy": event.get("privacy"),
        "extensions": event.get("extensions"),
    }


def _schema_validator(schema_path: Path = DEFAULT_SCHEMA) -> Draft202012Validator:
    schema = _read_json(schema_path, max_bytes=2 * MAX_EVENT_BYTES)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_event(
    event: dict[str, Any], *, schema_path: Path = DEFAULT_SCHEMA
) -> list[str]:
    errors: list[str] = []
    validator = _schema_validator(schema_path)
    for issue in sorted(validator.iter_errors(event), key=lambda item: list(item.absolute_path)):
        pointer = "/" + "/".join(str(part) for part in issue.absolute_path)
        errors.append(f"SCHEMA {pointer}: {issue.message}")
    if errors:
        return errors
    request = request_from_event(event)
    try:
        validate_request_safety(request)
    except EventStoreError as exc:
        errors.append(f"SAFETY: {exc}")
    expected_id = compute_event_id(request)
    if event.get("eventId") != expected_id:
        errors.append(f"EVENT_ID: expected {expected_id}, got {event.get('eventId')}")
    expected_request_hash = compute_request_hash(request)
    if event.get("integrity", {}).get("requestHash") != expected_request_hash:
        errors.append("REQUEST_HASH: requestHash 与 Event 来源字段不匹配。")
    expected_event_hash = compute_event_hash(event)
    if event.get("integrity", {}).get("eventHash") != expected_event_hash:
        errors.append("EVENT_HASH: eventHash 与 Event 内容不匹配。")
    return errors


def build_event(
    request: dict[str, Any],
    *,
    stream_id: str,
    epoch: int,
    sequence: int,
    previous_event_hash: str | None,
    recorded_at: str | None = None,
    schema_path: Path = DEFAULT_SCHEMA,
    project_root: Path | None = None,
) -> dict[str, Any]:
    validate_request_safety(request, project_root=project_root)
    event = {
        "formatVersion": EVENT_FORMAT,
        "eventId": compute_event_id(request),
        "idempotencyKey": request["idempotencyKey"],
        "eventType": request["eventType"],
        "recordedAt": _normalize_recorded_at(recorded_at),
        "occurredAt": request["occurredAt"],
        "timePrecision": request["timePrecision"],
        "actor": copy.deepcopy(request["actor"]),
        "projectBinding": copy.deepcopy(request["projectBinding"]),
        "sourceBinding": copy.deepcopy(request["sourceBinding"]),
        "stream": {
            "streamId": stream_id,
            "epoch": epoch,
            "sequence": sequence,
            "previousEventHash": previous_event_hash,
        },
        "correlation": copy.deepcopy(request["correlation"]),
        "subjectRefs": copy.deepcopy(request["subjectRefs"]),
        "authority": request["authority"],
        "confidence": request["confidence"],
        "payload": copy.deepcopy(request["payload"]),
        "evidenceBindings": copy.deepcopy(request["evidenceBindings"]),
        "informationGaps": copy.deepcopy(request["informationGaps"]),
        "outcome": copy.deepcopy(request["outcome"]),
        "privacy": copy.deepcopy(request["privacy"]),
        "integrity": {
            "hashAlgorithm": "sha256",
            "requestHash": compute_request_hash(request),
            "eventHash": "0" * 64,
        },
        "extensions": copy.deepcopy(request["extensions"]),
    }
    event["integrity"]["eventHash"] = compute_event_hash(event)
    errors = validate_event(event, schema_path=schema_path)
    if errors:
        raise EventStoreError("Event 无效：" + "；".join(errors[:20]))
    if len(_canonical_bytes(event)) > MAX_EVENT_BYTES:
        raise EventStoreError(f"Event 超过 {MAX_EVENT_BYTES} bytes。")
    return event


def _events_dir(store: Path) -> Path:
    return store / "events"


def _head_path(store: Path) -> Path:
    return store / "stream-head.json"


def _event_paths(store: Path) -> list[Path]:
    directory = _events_dir(store)
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def _head_value(
    *,
    project_id: str,
    stream_id: str,
    epoch: int,
    sequence: int,
    event_id: str | None,
    event_hash: str | None,
    updated_at: str,
) -> dict[str, Any]:
    return {
        "formatVersion": HEAD_FORMAT,
        "projectId": project_id,
        "streamId": stream_id,
        "epoch": epoch,
        "sequence": sequence,
        "eventId": event_id,
        "eventHash": event_hash,
        "updatedAt": updated_at,
    }


def _validate_head_shape(head: dict[str, Any]) -> list[str]:
    expected = {
        "formatVersion",
        "projectId",
        "streamId",
        "epoch",
        "sequence",
        "eventId",
        "eventHash",
        "updatedAt",
    }
    errors: list[str] = []
    if set(head) != expected:
        errors.append("HEAD_FIELDS: stream-head.json 字段不匹配。")
        return errors
    if head.get("formatVersion") != HEAD_FORMAT:
        errors.append(f"HEAD_FORMAT: 必须是 {HEAD_FORMAT}")
    if not isinstance(head.get("projectId"), str) or not head["projectId"]:
        errors.append("HEAD_PROJECT: projectId 无效。")
    if not isinstance(head.get("streamId"), str) or not head["streamId"]:
        errors.append("HEAD_STREAM: streamId 无效。")
    if not isinstance(head.get("epoch"), int) or head["epoch"] < 1:
        errors.append("HEAD_EPOCH: epoch 必须 >= 1。")
    if not isinstance(head.get("sequence"), int) or head["sequence"] < 0:
        errors.append("HEAD_SEQUENCE: sequence 必须 >= 0。")
    if head.get("eventId") is not None and not re.fullmatch(r"EVT-[A-F0-9]{32}", str(head["eventId"])):
        errors.append("HEAD_EVENT_ID: eventId 无效。")
    if head.get("eventHash") is not None and not re.fullmatch(r"[a-f0-9]{64}", str(head["eventHash"])):
        errors.append("HEAD_EVENT_HASH: eventHash 无效。")
    try:
        _normalize_recorded_at(head.get("updatedAt"))
    except EventStoreError as exc:
        errors.append(f"HEAD_TIME: {exc}")
    return errors


@contextmanager
def _exclusive_store_lock(store: Path) -> Iterator[None]:
    store.mkdir(parents=True, exist_ok=True)
    lock = store / ".event-store.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise EventConflictError(f"Event Store 正在被另一个 Writer 使用：{lock}") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock.unlink(missing_ok=True)


def _initialize_empty_store(store: Path, project_id: str, stream_id: str) -> dict[str, Any]:
    events = _event_paths(store)
    head_path = _head_path(store)
    if events and not head_path.exists():
        raise EventConflictError("Event 文件存在但 Stream Head 缺失；必须先执行确定性恢复。")
    if head_path.exists():
        return _read_json(head_path)
    _events_dir(store).mkdir(parents=True, exist_ok=True)
    (store / "checkpoints").mkdir(parents=True, exist_ok=True)
    (store / "redactions").mkdir(parents=True, exist_ok=True)
    head = _head_value(
        project_id=project_id,
        stream_id=stream_id,
        epoch=1,
        sequence=0,
        event_id=None,
        event_hash=None,
        updated_at=_now(),
    )
    atomic_write(head_path, json.dumps(head, ensure_ascii=False, indent=2) + "\n")
    return head


def _scan_chain(
    store: Path, *, schema_path: Path = DEFAULT_SCHEMA
) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in _event_paths(store):
        try:
            event = _read_json(path)
        except EventStoreError as exc:
            errors.append(f"EVENT_FILE {path.name}: {exc}")
            continue
        event_id = str(event.get("eventId", ""))
        if path.name != f"{event_id}.json":
            errors.append(f"EVENT_FILENAME {path.name}: 必须与 eventId 一致。")
        for error in validate_event(event, schema_path=schema_path):
            errors.append(f"{path.name} {error}")
        events.append(event)
    def sequence_key(item: dict[str, Any]) -> int:
        value = item.get("stream", {}).get("sequence", 0)
        return value if isinstance(value, int) else 0

    events.sort(key=sequence_key)
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    previous_hash: str | None = None
    project_id: str | None = None
    stream_id: str | None = None
    epoch: int | None = None
    for expected_sequence, event in enumerate(events, start=1):
        event_id = str(event.get("eventId", ""))
        raw_sequence = event.get("stream", {}).get("sequence")
        sequence = raw_sequence if isinstance(raw_sequence, int) else -1
        current_project = event.get("projectBinding", {}).get("projectId")
        current_stream = event.get("stream", {}).get("streamId")
        current_epoch = event.get("stream", {}).get("epoch")
        if event_id in seen_ids:
            errors.append(f"EVENT_DUPLICATE: eventId 重复 {event_id}")
        seen_ids.add(event_id)
        if sequence in seen_sequences:
            errors.append(f"SEQUENCE_DUPLICATE: sequence 重复 {sequence}")
        seen_sequences.add(sequence)
        if sequence != expected_sequence:
            errors.append(f"SEQUENCE_GAP: expected {expected_sequence}, got {sequence}")
        if event.get("stream", {}).get("previousEventHash") != previous_hash:
            errors.append(f"CHAIN: {event_id} previousEventHash 不匹配。")
        if project_id is None:
            project_id = current_project
            stream_id = current_stream
            epoch = current_epoch
        else:
            if current_project != project_id:
                errors.append(f"PROJECT_DRIFT: {event_id} projectId 不一致。")
            if current_stream != stream_id:
                errors.append(f"STREAM_DRIFT: {event_id} streamId 不一致。")
            if current_epoch != epoch:
                errors.append(f"EPOCH_DRIFT: {event_id} epoch 不一致；v0.1 Recorder 尚未执行 Redaction Epoch 切换。")
        previous_hash = event.get("integrity", {}).get("eventHash")
    return events, errors


def validate_store(
    store: Path, *, schema_path: Path = DEFAULT_SCHEMA
) -> dict[str, Any]:
    store = Path(store)
    events, errors = _scan_chain(store, schema_path=schema_path)
    head: dict[str, Any] | None = None
    head_path = _head_path(store)
    if not head_path.exists():
        errors.append("HEAD_MISSING: stream-head.json 不存在。")
    else:
        try:
            head = _read_json(head_path)
            errors.extend(_validate_head_shape(head))
        except EventStoreError as exc:
            errors.append(f"HEAD_INVALID: {exc}")
    if head is not None and not _validate_head_shape(head):
        if events:
            last = events[-1]
            expected = {
                "projectId": last.get("projectBinding", {}).get("projectId"),
                "streamId": last.get("stream", {}).get("streamId"),
                "epoch": last.get("stream", {}).get("epoch"),
                "sequence": last.get("stream", {}).get("sequence"),
                "eventId": last.get("eventId"),
                "eventHash": last.get("integrity", {}).get("eventHash"),
            }
        else:
            expected = {
                "projectId": head.get("projectId"),
                "streamId": head.get("streamId"),
                "epoch": head.get("epoch"),
                "sequence": 0,
                "eventId": None,
                "eventHash": None,
            }
        for field, value in expected.items():
            if head.get(field) != value:
                errors.append(f"HEAD_DRIFT: {field} expected {value!r}, got {head.get(field)!r}")
    return {
        "formatVersion": "panorama-engineering-event-store-validation.v0.1",
        "valid": not errors,
        "eventCount": len(events),
        "head": head,
        "errors": errors,
    }


def record_request(
    store: Path,
    request: dict[str, Any],
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    recorded_at: str | None = None,
    project_root: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    validate_request_safety(request, project_root=project_root)
    project_id = request["projectBinding"]["projectId"]
    stream_id = f"project-{project_id}"
    event_id = compute_event_id(request)
    store = Path(store)
    with _exclusive_store_lock(store):
        head = _initialize_empty_store(store, project_id, stream_id)
        report = validate_store(store, schema_path=schema_path)
        if not report["valid"]:
            raise EventConflictError(
                "Event Store 无效，写入已停止：" + "；".join(report["errors"][:20])
            )
        if head.get("projectId") != project_id or head.get("streamId") != stream_id:
            raise EventConflictError("Event Request 的 Project/Stream 与现有 Store 不匹配。")
        existing_path = _events_dir(store) / f"{event_id}.json"
        if existing_path.exists():
            existing = _read_json(existing_path)
            errors = validate_event(existing, schema_path=schema_path)
            if errors:
                raise EventConflictError("现有幂等 Event 无效：" + "；".join(errors[:20]))
            if existing["integrity"]["requestHash"] != compute_request_hash(request):
                raise EventConflictError("同一 Event ID 对应不同 Request Hash；禁止覆盖。")
            return existing, False
        event = build_event(
            request,
            stream_id=stream_id,
            epoch=head["epoch"],
            sequence=head["sequence"] + 1,
            previous_event_hash=head["eventHash"],
            recorded_at=recorded_at,
            schema_path=schema_path,
            project_root=project_root,
        )
        atomic_write(existing_path, json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        new_head = _head_value(
            project_id=project_id,
            stream_id=stream_id,
            epoch=event["stream"]["epoch"],
            sequence=event["stream"]["sequence"],
            event_id=event["eventId"],
            event_hash=event["integrity"]["eventHash"],
            updated_at=event["recordedAt"],
        )
        try:
            atomic_write(_head_path(store), json.dumps(new_head, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            raise EventStoreError(
                "Event 已原子写入但 Stream Head 未更新；Store 进入 recovery-required。"
            ) from exc
        return event, True


def recover_store_head(
    store: Path, *, schema_path: Path = DEFAULT_SCHEMA
) -> dict[str, Any]:
    store = Path(store)
    with _exclusive_store_lock(store):
        events, errors = _scan_chain(store, schema_path=schema_path)
        if errors:
            raise EventStoreError("Event Chain 无法恢复：" + "；".join(errors[:20]))
        old_head: dict[str, Any] | None = None
        if _head_path(store).exists():
            old_head = _read_json(_head_path(store))
            head_errors = _validate_head_shape(old_head)
            if head_errors:
                raise EventStoreError("现有 Stream Head 结构无效：" + "；".join(head_errors))
        if events:
            last = events[-1]
            head = _head_value(
                project_id=last["projectBinding"]["projectId"],
                stream_id=last["stream"]["streamId"],
                epoch=last["stream"]["epoch"],
                sequence=last["stream"]["sequence"],
                event_id=last["eventId"],
                event_hash=last["integrity"]["eventHash"],
                updated_at=_now(),
            )
        elif old_head is not None:
            head = _head_value(
                project_id=old_head["projectId"],
                stream_id=old_head["streamId"],
                epoch=old_head["epoch"],
                sequence=0,
                event_id=None,
                event_hash=None,
                updated_at=_now(),
            )
        else:
            raise EventStoreError("空 Store 没有 Project/Stream 身份，不能自动恢复。")
        atomic_write(_head_path(store), json.dumps(head, ensure_ascii=False, indent=2) + "\n")
        return head


def load_request(path: Path) -> dict[str, Any]:
    request = _read_json(path)
    validate_request_safety(request)
    return request


__all__ = [
    "DEFAULT_SCHEMA",
    "EVENT_FORMAT",
    "EventConflictError",
    "EventStoreError",
    "REQUEST_FORMAT",
    "build_event",
    "compute_event_hash",
    "compute_event_id",
    "compute_request_hash",
    "load_request",
    "record_request",
    "recover_store_head",
    "request_from_event",
    "validate_event",
    "validate_request_safety",
    "validate_store",
]
