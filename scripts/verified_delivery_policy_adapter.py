"""Approval-Policy adapter for atomic, resumable Verified Delivery promotion."""

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
from panorama_io import atomic_write, compute_canonical_hash
from verified_delivery import DELIVERY_REF, VerifiedDeliveryError, load_delivery


ROOT = Path(__file__).resolve().parents[1]
TRANSACTION_SCHEMA = ROOT / "schema" / "verified-delivery-promotion-transaction.schema.v0.1.json"
TRANSACTION_FORMAT = "panorama-verified-delivery-promotion-transaction.v0.1"
POLICY_STORE_REF = ".panorama-work/approval-policy-store/v0.1"
EVENT_STORE_REF = ".panorama-work/event-store/v0.1"
OPERATIONS_REF = ".panorama-work/approval-policy-operations/v0.1/verified-delivery-promote"
LAST_GOOD_REF = f"{DELIVERY_REF}/last-good/index.html"
ARTIFACT_REFS = [DELIVERY_REF]
POLICY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class VerifiedDeliveryPolicyAdapterError(RuntimeError):
    def __init__(self, message: str, *, code: str = "ADAPTER_INVALID") -> None:
        super().__init__(message)
        self.code = code


class VerifiedDeliveryPolicyConflictError(VerifiedDeliveryPolicyAdapterError):
    pass


class InjectedAdapterCrash(RuntimeError):
    pass


