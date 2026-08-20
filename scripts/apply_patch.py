"""Apply an approved, revision-guarded Panorama update package safely."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import (
    PanoramaIOError,
    atomic_write,
    compute_data_hash,
    compute_presentation_hash,
    create_backup,
    extract_data,
    replace_data,
)
from governance_outbox import (
    GovernanceOutboxError,
    GovernanceRecoveryRequired,
    default_outbox_store,
    ensure_governance_ready,
    prepare_governance_outbox,
    resolve_governance_outbox,
)
from validate_panorama import ValidationRuntimeError, validate_data


class ApplyPatchError(RuntimeError):
    """Base class for safe apply failures."""


class RevisionConflictError(ApplyPatchError):
    """The package was prepared from a stale Panorama revision or hash."""


class PatchOperationError(ApplyPatchError):
    """A minimal JSON Patch operation is invalid or cannot be applied."""


PROPOSAL_FIELDS = (
    "baseRevision",
    "baseDataHash",
    "operations",
    "changeRecords",
    "reviewDraft",
    "updateBatchDraft",
    "guidanceDraft",
)

PROPOSAL_WRAPPER_FIELDS = {"proposal", "facts", "validation", "sourceBinding"}
PROPOSAL_WRAPPER_REQUIRED_FIELDS = {"proposal", "facts", "validation"}
STUDIO_APPROVAL_VERSION = "studio-update-approval.v0.1"
STUDIO_APPROVAL_METHOD = "explicit_hash_confirmation"
STUDIO_APPROVAL_TIME_SOURCE = "approval_recorder_clock"
STUDIO_APPROVAL_REQUIRED_FIELDS = {
    "approvalVersion",
    "status",
    "proposalHash",
    "approvedBy",
    "approvalRecordedAt",
    "approvalMethod",
    "approvalTimeSource",
}
STUDIO_SOURCE_BINDING_FIELDS = {
    "projectId",
    "schemaVersion",
    "templateVersion",
    "baseRevision",
    "baseDataHash",
    "gitHead",
    "sourceSnapshotHash",
}


def parse_proposal_artifact(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Strictly distinguish a proposer wrapper from a legacy bare package.

    ``propose_update.py`` emits ``{proposal, facts, validation}``, while the
    original Apply API accepted the inner proposal directly.  Accept both,
    but never guess when fields from both shapes are present.
    """

    if not isinstance(artifact, dict):
        raise ApplyPatchError("Proposal artifact 必须是 JSON 对象。")
    has_wrapper = "proposal" in artifact
    wrapper_only_fields = {"facts", "validation", "sourceBinding"}
    package_top_level_fields = set(PROPOSAL_FIELDS) | {"proposalHash", "approval"}
    if has_wrapper:
        mixed = sorted(package_top_level_fields.intersection(artifact))
        if mixed:
            raise ApplyPatchError(
                "Proposal artifact 混合了 wrapper 与 bare package 字段："
                + ", ".join(mixed)
            )
        missing = sorted(PROPOSAL_WRAPPER_REQUIRED_FIELDS.difference(artifact))
        unknown = sorted(set(artifact).difference(PROPOSAL_WRAPPER_FIELDS))
        if missing or unknown:
            details = []
            if missing:
                details.append("缺少 " + ", ".join(missing))
            if unknown:
                details.append("未知字段 " + ", ".join(unknown))
            raise ApplyPatchError(
                "Proposal wrapper 字段不符合严格合同：" + "；".join(details)
            )
        package = artifact.get("proposal")
        facts = artifact.get("facts")
        validation = artifact.get("validation")
        if not isinstance(package, dict):
            raise ApplyPatchError("Proposal wrapper.proposal 必须是对象。")
        if not isinstance(facts, dict):
            raise ApplyPatchError("Proposal wrapper.facts 必须是对象。")
        if not isinstance(validation, dict):
            raise ApplyPatchError("Proposal wrapper.validation 必须是对象。")
        if facts.get("baseRevision") != package.get("baseRevision"):
            raise ApplyPatchError(
                "Proposal wrapper.facts.baseRevision 与 proposal 不一致。"
            )
        if facts.get("baseDataHash") != package.get("baseDataHash"):
            raise ApplyPatchError(
                "Proposal wrapper.facts.baseDataHash 与 proposal 不一致。"
            )
        operations = package.get("operations")
        if (
            "operationCount" in facts
            and isinstance(operations, list)
            and facts.get("operationCount") != len(operations)
        ):
            raise ApplyPatchError(
                "Proposal wrapper.facts.operationCount 与 proposal 不一致。"
            )
        source_binding = artifact.get("sourceBinding")
        if source_binding is not None and not isinstance(source_binding, dict):
            raise ApplyPatchError("Proposal wrapper.sourceBinding 必须是对象。")
        return copy.deepcopy(package), copy.deepcopy(source_binding)

    mixed = sorted(wrapper_only_fields.intersection(artifact))
    if mixed:
        raise ApplyPatchError(
            "Bare proposal 混入了 wrapper 字段：" + ", ".join(mixed)
        )
    return copy.deepcopy(artifact), None


