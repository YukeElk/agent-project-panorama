"""Governed ``event_head.recover`` operation adapter for Panorama V0.5.1.

The adapter binds one deterministic head repair to an approved Policy use and
to a durable project-local transaction.  It never repairs Event content,
redaction epochs, forks, mismatched identities, or ambiguous chains.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator

from jsonschema import Draft202012Validator, FormatChecker

import approval_policy
import event_store
from panorama_io import atomic_write, compute_canonical_hash, compute_data_hash
from validate_panorama import ValidationRuntimeError, load_panorama, validate_data


ROOT = Path(__file__).resolve().parents[1]
TRANSACTION_SCHEMA = (
    ROOT / "schema" / "event-head-recovery-transaction.schema.v0.1.json"
)
TRANSACTION_FORMAT = "panorama-event-head-recovery-transaction.v0.1"
INSPECTION_FORMAT = "panorama-event-head-policy-preview.v0.1"
STORE_REF = ".panorama-work/event-store/v0.1"
POLICY_STORE_REF = ".panorama-work/approval-policy-store/v0.1"
OPERATIONS_REF = ".panorama-work/approval-policy-operations/v0.1/event-head-recover"
ARTIFACT_REFS = [STORE_REF]
MAX_JSON_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 20
POLICY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class EventHeadPolicyAdapterError(RuntimeError):
    """An adapter input, binding, or durable state is invalid."""

    def __init__(self, message: str, *, code: str = "ADAPTER_INVALID") -> None:
        super().__init__(message)
        self.code = code


class EventHeadPolicyConflictError(EventHeadPolicyAdapterError):
    """A preview, transaction, Policy use, or observed state has drifted."""


class InjectedAdapterCrash(RuntimeError):
    """Test-only crash signal; deliberately bypasses failure finalization."""


FaultInjector = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _normalize_time(value: str | None, field: str) -> str:
    candidate = value or _now()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise EventHeadPolicyAdapterError(
            f"{field} 必须是带时区的 RFC 3339 时间。", code="TIME_INVALID"
        ) from exc
    if parsed.tzinfo is None:
        raise EventHeadPolicyAdapterError(
            f"{field} 必须包含时区。", code="TIME_INVALID"
        )
    timespec = "milliseconds" if parsed.microsecond else "seconds"
    return parsed.astimezone(timezone.utc).isoformat(timespec=timespec).replace(
        "+00:00", "Z"
    )


def _time(value: str, field: str) -> datetime:
    normalized = _normalize_time(value, field)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _json_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_NESTING_DEPTH:
        return depth
    if isinstance(value, dict):
        return max([depth] + [_json_depth(item, depth + 1) for item in value.values()])
    if isinstance(value, list):
        return max([depth] + [_json_depth(item, depth + 1) for item in value])
    return depth


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise EventHeadPolicyAdapterError(
                f"事务制品不允许是符号链接：{path.name}", code="PATH_SYMLINK"
            )
        if path.stat().st_size > MAX_JSON_BYTES:
            raise EventHeadPolicyAdapterError(
                f"事务制品超过 {MAX_JSON_BYTES} bytes。", code="TRANSACTION_TOO_LARGE"
            )
        value = json.loads(path.read_text(encoding="utf-8"))
    except EventHeadPolicyAdapterError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventHeadPolicyAdapterError(
            f"事务制品无效：{path.name}", code="TRANSACTION_INVALID"
        ) from exc
    if not isinstance(value, dict) or _json_depth(value) > MAX_NESTING_DEPTH:
        raise EventHeadPolicyAdapterError(
            "事务 JSON 必须是有界对象。", code="TRANSACTION_INVALID"
        )
    return value


def _schema_validator() -> Draft202012Validator:
    try:
        schema = json.loads(TRANSACTION_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventHeadPolicyAdapterError(
            "Recovery Transaction Schema 不可用。", code="RUNTIME_UNAVAILABLE"
        ) from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _state_hash(transaction: dict[str, Any]) -> str:
    value = copy.deepcopy(transaction)
    value.get("integrity", {}).pop("stateHash", None)
    return compute_canonical_hash(value)


def _validate_transaction(transaction: dict[str, Any]) -> None:
    errors = sorted(
        _schema_validator().iter_errors(transaction),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        rendered = []
        for issue in errors[:20]:
            pointer = "/" + "/".join(str(part) for part in issue.absolute_path)
            rendered.append(f"{pointer}: {issue.message}")
        raise EventHeadPolicyAdapterError(
            "Recovery Transaction Schema 无效：" + "；".join(rendered),
            code="TRANSACTION_SCHEMA_INVALID",
        )
    if transaction["integrity"]["stateHash"] != _state_hash(transaction):
        raise EventHeadPolicyConflictError(
            "Recovery Transaction State Hash 不匹配。", code="TRANSACTION_HASH_MISMATCH"
        )
    status = transaction["status"]
    use_number = transaction["policyBinding"]["useNumber"]
    observed = transaction["observedHead"]
    output_hash = transaction["outputHash"]
    receipt = transaction["receiptBinding"]
    event = transaction["eventBinding"]
    if status == "prepared" and use_number is not None:
        raise EventHeadPolicyAdapterError(
            "prepared Transaction 不得绑定 Use。", code="TRANSACTION_STATE_INVALID"
        )
    if status in {"allocated", "effect_observed", "finalized"} and use_number is None:
        raise EventHeadPolicyAdapterError(
            "已分配 Transaction 缺少 Use Binding。", code="TRANSACTION_STATE_INVALID"
        )
    if status in {"effect_observed", "finalized"} and (
        observed is None or output_hash is None
    ):
        raise EventHeadPolicyAdapterError(
            "effect_observed/finalized Transaction 缺少恢复结果。",
            code="TRANSACTION_STATE_INVALID",
        )
    if status == "finalized" and (
        receipt is None or event is None or transaction["finalHead"] is None
    ):
        raise EventHeadPolicyAdapterError(
            "finalized Transaction 缺少 Receipt/Event/Final Head Binding。",
            code="TRANSACTION_STATE_INVALID",
        )


def _seal_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(transaction)
    value["integrity"]["stateHash"] = _state_hash(value)
    _validate_transaction(value)
    event_store._validate_redaction(value)
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise EventHeadPolicyAdapterError(
            "Recovery Transaction 超过大小上限。", code="TRANSACTION_TOO_LARGE"
        )
    return value


def _assert_no_symlink_components(path: Path, root: Path, *, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise EventHeadPolicyAdapterError(
            f"{label} 越出 Project Root。", code="PATH_ESCAPE"
        ) from exc
    current = root
    if current.is_symlink():
        raise EventHeadPolicyAdapterError(
            "Project Root 不允许是符号链接。", code="PATH_SYMLINK"
        )
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise EventHeadPolicyAdapterError(
                f"{label} 路径包含符号链接。", code="PATH_SYMLINK"
            )


def _resolve_paths(project_root: Path, panorama_path: Path) -> dict[str, Path]:
    raw_root = Path(project_root)
    if raw_root.is_symlink():
        raise EventHeadPolicyAdapterError(
            "Project Root 不允许是符号链接。", code="PATH_SYMLINK"
        )
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise EventHeadPolicyAdapterError(
            "Project Root 不存在或不可访问。", code="PROJECT_ROOT_INVALID"
        ) from exc
    if not root.is_dir():
        raise EventHeadPolicyAdapterError(
            "Project Root 必须是目录。", code="PROJECT_ROOT_INVALID"
        )
    raw_panorama = Path(panorama_path)
    if raw_panorama.is_symlink():
        raise EventHeadPolicyAdapterError(
            "Panorama 输入不允许是符号链接。", code="PATH_SYMLINK"
        )
    try:
        panorama = raw_panorama.resolve(strict=True)
    except OSError as exc:
        raise EventHeadPolicyAdapterError(
            "Panorama 输入不存在或不可访问。", code="PANORAMA_INVALID"
        ) from exc
    _assert_no_symlink_components(panorama, root, label="Panorama")
    event_path = root / Path(*STORE_REF.split("/"))
    policy_path = root / Path(*POLICY_STORE_REF.split("/"))
    operations_path = root / Path(*OPERATIONS_REF.split("/"))
    for label, path in (("Event Store", event_path), ("Policy Store", policy_path)):
        _assert_no_symlink_components(path, root, label=label)
        if not path.is_dir():
            raise EventHeadPolicyAdapterError(
                f"{label} 不存在。", code="STORE_NOT_FOUND"
            )
    _assert_no_symlink_components(operations_path, root, label="Operation Store")
    return {
        "root": root,
        "panorama": panorama,
        "eventStore": event_path,
        "policyStore": policy_path,
        "operations": operations_path,
    }


def _validate_policy_local_paths(policy_store: Path, policy_id: str) -> None:
    if POLICY_ID_PATTERN.fullmatch(policy_id) is None:
        raise EventHeadPolicyAdapterError(
            "policyId 格式无效。", code="POLICY_ID_INVALID"
        )
    for name in ("policies", "ledgers", "receipts", "revocations"):
        candidate = policy_store / name
        if candidate.exists() and candidate.is_symlink():
            raise EventHeadPolicyAdapterError(
                f"Policy Store 的 {name} 不允许是符号链接。", code="PATH_SYMLINK"
            )
    exact_paths = (
        policy_store / "policies" / f"{policy_id}.json",
        policy_store / "ledgers" / f"{policy_id}.json",
        policy_store / "revocations" / f"{policy_id}.json",
        policy_store / "receipts" / policy_id,
    )
    for candidate in exact_paths:
        if candidate.exists() and candidate.is_symlink():
            raise EventHeadPolicyAdapterError(
                "Policy/Use/Receipt Binding 不允许经过符号链接。", code="PATH_SYMLINK"
            )
    receipt_dir = policy_store / "receipts" / policy_id
    if receipt_dir.is_dir():
        for candidate in receipt_dir.glob("*.json"):
            if candidate.is_symlink():
                raise EventHeadPolicyAdapterError(
                    "Execution Receipt 不允许是符号链接。", code="PATH_SYMLINK"
                )


def _load_project_binding(panorama: Path) -> dict[str, Any]:
    try:
        data, base_dir = load_panorama(panorama)
        report = validate_data(
            data, base_dir=base_dir, source_path=panorama
        )
    except ValidationRuntimeError as exc:
        raise EventHeadPolicyAdapterError(
            "无法读取正式 Panorama。", code="PANORAMA_INVALID"
        ) from exc
    if report.errors:
        raise EventHeadPolicyAdapterError(
            "正式 Panorama 未通过 Formal Validation。", code="PANORAMA_INVALID"
        )
    try:
        project_id = data["project"]["id"]
        schema_version = str(data["schemaVersion"])
        revision = int(data["meta"]["revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EventHeadPolicyAdapterError(
            "正式 Panorama 缺少 Project/Schema/Revision Binding。",
            code="PANORAMA_BINDING_INVALID",
        ) from exc
    return {
        "projectId": project_id,
        "panoramaSchemaVersion": schema_version,
        "revision": revision,
        "dataHash": compute_data_hash(data),
    }


def _validate_policy_profile(
    state: dict[str, Any], project: dict[str, Any], head_status: str,
    *, at: str, allow_pending: bool = False, enforce_active: bool = True
) -> dict[str, Any]:
    policy = state["policy"]
    if state["revoked"]:
        raise EventHeadPolicyConflictError(
            "Policy 已撤销。", code="POLICY_REVOKED"
        )
    if policy["operation"] != "event_head.recover" or policy["effectClass"] != "deterministic_recovery":
        raise EventHeadPolicyConflictError(
            "Policy Operation/Effect Class 不匹配。", code="POLICY_PROFILE_MISMATCH"
        )
    if (
        policy["projectBinding"]["projectId"] != project["projectId"]
        or project["panoramaSchemaVersion"]
        not in policy["projectBinding"]["panoramaSchemaVersions"]
    ):
        raise EventHeadPolicyConflictError(
            "Policy 与正式 Panorama Project Binding 不匹配。",
            code="PROJECT_BINDING_MISMATCH",
        )
    scope = policy["scope"]
    exact_scope = (
        scope["governanceProtectionProfile"] == "panorama-governance-v0.5"
        and scope["allowedPanoramaPathTemplates"] == []
        and scope["additionalForbiddenPathTemplates"] == []
        and scope["artifactRoots"] == ARTIFACT_REFS
        and scope["producerBindings"] == []
        and scope["commandBindings"] == []
        and scope["receiptRules"] is None
        and scope["deliveryRules"] is None
    )
    rules = scope.get("recoveryRules")
    required_rules = {
        "requireSchemaValidChain": True,
        "requireHashValidChain": True,
        "requireContiguousSequence": True,
        "requireUniqueTail": True,
        "requireExactProjectStreamEpochBinding": True,
        "eventMutationAllowed": False,
    }
    if (
        not exact_scope
        or not isinstance(rules, dict)
        or any(rules.get(key) != expected for key, expected in required_rules.items())
        or set(rules) != {"allowedHeadStates", *required_rules}
    ):
        raise EventHeadPolicyConflictError(
            "Policy Scope/Recovery Rules 不是 V0.5.1 fail-closed Profile。",
            code="POLICY_PROFILE_MISMATCH",
        )
    if policy["validity"]["sourceBindingRule"] != "not_applicable":
        raise EventHeadPolicyConflictError(
            "event_head.recover 必须使用 not_applicable Source Binding。",
            code="POLICY_PROFILE_MISMATCH",
        )
    if not all(value is True for value in policy["stopConditions"].values()):
        raise EventHeadPolicyConflictError(
            "Policy Stop Conditions 未全部 fail closed。", code="POLICY_PROFILE_MISMATCH"
        )
    if policy["audit"] != {
        "executionReceiptRequired": True,
        "policyBindingRequired": True,
        "eventRecordingRequired": True,
        "failureMode": "fail_closed",
    }:
        raise EventHeadPolicyConflictError(
            "Policy Audit Profile 不匹配。", code="POLICY_PROFILE_MISMATCH"
        )
    if enforce_active:
        moment = _time(at, "policyCheckAt")
        active_from = max(
            _time(policy["validity"]["effectiveAt"], "effectiveAt"),
            _time(policy["approvalBinding"]["approvalRecordedAt"], "approvalRecordedAt"),
        )
        if moment < active_from or moment >= _time(
            policy["validity"]["expiresAt"], "expiresAt"
        ):
            raise EventHeadPolicyConflictError(
                "Policy 当前不在有效期内。", code="POLICY_INACTIVE"
            )
        next_use = state["ledger"]["nextUseNumber"]
        max_uses = policy["validity"]["maxUses"]
        if max_uses is not None and next_use > max_uses:
            raise EventHeadPolicyConflictError(
                "Policy 已达到 maxUses。", code="POLICY_USE_LIMIT"
            )
    if not allow_pending and state["ledger"]["pending"] is not None:
        raise EventHeadPolicyConflictError(
            "Policy 存在 Pending Use；必须先恢复精确事务。",
            code="PENDING_USE_CONFLICT",
        )
    if head_status in {"head_missing", "head_behind"} and head_status not in rules["allowedHeadStates"]:
        raise EventHeadPolicyConflictError(
            "当前 Head State 未被 Policy 精确授权。", code="HEAD_STATE_NOT_ALLOWED"
        )
    return policy


def _input_hash(
    policy: dict[str, Any], project: dict[str, Any], inspection: dict[str, Any]
) -> str:
    return compute_canonical_hash(
        {
            "operation": "event_head.recover",
            "policyId": policy["policyId"],
            "policyHash": policy["approvalBinding"]["policyHash"],
            "projectBinding": project,
            "storeRef": STORE_REF,
            "headStatus": inspection["status"],
            "beforeHead": inspection["head"],
            "expectedTail": inspection["expectedTail"],
            "recoveryRules": policy["scope"]["recoveryRules"],
        }
    )


def _transaction_id(policy_id: str, policy_hash: str, input_hash: str) -> str:
    identity = {
        "policyId": policy_id,
        "policyHash": policy_hash,
        "inputHash": input_hash,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32].upper()
    return "EHR-" + digest


def preview(
    project_root: Path, panorama_path: Path, policy_id: str, *, previewed_at: str | None = None
) -> dict[str, Any]:
    at = _normalize_time(previewed_at, "previewedAt")
    paths = _resolve_paths(project_root, panorama_path)
    _validate_policy_local_paths(paths["policyStore"], policy_id)
    project = _load_project_binding(paths["panorama"])
    state = approval_policy.inspect_policy_state(paths["policyStore"], policy_id)
    inspection = event_store.inspect_head_state(paths["eventStore"])
    policy = _validate_policy_profile(
        state, project, inspection["status"], at=at
    )
    if inspection["expectedTail"] is not None and (
        inspection["expectedTail"].get("projectId") != project["projectId"]
    ):
        raise EventHeadPolicyConflictError(
            "Event Store 与正式 Panorama Project Binding 不匹配。",
            code="PROJECT_BINDING_MISMATCH",
        )
    if inspection["status"] == "current":
        return {
            "formatVersion": INSPECTION_FORMAT,
            "status": "no_recovery_needed",
            "code": "HEAD_CURRENT",
            "recoverable": False,
            "policyId": policy_id,
            "policyHash": policy["approvalBinding"]["policyHash"],
            "projectBinding": project,
            "headInspection": inspection,
            "inputHash": None,
            "transactionId": None,
            "pendingUse": state["ledger"]["pending"],
        }
    if inspection["status"] not in {"head_missing", "head_behind"}:
        return {
            "formatVersion": INSPECTION_FORMAT,
            "status": "blocked",
            "code": "HEAD_NOT_RECOVERABLE",
            "recoverable": False,
            "policyId": policy_id,
            "policyHash": policy["approvalBinding"]["policyHash"],
            "projectBinding": project,
            "headInspection": inspection,
            "inputHash": None,
            "transactionId": None,
            "pendingUse": state["ledger"]["pending"],
        }
    input_hash = _input_hash(policy, project, inspection)
    transaction_id = _transaction_id(
        policy_id, policy["approvalBinding"]["policyHash"], input_hash
    )
    return {
        "formatVersion": INSPECTION_FORMAT,
        "status": "recoverable",
        "code": "HEAD_RECOVERY_ALLOWED",
        "recoverable": True,
        "policyId": policy_id,
        "policyHash": policy["approvalBinding"]["policyHash"],
        "projectBinding": project,
        "headInspection": inspection,
        "inputHash": input_hash,
        "transactionId": transaction_id,
        "pendingUse": state["ledger"]["pending"],
    }


def _transaction_path(operations: Path, transaction_id: str) -> Path:
    if re.fullmatch(r"EHR-[A-F0-9]{32}", transaction_id) is None:
        raise EventHeadPolicyAdapterError(
            "transactionId 格式无效。", code="TRANSACTION_ID_INVALID"
        )
    return operations / f"{transaction_id}.json"


def _write_transaction(path: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    value = _seal_transaction(transaction)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise EventHeadPolicyAdapterError(
            "Operation Store 不允许是符号链接。", code="PATH_SYMLINK"
        )
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    atomic_write(path, encoded)
    return value


def _create_transaction(
    path: Path, preview_result: dict[str, Any], at: str
) -> dict[str, Any]:
    inspection = preview_result["headInspection"]
    transaction = {
        "formatVersion": TRANSACTION_FORMAT,
        "transactionId": preview_result["transactionId"],
        "status": "prepared",
        "policyBinding": {
            "policyId": preview_result["policyId"],
            "policyHash": preview_result["policyHash"],
            "useNumber": None,
        },
        "projectBinding": preview_result["projectBinding"],
        "storeRef": STORE_REF,
        "inputHash": preview_result["inputHash"],
        "before": {
            "status": inspection["status"],
            "head": inspection["head"],
            "expectedTail": inspection["expectedTail"],
        },
        "observedHead": None,
        "finalHead": None,
        "outputHash": None,
        "result": {"status": "pending", "errorCode": None, "summary": ""},
        "receiptBinding": None,
        "eventBinding": None,
        "timestamps": {
            "preparedAt": at,
            "updatedAt": at,
            "effectObservedAt": None,
            "finalizedAt": None,
        },
        "integrity": {
            "hashAlgorithm": "sha256",
            "stateHash": "0" * 64,
            "hashScope": "transaction_without_integrity.stateHash",
        },
        "extensions": {},
    }
    if path.exists():
        existing = _read_json(path)
        _validate_transaction(existing)
        if (
            existing["transactionId"] != transaction["transactionId"]
            or existing["inputHash"] != transaction["inputHash"]
            or existing["policyBinding"]["policyHash"]
            != transaction["policyBinding"]["policyHash"]
        ):
            raise EventHeadPolicyConflictError(
                "确定性 Transaction ID 已存在但 Binding 冲突。",
                code="TRANSACTION_CONFLICT",
            )
        return existing
    return _write_transaction(path, transaction)


def _project_for_begin(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "projectId": project["projectId"],
        "panoramaSchemaVersion": project["panoramaSchemaVersion"],
        "baseRevision": project["revision"],
        "baseDataHash": project["dataHash"],
        "sourceBindingHash": None,
    }


def _project_for_result(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "projectId": project["projectId"],
        "panoramaSchemaVersion": project["panoramaSchemaVersion"],
        "resultRevision": project["revision"],
        "resultDataHash": project["dataHash"],
    }


def _receipt_binding(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "receiptId": receipt["receiptId"],
        "receiptHash": receipt["integrity"]["receiptHash"],
    }


def _head_identity(head: dict[str, Any] | None) -> dict[str, Any] | None:
    if head is None:
        return None
    return {
        key: head.get(key)
        for key in ("projectId", "streamId", "epoch", "sequence", "eventId", "eventHash")
    }


def _trip(fault_injector: FaultInjector | None, point: str) -> None:
    if fault_injector is not None:
        fault_injector(point)


def _active_transaction_for_policy(
    operations: Path, policy_id: str
) -> tuple[Path, dict[str, Any]] | None:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(operations.glob("EHR-*.json")) if operations.is_dir() else []:
        transaction = _read_json(path)
        _validate_transaction(transaction)
        if (
            transaction["policyBinding"]["policyId"] == policy_id
            and transaction["status"] in {"prepared", "allocated", "effect_observed"}
        ):
            matches.append((path, transaction))
    if len(matches) > 1:
        raise EventHeadPolicyConflictError(
            "同一 Policy 存在多个非终态 Recovery Transaction。",
            code="TRANSACTION_CONFLICT",
        )
    return matches[0] if matches else None


@contextmanager
def _exclusive_adapter_lock(operations: Path) -> Iterator[None]:
    operations.mkdir(parents=True, exist_ok=True)
    if operations.is_symlink():
        raise EventHeadPolicyAdapterError(
            "Operation Store 不允许是符号链接。", code="PATH_SYMLINK"
        )
    lock = operations / ".event-head-recover.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise EventHeadPolicyConflictError(
            "Event Head Adapter 正在被另一个 Writer 使用。", code="ADAPTER_BUSY"
        ) from exc
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


def _finalize_failed_use(
    paths: dict[str, Path], transaction_path: Path, transaction: dict[str, Any],
    *, at: str, conflict: bool, code: str
) -> dict[str, Any]:
    use_number = transaction["policyBinding"]["useNumber"]
    if use_number is None:
        transaction["status"] = "conflict" if conflict else "failed"
        transaction["result"] = {
            "status": "conflict" if conflict else "failed",
            "errorCode": code,
            "summary": "Recovery 未分配 Policy Use。",
        }
        transaction["timestamps"]["updatedAt"] = at
        transaction["timestamps"]["finalizedAt"] = at
        return _write_transaction(transaction_path, transaction)
    receipt, _ = approval_policy.complete_execution(
        paths["policyStore"],
        transaction["policyBinding"]["policyId"],
        use_number=use_number,
        status="failed",
        result_project_binding=_project_for_result(transaction["projectBinding"]),
        output_hash=None,
        artifact_refs=ARTIFACT_REFS,
        formal_validation="failed",
        summary="Event Head Recovery 在副作用前 fail closed。",
        completed_at=at,
    )
    transaction["status"] = "conflict" if conflict else "failed"
    transaction["result"] = {
        "status": "conflict" if conflict else "failed",
        "errorCode": code,
        "summary": "Recovery 未修改 Event Chain；Policy Use 已审计为 failed。",
    }
    transaction["receiptBinding"] = _receipt_binding(receipt)
    transaction["timestamps"]["updatedAt"] = at
    transaction["timestamps"]["finalizedAt"] = at
    return _write_transaction(transaction_path, transaction)


def _perform_effect(
    paths: dict[str, Path], transaction_path: Path, transaction: dict[str, Any], at: str
) -> dict[str, Any]:
    try:
        recovered = event_store.recover_store_head(
            paths["eventStore"],
            recovered_at=at,
            expected_tail=transaction["before"]["expectedTail"],
            expected_status=transaction["before"]["status"],
            expected_head=transaction["before"]["head"],
        )
    except (event_store.EventStoreError, OSError) as exc:
        observed = event_store.inspect_head_state(paths["eventStore"])
        if (
            observed["status"] == "current"
            and _head_identity(observed["head"])
            == transaction["before"]["expectedTail"]
        ):
            recovered = observed["head"]
        elif (
            observed["status"] == transaction["before"]["status"]
            and observed["head"] == transaction["before"]["head"]
            and observed["expectedTail"] == transaction["before"]["expectedTail"]
        ):
            raise EventHeadPolicyAdapterError(
                "Event Head 原子恢复失败且未观察到副作用。", code="HEAD_WRITE_FAILED"
            ) from exc
        else:
            raise EventHeadPolicyConflictError(
                "恢复失败后观察到第三状态；拒绝自动推断或 rebase。",
                code="HEAD_STATE_CONFLICT",
            ) from exc
    transaction["status"] = "effect_observed"
    transaction["observedHead"] = recovered
    transaction["outputHash"] = compute_canonical_hash(recovered)
    transaction["result"] = {
        "status": "pending",
        "errorCode": None,
        "summary": "Event Head 已精确重建，等待 Audit Event 与 Receipt。",
    }
    transaction["timestamps"]["updatedAt"] = at
    transaction["timestamps"]["effectObservedAt"] = at
    return _write_transaction(transaction_path, transaction)


def _complete_success(
    paths: dict[str, Path], transaction_path: Path, transaction: dict[str, Any],
    completed_at: str, *, finalized_at: str | None = None
) -> dict[str, Any]:
    final_at = finalized_at or completed_at
    validation = event_store.validate_store(paths["eventStore"])
    if not validation["valid"]:
        inspection = event_store.inspect_head_state(paths["eventStore"])
        audit_event = _find_expected_audit_event(paths["eventStore"], transaction)
        audit_tail = (
            {
                "projectId": audit_event["projectBinding"]["projectId"],
                "streamId": audit_event["stream"]["streamId"],
                "epoch": audit_event["stream"]["epoch"],
                "sequence": audit_event["stream"]["sequence"],
                "eventId": audit_event["eventId"],
                "eventHash": audit_event["integrity"]["eventHash"],
            }
            if audit_event is not None
            else None
        )
        if (
            inspection["status"] == "head_behind"
            and audit_tail == inspection["expectedTail"]
            and _head_identity(inspection["head"])
            == _head_identity(transaction["observedHead"])
        ):
            event_store.recover_store_head(
                paths["eventStore"],
                recovered_at=final_at,
                expected_tail=audit_tail,
                expected_status="head_behind",
                expected_head=inspection["head"],
            )
        else:
            raise EventHeadPolicyConflictError(
                "恢复后的 Event Store 未通过 Formal Validation。",
                code="EVENT_STORE_VALIDATION_FAILED",
            )
    use_number = transaction["policyBinding"]["useNumber"]
    assert isinstance(use_number, int)
    receipt, _ = approval_policy.complete_execution(
        paths["policyStore"],
        transaction["policyBinding"]["policyId"],
        use_number=use_number,
        status="succeeded",
        result_project_binding=_project_for_result(transaction["projectBinding"]),
        output_hash=transaction["outputHash"],
        artifact_refs=ARTIFACT_REFS,
        formal_validation="passed",
        summary="Event Head 已从唯一、完整且 Hash 有效的 Chain Tail 确定性重建。",
        event_store_path=paths["eventStore"],
        project_root=paths["root"],
        completed_at=completed_at,
    )
    final_inspection = event_store.inspect_head_state(paths["eventStore"])
    policy_validation = approval_policy.validate_policy_store(paths["policyStore"])
    if final_inspection["status"] != "current" or not policy_validation["valid"]:
        raise EventHeadPolicyConflictError(
            "Core Receipt 已写入，但最终 Store 对账失败；需要人工诊断。",
            code="FINAL_RECONCILIATION_FAILED",
        )
    transaction["status"] = "finalized"
    transaction["finalHead"] = final_inspection["head"]
    transaction["receiptBinding"] = _receipt_binding(receipt)
    transaction["eventBinding"] = copy.deepcopy(receipt["eventBinding"])
    transaction["result"] = {
        "status": receipt["execution"]["status"],
        "errorCode": None,
        "summary": "Recovery、Audit Event、Execution Receipt 与 Ledger 已完成对账。",
    }
    transaction["timestamps"]["updatedAt"] = final_at
    transaction["timestamps"]["finalizedAt"] = final_at
    return _write_transaction(transaction_path, transaction)


def execute(
    project_root: Path,
    panorama_path: Path,
    policy_id: str,
    *,
    executed_at: str | None = None,
    fault_injector: FaultInjector | None = None,
) -> dict[str, Any]:
    """Execute one approved recovery or return a zero-use current-head no-op."""

    at = _normalize_time(executed_at, "executedAt")
    paths = _resolve_paths(project_root, panorama_path)
    with _exclusive_adapter_lock(paths["operations"]):
        active = _active_transaction_for_policy(paths["operations"], policy_id)
        if active is not None:
            return _resume_locked(paths, active[0], active[1], at, fault_injector)
        preview_result = preview(
            paths["root"], paths["panorama"], policy_id, previewed_at=at
        )
        if preview_result["status"] == "no_recovery_needed":
            return preview_result
        if preview_result["status"] != "recoverable":
            raise EventHeadPolicyConflictError(
                "当前 Head State 不可由 Delegated Policy 恢复。",
                code="HEAD_NOT_RECOVERABLE",
            )
        transaction_path = _transaction_path(
            paths["operations"], preview_result["transactionId"]
        )
        transaction = _create_transaction(transaction_path, preview_result, at)
        if transaction["status"] != "prepared":
            return _resume_locked(paths, transaction_path, transaction, at, fault_injector)
        _trip(fault_injector, "after_prepared")
        try:
            pending = approval_policy.begin_execution(
                paths["policyStore"],
                policy_id,
                input_hash=transaction["inputHash"],
                project_binding=_project_for_begin(transaction["projectBinding"]),
                source_binding_status="not_applicable",
                artifact_refs=ARTIFACT_REFS,
                started_at=at,
            )
        except (approval_policy.ApprovalPolicyError, OSError):
            transaction["result"] = {
                "status": "failed",
                "errorCode": "USE_ALLOCATION_FAILED",
                "summary": "Policy Use 未分配；未发生 Recovery 副作用。",
            }
            transaction["timestamps"]["updatedAt"] = at
            _write_transaction(transaction_path, transaction)
            raise
        transaction["status"] = "allocated"
        transaction["policyBinding"]["useNumber"] = pending["useNumber"]
        transaction["timestamps"]["updatedAt"] = at
        transaction = _write_transaction(transaction_path, transaction)
        _trip(fault_injector, "after_allocated")
        try:
            transaction = _perform_effect(paths, transaction_path, transaction, at)
        except EventHeadPolicyConflictError as exc:
            _finalize_failed_use(
                paths, transaction_path, transaction, at=at, conflict=True, code=exc.code
            )
            raise
        except EventHeadPolicyAdapterError as exc:
            _finalize_failed_use(
                paths, transaction_path, transaction, at=at, conflict=False, code=exc.code
            )
            raise
        _trip(fault_injector, "after_effect")
        transaction = _complete_success(paths, transaction_path, transaction, at)
        _trip(fault_injector, "after_completion")
        return transaction


def _expected_audit_event_id(transaction: dict[str, Any]) -> str:
    idempotency = compute_canonical_hash(
        {
            "policyId": transaction["policyBinding"]["policyId"],
            "policyHash": transaction["policyBinding"]["policyHash"],
            "useNumber": transaction["policyBinding"]["useNumber"],
            "inputHash": transaction["inputHash"],
        }
    )
    return event_store.compute_event_id(
        {
            "projectBinding": {"projectId": transaction["projectBinding"]["projectId"]},
            "eventType": "approval_policy.execution_recorded",
            "idempotencyKey": idempotency,
        }
    )


def _find_expected_audit_event(
    event_store_path: Path, transaction: dict[str, Any]
) -> dict[str, Any] | None:
    event_id = _expected_audit_event_id(transaction)
    candidates = sorted((event_store_path / "events").glob(f"{event_id}.json"))
    if not candidates:
        return None
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise EventHeadPolicyConflictError(
            "Policy Audit Event 身份不唯一。", code="AUDIT_EVENT_CONFLICT"
        )
    event = event_store._read_json(candidates[0])
    if event_store.validate_event(event):
        raise EventHeadPolicyConflictError(
            "Policy Audit Event 无效。", code="AUDIT_EVENT_CONFLICT"
        )
    payload = event.get("payload", {})
    if (
        event.get("eventId") != event_id
        or event.get("eventType") != "approval_policy.execution_recorded"
        or payload.get("policyId") != transaction["policyBinding"]["policyId"]
        or payload.get("useNumber") != transaction["policyBinding"]["useNumber"]
    ):
        raise EventHeadPolicyConflictError(
            "Policy Audit Event Binding 不匹配。", code="AUDIT_EVENT_CONFLICT"
        )
    return event


def _reconcile_completed_core(
    paths: dict[str, Path], transaction_path: Path, transaction: dict[str, Any],
    receipt: dict[str, Any], at: str
) -> dict[str, Any]:
    if (
        receipt["policyBinding"]["policyHash"]
        != transaction["policyBinding"]["policyHash"]
        or receipt["execution"]["inputHash"] != transaction["inputHash"]
        or receipt["execution"]["outputHash"] != transaction["outputHash"]
        or receipt["execution"]["status"] not in {"succeeded", "no_op"}
        or receipt["eventBinding"] is None
    ):
        raise EventHeadPolicyConflictError(
            "Core Receipt 与 Recovery Transaction 不匹配。",
            code="RECEIPT_BINDING_CONFLICT",
        )
    policy_state = approval_policy.inspect_policy_state(
        paths["policyStore"], transaction["policyBinding"]["policyId"]
    )
    if policy_state["ledger"]["pending"] is not None:
        approval_policy.recover_pending(
            paths["policyStore"],
            transaction["policyBinding"]["policyId"],
            recovered_at=at,
        )
    final_inspection = event_store.inspect_head_state(paths["eventStore"])
    if final_inspection["status"] != "current":
        raise EventHeadPolicyConflictError(
            "Receipt 已持久化但 Event Store 非 current。",
            code="FINAL_RECONCILIATION_FAILED",
        )
    transaction["status"] = "finalized"
    transaction["finalHead"] = final_inspection["head"]
    transaction["receiptBinding"] = _receipt_binding(receipt)
    transaction["eventBinding"] = copy.deepcopy(receipt["eventBinding"])
    transaction["result"] = {
        "status": receipt["execution"]["status"],
        "errorCode": None,
        "summary": "已从 durable Core Receipt 恢复并完成 Ledger/Transaction 对账。",
    }
    transaction["timestamps"]["updatedAt"] = at
    transaction["timestamps"]["finalizedAt"] = at
    return _write_transaction(transaction_path, transaction)


def _resume_locked(
    paths: dict[str, Path],
    transaction_path: Path,
    transaction: dict[str, Any],
    at: str,
    fault_injector: FaultInjector | None,
) -> dict[str, Any]:
    _validate_transaction(transaction)
    if transaction["status"] in {"finalized", "failed", "conflict"}:
        return transaction
    project = _load_project_binding(paths["panorama"])
    if project != transaction["projectBinding"]:
        raise EventHeadPolicyConflictError(
            "Panorama Project/Data Binding 自 Transaction 创建后已变化。",
            code="PROJECT_BINDING_DRIFT",
        )
    policy_state = approval_policy.inspect_policy_state(
        paths["policyStore"], transaction["policyBinding"]["policyId"]
    )
    _validate_policy_profile(
        policy_state,
        project,
        transaction["before"]["status"],
        at=at,
        allow_pending=True,
        enforce_active=transaction["status"] == "prepared",
    )
    if (
        policy_state["policy"]["approvalBinding"]["policyHash"]
        != transaction["policyBinding"]["policyHash"]
    ):
        raise EventHeadPolicyConflictError(
            "Policy Hash 自 Transaction 创建后已变化。", code="POLICY_HASH_MISMATCH"
        )
    if transaction["status"] == "prepared":
        inspection = event_store.inspect_head_state(paths["eventStore"])
        if not (
            inspection["status"] == transaction["before"]["status"]
            and inspection["head"] == transaction["before"]["head"]
            and inspection["expectedTail"] == transaction["before"]["expectedTail"]
        ):
            raise EventHeadPolicyConflictError(
                "prepared Transaction 的 Head Preview 已漂移。", code="HEAD_STATE_CONFLICT"
            )
        if policy_state["ledger"]["pending"] is not None:
            raise EventHeadPolicyConflictError(
                "prepared Transaction 遇到不相关 Pending Use。", code="PENDING_USE_CONFLICT"
            )
        pending = approval_policy.begin_execution(
            paths["policyStore"],
            transaction["policyBinding"]["policyId"],
            input_hash=transaction["inputHash"],
            project_binding=_project_for_begin(project),
            source_binding_status="not_applicable",
            artifact_refs=ARTIFACT_REFS,
            started_at=at,
        )
        transaction["status"] = "allocated"
        transaction["policyBinding"]["useNumber"] = pending["useNumber"]
        transaction["timestamps"]["updatedAt"] = at
        transaction = _write_transaction(transaction_path, transaction)
        _trip(fault_injector, "after_allocated")

    use_number = transaction["policyBinding"]["useNumber"]
    assert isinstance(use_number, int)
    receipt = approval_policy.read_execution_receipt(
        paths["policyStore"], transaction["policyBinding"]["policyId"], use_number
    )
    if receipt is not None:
        return _reconcile_completed_core(
            paths, transaction_path, transaction, receipt, at
        )
    policy_state = approval_policy.inspect_policy_state(
        paths["policyStore"], transaction["policyBinding"]["policyId"]
    )
    pending = policy_state["ledger"]["pending"]
    if (
        pending is None
        or pending["useNumber"] != use_number
        or pending["inputHash"] != transaction["inputHash"]
    ):
        raise EventHeadPolicyConflictError(
            "Recovery Transaction 与 Pending Policy Use 不匹配。",
            code="PENDING_USE_CONFLICT",
        )
    if transaction["status"] == "allocated":
        inspection = event_store.inspect_head_state(paths["eventStore"])
        if (
            inspection["status"] == transaction["before"]["status"]
            and inspection["head"] == transaction["before"]["head"]
            and inspection["expectedTail"] == transaction["before"]["expectedTail"]
        ):
            transaction = _perform_effect(paths, transaction_path, transaction, at)
        elif (
            inspection["status"] == "current"
            and _head_identity(inspection["head"])
            == transaction["before"]["expectedTail"]
        ):
            transaction["status"] = "effect_observed"
            transaction["observedHead"] = inspection["head"]
            transaction["outputHash"] = compute_canonical_hash(inspection["head"])
            transaction["result"] = {
                "status": "pending",
                "errorCode": None,
                "summary": "Resume 已证明 Head 等于原 Expected Tail。",
            }
            transaction["timestamps"]["updatedAt"] = at
            transaction["timestamps"]["effectObservedAt"] = at
            transaction = _write_transaction(transaction_path, transaction)
        else:
            audit_event = _find_expected_audit_event(paths["eventStore"], transaction)
            if audit_event is None:
                raise EventHeadPolicyConflictError(
                    "Pending Recovery 遇到第三 Head State；拒绝自动 rebase。",
                    code="HEAD_STATE_CONFLICT",
                )
            if transaction["observedHead"] is None or transaction["outputHash"] is None:
                raise EventHeadPolicyConflictError(
                    "Audit Event 已存在但 Transaction 缺少可证明的恢复结果。",
                    code="TRANSACTION_EFFECT_UNKNOWN",
                )
    _trip(fault_injector, "after_effect")
    completed_at = transaction["timestamps"]["effectObservedAt"] or at
    return _complete_success(
        paths, transaction_path, transaction, completed_at, finalized_at=at
    )


def resume(
    project_root: Path,
    panorama_path: Path,
    policy_id: str,
    transaction_id: str,
    *,
    resumed_at: str | None = None,
    fault_injector: FaultInjector | None = None,
) -> dict[str, Any]:
    """Resume one exact durable transaction without allocating a second use."""

    at = _normalize_time(resumed_at, "resumedAt")
    paths = _resolve_paths(project_root, panorama_path)
    _validate_policy_local_paths(paths["policyStore"], policy_id)
    with _exclusive_adapter_lock(paths["operations"]):
        transaction_path = _transaction_path(paths["operations"], transaction_id)
        if not transaction_path.is_file():
            raise EventHeadPolicyAdapterError(
                "Recovery Transaction 不存在。", code="TRANSACTION_NOT_FOUND"
            )
        transaction = _read_json(transaction_path)
        _validate_transaction(transaction)
        if transaction["policyBinding"]["policyId"] != policy_id:
            raise EventHeadPolicyConflictError(
                "Transaction Policy ID 不匹配。", code="POLICY_BINDING_MISMATCH"
            )
        return _resume_locked(paths, transaction_path, transaction, at, fault_injector)


def validate_transactions(project_root: Path) -> dict[str, Any]:
    """Read-only validation of all local Event Head recovery transactions."""

    raw_root = Path(project_root)
    if raw_root.is_symlink():
        raise EventHeadPolicyAdapterError(
            "Project Root 不允许是符号链接。", code="PATH_SYMLINK"
        )
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise EventHeadPolicyAdapterError(
            "Project Root 不可用。", code="PROJECT_ROOT_INVALID"
        ) from exc
    operations = root / Path(*OPERATIONS_REF.split("/"))
    _assert_no_symlink_components(operations, root, label="Operation Store")
    errors: list[str] = []
    counts = {status: 0 for status in ("prepared", "allocated", "effect_observed", "finalized", "failed", "conflict")}
    paths = sorted(operations.glob("EHR-*.json")) if operations.is_dir() else []
    for path in paths:
        try:
            transaction = _read_json(path)
            _validate_transaction(transaction)
            expected = f"{transaction['transactionId']}.json"
            if path.name != expected:
                errors.append(f"TRANSACTION_FILENAME {path.name}: expected {expected}")
            counts[transaction["status"]] += 1
        except EventHeadPolicyAdapterError as exc:
            errors.append(f"{exc.code} {path.name}: {exc}")
    return {
        "formatVersion": "panorama-event-head-recovery-store-validation.v0.1",
        "valid": not errors,
        "transactionCount": len(paths),
        "counts": counts,
        "recoveryRequired": counts["allocated"] > 0 or counts["effect_observed"] > 0,
        "errors": errors,
    }


__all__ = [
    "EventHeadPolicyAdapterError",
    "EventHeadPolicyConflictError",
    "InjectedAdapterCrash",
    "execute",
    "preview",
    "resume",
    "validate_transactions",
]