FaultInjector = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_time(value: str | None) -> str:
    candidate = value or _now()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise VerifiedDeliveryPolicyAdapterError("时间必须是带时区的 RFC 3339。", code="TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise VerifiedDeliveryPolicyAdapterError("时间必须包含时区。", code="TIME_INVALID")
    timespec = "milliseconds" if parsed.microsecond else "seconds"
    return parsed.astimezone(timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")


def _time(value: str) -> datetime:
    return datetime.fromisoformat(_normalize_time(value).replace("Z", "+00:00"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _state_hash(transaction: dict[str, Any]) -> str:
    value = copy.deepcopy(transaction)
    value["integrity"].pop("stateHash", None)
    return compute_canonical_hash(value)


def _validator() -> Draft202012Validator:
    schema = json.loads(TRANSACTION_SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_transaction(transaction: dict[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(transaction), key=lambda item: list(item.absolute_path))
    if errors:
        rendered = []
        for issue in errors[:20]:
            pointer = "/" + "/".join(str(part) for part in issue.absolute_path)
            rendered.append(f"{pointer}: {issue.message}")
        raise VerifiedDeliveryPolicyAdapterError("Promotion Transaction Schema 无效：" + "；".join(rendered), code="TRANSACTION_SCHEMA_INVALID")
    if transaction["integrity"]["stateHash"] != _state_hash(transaction):
        raise VerifiedDeliveryPolicyConflictError("Promotion Transaction State Hash 不匹配。", code="TRANSACTION_HASH_MISMATCH")
    status = transaction["status"]
    use_number = transaction["policyBinding"]["useNumber"]
    if status == "prepared" and use_number is not None:
        raise VerifiedDeliveryPolicyAdapterError("prepared Transaction 不得绑定 Use。", code="TRANSACTION_STATE_INVALID")
    if status in {"allocated", "effect_observed", "finalized"} and use_number is None:
        raise VerifiedDeliveryPolicyAdapterError("已分配 Transaction 缺少 Use。", code="TRANSACTION_STATE_INVALID")
    if status in {"effect_observed", "finalized"} and transaction["outputHash"] is None:
        raise VerifiedDeliveryPolicyAdapterError("已观察效果的 Transaction 缺少 outputHash。", code="TRANSACTION_STATE_INVALID")
    if status == "finalized" and (transaction["receiptBinding"] is None or transaction["eventBinding"] is None):
        raise VerifiedDeliveryPolicyAdapterError("finalized Transaction 缺少审计 Binding。", code="TRANSACTION_STATE_INVALID")


def _read_transaction(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise VerifiedDeliveryPolicyAdapterError("Promotion Transaction 路径无效。", code="TRANSACTION_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerifiedDeliveryPolicyAdapterError("Promotion Transaction JSON 无效。", code="TRANSACTION_INVALID") from exc
    if not isinstance(value, dict):
        raise VerifiedDeliveryPolicyAdapterError("Promotion Transaction 必须是 object。", code="TRANSACTION_INVALID")
    _validate_transaction(value)
    return value


def _write_transaction(path: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(transaction)
    value["integrity"]["stateHash"] = "0" * 64
    value["integrity"]["stateHash"] = _state_hash(value)
    _validate_transaction(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise VerifiedDeliveryPolicyAdapterError("Operation Store 不允许是符号链接。", code="PATH_SYMLINK")
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return value


def _resolve_paths(project_root: Path) -> dict[str, Path]:
    raw = Path(project_root)
    if raw.is_symlink():
        raise VerifiedDeliveryPolicyAdapterError("Project Root 不允许是符号链接。", code="PATH_SYMLINK")
    try:
        root = raw.resolve(strict=True)
    except OSError as exc:
        raise VerifiedDeliveryPolicyAdapterError("Project Root 不存在。", code="PROJECT_ROOT_INVALID") from exc
    if not root.is_dir():
        raise VerifiedDeliveryPolicyAdapterError("Project Root 必须是目录。", code="PROJECT_ROOT_INVALID")
    paths = {
        "root": root,
        "delivery": root / Path(*DELIVERY_REF.split("/")),
        "lastGood": root / Path(*LAST_GOOD_REF.split("/")),
        "policyStore": root / Path(*POLICY_STORE_REF.split("/")),
        "eventStore": root / Path(*EVENT_STORE_REF.split("/")),
        "operations": root / Path(*OPERATIONS_REF.split("/")),
    }
    for label, path in paths.items():
        if label == "root":
            continue
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise VerifiedDeliveryPolicyAdapterError(f"{label} 路径不允许经过符号链接。", code="PATH_SYMLINK")
    if not paths["delivery"].is_dir() or not paths["policyStore"].is_dir():
        raise VerifiedDeliveryPolicyAdapterError("Delivery 或 Approval Policy Store 不存在。", code="STORE_NOT_FOUND")
    return paths


def _artifact_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing"}
    if path.is_symlink() or not path.is_file():
        raise VerifiedDeliveryPolicyConflictError("last-good 不是安全普通文件。", code="LAST_GOOD_INVALID")
    value = path.read_bytes()
    return {"status": "present", "sha256": _sha256(value), "bytes": len(value)}


def _expected_state(receipt: dict[str, Any]) -> dict[str, Any]:
    return {"status": "present", "sha256": receipt["artifact"]["sha256"], "bytes": receipt["artifact"]["bytes"]}


def _validate_policy_profile(state: dict[str, Any], receipt: dict[str, Any], *, at: str, allow_pending: bool = False) -> dict[str, Any]:
    policy = state["policy"]
    if state["revoked"]:
        raise VerifiedDeliveryPolicyConflictError("Policy 已撤销。", code="POLICY_REVOKED")
    if policy["operation"] != "verified_delivery.promote" or policy["effectClass"] != "artifact_promotion":
        raise VerifiedDeliveryPolicyConflictError("Policy Operation/Effect Class 不匹配。", code="POLICY_PROFILE_MISMATCH")
    project = receipt["projectBinding"]
    if policy["projectBinding"]["projectId"] != project["projectId"] or project["panoramaSchemaVersion"] not in policy["projectBinding"]["panoramaSchemaVersions"]:
        raise VerifiedDeliveryPolicyConflictError("Policy 与 Delivery Project Binding 不匹配。", code="PROJECT_BINDING_MISMATCH")
    rules = policy["scope"].get("deliveryRules")
    exact_rules = {
        "allowPendingVisualReview": True,
        "rejectedVisualReviewBlocks": True,
        "atomicLastGoodReplace": True,
        "coreMutationAllowed": False,
        "externalPublicationAllowed": False,
    }
    scope = policy["scope"]
    if not (
        scope["governanceProtectionProfile"] == "panorama-governance-v0.5"
        and scope["allowedPanoramaPathTemplates"] == []
        and scope["additionalForbiddenPathTemplates"] == []
        and scope["artifactRoots"] == ARTIFACT_REFS
        and scope["producerBindings"] == []
        and scope["commandBindings"] == []
        and scope["receiptRules"] is None
        and scope["recoveryRules"] is None
        and isinstance(rules, dict)
        and set(rules.get("requiredMachineChecks", [])) == {"semantic", "schema", "geometry", "browser", "privacy"}
        and {key: value for key, value in rules.items() if key != "requiredMachineChecks"} == exact_rules
    ):
        raise VerifiedDeliveryPolicyConflictError("Policy Scope/Delivery Rules 不是 V0.6 fail-closed Profile。", code="POLICY_PROFILE_MISMATCH")
    if policy["validity"]["sourceBindingRule"] != "not_applicable" or not all(policy["stopConditions"].values()):
        raise VerifiedDeliveryPolicyConflictError("Policy Validity/Stop Conditions 不匹配。", code="POLICY_PROFILE_MISMATCH")
    if policy["audit"] != {"executionReceiptRequired": True, "policyBindingRequired": True, "eventRecordingRequired": True, "failureMode": "fail_closed"}:
        raise VerifiedDeliveryPolicyConflictError("Policy Audit Profile 不匹配。", code="POLICY_PROFILE_MISMATCH")
    if receipt["gates"]["browser"]["status"] != "passed" or receipt["gates"]["visualReview"] == "rejected":
        raise VerifiedDeliveryPolicyConflictError("Delivery 门禁不允许晋升。", code="DELIVERY_GATE_FAILED")
    moment = _time(at)
    active_from = max(_time(policy["validity"]["effectiveAt"]), _time(policy["approvalBinding"]["approvalRecordedAt"]))
    if moment < active_from or moment >= _time(policy["validity"]["expiresAt"]):
        raise VerifiedDeliveryPolicyConflictError("Policy 当前不在有效期内。", code="POLICY_INACTIVE")
    max_uses = policy["validity"]["maxUses"]
    if max_uses is not None and state["ledger"]["nextUseNumber"] > max_uses:
        raise VerifiedDeliveryPolicyConflictError("Policy 已达到 maxUses。", code="POLICY_USE_LIMIT")
    if not allow_pending and state["ledger"]["pending"] is not None:
        raise VerifiedDeliveryPolicyConflictError("Policy 存在 Pending Use。", code="PENDING_USE_CONFLICT")
    return policy


def _input_hash(policy: dict[str, Any], receipt: dict[str, Any], before: dict[str, Any]) -> str:
    return compute_canonical_hash({
        "operation": "verified_delivery.promote",
        "policyId": policy["policyId"],
        "policyHash": policy["approvalBinding"]["policyHash"],
        "deliveryId": receipt["deliveryId"],
        "deliveryReceiptHash": receipt["integrity"]["receiptHash"],
        "candidate": _expected_state(receipt),
        "lastGoodBefore": before,
        "lastGoodRef": LAST_GOOD_REF,
        "deliveryRules": policy["scope"]["deliveryRules"],
    })


def _transaction_id(policy: dict[str, Any], input_hash: str) -> str:
    digest = compute_canonical_hash({"policyId": policy["policyId"], "policyHash": policy["approvalBinding"]["policyHash"], "inputHash": input_hash})
    return "VDP-" + digest[:32].upper()


def preview(project_root: Path, delivery_id: str, policy_id: str, *, previewed_at: str | None = None) -> dict[str, Any]:
    at = _normalize_time(previewed_at)
    if POLICY_ID_PATTERN.fullmatch(policy_id) is None:
        raise VerifiedDeliveryPolicyAdapterError("policyId 格式无效。", code="POLICY_ID_INVALID")
    paths = _resolve_paths(project_root)
    try:
        receipt, _ = load_delivery(paths["root"], delivery_id)
    except VerifiedDeliveryError as exc:
        raise VerifiedDeliveryPolicyAdapterError(str(exc), code="DELIVERY_INVALID") from exc
    state = approval_policy.inspect_policy_state(paths["policyStore"], policy_id)
    policy = _validate_policy_profile(state, receipt, at=at)
    before = _artifact_state(paths["lastGood"])
    expected = _expected_state(receipt)
    if before == expected:
        return {"status": "no_op", "code": "ALREADY_LAST_GOOD", "deliveryId": delivery_id, "policyId": policy_id, "inputHash": None, "transactionId": None, "lastGood": before}
    input_hash = _input_hash(policy, receipt, before)
    return {
        "status": "promotable", "code": "PROMOTION_ALLOWED", "deliveryId": delivery_id,
        "policyId": policy_id, "policyHash": policy["approvalBinding"]["policyHash"],
        "projectBinding": receipt["projectBinding"], "receiptHash": receipt["integrity"]["receiptHash"],
        "artifact": receipt["artifact"], "visualReview": receipt["gates"]["visualReview"],
        "lastGoodBefore": before, "lastGoodExpected": expected, "inputHash": input_hash,
        "transactionId": _transaction_id(policy, input_hash),
    }


@contextmanager
def _lock(operations: Path) -> Iterator[None]:
    operations.mkdir(parents=True, exist_ok=True)
    lock = operations / ".verified-delivery-promote.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise VerifiedDeliveryPolicyConflictError("Promotion Adapter 正被另一个 Writer 使用。", code="ADAPTER_BUSY") from exc
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


def _active_transaction(operations: Path, policy_id: str) -> tuple[Path, dict[str, Any]] | None:
    matches = []
    for path in sorted(operations.glob("VDP-*.json")) if operations.is_dir() else []:
        value = _read_transaction(path)
        if value["policyBinding"]["policyId"] == policy_id and value["status"] in {"prepared", "allocated", "effect_observed"}:
            matches.append((path, value))
    if len(matches) > 1:
        raise VerifiedDeliveryPolicyConflictError("同一 Policy 存在多个非终态 Promotion Transaction。", code="TRANSACTION_CONFLICT")
    return matches[0] if matches else None


def _create_transaction(path: Path, value: dict[str, Any], at: str) -> dict[str, Any]:
    transaction = {
        "formatVersion": TRANSACTION_FORMAT, "transactionId": value["transactionId"], "status": "prepared",
        "policyBinding": {"policyId": value["policyId"], "policyHash": value["policyHash"], "useNumber": None},
        "projectBinding": value["projectBinding"],
        "deliveryBinding": {"deliveryId": value["deliveryId"], "receiptHash": value["receiptHash"], "artifactSha256": value["artifact"]["sha256"], "artifactBytes": value["artifact"]["bytes"], "visualReview": value["visualReview"]},
        "lastGood": {"ref": LAST_GOOD_REF, "before": value["lastGoodBefore"], "expected": value["lastGoodExpected"]},
        "inputHash": value["inputHash"], "outputHash": None,
        "result": {"status": "pending", "errorCode": None, "summary": ""},
        "receiptBinding": None, "eventBinding": None,
        "timestamps": {"preparedAt": at, "updatedAt": at, "effectObservedAt": None, "finalizedAt": None},
        "integrity": {"hashAlgorithm": "sha256", "stateHash": "0" * 64, "hashScope": "transaction_without_integrity.stateHash"},
    }
    if path.exists():
        existing = _read_transaction(path)
        if existing["inputHash"] != transaction["inputHash"] or existing["policyBinding"]["policyHash"] != transaction["policyBinding"]["policyHash"]:
            raise VerifiedDeliveryPolicyConflictError("确定性 Transaction ID Binding 冲突。", code="TRANSACTION_CONFLICT")
        return existing
    return _write_transaction(path, transaction)


def _project_begin(project: dict[str, Any]) -> dict[str, Any]:
    return {"projectId": project["projectId"], "panoramaSchemaVersion": project["panoramaSchemaVersion"], "baseRevision": project["revision"], "baseDataHash": project["dataHash"], "sourceBindingHash": None}


def _project_result(project: dict[str, Any]) -> dict[str, Any]:
    return {"projectId": project["projectId"], "panoramaSchemaVersion": project["panoramaSchemaVersion"], "resultRevision": project["revision"], "resultDataHash": project["dataHash"]}


def _receipt_binding(receipt: dict[str, Any]) -> dict[str, Any]:
    return {"receiptId": receipt["receiptId"], "receiptHash": receipt["integrity"]["receiptHash"]}


def _trip(injector: FaultInjector | None, point: str) -> None:
    if injector is not None:
        injector(point)


def _perform_effect(paths: dict[str, Path], path: Path, transaction: dict[str, Any], at: str) -> dict[str, Any]:
    observed = _artifact_state(paths["lastGood"])
    if observed == transaction["lastGood"]["expected"]:
        pass
    elif observed == transaction["lastGood"]["before"]:
        try:
            _, candidate = load_delivery(paths["root"], transaction["deliveryBinding"]["deliveryId"])
            paths["lastGood"].parent.mkdir(parents=True, exist_ok=True)
            atomic_write(paths["lastGood"], candidate.read_text(encoding="utf-8"))
        except (VerifiedDeliveryError, OSError, UnicodeError) as exc:
            after_error = _artifact_state(paths["lastGood"])
            if after_error == transaction["lastGood"]["expected"]:
                pass
            elif after_error == transaction["lastGood"]["before"]:
                raise VerifiedDeliveryPolicyAdapterError("候选复验或 last-good 原子替换失败，未观察到副作用。", code="LAST_GOOD_WRITE_FAILED") from exc
            else:
                raise VerifiedDeliveryPolicyConflictError("Promotion 失败后 last-good 进入第三状态。", code="LAST_GOOD_STATE_CONFLICT") from exc
    else:
        raise VerifiedDeliveryPolicyConflictError("last-good 已进入第三状态；拒绝覆盖或 rebase。", code="LAST_GOOD_STATE_CONFLICT")
    if _artifact_state(paths["lastGood"]) != transaction["lastGood"]["expected"]:
        raise VerifiedDeliveryPolicyAdapterError("last-good 原子替换未产生精确候选字节。", code="LAST_GOOD_WRITE_FAILED")
    transaction["status"] = "effect_observed"
    transaction["outputHash"] = compute_canonical_hash({"lastGood": transaction["lastGood"]["expected"], "deliveryReceiptHash": transaction["deliveryBinding"]["receiptHash"]})
    transaction["result"] = {"status": "pending", "errorCode": None, "summary": "last-good 已替换，等待 Audit Event 与 Receipt。"}
    transaction["timestamps"]["updatedAt"] = at
    transaction["timestamps"]["effectObservedAt"] = at
    return _write_transaction(path, transaction)


def _complete_success(paths: dict[str, Path], path: Path, transaction: dict[str, Any], at: str) -> dict[str, Any]:
    if _artifact_state(paths["lastGood"]) != transaction["lastGood"]["expected"]:
        raise VerifiedDeliveryPolicyConflictError("完成审计前 last-good 已漂移。", code="LAST_GOOD_STATE_CONFLICT")
    use_number = transaction["policyBinding"]["useNumber"]
    assert isinstance(use_number, int)
    durable = approval_policy.read_execution_receipt(paths["policyStore"], transaction["policyBinding"]["policyId"], use_number)
    if durable is not None:
        state = approval_policy.inspect_policy_state(paths["policyStore"], transaction["policyBinding"]["policyId"])
        if state["ledger"]["pending"] is not None:
            approval_policy.recover_pending(paths["policyStore"], transaction["policyBinding"]["policyId"], recovered_at=at)
        receipt = durable
    else:
        receipt, _ = approval_policy.complete_execution(
            paths["policyStore"], transaction["policyBinding"]["policyId"], use_number=use_number,
            status="succeeded", result_project_binding=_project_result(transaction["projectBinding"]),
            output_hash=transaction["outputHash"], artifact_refs=ARTIFACT_REFS, formal_validation="passed",
            summary="Verified Delivery 候选已按精确 Before/Expected Hash 原子晋升为本地 last-good。",
            event_store_path=paths["eventStore"], project_root=paths["root"], completed_at=at,
        )
    if receipt["execution"]["status"] != "succeeded" or receipt["execution"]["inputHash"] != transaction["inputHash"] or receipt["execution"]["outputHash"] != transaction["outputHash"]:
        raise VerifiedDeliveryPolicyConflictError("Execution Receipt 与 Promotion Transaction 不匹配。", code="RECEIPT_BINDING_MISMATCH")
    transaction["status"] = "finalized"
    transaction["receiptBinding"] = _receipt_binding(receipt)
    transaction["eventBinding"] = copy.deepcopy(receipt["eventBinding"])
    transaction["result"] = {"status": "succeeded", "errorCode": None, "summary": "Promotion、Audit Event、Execution Receipt 与 Ledger 已完成对账。"}
    transaction["timestamps"]["updatedAt"] = at
    transaction["timestamps"]["finalizedAt"] = at
    return _write_transaction(path, transaction)


def _finalize_failed(paths: dict[str, Path], path: Path, transaction: dict[str, Any], at: str, exc: VerifiedDeliveryPolicyAdapterError) -> None:
    use_number = transaction["policyBinding"]["useNumber"]
    effect_observed = _artifact_state(paths["lastGood"]) != transaction["lastGood"]["before"]
    if use_number is not None:
        approval_policy.complete_execution(
            paths["policyStore"], transaction["policyBinding"]["policyId"], use_number=use_number,
            status="failed", result_project_binding=_project_result(transaction["projectBinding"]), output_hash=None,
            artifact_refs=ARTIFACT_REFS, formal_validation="failed", external_effect_observed=effect_observed,
            summary=("Verified Delivery Promotion 观察到第三状态并 fail closed。" if effect_observed else "Verified Delivery Promotion 在副作用前 fail closed。"), completed_at=at,
        )
    transaction["status"] = "conflict" if isinstance(exc, VerifiedDeliveryPolicyConflictError) else "failed"
    transaction["result"] = {"status": transaction["status"], "errorCode": exc.code, "summary": str(exc)}
    transaction["timestamps"]["updatedAt"] = at
    transaction["timestamps"]["finalizedAt"] = at
    _write_transaction(path, transaction)


def _resume_locked(paths: dict[str, Path], path: Path, transaction: dict[str, Any], at: str, injector: FaultInjector | None) -> dict[str, Any]:
    try:
        receipt, _ = load_delivery(paths["root"], transaction["deliveryBinding"]["deliveryId"])
    except VerifiedDeliveryError as exc:
        wrapped = VerifiedDeliveryPolicyAdapterError(str(exc), code="DELIVERY_INVALID")
        if transaction["policyBinding"]["useNumber"] is not None:
            _finalize_failed(paths, path, transaction, at, wrapped)
        raise wrapped from exc
    state = approval_policy.inspect_policy_state(paths["policyStore"], transaction["policyBinding"]["policyId"])
    _validate_policy_profile(state, receipt, at=at, allow_pending=True)
    if transaction["status"] == "prepared":
        pending = approval_policy.begin_execution(paths["policyStore"], transaction["policyBinding"]["policyId"], input_hash=transaction["inputHash"], project_binding=_project_begin(transaction["projectBinding"]), source_binding_status="not_applicable", artifact_refs=ARTIFACT_REFS, started_at=at)
        transaction["status"] = "allocated"
        transaction["policyBinding"]["useNumber"] = pending["useNumber"]
        transaction["timestamps"]["updatedAt"] = at
        transaction = _write_transaction(path, transaction)
        _trip(injector, "after_allocated")
    if transaction["status"] == "allocated":
        try:
            transaction = _perform_effect(paths, path, transaction, at)
        except VerifiedDeliveryPolicyAdapterError as exc:
            _finalize_failed(paths, path, transaction, at, exc)
            raise
        _trip(injector, "after_effect")
    if transaction["status"] == "effect_observed":
        transaction = _complete_success(paths, path, transaction, at)
        _trip(injector, "after_completion")
    return transaction


def execute(project_root: Path, delivery_id: str, policy_id: str, *, executed_at: str | None = None, fault_injector: FaultInjector | None = None) -> dict[str, Any]:
    at = _normalize_time(executed_at)
    paths = _resolve_paths(project_root)
    with _lock(paths["operations"]):
        active = _active_transaction(paths["operations"], policy_id)
        if active is not None:
            return _resume_locked(paths, active[0], active[1], at, fault_injector)
        proposed = preview(paths["root"], delivery_id, policy_id, previewed_at=at)
        if proposed["status"] == "no_op":
            return proposed
        path = paths["operations"] / f"{proposed['transactionId']}.json"
        transaction = _create_transaction(path, proposed, at)
        _trip(fault_injector, "after_prepared")
        return _resume_locked(paths, path, transaction, at, fault_injector)


def validate_transactions(project_root: Path) -> dict[str, Any]:
    paths = _resolve_paths(project_root)
    errors = []
    count = 0
    for path in sorted(paths["operations"].glob("VDP-*.json")) if paths["operations"].is_dir() else []:
        try:
            _read_transaction(path)
            count += 1
        except VerifiedDeliveryPolicyAdapterError as exc:
            errors.append(f"{path.name}: {exc}")
    return {"formatVersion": "panorama-verified-delivery-promotion-store-validation.v0.1", "valid": not errors, "transactionCount": count, "errors": errors}


__all__ = ["InjectedAdapterCrash", "VerifiedDeliveryPolicyAdapterError", "VerifiedDeliveryPolicyConflictError", "execute", "preview", "validate_transactions"]
