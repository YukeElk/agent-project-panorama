"""Transactional Engineering Event Outbox for governed Panorama Apply.

The adapter is opt-in.  It persists a pending intent under the existing
Panorama writer lock, then reconciles the actually observed Panorama state.
Governance success is not returned until the Engineering Event is durable and
the Outbox is finalized.  A pending/conflict record blocks later governance
writes until explicit recovery.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

import event_store
from panorama_io import atomic_write, compute_canonical_hash, compute_data_hash, extract_data


ROOT = Path(__file__).resolve().parents[1]
OUTBOX_SCHEMA = ROOT / "schema" / "engineering-event-outbox.schema.v0.1.json"
OUTBOX_FORMAT = "panorama-engineering-event-outbox.v0.1"
OUTBOX_EXTENSION_FIELDS = {
    "proposalHash",
    "panoramaSchemaVersion",
    "sourceBinding",
    "adapter",
}
MAX_OUTBOX_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 20
GIT_HASH = re.compile(r"^[a-f0-9]{40}([a-f0-9]{24})?$")


class GovernanceOutboxError(RuntimeError):
    """The governance Outbox is invalid or cannot be safely resolved."""


class GovernanceRecoveryRequired(GovernanceOutboxError):
    """Governance writes must stop until an Outbox is explicitly recovered."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _normalize_time(value: str | None) -> str:
    candidate = value or _now()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise GovernanceOutboxError("Outbox 时间必须是带时区的 RFC 3339。") from exc
    if parsed.tzinfo is None:
        raise GovernanceOutboxError("Outbox 时间必须包含时区。")
    parsed = parsed.astimezone(timezone.utc)
    timespec = "milliseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


