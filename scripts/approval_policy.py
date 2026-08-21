"""Fail-closed Approval Policy runtime for Panorama V0.5.

This module turns an exact-hash human approval into a bounded, revocable
capability.  It deliberately keeps policy state outside formal Panorama JSON.
Every use is allocated before the caller performs an effect, and every
successful/no-op completion is bound to an immutable Engineering Event.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from jsonschema import Draft202012Validator, FormatChecker

import event_store
from panorama_io import atomic_write, compute_canonical_hash


ROOT = Path(__file__).resolve().parents[1]
POLICY_SCHEMA = ROOT / "schema" / "approval-policy.schema.v0.1.json"
LEDGER_SCHEMA = ROOT / "schema" / "approval-policy-use-ledger.schema.v0.1.json"
RECEIPT_SCHEMA = ROOT / "schema" / "policy-execution-receipt.schema.v0.1.json"
REVOCATION_SCHEMA = ROOT / "schema" / "approval-policy-revocation.schema.v0.1.json"

POLICY_FORMAT = "panorama-approval-policy.v0.1"
PREPARED_FORMAT = "panorama-prepared-approval-policy.v0.1"
APPROVAL_FORMAT = "panorama-approval-policy-approval.v0.1"
LEDGER_FORMAT = "panorama-approval-policy-use-ledger.v0.1"
RECEIPT_FORMAT = "panorama-policy-execution-receipt.v0.1"
REVOCATION_FORMAT = "panorama-approval-policy-revocation.v0.1"
MAX_JSON_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 20

SEMANTIC_FIELDS = {
    "formatVersion",
    "policyId",
    "supersedesPolicyId",
    "operation",
    "effectClass",
    "projectBinding",
    "validity",
    "scope",
    "stopConditions",
    "audit",
    "extensions",
}
PREPARED_FIELDS = {
    "preparedVersion",
    "preparedAt",
    "policy",
    "policyHash",
    "validation",
    "approvalState",
}
APPROVAL_FIELDS = {
    "approvalVersion",
    "status",
    "policyId",
    "policyHash",
    "approvedBy",
    "approvalRecordedAt",
    "approvalMethod",
    "approvalTimeSource",
}

# These paths remain human-governed even when a broad-looking template is
# approved.  The comparison is segment-based, so names merely containing the
# same text are not accidentally blocked.
GOVERNANCE_PROTECTED_TEMPLATES = (
    "/schemaVersion",
    "/intent",
    "/requirements",
    "/decisions",
    "/reviews",
    "/guidance",
    "/observationPolicy",
    "/architecture/target",
    "/modules/*/targetDesign",
    "/modules/*/governanceReferenceIds",
    "/gates/*/status",
    "/gates/*/lastRunAt",
    "/gates/*/resultSummary",
    "/acceptanceCriteria/*/status",
    "/acceptanceCriteria/*/verificationStatus",
    "/resources/*/access",
    "/meta/templateVersion",
    "/meta/createdAt",
)


class ApprovalPolicyError(RuntimeError):
    """A policy artifact or requested operation is invalid."""


class ApprovalPolicyConflictError(ApprovalPolicyError):
    """An immutable policy state or concurrent execution conflicts."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ApprovalPolicyError(f"{field} 必须是带时区的 RFC 3339 时间。") from exc
    if parsed.tzinfo is None:
        raise ApprovalPolicyError(f"{field} 必须包含时区。")
    return parsed.astimezone(timezone.utc)


def _normalize_time(value: str | None, field: str) -> str:
    parsed = _time(value or _now(), field)
    timespec = "milliseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