def unwrap_proposal_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return a detached inner package from a strict wrapper or bare input."""

    package, _ = parse_proposal_artifact(artifact)
    return package


def proposal_semantics(package: dict[str, Any]) -> dict[str, Any]:
    """Return the exact stable semantics covered by user approval."""

    if not isinstance(package, dict):
        raise ApplyPatchError("更新包必须是 JSON 对象。")
    defaults: dict[str, Any] = {
        "operations": [],
        "changeRecords": [],
        "reviewDraft": {},
        "updateBatchDraft": {},
        "guidanceDraft": {},
    }
    return {
        field: copy.deepcopy(package.get(field, defaults.get(field)))
        for field in PROPOSAL_FIELDS
    }


def compute_proposal_hash(package: dict[str, Any]) -> str:
    """Hash every mutable proposal field that the user reviews."""

    canonical = json.dumps(
        proposal_semantics(package),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validated_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ApplyPatchError(f"{field} 必须是非空时间戳。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApplyPatchError(f"{field} 必须是 ISO 8601 时间戳。") from exc
    if parsed.utcoffset() is None:
        raise ApplyPatchError(f"{field} 必须包含 UTC offset。")
    return value


def _validate_studio_source_binding(
    source_binding: Any,
    package: dict[str, Any],
    current: dict[str, Any] | None,
) -> None:
    if source_binding is None:
        return
    if not isinstance(source_binding, dict) or not source_binding:
        raise ApplyPatchError("Studio approval sourceBinding 必须是非空对象。")
    unknown = sorted(set(source_binding).difference(STUDIO_SOURCE_BINDING_FIELDS))
    if unknown:
        raise ApplyPatchError(
            "Studio approval sourceBinding 含未知字段：" + ", ".join(unknown)
        )
    if source_binding.get("baseRevision") != package.get("baseRevision"):
        raise ApplyPatchError(
            "Studio approval sourceBinding.baseRevision 与 Proposal 不一致。"
        )
    if source_binding.get("baseDataHash") != package.get("baseDataHash"):
        raise ApplyPatchError(
            "Studio approval sourceBinding.baseDataHash 与 Proposal 不一致。"
        )
    if current is None:
        return
    current_source = current.get("sourceBinding")
    if not isinstance(current_source, dict):
        current_source = {}
    actual = {
        "projectId": current.get("project", {}).get("id"),
        "schemaVersion": str(current.get("schemaVersion", "")),
        "templateVersion": str(current.get("meta", {}).get("templateVersion", "")),
        "baseRevision": current.get("meta", {}).get("revision"),
        "baseDataHash": compute_data_hash(current),
        "gitHead": current_source.get("gitHead"),
        "sourceSnapshotHash": current_source.get("sourceSnapshotHash"),
    }
    for field, expected in source_binding.items():
        if actual.get(field) != expected:
            raise RevisionConflictError(
                f"Studio approval sourceBinding.{field} 不匹配："
                f"批准={expected!r}，当前={actual.get(field)!r}。"
            )


def inject_studio_approval(
    package: dict[str, Any],
    approval_artifact: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a separate approval and adapt it to the legacy package shape.

    The returned package is a deep copy.  The stored Proposal remains pending
    and immutable; only Apply's in-memory legacy adapter receives approval.
    """

    if not isinstance(package, dict):
        raise ApplyPatchError("更新包必须是 JSON 对象。")
    inline = package.get("approval")
    if (
        isinstance(inline, dict)
        and inline.get("status") in {"approved", "waived"}
    ):
        raise ApplyPatchError(
            "不能同时使用 inline approval 与独立 Studio approval。"
        )
    if not isinstance(approval_artifact, dict):
        raise ApplyPatchError("Studio approval artifact 必须是 JSON 对象。")
    allowed = STUDIO_APPROVAL_REQUIRED_FIELDS | {"sourceBinding"}
    missing = sorted(STUDIO_APPROVAL_REQUIRED_FIELDS.difference(approval_artifact))
    unknown = sorted(set(approval_artifact).difference(allowed))
    if missing or unknown:
        details = []
        if missing:
            details.append("缺少 " + ", ".join(missing))
        if unknown:
            details.append("未知字段 " + ", ".join(unknown))
        raise ApplyPatchError(
            "Studio approval artifact 字段不符合严格合同："
            + "；".join(details)
        )
    if approval_artifact.get("approvalVersion") != STUDIO_APPROVAL_VERSION:
        raise ApplyPatchError(
            f"approvalVersion 必须为 {STUDIO_APPROVAL_VERSION}。"
        )
    if approval_artifact.get("status") != "approved":
        raise ApplyPatchError("Studio approval status 必须为 'approved'。")
    if approval_artifact.get("approvalMethod") != STUDIO_APPROVAL_METHOD:
        raise ApplyPatchError(
            f"approvalMethod 必须为 {STUDIO_APPROVAL_METHOD}。"
        )
    if approval_artifact.get("approvalTimeSource") != STUDIO_APPROVAL_TIME_SOURCE:
        raise ApplyPatchError(
            f"approvalTimeSource 必须为 {STUDIO_APPROVAL_TIME_SOURCE}。"
        )
    approved_by = approval_artifact.get("approvedBy")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ApplyPatchError("Studio approval approvedBy 必须标识批准用户。")
    recorded_at = _validated_timestamp(
        approval_artifact.get("approvalRecordedAt"), "approvalRecordedAt"
    )
    proposal_hash = package.get("proposalHash")
    actual_hash = compute_proposal_hash(package)
    approved_hash = approval_artifact.get("proposalHash")
    if proposal_hash != actual_hash or approved_hash != actual_hash:
        raise ApplyPatchError(
            "Studio approval、Proposal Hash 与当前 Proposal 语义不完全一致。"
        )
    _validate_studio_source_binding(
        approval_artifact.get("sourceBinding"), package, current
    )
    adapted = copy.deepcopy(package)
    adapted["approval"] = {
        "status": "approved",
        "approvedBy": approved_by.strip(),
        "approvedAt": recorded_at,
        "proposalHash": actual_hash,
    }
    return adapted