def _validate_json_depth(value: Any, *, name: str) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, parent_depth = stack.pop()
        if isinstance(current, (dict, list)):
            depth = parent_depth + 1
            if depth > MAX_NESTING_DEPTH:
                raise GovernanceOutboxError(
                    f"{name} JSON 嵌套深度超过 {MAX_NESTING_DEPTH}。"
                )
            children = current.values() if isinstance(current, dict) else current
            stack.extend((child, depth) for child in children)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_OUTBOX_BYTES:
            raise GovernanceOutboxError(f"Outbox 超过 {MAX_OUTBOX_BYTES} bytes：{path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except GovernanceOutboxError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceOutboxError(f"Outbox JSON 无效 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GovernanceOutboxError(f"Outbox 必须是 JSON 对象：{path}")
    _validate_json_depth(value, name=str(path))
    return value


def _write_json(path: Path, value: dict[str, Any], *, create_only: bool = False) -> None:
    _validate_json_depth(value, name=path.name)
    event_store._validate_redaction(value)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTBOX_BYTES:
        raise GovernanceOutboxError(f"Outbox 超过 {MAX_OUTBOX_BYTES} bytes。")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not create_only:
        atomic_write(path, encoded.decode("utf-8"))
        return
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise GovernanceRecoveryRequired(f"Outbox 已存在，禁止覆盖：{path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def default_outbox_store(panorama_path: Path) -> Path:
    return Path(panorama_path).parent / ".panorama-work" / "event-outbox" / "v0.1"


def _schema_errors(outbox: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(
        _read_json(OUTBOX_SCHEMA), format_checker=FormatChecker()
    )
    errors: list[str] = []
    for issue in sorted(
        validator.iter_errors(outbox), key=lambda item: list(item.absolute_path)
    ):
        pointer = "/" + "/".join(str(part) for part in issue.absolute_path)
        errors.append(f"{pointer}: {issue.message}")
    return errors


def _source_binding(data: dict[str, Any]) -> dict[str, Any]:
    formal = data.get("sourceBinding")
    if not isinstance(formal, dict):
        formal = {}
    git_head = formal.get("gitHead")
    snapshot = formal.get("sourceSnapshotHash")
    if not isinstance(git_head, str) or not GIT_HASH.fullmatch(git_head):
        git_head = None
    if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
        snapshot = None
    return {
        "mode": "git" if git_head is not None else "unknown",
        "gitHead": git_head,
        "sourceSnapshotHash": snapshot,
        "sourceContentDigest": None,
        "coverage": "partial" if git_head is not None or snapshot is not None else "unknown",
    }


def _transaction_id(
    *, project_id: str, proposal_hash: str, base_hash: str, expected_hash: str
) -> str:
    identity = {
        "projectId": project_id,
        "proposalHash": proposal_hash,
        "baseDataHash": base_hash,
        "expectedResultDataHash": expected_hash,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32].upper()
    return f"TXN-{digest}"


def build_event_request(outbox: dict[str, Any]) -> dict[str, Any]:
    """Deterministically reconstruct the request bound by eventRequestHash."""

    binding = outbox["projectBinding"]
    extensions = outbox["extensions"]
    transaction_id = outbox["transactionId"]
    return {
        "requestVersion": event_store.REQUEST_FORMAT,
        "idempotencyKey": compute_canonical_hash(
            {"transactionId": transaction_id, "eventType": "governance.proposal_applied"}
        ),
        "eventType": "governance.proposal_applied",
        "occurredAt": outbox["createdAt"],
        "timePrecision": "millisecond",
        "actor": {
            "kind": "tool",
            "id": "apply-patch",
            "version": extensions["adapter"]["version"],
        },
        "projectBinding": {
            "projectId": binding["projectId"],
            "panoramaSchemaVersion": extensions["panoramaSchemaVersion"],
            "baseRevision": binding["baseRevision"],
            "resultRevision": binding["expectedResultRevision"],
            "baseDataHash": binding["baseDataHash"],
            "resultDataHash": binding["expectedResultDataHash"],
        },
        "sourceBinding": copy.deepcopy(extensions["sourceBinding"]),
        "correlation": {
            "correlationId": transaction_id,
            "causationEventIds": [],
            "supersedesEventIds": [],
        },
        "subjectRefs": [
            {
                "type": "studio_proposal",
                "id": extensions["proposalHash"],
                "relationship": "applies",
            }
        ],
        "authority": "observed",
        "confidence": "high",
        "payload": {
            "transactionId": transaction_id,
            "operationClass": "governance",
            "proposalHash": extensions["proposalHash"],
            "baseRevision": binding["baseRevision"],
            "resultRevision": binding["expectedResultRevision"],
        },
        "evidenceBindings": [],
        "informationGaps": [],
        "outcome": {
            "status": "succeeded",
            "labelStrength": "user_approved",
            "summary": "An exact-hash approved Studio Proposal was atomically applied and validated.",
        },
        "privacy": {
            "accessClass": "PROJECT_OPERATIONAL_METADATA",
            "containsProjectContent": False,
            "containsSecret": False,
            "containsSensitivePersonalData": False,
            "redactionState": "none",
            "retentionPolicyId": None,
        },
        "extensions": {
            "panoramaProjection": {
                "lifecycleTransitions": [
                    {
                        "subjectType": "studio_proposal",
                        "subjectId": extensions["proposalHash"],
                        "fromState": "approved_candidate",
                        "toState": "applied",
                        "trigger": "governance.proposal_applied",
                    }
                ]
            }
        },
    }


def _outbox_hash(outbox: dict[str, Any]) -> str:
    semantics = copy.deepcopy(outbox)
    semantics.pop("integrity", None)
    return compute_canonical_hash(semantics)


def validate_outbox(outbox: dict[str, Any]) -> list[str]:
    try:
        _validate_json_depth(outbox, name="Outbox")
    except GovernanceOutboxError as exc:
        return [f"JSON_DEPTH: {exc}"]
    errors = _schema_errors(outbox)
    if errors:
        return errors
    if outbox["integrity"]["stateHash"] != _outbox_hash(outbox):
        errors.append("STATE_HASH: Outbox stateHash 与完整状态不匹配。")
    if set(outbox.get("extensions", {})) != OUTBOX_EXTENSION_FIELDS:
        errors.append("/extensions: Outbox extension fields 不匹配。")
        return errors
    adapter = outbox["extensions"].get("adapter")
    if adapter != {"id": "apply-patch", "version": "0.5.0"}:
        errors.append("/extensions/adapter: Adapter identity 不匹配。")
    try:
        request = build_event_request(outbox)
        event_store.validate_request_safety(request)
    except (KeyError, event_store.EventStoreError) as exc:
        errors.append(f"EVENT_REQUEST: {exc}")
        return errors
    expected_request_hash = event_store.compute_request_hash(request)
    if outbox["eventRequestHash"] != expected_request_hash:
        errors.append("EVENT_REQUEST_HASH: eventRequestHash 与确定性 Request 不匹配。")
    transaction_id = _transaction_id(
        project_id=outbox["projectBinding"]["projectId"],
        proposal_hash=outbox["extensions"]["proposalHash"],
        base_hash=outbox["projectBinding"]["baseDataHash"],
        expected_hash=outbox["projectBinding"]["expectedResultDataHash"],
    )
    if outbox["transactionId"] != transaction_id:
        errors.append("TRANSACTION_ID: Transaction ID 与绑定语义不匹配。")
    return errors


def _finalized_event_errors(
    outbox: dict[str, Any], event_store_path: Path
) -> list[str]:
    if outbox.get("status") != "finalized":
        return []
    event_id = outbox.get("resolution", {}).get("eventId")
    event_path = Path(event_store_path) / "events" / f"{event_id}.json"
    if not event_path.exists():
        return [f"FINALIZED_EVENT: durable Event 缺失 {event_id}。"]
    try:
        event = _read_json(event_path)
    except GovernanceOutboxError as exc:
        return [f"FINALIZED_EVENT: {exc}"]
    errors = [f"FINALIZED_EVENT: {item}" for item in event_store.validate_event(event)]
    if event.get("integrity", {}).get("eventHash") != outbox["resolution"].get("eventHash"):
        errors.append("FINALIZED_EVENT_HASH: Outbox 与 Event Hash 不匹配。")
    if event.get("integrity", {}).get("requestHash") != outbox.get("eventRequestHash"):
        errors.append("FINALIZED_REQUEST_HASH: Outbox 与 Event Request Hash 不匹配。")
    return errors


def validate_outbox_store(
    store: Path, *, event_store_path: Path | None = None
) -> dict[str, Any]:
    store = Path(store)
    errors: list[str] = []
    counts = {"pending": 0, "finalized": 0, "abandoned": 0, "conflict": 0}
    paths = sorted(store.glob("*.json")) if store.exists() else []
    for path in paths:
        try:
            outbox = _read_json(path)
            for error in validate_outbox(outbox):
                errors.append(f"{path.name} {error}")
            if event_store_path is not None:
                for error in _finalized_event_errors(outbox, Path(event_store_path)):
                    errors.append(f"{path.name} {error}")
            transaction_id = outbox.get("transactionId")
            if path.name != f"{transaction_id}.json":
                errors.append(f"{path.name} FILENAME: 必须与 transactionId 一致。")
            status = outbox.get("status")
            if status in counts:
                counts[status] += 1
        except GovernanceOutboxError as exc:
            errors.append(f"{path.name}: {exc}")
    unresolved = counts["pending"] + counts["conflict"]
    return {
        "formatVersion": "panorama-engineering-event-outbox-store-validation.v0.1",
        "valid": not errors,
        "outboxCount": len(paths),
        "counts": counts,
        "eventBindingsChecked": event_store_path is not None,
        "recoveryRequired": unresolved > 0 or bool(errors),
        "errors": errors,
    }


def ensure_governance_ready(
    store: Path, *, event_store_path: Path | None = None
) -> None:
    report = validate_outbox_store(store, event_store_path=event_store_path)
    if not report["valid"]:
        raise GovernanceRecoveryRequired(
            "Outbox Store 无效，治理写入已停止：" + "；".join(report["errors"][:10])
        )
    if report["recoveryRequired"]:
        raise GovernanceRecoveryRequired(
            "存在 pending/conflict Governance Outbox；必须先显式恢复。"
        )


def prepare_governance_outbox(
    store: Path,
    *,
    current: dict[str, Any],
    updated: dict[str, Any],
    proposal_hash: str,
    created_at: str | None = None,
    event_store_path: Path | None = None,
) -> Path:
    """Persist pending intent.  Caller must already hold the Panorama lock."""

    store = Path(store)
    ensure_governance_ready(store, event_store_path=event_store_path)
    at = _normalize_time(created_at)
    project_id = current.get("project", {}).get("id")
    base_revision = current.get("meta", {}).get("revision")
    result_revision = updated.get("meta", {}).get("revision")
    base_hash = compute_data_hash(current)
    result_hash = compute_data_hash(updated)
    transaction_id = _transaction_id(
        project_id=project_id,
        proposal_hash=proposal_hash,
        base_hash=base_hash,
        expected_hash=result_hash,
    )
    outbox = {
        "formatVersion": OUTBOX_FORMAT,
        "transactionId": transaction_id,
        "operationClass": "governance",
        "policy": "fail_closed",
        "status": "pending",
        "createdAt": at,
        "updatedAt": at,
        "projectBinding": {
            "projectId": project_id,
            "baseRevision": base_revision,
            "expectedResultRevision": result_revision,
            "baseDataHash": base_hash,
            "expectedResultDataHash": result_hash,
        },
        "eventRequestHash": "0" * 64,
        "observedResult": None,
        "resolution": {"eventId": None, "eventHash": None, "reason": None},
        "integrity": {
            "hashAlgorithm": "sha256",
            "stateHash": "0" * 64,
            "hashScope": "outbox_without_integrity",
        },
        "extensions": {
            "proposalHash": proposal_hash,
            "panoramaSchemaVersion": str(current.get("schemaVersion")),
            "sourceBinding": _source_binding(current),
            "adapter": {"id": "apply-patch", "version": "0.5.0"},
        },
    }
    outbox["eventRequestHash"] = event_store.compute_request_hash(
        build_event_request(outbox)
    )
    outbox["integrity"]["stateHash"] = _outbox_hash(outbox)
    errors = validate_outbox(outbox)
    if errors:
        raise GovernanceOutboxError("Pending Outbox 无效：" + "；".join(errors[:20]))
    path = store / f"{transaction_id}.json"
    _write_json(path, outbox, create_only=True)
    return path


def _observed_result(panorama_path: Path, at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data = extract_data(panorama_path)
    return data, {
        "revision": data.get("meta", {}).get("revision"),
        "dataHash": compute_data_hash(data),
        "observedAt": at,
    }


def resolve_governance_outbox(
    outbox_path: Path,
    *,
    panorama_path: Path,
    event_store_path: Path,
    resolved_at: str | None = None,
) -> dict[str, Any]:
    """Reconcile current/base/conflict state and finalize only after Event durability."""

    outbox_path = Path(outbox_path)
    outbox = _read_json(outbox_path)
    errors = validate_outbox(outbox)
    if errors:
        raise GovernanceRecoveryRequired("Outbox 无效：" + "；".join(errors[:20]))
    if outbox["status"] in {"finalized", "abandoned", "conflict"}:
        finalized_errors = _finalized_event_errors(outbox, Path(event_store_path))
        if finalized_errors:
            raise GovernanceRecoveryRequired("；".join(finalized_errors))
        return outbox
    at = _normalize_time(resolved_at)
    _, observed = _observed_result(Path(panorama_path), at)
    binding = outbox["projectBinding"]
    expected = (
        observed["revision"] == binding["expectedResultRevision"]
        and observed["dataHash"] == binding["expectedResultDataHash"]
    )
    base = (
        observed["revision"] == binding["baseRevision"]
        and observed["dataHash"] == binding["baseDataHash"]
    )
    outbox["observedResult"] = observed
    outbox["updatedAt"] = at
    if expected:
        request = build_event_request(outbox)
        if event_store.compute_request_hash(request) != outbox["eventRequestHash"]:
            raise GovernanceRecoveryRequired("恢复时 Event Request Hash 不匹配。")
        try:
            event, _ = event_store.record_request(
                Path(event_store_path),
                request,
                recorded_at=at,
                project_root=Path(panorama_path).parent,
            )
        except event_store.EventStoreError as exc:
            raise GovernanceRecoveryRequired(
                "Panorama 已达到 expected result，但 Engineering Event 尚未 durable；"
                "Outbox 保持 pending。"
            ) from exc
        outbox["status"] = "finalized"
        outbox["resolution"] = {
            "eventId": event["eventId"],
            "eventHash": event["integrity"]["eventHash"],
            "reason": None,
        }
    elif base:
        outbox["status"] = "abandoned"
        outbox["resolution"] = {
            "eventId": None,
            "eventHash": None,
            "reason": "Observed Panorama still equals the exact base state; transaction had no effect.",
        }
    else:
        outbox["status"] = "conflict"
        outbox["resolution"] = {
            "eventId": None,
            "eventHash": None,
            "reason": "Observed Panorama matches neither exact base nor expected result; manual reconciliation required.",
        }
    outbox["integrity"]["stateHash"] = _outbox_hash(outbox)
    errors = validate_outbox(outbox)
    if errors:
        raise GovernanceRecoveryRequired("Resolved Outbox 无效：" + "；".join(errors[:20]))
    _write_json(outbox_path, outbox)
    return outbox


__all__ = [
    "GovernanceOutboxError",
    "GovernanceRecoveryRequired",
    "build_event_request",
    "default_outbox_store",
    "ensure_governance_ready",
    "prepare_governance_outbox",
    "resolve_governance_outbox",
    "validate_outbox",
    "validate_outbox_store",
]