def _validate_json_depth(value: Any, *, name: str) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, parent_depth = stack.pop()
        if isinstance(current, (dict, list)):
            depth = parent_depth + 1
            if depth > MAX_NESTING_DEPTH:
                raise ApprovalPolicyError(
                    f"{name} JSON 嵌套深度超过 {MAX_NESTING_DEPTH}。"
                )
            children = current.values() if isinstance(current, dict) else current
            stack.extend((child, depth) for child in children)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise ApprovalPolicyError(f"Policy JSON 制品不允许是符号链接：{path}")
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ApprovalPolicyError(f"JSON 制品超过 {MAX_JSON_BYTES} bytes：{path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except ApprovalPolicyError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovalPolicyError(f"JSON 制品无效 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ApprovalPolicyError(f"JSON 制品必须是对象：{path}")
    _validate_json_depth(value, name=str(path))
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _validate_json_depth(value, name=path.name)
    event_store._validate_redaction(value)  # Shared deny-list and path checks.
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ApprovalPolicyError(f"JSON 制品超过 {MAX_JSON_BYTES} bytes：{path.name}")
    atomic_write(path, encoded)


def _validator(path: Path) -> Draft202012Validator:
    return Draft202012Validator(
        _read_json(path), format_checker=FormatChecker()
    )


def _schema_errors(value: dict[str, Any], schema: Path) -> list[str]:
    errors: list[str] = []
    for issue in sorted(
        _validator(schema).iter_errors(value), key=lambda item: list(item.absolute_path)
    ):
        pointer = "/" + "/".join(str(part) for part in issue.absolute_path)
        errors.append(f"{pointer}: {issue.message}")
    return errors


def _require_fields(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ApprovalPolicyError(f"{name} 字段不匹配；missing={missing}, extra={extra}")


def compute_policy_hash(policy: dict[str, Any]) -> str:
    semantics = copy.deepcopy(policy)
    semantics.pop("approvalBinding", None)
    return compute_canonical_hash(semantics)


def _hash_without(value: dict[str, Any], section: str, field: str) -> str:
    semantics = copy.deepcopy(value)
    target = semantics.get(section)
    if isinstance(target, dict):
        target.pop(field, None)
    return compute_canonical_hash(semantics)


def _validate_semantics(semantics: dict[str, Any]) -> None:
    _validate_json_depth(semantics, name="Policy semantics")
    _require_fields(semantics, SEMANTIC_FIELDS, "Policy semantics")
    active = copy.deepcopy(semantics)
    active["approvalBinding"] = {
        "status": "approved",
        "approvalMethod": "explicit_hash_confirmation",
        "approvedBy": "schema-probe",
        "approvalRecordedAt": active["validity"]["effectiveAt"],
        "approvalTimeSource": "approval_recorder_clock",
        "policyHash": compute_policy_hash(active),
        "hashScope": "policy_without_approvalBinding",
    }
    errors = _schema_errors(active, POLICY_SCHEMA)
    if errors:
        raise ApprovalPolicyError("Policy semantics 无效：" + "；".join(errors[:20]))
    event_store._validate_redaction(semantics)


def _validate_prepared(prepared: dict[str, Any]) -> str:
    _validate_json_depth(prepared, name="Prepared Policy")
    _require_fields(prepared, PREPARED_FIELDS, "Prepared Policy")
    if prepared.get("preparedVersion") != PREPARED_FORMAT:
        raise ApprovalPolicyError(f"preparedVersion 必须是 {PREPARED_FORMAT}")
    if prepared.get("approvalState") != "awaiting_user_approval":
        raise ApprovalPolicyError("Prepared Policy 必须处于 awaiting_user_approval。")
    expected_validation = {
        "valid": True,
        "schema": POLICY_SCHEMA.name,
        "errors": [],
    }
    if prepared.get("validation") != expected_validation:
        raise ApprovalPolicyError("Prepared Policy validation 必须是当前 Schema 的有效结果。")
    _time(prepared.get("preparedAt"), "preparedAt")
    _validate_semantics(prepared.get("policy", {}))
    expected = compute_policy_hash(prepared["policy"])
    if prepared.get("policyHash") != expected:
        raise ApprovalPolicyConflictError("Prepared Policy Hash 与语义不一致。")
    return expected


def _validate_approval(approval: dict[str, Any], prepared: dict[str, Any], expected: str) -> None:
    _validate_json_depth(approval, name="Policy Approval")
    _require_fields(approval, APPROVAL_FIELDS, "Policy Approval")
    if approval.get("approvalVersion") != APPROVAL_FORMAT:
        raise ApprovalPolicyError(f"approvalVersion 必须是 {APPROVAL_FORMAT}")
    if approval.get("status") != "approved":
        raise ApprovalPolicyError("Approval status 必须是 approved。")
    if approval.get("approvalMethod") != "explicit_hash_confirmation":
        raise ApprovalPolicyError("Approval 必须是显式 Hash 确认。")
    if approval.get("approvalTimeSource") != "approval_recorder_clock":
        raise ApprovalPolicyError("approvalTimeSource 必须是 approval_recorder_clock。")
    if (
        approval.get("policyHash") != expected
        or approval.get("policyId") != prepared["policy"].get("policyId")
    ):
        raise ApprovalPolicyConflictError("Prepared Policy 与 Approval 绑定不一致。")
    approved_by = approval.get("approvedBy")
    if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 256:
        raise ApprovalPolicyError("approvedBy 必须是 1..256 字符。")
    approval_time = _time(approval.get("approvalRecordedAt"), "approvalRecordedAt")
    if approval_time < _time(prepared["preparedAt"], "preparedAt"):
        raise ApprovalPolicyError("approvalRecordedAt 不能早于 preparedAt。")
    event_store._validate_redaction(approval)


def prepare_policy(
    semantics: dict[str, Any], *, prepared_at: str | None = None
) -> dict[str, Any]:
    """Prepare exact policy semantics for a later human hash approval."""

    semantics = copy.deepcopy(semantics)
    _validate_semantics(semantics)
    return {
        "preparedVersion": PREPARED_FORMAT,
        "preparedAt": _normalize_time(prepared_at, "preparedAt"),
        "policy": semantics,
        "policyHash": compute_policy_hash(semantics),
        "validation": {
            "valid": True,
            "schema": POLICY_SCHEMA.name,
            "errors": [],
        },
        "approvalState": "awaiting_user_approval",
    }


def record_policy_approval(
    prepared: dict[str, Any],
    *,
    confirmed_policy_hash: str,
    approved_by: str,
    approval_recorded_at: str | None = None,
) -> dict[str, Any]:
    """Record explicit confirmation of the exact prepared hash."""

    expected = _validate_prepared(prepared)
    if confirmed_policy_hash != expected:
        raise ApprovalPolicyConflictError("确认的 Policy Hash 与已准备语义不一致。")
    approved_by = approved_by.strip()
    if not approved_by or len(approved_by) > 256:
        raise ApprovalPolicyError("approvedBy 必须是 1..256 字符。")
    approval_time = _normalize_time(approval_recorded_at, "approvalRecordedAt")
    if _time(approval_time, "approvalRecordedAt") < _time(
        prepared["preparedAt"], "preparedAt"
    ):
        raise ApprovalPolicyError("approvalRecordedAt 不能早于 preparedAt。")
    approval = {
        "approvalVersion": APPROVAL_FORMAT,
        "status": "approved",
        "policyId": prepared["policy"]["policyId"],
        "policyHash": expected,
        "approvedBy": approved_by,
        "approvalRecordedAt": approval_time,
        "approvalMethod": "explicit_hash_confirmation",
        "approvalTimeSource": "approval_recorder_clock",
    }
    event_store._validate_redaction(approval)
    return approval


def materialize_policy(
    prepared: dict[str, Any], approval: dict[str, Any]
) -> dict[str, Any]:
    """Bind a valid approval record to immutable policy semantics."""

    expected = _validate_prepared(prepared)
    _validate_approval(approval, prepared, expected)
    policy = copy.deepcopy(prepared["policy"])
    policy["approvalBinding"] = {
        "status": "approved",
        "approvalMethod": "explicit_hash_confirmation",
        "approvedBy": approval["approvedBy"],
        "approvalRecordedAt": approval["approvalRecordedAt"],
        "approvalTimeSource": approval["approvalTimeSource"],
        "policyHash": expected,
        "hashScope": "policy_without_approvalBinding",
    }
    validate_active_policy(policy)
    return policy


def validate_active_policy(policy: dict[str, Any]) -> None:
    errors = _schema_errors(policy, POLICY_SCHEMA)
    if errors:
        raise ApprovalPolicyError("Active Policy 无效：" + "；".join(errors[:20]))
    expected = compute_policy_hash(policy)
    if policy["approvalBinding"]["policyHash"] != expected:
        raise ApprovalPolicyConflictError("Active Policy Hash 与语义不一致。")
    approved = _time(policy["approvalBinding"]["approvalRecordedAt"], "approvalRecordedAt")
    effective = _time(policy["validity"]["effectiveAt"], "effectiveAt")
    expires = _time(policy["validity"]["expiresAt"], "expiresAt")
    if expires <= max(approved, effective):
        raise ApprovalPolicyError("expiresAt 必须晚于 approvalRecordedAt 与 effectiveAt。")
    event_store._validate_redaction(policy)


def _policy_path(store: Path, policy_id: str) -> Path:
    return store / "policies" / f"{policy_id}.json"


def _ledger_path(store: Path, policy_id: str) -> Path:
    return store / "ledgers" / f"{policy_id}.json"


def _revocation_path(store: Path, policy_id: str) -> Path:
    return store / "revocations" / f"{policy_id}.json"


def _receipt_dir(store: Path, policy_id: str) -> Path:
    return store / "receipts" / policy_id


@contextmanager
def _exclusive_policy_lock(store: Path) -> Iterator[None]:
    store.mkdir(parents=True, exist_ok=True)
    lock = store / ".approval-policy.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ApprovalPolicyConflictError(f"Policy Store 正在被另一个 Writer 使用：{lock}") from exc
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


def _ledger_hash(ledger: dict[str, Any]) -> str:
    return _hash_without(ledger, "integrity", "ledgerHash")


def _receipt_hash(receipt: dict[str, Any]) -> str:
    return _hash_without(receipt, "integrity", "receiptHash")


def _revocation_hash(revocation: dict[str, Any]) -> str:
    return _hash_without(revocation, "integrity", "revocationHash")


def _new_ledger(policy: dict[str, Any], at: str) -> dict[str, Any]:
    ledger = {
        "formatVersion": LEDGER_FORMAT,
        "policyId": policy["policyId"],
        "policyHash": policy["approvalBinding"]["policyHash"],
        "nextUseNumber": 1,
        "previousReceiptHash": None,
        "pending": None,
        "updatedAt": at,
        "integrity": {
            "hashAlgorithm": "sha256",
            "ledgerHash": "0" * 64,
            "hashScope": "ledger_without_integrity.ledgerHash",
        },
        "extensions": {},
    }
    ledger["integrity"]["ledgerHash"] = _ledger_hash(ledger)
    return ledger


def _validate_ledger(ledger: dict[str, Any], policy: dict[str, Any]) -> None:
    errors = _schema_errors(ledger, LEDGER_SCHEMA)
    if errors:
        raise ApprovalPolicyError("Use Ledger 无效：" + "；".join(errors[:20]))
    if (
        ledger["policyId"] != policy["policyId"]
        or ledger["policyHash"] != policy["approvalBinding"]["policyHash"]
    ):
        raise ApprovalPolicyConflictError("Use Ledger 与 Policy 绑定不一致。")
    if ledger["integrity"]["ledgerHash"] != _ledger_hash(ledger):
        raise ApprovalPolicyConflictError("Use Ledger Hash 不匹配。")


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["integrity"]["ledgerHash"] = _ledger_hash(ledger)
    errors = _schema_errors(ledger, LEDGER_SCHEMA)
    if errors:
        raise ApprovalPolicyError("Use Ledger 无效：" + "；".join(errors[:20]))
    _write_json(path, ledger)


def _build_revocation(
    policy: dict[str, Any], *, at: str, by: str, method: str, reason: str, summary: str
) -> dict[str, Any]:
    identity = {
        "policyId": policy["policyId"],
        "policyHash": policy["approvalBinding"]["policyHash"],
        "revokedAt": at,
        "method": method,
    }
    revocation = {
        "formatVersion": REVOCATION_FORMAT,
        "revocationId": "PRV-" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32].upper(),
        "projectId": policy["projectBinding"]["projectId"],
        "policyId": policy["policyId"],
        "policyHash": policy["approvalBinding"]["policyHash"],
        "revokedAt": at,
        "revokedBy": by,
        "revocationMethod": method,
        "reasonClass": reason,
        "summary": summary,
        "privacy": {
            "containsProjectContent": False,
            "containsSecret": False,
            "containsSensitivePersonalData": False,
        },
        "integrity": {
            "hashAlgorithm": "sha256",
            "revocationHash": "0" * 64,
            "hashScope": "revocation_without_integrity.revocationHash",
        },
        "extensions": {},
    }
    revocation["integrity"]["revocationHash"] = _revocation_hash(revocation)
    errors = _schema_errors(revocation, REVOCATION_SCHEMA)
    if errors:
        raise ApprovalPolicyError("Revocation 无效：" + "；".join(errors[:20]))
    return revocation


def _write_once(path: Path, value: dict[str, Any], *, hash_path: tuple[str, str]) -> bool:
    if path.exists():
        existing = _read_json(path)
        section, field = hash_path
        if existing.get(section, {}).get(field) != value.get(section, {}).get(field):
            raise ApprovalPolicyConflictError(f"不可变制品已存在且内容冲突：{path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, value)
    return True


def install_policy(
    store: Path, policy: dict[str, Any], *, installed_at: str | None = None
) -> bool:
    """Activate a policy.  A superseded policy is revoked before activation."""

    store = Path(store)
    validate_active_policy(policy)
    at = _normalize_time(installed_at, "installedAt")
    policy_id = policy["policyId"]
    with _exclusive_policy_lock(store):
        target = _policy_path(store, policy_id)
        ledger_path = _ledger_path(store, policy_id)
        if target.exists():
            existing = _read_json(target)
            validate_active_policy(existing)
            if existing["approvalBinding"]["policyHash"] != policy["approvalBinding"]["policyHash"]:
                raise ApprovalPolicyConflictError("同一 policyId 已绑定不同 Policy Hash。")
            if not ledger_path.exists():
                raise ApprovalPolicyConflictError("Policy 已存在但 Use Ledger 缺失；禁止隐式修复。")
            _validate_ledger(_read_json(ledger_path), existing)
            return False

        supersedes = policy.get("supersedesPolicyId")
        if supersedes is not None:
            old_path = _policy_path(store, supersedes)
            if not old_path.exists():
                raise ApprovalPolicyConflictError("supersedesPolicyId 指向不存在的 Policy。")
            old = _read_json(old_path)
            validate_active_policy(old)
            if old["projectBinding"]["projectId"] != policy["projectBinding"]["projectId"]:
                raise ApprovalPolicyConflictError("不能跨 Project supersede Policy。")
            old_revocation = _build_revocation(
                old,
                at=at,
                by=policy["approvalBinding"]["approvedBy"],
                method="superseding_policy_approval",
                reason="superseded",
                summary=f"Superseded by {policy_id}.",
            )
            old_revocation_path = _revocation_path(store, supersedes)
            if old_revocation_path.exists():
                existing_revocation = _read_json(old_revocation_path)
                revocation_errors = _schema_errors(existing_revocation, REVOCATION_SCHEMA)
                stable_expected = {
                    "projectId": old_revocation["projectId"],
                    "policyId": old_revocation["policyId"],
                    "policyHash": old_revocation["policyHash"],
                    "revokedBy": old_revocation["revokedBy"],
                    "revocationMethod": old_revocation["revocationMethod"],
                    "reasonClass": old_revocation["reasonClass"],
                    "summary": old_revocation["summary"],
                }
                if (
                    revocation_errors
                    or existing_revocation.get("integrity", {}).get("revocationHash")
                    != _revocation_hash(existing_revocation)
                    or any(
                        existing_revocation.get(field) != expected
                        for field, expected in stable_expected.items()
                    )
                ):
                    raise ApprovalPolicyConflictError(
                        "已存在的 superseding Revocation 与待激活 Policy 不一致。"
                    )
            else:
                _write_once(
                    old_revocation_path,
                    old_revocation,
                    hash_path=("integrity", "revocationHash"),
                )

        # The policy file is the activation marker, so its ledger is durable first.
        if ledger_path.exists():
            ledger = _read_json(ledger_path)
            _validate_ledger(ledger, policy)
            if not (
                ledger["nextUseNumber"] == 1
                and ledger["previousReceiptHash"] is None
                and ledger["pending"] is None
                and ledger["extensions"] == {}
            ):
                raise ApprovalPolicyConflictError(
                    "Orphan Use Ledger 不是未使用初始状态；禁止隐式恢复。"
                )
        else:
            ledger = _new_ledger(policy, at)
            _write_once(
                ledger_path, ledger, hash_path=("integrity", "ledgerHash")
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_json(target, policy)
        return True


def revoke_policy(
    store: Path,
    policy_id: str,
    *,
    policy_hash: str,
    revoked_by: str,
    reason_class: str = "user_request",
    summary: str = "Explicit policy revocation.",
    revoked_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    store = Path(store)
    at = _normalize_time(revoked_at, "revokedAt")
    with _exclusive_policy_lock(store):
        path = _policy_path(store, policy_id)
        if not path.exists():
            raise ApprovalPolicyError(f"Policy 不存在：{policy_id}")
        policy = _read_json(path)
        validate_active_policy(policy)
        if policy["approvalBinding"]["policyHash"] != policy_hash:
            raise ApprovalPolicyConflictError("撤销请求的 Policy Hash 不匹配。")
        revocation = _build_revocation(
            policy,
            at=at,
            by=revoked_by,
            method="explicit_policy_reference",
            reason=reason_class,
            summary=summary,
        )
        created = _write_once(
            _revocation_path(store, policy_id),
            revocation,
            hash_path=("integrity", "revocationHash"),
        )
        return revocation, created


def _segments(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ApprovalPolicyError(f"非法 JSON Pointer：{pointer!r}")
    return pointer[1:].split("/")


def pointer_matches(template: str, pointer: str) -> bool:
    template_parts = _segments(template)
    pointer_parts = _segments(pointer)
    if len(template_parts) != len(pointer_parts):
        return False
    return all(expected == "*" or expected == actual for expected, actual in zip(template_parts, pointer_parts))


def protected_pointer_matches(template: str, pointer: str) -> bool:
    """Match a protected root and every descendant below that root."""

    template_parts = _segments(template)
    pointer_parts = _segments(pointer)
    if len(pointer_parts) < len(template_parts):
        return False
    return all(
        expected == "*" or expected == actual
        for expected, actual in zip(template_parts, pointer_parts)
    )


def _scope_results(
    policy: dict[str, Any], paths: list[str], artifacts: list[str]
) -> tuple[bool, bool]:
    scope = policy["scope"]
    allowed = scope["allowedPanoramaPathTemplates"]
    forbidden = list(GOVERNANCE_PROTECTED_TEMPLATES) + scope["additionalForbiddenPathTemplates"]
    governance_protected = not any(
        protected_pointer_matches(template, path)
        for path in paths
        for template in forbidden
    )
    scope_matched = all(
        any(pointer_matches(template, path) for template in allowed) for path in paths
    )
    roots = [PurePosixPath(root) for root in scope["artifactRoots"]]
    for raw in artifacts:
        pure = PurePosixPath(raw.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            scope_matched = False
            continue
        if not any(pure == root or root in pure.parents for root in roots):
            scope_matched = False
    return scope_matched, governance_protected


def _binding_allowed(policy: dict[str, Any], binding: dict[str, Any] | None) -> tuple[bool, str | None]:
    operation = policy["operation"]
    if operation == "verification_receipt.import":
        candidates = policy["scope"]["producerBindings"]
        if binding is None:
            return False, None
        exact = any(binding == item for item in candidates)
        return exact, binding.get("producerId") if exact else None
    if operation == "validation_manifest.execute":
        candidates = policy["scope"]["commandBindings"]
        if binding is None:
            return False, None
        exact = any(binding == item for item in candidates)
        return exact, binding.get("commandId") if exact else None
    return binding is None, None


def _load_policy_and_ledger(store: Path, policy_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    policy_path = _policy_path(store, policy_id)
    ledger_path = _ledger_path(store, policy_id)
    if not policy_path.exists() or not ledger_path.exists():
        raise ApprovalPolicyError(f"Active Policy 或 Use Ledger 不存在：{policy_id}")
    policy = _read_json(policy_path)
    validate_active_policy(policy)
    ledger = _read_json(ledger_path)
    _validate_ledger(ledger, policy)
    return policy, ledger


def begin_execution(
    store: Path,
    policy_id: str,
    *,
    input_hash: str,
    project_binding: dict[str, Any],
    source_binding_status: str,
    panorama_paths: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    binding: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Validate scope and allocate one use before any caller-side effect."""

    store = Path(store)
    at = _normalize_time(started_at, "startedAt")
    paths = sorted(set(panorama_paths or []))
    artifacts = sorted(set(artifact_refs or []))
    if len(input_hash) != 64 or any(ch not in "0123456789abcdef" for ch in input_hash):
        raise ApprovalPolicyError("inputHash 必须是小写 SHA-256。")
    with _exclusive_policy_lock(store):
        policy, ledger = _load_policy_and_ledger(store, policy_id)
        if _revocation_path(store, policy_id).exists():
            raise ApprovalPolicyConflictError("Policy 已撤销。")
        if ledger["pending"] is not None:
            raise ApprovalPolicyConflictError("Policy 存在 pending use；必须先完成或恢复。")
        moment = _time(at, "startedAt")
        active_from = max(
            _time(policy["validity"]["effectiveAt"], "effectiveAt"),
            _time(policy["approvalBinding"]["approvalRecordedAt"], "approvalRecordedAt"),
        )
        if moment < active_from or moment >= _time(policy["validity"]["expiresAt"], "expiresAt"):
            raise ApprovalPolicyConflictError("Policy 当前不在有效期内。")
        use_number = ledger["nextUseNumber"]
        max_uses = policy["validity"]["maxUses"]
        if max_uses is not None and use_number > max_uses:
            raise ApprovalPolicyConflictError("Policy 已达到 maxUses。")
        if project_binding.get("projectId") != policy["projectBinding"]["projectId"]:
            raise ApprovalPolicyConflictError("Project Binding 不匹配。")
        schema_version = project_binding.get("panoramaSchemaVersion")
        if schema_version not in policy["projectBinding"]["panoramaSchemaVersions"]:
            raise ApprovalPolicyConflictError("Panorama schemaVersion 不在 Policy 范围内。")
        required_source = policy["validity"]["sourceBindingRule"]
        if required_source == "revalidate_current_each_use" and source_binding_status != "matched":
            raise ApprovalPolicyConflictError("当前 Source Binding 未精确重验证。")
        if required_source == "not_applicable" and source_binding_status != "not_applicable":
            raise ApprovalPolicyConflictError("Source Binding 状态必须是 not_applicable。")
        scope_matched, governance_protected = _scope_results(policy, paths, artifacts)
        if not scope_matched:
            raise ApprovalPolicyConflictError("请求作用域越出允许路径或制品根。")
        if not governance_protected:
            raise ApprovalPolicyConflictError("请求触及人工治理保护字段。")
        binding_ok, binding_id = _binding_allowed(policy, binding)
        if not binding_ok:
            raise ApprovalPolicyConflictError("Producer/Command Binding 未被 Policy 精确授权。")
        pending = {
            "useNumber": use_number,
            "allocatedAt": at,
            "startedAt": at,
            "operation": policy["operation"],
            "effectClass": policy["effectClass"],
            "inputHash": input_hash,
            "bindingId": binding_id,
            "projectBinding": copy.deepcopy(project_binding),
            "sourceBindingStatus": source_binding_status,
            "actualPanoramaPaths": paths,
            "artifactRefs": artifacts,
        }
        ledger["pending"] = pending
        ledger["nextUseNumber"] = use_number + 1
        ledger["updatedAt"] = at
        _write_ledger(_ledger_path(store, policy_id), ledger)
        return copy.deepcopy(pending)


def _receipt_id(policy: dict[str, Any], pending: dict[str, Any]) -> str:
    identity = {
        "policyId": policy["policyId"],
        "policyHash": policy["approvalBinding"]["policyHash"],
        "useNumber": pending["useNumber"],
        "inputHash": pending["inputHash"],
    }
    return "PEX-" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32].upper()


def _event_request(
    policy: dict[str, Any], receipt_id: str, pending: dict[str, Any], result: dict[str, Any],
    *, status: str, summary: str, completed_at: str, formal_validation: str
) -> dict[str, Any]:
    idempotency = compute_canonical_hash(
        {
            "policyId": policy["policyId"],
            "policyHash": policy["approvalBinding"]["policyHash"],
            "useNumber": pending["useNumber"],
            "inputHash": pending["inputHash"],
        }
    )
    source_binding = (
        {
            "mode": "not_applicable",
            "gitHead": None,
            "sourceSnapshotHash": None,
            "sourceContentDigest": None,
            "coverage": "not_applicable",
        }
        if pending["sourceBindingStatus"] == "not_applicable"
        else {
            "mode": "unknown",
            "gitHead": None,
            "sourceSnapshotHash": pending["projectBinding"].get("sourceBindingHash"),
            "sourceContentDigest": None,
            "coverage": "unknown",
        }
    )
    return {
        "requestVersion": event_store.REQUEST_FORMAT,
        "idempotencyKey": idempotency,
        "eventType": "approval_policy.execution_recorded",
        "occurredAt": completed_at,
        "timePrecision": "millisecond",
        "actor": {"kind": "tool", "id": "approval-policy-runtime", "version": "0.1.0"},
        "projectBinding": {
            "projectId": result["projectId"],
            "panoramaSchemaVersion": result.get("panoramaSchemaVersion"),
            "baseRevision": pending["projectBinding"].get("baseRevision"),
            "resultRevision": result.get("resultRevision"),
            "baseDataHash": pending["projectBinding"].get("baseDataHash"),
            "resultDataHash": result.get("resultDataHash"),
        },
        "sourceBinding": source_binding,
        "correlation": {
            "correlationId": f"{policy['policyId']}-use-{pending['useNumber']}",
            "causationEventIds": [],
            "supersedesEventIds": [],
        },
        "subjectRefs": [
            {"type": "approval_policy", "id": policy["policyId"], "relationship": "authorizes"},
            {"type": "execution_receipt", "id": receipt_id, "relationship": "records"},
        ],
        "authority": "observed",
        "confidence": "high" if formal_validation == "passed" else "medium",
        "payload": {
            "policyId": policy["policyId"],
            "policyHash": policy["approvalBinding"]["policyHash"],
            "useNumber": pending["useNumber"],
            "operation": policy["operation"],
            "effectClass": policy["effectClass"],
            "receiptId": receipt_id,
        },
        "evidenceBindings": [],
        "informationGaps": [],
        "outcome": {
            "status": status,
            "labelStrength": "formally_validated" if formal_validation == "passed" else "observed",
            "summary": summary,
        },
        "privacy": {
            "accessClass": "PROJECT_OPERATIONAL_METADATA",
            "containsProjectContent": False,
            "containsSecret": False,
            "containsSensitivePersonalData": False,
            "redactionState": "none",
            "retentionPolicyId": None,
        },
        "extensions": {},
    }


def complete_execution(
    store: Path,
    policy_id: str,
    *,
    use_number: int,
    status: str,
    result_project_binding: dict[str, Any],
    output_hash: str | None,
    panorama_paths: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    formal_validation: str = "not_applicable",
    external_effect_observed: bool = False,
    governance_mutation_observed: bool = False,
    summary: str = "",
    event_store_path: Path | None = None,
    project_root: Path | None = None,
    completed_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Finalize a pending use; failed attempts also consume their use number."""

    store = Path(store)
    at = _normalize_time(completed_at, "completedAt")
    if status not in {"succeeded", "failed", "no_op"}:
        raise ApprovalPolicyError("status 必须是 succeeded、failed 或 no_op。")
    if formal_validation not in {"passed", "failed", "not_applicable"}:
        raise ApprovalPolicyError("formalValidation 值无效。")
    paths = sorted(set(panorama_paths or []))
    artifacts = sorted(set(artifact_refs or []))
    with _exclusive_policy_lock(store):
        policy, ledger = _load_policy_and_ledger(store, policy_id)
        pending = ledger["pending"]
        if pending is None or pending["useNumber"] != use_number:
            raise ApprovalPolicyConflictError("待完成的 useNumber 不匹配。")
        scope_matched, governance_protected = _scope_results(policy, paths, artifacts)
        project_matched = result_project_binding.get("projectId") == policy["projectBinding"]["projectId"]
        source_ok = pending["sourceBindingStatus"] in {"matched", "not_applicable"}
        within_validity = _time(at, "completedAt") < _time(policy["validity"]["expiresAt"], "expiresAt")
        within_use_limit = policy["validity"]["maxUses"] is None or use_number <= policy["validity"]["maxUses"]
        receipt_rules = policy["scope"].get("receiptRules") or {}
        formal_validation_ok = (
            formal_validation == "passed"
            if receipt_rules.get("requireFormalValidation") is True
            else formal_validation != "failed"
        )
        success_valid = all(
            [
                project_matched,
                source_ok,
                within_validity,
                within_use_limit,
                scope_matched,
                governance_protected,
                formal_validation_ok,
                not external_effect_observed,
                not governance_mutation_observed,
            ]
        )
        final_status = status
        if status in {"succeeded", "no_op"} and not success_valid:
            final_status = "failed"
            summary = summary or "Execution result violated an Approval Policy stop condition."
        if final_status == "succeeded" and output_hash is None:
            final_status = "failed"
            summary = summary or "Succeeded execution did not provide outputHash."
        receipt_id = _receipt_id(policy, pending)
        event_binding: dict[str, Any] | None = None
        if final_status in {"succeeded", "no_op"}:
            if event_store_path is None:
                raise ApprovalPolicyError(
                    "成功或 no-op 必须先写入 Engineering Event；pending use 已保留。"
                )
            request = _event_request(
                policy,
                receipt_id,
                pending,
                result_project_binding,
                status=final_status,
                summary=summary,
                completed_at=at,
                formal_validation=formal_validation,
            )
            event, _ = event_store.record_request(
                Path(event_store_path), request, recorded_at=at, project_root=project_root
            )
            event_binding = {
                "eventId": event["eventId"],
                "eventHash": event["integrity"]["eventHash"],
            }
        receipt = {
            "formatVersion": RECEIPT_FORMAT,
            "receiptId": receipt_id,
            "policyBinding": {
                "policyId": policy["policyId"],
                "policyHash": policy["approvalBinding"]["policyHash"],
                "operation": policy["operation"],
            },
            "projectBinding": {
                "projectId": result_project_binding.get("projectId"),
                "panoramaSchemaVersion": result_project_binding.get("panoramaSchemaVersion"),
                "baseRevision": pending["projectBinding"].get("baseRevision"),
                "resultRevision": result_project_binding.get("resultRevision"),
                "baseDataHash": pending["projectBinding"].get("baseDataHash"),
                "resultDataHash": result_project_binding.get("resultDataHash"),
                "sourceBindingHash": pending["projectBinding"].get("sourceBindingHash"),
            },
            "execution": {
                "useNumber": use_number,
                "allocatedAt": pending["allocatedAt"],
                "startedAt": pending["startedAt"],
                "completedAt": at,
                "status": final_status,
                "inputHash": pending["inputHash"],
                "outputHash": output_hash,
                "effectClass": policy["effectClass"],
                "bindingId": pending["bindingId"],
                "summary": summary,
            },
            "validation": {
                "policyHashValid": True,
                "withinValidity": within_validity,
                "withinUseLimit": within_use_limit,
                "projectMatched": project_matched,
                "sourceBindingStatus": pending["sourceBindingStatus"] if source_ok else "mismatch",
                "operationAllowed": True,
                "scopeMatched": scope_matched,
                "governanceProtected": governance_protected,
                "formalValidation": formal_validation,
            },
            "effect": {
                "actualPanoramaPaths": paths,
                "artifactRefs": artifacts,
                "externalEffectObserved": bool(external_effect_observed),
                "governanceMutationObserved": bool(governance_mutation_observed),
            },
            "eventBinding": event_binding,
            "privacy": {
                "containsProjectContent": False,
                "containsSecret": False,
                "containsSensitivePersonalData": False,
            },
            "integrity": {
                "hashAlgorithm": "sha256",
                "previousReceiptHash": ledger["previousReceiptHash"],
                "receiptHash": "0" * 64,
                "hashScope": "receipt_without_integrity.receiptHash",
            },
            "extensions": {},
        }
        receipt["integrity"]["receiptHash"] = _receipt_hash(receipt)
        errors = _schema_errors(receipt, RECEIPT_SCHEMA)
        if errors:
            raise ApprovalPolicyError("Execution Receipt 无效：" + "；".join(errors[:20]))
        receipt_path = _receipt_dir(store, policy_id) / f"{use_number:08d}-{receipt_id}.json"
        created = _write_once(
            receipt_path, receipt, hash_path=("integrity", "receiptHash")
        )
        ledger["pending"] = None
        ledger["previousReceiptHash"] = receipt["integrity"]["receiptHash"]
        ledger["updatedAt"] = at
        _write_ledger(_ledger_path(store, policy_id), ledger)
        return receipt, created


def recover_pending(store: Path, policy_id: str, *, recovered_at: str | None = None) -> dict[str, Any]:
    """Advance a ledger only when its deterministic receipt is already durable."""

    store = Path(store)
    at = _normalize_time(recovered_at, "recoveredAt")
    with _exclusive_policy_lock(store):
        policy, ledger = _load_policy_and_ledger(store, policy_id)
        pending = ledger["pending"]
        if pending is None:
            return {"recovered": False, "reason": "no_pending"}
        receipt_id = _receipt_id(policy, pending)
        path = _receipt_dir(store, policy_id) / f"{pending['useNumber']:08d}-{receipt_id}.json"
        if not path.exists():
            raise ApprovalPolicyConflictError(
                "pending use 尚无 durable receipt；必须以 failed 或实际结果完成，不能自动跳过。"
            )
        receipt = _read_json(path)
        errors = _schema_errors(receipt, RECEIPT_SCHEMA)
        if errors or receipt["integrity"]["receiptHash"] != _receipt_hash(receipt):
            raise ApprovalPolicyConflictError("pending use 的 durable receipt 无效。")
        if receipt["integrity"]["previousReceiptHash"] != ledger["previousReceiptHash"]:
            raise ApprovalPolicyConflictError("pending receipt 与当前 Receipt Chain 不连续。")
        ledger["pending"] = None
        ledger["previousReceiptHash"] = receipt["integrity"]["receiptHash"]
        ledger["updatedAt"] = at
        _write_ledger(_ledger_path(store, policy_id), ledger)
        return {"recovered": True, "receiptId": receipt["receiptId"]}


def inspect_policy_state(store: Path, policy_id: str) -> dict[str, Any]:
    """Return a validated, read-only snapshot for an operation adapter."""

    store = Path(store)
    with _exclusive_policy_lock(store):
        policy, ledger = _load_policy_and_ledger(store, policy_id)
        return {
            "policy": copy.deepcopy(policy),
            "ledger": copy.deepcopy(ledger),
            "revoked": _revocation_path(store, policy_id).exists(),
        }


def read_execution_receipt(
    store: Path, policy_id: str, use_number: int
) -> dict[str, Any] | None:
    """Read one validated durable receipt without changing the use ledger."""

    store = Path(store)
    with _exclusive_policy_lock(store):
        policy, _ = _load_policy_and_ledger(store, policy_id)
        paths = sorted(_receipt_dir(store, policy_id).glob(f"{use_number:08d}-*.json"))
        if not paths:
            return None
        if len(paths) != 1:
            raise ApprovalPolicyConflictError(
                f"Use {use_number} 存在多个 Execution Receipt。"
            )
        receipt = _read_json(paths[0])
        errors = _schema_errors(receipt, RECEIPT_SCHEMA)
        if errors or receipt["integrity"]["receiptHash"] != _receipt_hash(receipt):
            raise ApprovalPolicyConflictError("Execution Receipt 无效或已被篡改。")
        if (
            receipt["policyBinding"]["policyId"] != policy_id
            or receipt["policyBinding"]["policyHash"]
            != policy["approvalBinding"]["policyHash"]
            or receipt["execution"]["useNumber"] != use_number
        ):
            raise ApprovalPolicyConflictError("Execution Receipt 与 Policy/Use Binding 不匹配。")
        expected_name = f"{use_number:08d}-{receipt['receiptId']}.json"
        if paths[0].name != expected_name:
            raise ApprovalPolicyConflictError("Execution Receipt 文件名 Binding 不匹配。")
        return copy.deepcopy(receipt)


def validate_policy_store(store: Path) -> dict[str, Any]:
    """Return a machine-readable audit of policies, ledgers and receipt chains."""

    store = Path(store)
    errors: list[str] = []
    policy_count = receipt_count = pending_count = 0
    policy_dir = store / "policies"
    paths = sorted(policy_dir.glob("*.json")) if policy_dir.exists() else []
    for path in paths:
        policy_count += 1
        try:
            policy = _read_json(path)
            validate_active_policy(policy)
            policy_id = policy["policyId"]
            if path.name != f"{policy_id}.json":
                errors.append(f"POLICY_FILENAME {path.name}: 与 policyId 不一致。")
            ledger = _read_json(_ledger_path(store, policy_id))
            _validate_ledger(ledger, policy)
            if ledger["pending"] is not None:
                pending_count += 1
            previous: str | None = None
            expected_use = 1
            receipt_paths = sorted(_receipt_dir(store, policy_id).glob("*.json")) if _receipt_dir(store, policy_id).exists() else []
            for receipt_path in receipt_paths:
                receipt_count += 1
                receipt = _read_json(receipt_path)
                schema_errors = _schema_errors(receipt, RECEIPT_SCHEMA)
                if schema_errors:
                    errors.append(f"RECEIPT_SCHEMA {receipt_path.name}: {'; '.join(schema_errors[:5])}")
                    continue
                if receipt["integrity"]["receiptHash"] != _receipt_hash(receipt):
                    errors.append(f"RECEIPT_HASH {receipt_path.name}: hash 不匹配。")
                if receipt["policyBinding"]["policyHash"] != policy["approvalBinding"]["policyHash"]:
                    errors.append(f"RECEIPT_POLICY {receipt_path.name}: Policy Hash 不匹配。")
                if receipt["execution"]["useNumber"] != expected_use:
                    errors.append(f"RECEIPT_USE {receipt_path.name}: expected {expected_use}。")
                expected_name = (
                    f"{receipt['execution']['useNumber']:08d}-{receipt['receiptId']}.json"
                )
                if receipt_path.name != expected_name:
                    errors.append(
                        f"RECEIPT_FILENAME {receipt_path.name}: expected {expected_name}。"
                    )
                if receipt["integrity"]["previousReceiptHash"] != previous:
                    errors.append(f"RECEIPT_CHAIN {receipt_path.name}: previousReceiptHash 不匹配。")
                previous = receipt["integrity"]["receiptHash"]
                expected_use += 1
            pending = ledger["pending"]
            expected_next_use = expected_use
            if pending is not None and pending["useNumber"] >= expected_use:
                expected_next_use = pending["useNumber"] + 1
            if ledger["nextUseNumber"] != expected_next_use:
                errors.append(
                    f"LEDGER_USE {policy_id}: nextUseNumber expected "
                    f"{expected_next_use}, got {ledger['nextUseNumber']}。"
                )
            if ledger["previousReceiptHash"] != previous:
                # A durable pending receipt is the one allowed recovery-required state.
                allowed_pending_tail = False
                if pending is not None:
                    rid = _receipt_id(policy, pending)
                    expected_path = _receipt_dir(store, policy_id) / f"{pending['useNumber']:08d}-{rid}.json"
                    allowed_pending_tail = expected_path.exists() and previous != ledger["previousReceiptHash"]
                if not allowed_pending_tail:
                    errors.append(f"LEDGER_CHAIN {policy_id}: previousReceiptHash 与 Receipt Chain 不一致。")
            revocation_path = _revocation_path(store, policy_id)
            if revocation_path.exists():
                revocation = _read_json(revocation_path)
                rev_errors = _schema_errors(revocation, REVOCATION_SCHEMA)
                if rev_errors:
                    errors.append(f"REVOCATION_SCHEMA {policy_id}: {'; '.join(rev_errors[:5])}")
                elif (
                    revocation["policyHash"] != policy["approvalBinding"]["policyHash"]
                    or revocation["integrity"]["revocationHash"] != _revocation_hash(revocation)
                ):
                    errors.append(f"REVOCATION_BINDING {policy_id}: Revocation 不匹配。")
            supersedes = policy.get("supersedesPolicyId")
            if supersedes is not None:
                old_path = _policy_path(store, supersedes)
                old_revocation_path = _revocation_path(store, supersedes)
                if not old_path.exists() or not old_revocation_path.exists():
                    errors.append(
                        f"SUPERSEDES {policy_id}: 被替代 Policy 或其 Revocation 缺失。"
                    )
                else:
                    old = _read_json(old_path)
                    old_revocation = _read_json(old_revocation_path)
                    if (
                        old_revocation.get("revocationMethod")
                        != "superseding_policy_approval"
                        or old_revocation.get("policyHash")
                        != old.get("approvalBinding", {}).get("policyHash")
                    ):
                        errors.append(
                            f"SUPERSEDES {policy_id}: 旧 Policy 未由 superseding approval 精确撤销。"
                        )
        except ApprovalPolicyError as exc:
            errors.append(f"POLICY {path.name}: {exc}")
    ledger_dir = store / "ledgers"
    if ledger_dir.exists():
        for path in ledger_dir.glob("*.json"):
            if not _policy_path(store, path.stem).exists():
                errors.append(f"ORPHAN_LEDGER {path.name}: 缺少 Active Policy。")
    return {
        "formatVersion": "panorama-approval-policy-store-validation.v0.1",
        "valid": not errors,
        "policyCount": policy_count,
        "receiptCount": receipt_count,
        "pendingCount": pending_count,
        "recoveryRequired": pending_count > 0,
        "errors": errors,
    }


__all__ = [
    "APPROVAL_FORMAT",
    "ApprovalPolicyConflictError",
    "ApprovalPolicyError",
    "PREPARED_FORMAT",
    "begin_execution",
    "complete_execution",
    "compute_policy_hash",
    "install_policy",
    "inspect_policy_state",
    "materialize_policy",
    "pointer_matches",
    "protected_pointer_matches",
    "prepare_policy",
    "record_policy_approval",
    "read_execution_receipt",
    "recover_pending",
    "revoke_policy",
    "validate_active_policy",
    "validate_policy_store",
]