def validate_package_approval(package: dict[str, Any]) -> None:
    """Enforce that approval is bound to the package's current semantics."""

    proposal_hash = package.get("proposalHash")
    actual_hash = compute_proposal_hash(package)
    if not isinstance(proposal_hash, str) or proposal_hash != actual_hash:
        raise ApplyPatchError(
            "Proposal Hash 缺失，或与当前更新包不匹配。"
        )
    approval = package.get("approval")
    if not isinstance(approval, dict) or approval.get("status") not in {
        "approved",
        "waived",
    }:
        raise ApplyPatchError("更新包 approval.status 必须为 'approved' 或 'waived'。")
    if approval.get("proposalHash") != actual_hash:
        raise ApplyPatchError("更新包的批准 Hash 与 Proposal Hash 不一致。")
    if not str(approval.get("approvedBy", "")).strip():
        raise ApplyPatchError("更新包必须填写 approval.approvedBy。")
    if not str(approval.get("approvedAt", "")).strip():
        raise ApplyPatchError("更新包必须填写 approval.approvedAt。")

    review = package.get("reviewDraft")
    batch = package.get("updateBatchDraft")
    if batch not in (None, {}):
        if not isinstance(review, dict) or not review.get("id"):
            raise ApplyPatchError(
                "应用 Update Batch 前必须提供 reviewDraft。"
            )
        if review.get("status") not in {"pending", "approved", "waived"}:
            raise ApplyPatchError(
                "reviewDraft.status 必须为 pending、approved 或 waived。"
            )
        if batch.get("reviewId") != review.get("id"):
            raise ApplyPatchError(
                "updateBatchDraft.reviewId 必须与 reviewDraft.id 一致。"
            )
        for change in package.get("changeRecords", []):
            if isinstance(change, dict) and change.get("reviewId") not in {
                None,
                review.get("id"),
            }:
                raise ApplyPatchError(
                    "Change 的 reviewId 必须与更新包 reviewDraft.id 一致。"
                )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _pointer_parts(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise PatchOperationError(
            f"JSON Pointer 必须以 '/' 开头；实际为 {pointer!r}。"
        )
    if pointer == "/":
        return [""]
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _list_index(token: str, length: int, *, allow_end: bool = False) -> int:
    if token == "-" and allow_end:
        return length
    try:
        index = int(token)
    except (TypeError, ValueError) as exc:
        raise PatchOperationError(f"无效的数组索引 {token!r}。") from exc
    maximum = length if allow_end else length - 1
    if index < 0 or index > maximum:
        raise PatchOperationError(
            f"数组索引 {index} 超出长度为 {length} 的有效范围。"
        )
    return index


def _parent(document: Any, pointer: str) -> tuple[Any, str]:
    parts = _pointer_parts(pointer)
    if not parts:
        raise PatchOperationError("不支持对根对象执行 Patch 操作。")
    current = document
    for part in parts[:-1]:
        if isinstance(current, dict):
            if part not in current:
                raise PatchOperationError(
                    f"JSON Pointer 的父路径不存在：{pointer!r}。"
                )
            current = current[part]
        elif isinstance(current, list):
            current = current[_list_index(part, len(current))]
        else:
            raise PatchOperationError(
                f"JSON Pointer 穿越了标量值：{pointer!r}。"
            )
    return current, parts[-1]


def apply_operations(
    document: dict[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply minimal add/replace/remove operations to a deep copy."""

    if not isinstance(document, dict):
        raise PatchOperationError("Panorama 数据必须是 JSON 对象。")
    if not isinstance(operations, list):
        raise PatchOperationError("operations 必须是数组。")
    result = copy.deepcopy(document)
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise PatchOperationError(f"操作 {index} 必须是对象。")
        op = operation.get("op")
        pointer = operation.get("path")
        if op not in {"add", "replace", "remove"}:
            raise PatchOperationError(
                f"操作 {index} 使用了不支持的 op {op!r}。"
            )
        parent, token = _parent(result, pointer)
        if isinstance(parent, dict):
            if op in {"replace", "remove"} and token not in parent:
                raise PatchOperationError(
                    f"操作 {index} 的目标不存在：{pointer!r}。"
                )
            if op == "remove":
                del parent[token]
            else:
                if "value" not in operation:
                    raise PatchOperationError(
                        f"操作 {index} 必须提供 value。"
                    )
                parent[token] = copy.deepcopy(operation["value"])
        elif isinstance(parent, list):
            if op == "add":
                if "value" not in operation:
                    raise PatchOperationError(
                        f"操作 {index} 必须提供 value。"
                    )
                parent.insert(
                    _list_index(token, len(parent), allow_end=True),
                    copy.deepcopy(operation["value"]),
                )
            else:
                array_index = _list_index(token, len(parent))
                if op == "remove":
                    parent.pop(array_index)
                else:
                    if "value" not in operation:
                        raise PatchOperationError(
                            f"操作 {index} 必须提供 value。"
                        )
                    parent[array_index] = copy.deepcopy(operation["value"])
        else:
            raise PatchOperationError(
                f"操作 {index} 指向了标量父节点：{pointer!r}。"
            )
    return result


def compose_updated_data(
    current: dict[str, Any], package: dict[str, Any]
) -> dict[str, Any]:
    updated = apply_operations(current, package.get("operations", []))
    now = _timestamp()
    base_revision = current["meta"]["revision"]
    next_revision = base_revision + 1

    change_records = package.get("changeRecords", [])
    if not isinstance(change_records, list):
        raise ApplyPatchError("changeRecords 必须是数组。")
    updated.setdefault("changes", []).extend(copy.deepcopy(change_records))
    review_draft = package.get("reviewDraft")
    if review_draft not in (None, {}):
        if not isinstance(review_draft, dict):
            raise ApplyPatchError("reviewDraft 必须是对象。")
        review = copy.deepcopy(review_draft)
        approval = package.get("approval")
        if isinstance(approval, dict) and approval.get("status") in {
            "approved",
            "waived",
        }:
            review["status"] = approval["status"]
            review["reviewedBy"] = approval.get("approvedBy", "")
            review["reviewedAt"] = approval.get("approvedAt")
        updated.setdefault("reviews", []).append(review)

    update_batch = package.get("updateBatchDraft")
    if update_batch not in (None, {}):
        if not isinstance(update_batch, dict):
            raise ApplyPatchError("updateBatchDraft 必须是对象。")
        update_batch = copy.deepcopy(update_batch)
        update_batch["revisionFrom"] = base_revision
        update_batch["revisionTo"] = next_revision
        update_batch.setdefault("createdAt", now)
        update_batch["status"] = "applied"
        updated.setdefault("updateBatches", []).append(update_batch)
        updated.setdefault("meta", {})["latestUpdateBatchId"] = update_batch.get("id")

    if "guidanceDraft" in package and package.get("guidanceDraft") not in (None, {}):
        if not isinstance(package["guidanceDraft"], dict):
            raise ApplyPatchError("guidanceDraft 必须是对象。")
        next_guidance = copy.deepcopy(package["guidanceDraft"])
        next_extensions = next_guidance.setdefault("extensions", {})
        if not isinstance(next_extensions, dict):
            raise ApplyPatchError("guidanceDraft.extensions 必须是对象。")

        historical_by_id: dict[str, dict[str, Any]] = {}
        current_guidance = current.get("guidance", {})
        current_extensions = current_guidance.get("extensions", {})
        for record in current_extensions.get("historicalOptions", []) if isinstance(current_extensions, dict) else []:
            option = record.get("option", {}) if isinstance(record, dict) else {}
            if isinstance(option, dict) and isinstance(option.get("id"), str):
                historical_by_id[option["id"]] = copy.deepcopy(record)

        referenced_ids = {
            option_id
            for batch in current.get("updateBatches", [])
            if isinstance(batch, dict)
            for option_id in batch.get("nextFocusOptionIds", [])
            if isinstance(option_id, str)
        }
        next_ids = {
            option.get("id")
            for option in next_guidance.get("options", [])
            if isinstance(option, dict)
        }
        for option in current_guidance.get("options", []):
            if (
                isinstance(option, dict)
                and option.get("id") in referenced_ids
                and option.get("id") not in next_ids
            ):
                historical_by_id[option["id"]] = {
                    "option": copy.deepcopy(option),
                    "lifecycle": "historical",
                    "active": False,
                    "supersededAt": now,
                }
        if historical_by_id:
            next_extensions["historicalOptions"] = list(historical_by_id.values())
        updated["guidance"] = next_guidance

    updated.setdefault("meta", {})["revision"] = next_revision
    updated["meta"]["updatedAt"] = now
    updated["meta"]["lastValidatedAt"] = now
    return updated


def _read_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


@contextmanager
def _exclusive_update_lock(source: Path):
    """Serialize cooperating writers with a sibling exclusive lock file."""

    lock_path = source.with_name(f".{source.name}.panorama.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RevisionConflictError(
            f"另一个 Panorama 更新正在持有锁 {lock_path.name}。"
        ) from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _apply_update_package_locked(
    html_path: str | os.PathLike[str],
    package: dict[str, Any],
    schema_path: str | os.PathLike[str] | None,
    approval_artifact: dict[str, Any] | None = None,
    event_store_path: str | os.PathLike[str] | None = None,
    outbox_store: str | os.PathLike[str] | None = None,
) -> tuple[Path, dict[str, Any], list[str]]:
    """Validate, stage, and atomically apply one approved update package."""

    source = Path(html_path)
    event_target = Path(event_store_path) if event_store_path is not None else None
    outbox_target = Path(outbox_store) if outbox_store is not None else None
    if outbox_target is not None and event_target is None:
        raise ApplyPatchError("启用 Governance Outbox 时必须同时提供 Event Store。")
    if event_target is not None and outbox_target is None:
        outbox_target = default_outbox_store(source)
    if outbox_target is not None:
        # recovery_required blocks before a new backup, Proposal composition or
        # formal Panorama mutation is attempted.
        ensure_governance_ready(outbox_target, event_store_path=event_target)
    if not isinstance(package, dict):
        raise ApplyPatchError("更新包必须是 JSON 对象。")
    original_text = _read_exact(source)
    original_presentation_hash = compute_presentation_hash(original_text)
    current = extract_data(source)
    if approval_artifact is not None:
        package = inject_studio_approval(
            package, approval_artifact, current=current
        )
    validate_package_approval(package)
    expected_revision = package.get("baseRevision")
    expected_hash = package.get("baseDataHash")
    actual_revision = current.get("meta", {}).get("revision")
    actual_hash = compute_data_hash(current)
    if expected_revision != actual_revision:
        raise RevisionConflictError(
            f"Base Revision 不匹配：更新包={expected_revision!r}，当前={actual_revision!r}。"
        )
    if expected_hash != actual_hash:
        raise RevisionConflictError(
            f"Base Data Hash 不匹配：更新包={expected_hash!r}，当前={actual_hash!r}。"
        )

    # The brief requires a backup before mutation.  The original remains intact
    # even if a later patch or validation step fails.
    backup = create_backup(source)
    updated = compose_updated_data(current, package)
    report = validate_data(
        updated,
        schema_path,
        base_dir=source.parent,
        source_path=source,
    )
    if report.errors:
        messages = "\n".join(issue.render() for issue in report.errors)
        raise ApplyPatchError(f"更新后的数据校验失败：\n{messages}")

    file_descriptor, stage_name = tempfile.mkstemp(
        dir=source.parent,
        prefix=f".{source.name}.",
        suffix=".staged",
    )
    os.close(file_descriptor)
    staged = Path(stage_name)
    staged.unlink(missing_ok=True)
    pending_outbox: Path | None = None
    try:
        replace_data(source, updated, staged)
        staged_text = _read_exact(staged)
        if compute_presentation_hash(staged_text) != original_presentation_hash:
            raise ApplyPatchError(
                "更新暂存期间 Presentation Layer Hash 发生变化。"
            )
        if _read_exact(source) != original_text:
            raise RevisionConflictError(
                "目标 Panorama 在 Base Revision/Data Hash 检查后发生变化；"
                "已批准更新包未被应用。"
            )
        if outbox_target is not None:
            pending_outbox = prepare_governance_outbox(
                outbox_target,
                current=current,
                updated=updated,
                proposal_hash=package["proposalHash"],
                event_store_path=event_target,
            )
        try:
            atomic_write(source, staged_text)
            final_text = _read_exact(source)
            if final_text != staged_text:
                raise RevisionConflictError(
                    "目标 Panorama 在提交期间发生变化；并发状态已保留。"
                )
            if compute_presentation_hash(final_text) != original_presentation_hash:
                raise ApplyPatchError(
                    "原子写入后 Presentation Layer Hash 发生变化。"
                )
        except Exception:
            # Revert only our own committed bytes.  Never overwrite a state that
            # another writer installed after the pre-commit comparison.
            try:
                if _read_exact(source) == staged_text:
                    atomic_write(source, original_text)
            except (OSError, PanoramaIOError):
                pass
            if pending_outbox is not None and event_target is not None:
                try:
                    resolve_governance_outbox(
                        pending_outbox,
                        panorama_path=source,
                        event_store_path=event_target,
                    )
                except GovernanceOutboxError as recovery_exc:
                    raise GovernanceRecoveryRequired(
                        "Apply 失败，且 pending Outbox 未能确定性解析；"
                        "后续治理写入已停止。"
                    ) from recovery_exc
            raise
        if pending_outbox is not None and event_target is not None:
            resolved = resolve_governance_outbox(
                pending_outbox,
                panorama_path=source,
                event_store_path=event_target,
            )
            if resolved["status"] != "finalized":
                raise GovernanceRecoveryRequired(
                    "Apply 后的真实状态未产生 finalized Outbox；后续治理写入已停止。"
                )
    finally:
        staged.unlink(missing_ok=True)
    warning_lines = [issue.render() for issue in report.warnings]
    return backup, updated, warning_lines


def apply_update_package(
    html_path: str | os.PathLike[str],
    package: dict[str, Any],
    schema_path: str | os.PathLike[str] | None,
    approval_artifact: dict[str, Any] | None = None,
    event_store_path: str | os.PathLike[str] | None = None,
    outbox_store: str | os.PathLike[str] | None = None,
) -> tuple[Path, dict[str, Any], list[str]]:
    """Lock, validate, stage, and atomically apply one approved package."""

    source = Path(html_path)
    with _exclusive_update_lock(source):
        return _apply_update_package_locked(
            source,
            package,
            schema_path,
            approval_artifact,
            event_store_path,
            outbox_store,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="应用经过批准且受 Revision 保护的 Panorama 更新包。"
    )
    parser.add_argument("html", type=Path, help="目标 Panorama HTML")
    parser.add_argument("patch", type=Path, help="待应用更新包 JSON")
    parser.add_argument("--schema", type=Path, default=None, help="覆盖按 schemaVersion 自动选择的 Schema")
    parser.add_argument(
        "--approval",
        type=Path,
        default=None,
        help="独立的 Studio approval artifact；仅在内存中适配 legacy approval",
    )
    parser.add_argument(
        "--event-store",
        type=Path,
        default=None,
        help="启用 V0.5 fail-closed Event Outbox 并写入指定 Engineering Event Store",
    )
    parser.add_argument(
        "--outbox-store",
        type=Path,
        default=None,
        help="覆盖默认 .panorama-work/event-outbox/v0.1 路径；必须与 --event-store 同用",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with args.patch.open("r", encoding="utf-8") as handle:
            proposal_artifact = json.load(handle)
        package = unwrap_proposal_artifact(proposal_artifact)
        approval_artifact = None
        if args.approval is not None:
            with args.approval.open("r", encoding="utf-8") as handle:
                approval_artifact = json.load(handle)
        backup, updated, warnings = apply_update_package(
            args.html,
            package,
            args.schema,
            approval_artifact,
            args.event_store,
            args.outbox_store,
        )
    except RevisionConflictError as exc:
        print(f"冲突错误：{exc}", file=sys.stderr)
        return 1
    except (
        ApplyPatchError,
        PanoramaIOError,
        ValidationRuntimeError,
        GovernanceOutboxError,
    ) as exc:
        print(f"应用错误：{exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"文件错误：{exc}", file=sys.stderr)
        return 2

    print(f"已应用修订：{updated['meta']['revision']}")
    print(f"数据 SHA-256：{compute_data_hash(updated)}")
    print(f"备份：{backup}")
    for warning in warnings:
        print(warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
