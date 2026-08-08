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
    schema_path: str | os.PathLike[str],
) -> tuple[Path, dict[str, Any], list[str]]:
    """Validate, stage, and atomically apply one approved update package."""

    source = Path(html_path)
    if not isinstance(package, dict):
        raise ApplyPatchError("更新包必须是 JSON 对象。")
    validate_package_approval(package)
    original_text = _read_exact(source)
    original_presentation_hash = compute_presentation_hash(original_text)
    current = extract_data(source)
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
            raise
    finally:
        staged.unlink(missing_ok=True)
    warning_lines = [issue.render() for issue in report.warnings]
    return backup, updated, warning_lines


def apply_update_package(
    html_path: str | os.PathLike[str],
    package: dict[str, Any],
    schema_path: str | os.PathLike[str],
) -> tuple[Path, dict[str, Any], list[str]]:
    """Lock, validate, stage, and atomically apply one approved package."""

    source = Path(html_path)
    with _exclusive_update_lock(source):
        return _apply_update_package_locked(source, package, schema_path)


def build_parser() -> argparse.ArgumentParser:
    default_schema = (
        Path(__file__).resolve().parents[1]
        / "schema"
        / "panorama.schema.v0.1.json"
    )
    parser = ChineseArgumentParser(
        description="应用经过批准且受 Revision 保护的 Panorama 更新包。"
    )
    parser.add_argument("html", type=Path, help="目标 Panorama HTML")
    parser.add_argument("patch", type=Path, help="待应用更新包 JSON")
    parser.add_argument("--schema", type=Path, default=default_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with args.patch.open("r", encoding="utf-8") as handle:
            package = json.load(handle)
        backup, updated, warnings = apply_update_package(
            args.html, package, args.schema
        )
    except RevisionConflictError as exc:
        print(f"冲突错误：{exc}", file=sys.stderr)
        return 1
    except (ApplyPatchError, PanoramaIOError, ValidationRuntimeError) as exc:
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
