"""Apply an approved, revision-guarded Panorama update package safely."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _pointer_parts(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise PatchOperationError(
            f"JSON Pointer must start with '/'; got {pointer!r}."
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
        raise PatchOperationError(f"Invalid array index {token!r}.") from exc
    maximum = length if allow_end else length - 1
    if index < 0 or index > maximum:
        raise PatchOperationError(
            f"Array index {index} is out of range for length {length}."
        )
    return index


def _parent(document: Any, pointer: str) -> tuple[Any, str]:
    parts = _pointer_parts(pointer)
    if not parts:
        raise PatchOperationError("Root-level patch operations are not supported.")
    current = document
    for part in parts[:-1]:
        if isinstance(current, dict):
            if part not in current:
                raise PatchOperationError(
                    f"JSON Pointer parent does not exist: {pointer!r}."
                )
            current = current[part]
        elif isinstance(current, list):
            current = current[_list_index(part, len(current))]
        else:
            raise PatchOperationError(
                f"JSON Pointer traverses a scalar value: {pointer!r}."
            )
    return current, parts[-1]


def apply_operations(
    document: dict[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply minimal add/replace/remove operations to a deep copy."""

    if not isinstance(document, dict):
        raise PatchOperationError("Panorama data must be a JSON object.")
    if not isinstance(operations, list):
        raise PatchOperationError("operations must be an array.")
    result = copy.deepcopy(document)
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise PatchOperationError(f"Operation {index} must be an object.")
        op = operation.get("op")
        pointer = operation.get("path")
        if op not in {"add", "replace", "remove"}:
            raise PatchOperationError(
                f"Operation {index} uses unsupported op {op!r}."
            )
        parent, token = _parent(result, pointer)
        if isinstance(parent, dict):
            if op in {"replace", "remove"} and token not in parent:
                raise PatchOperationError(
                    f"Operation {index} target does not exist: {pointer!r}."
                )
            if op == "remove":
                del parent[token]
            else:
                if "value" not in operation:
                    raise PatchOperationError(
                        f"Operation {index} requires a value."
                    )
                parent[token] = copy.deepcopy(operation["value"])
        elif isinstance(parent, list):
            if op == "add":
                if "value" not in operation:
                    raise PatchOperationError(
                        f"Operation {index} requires a value."
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
                            f"Operation {index} requires a value."
                        )
                    parent[array_index] = copy.deepcopy(operation["value"])
        else:
            raise PatchOperationError(
                f"Operation {index} targets a scalar parent: {pointer!r}."
            )
    return result


def _append_if_present(
    target: list[dict[str, Any]], value: Any, field_name: str
) -> None:
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        raise ApplyPatchError(f"{field_name} must be an object when present.")
    target.append(copy.deepcopy(value))


def compose_updated_data(
    current: dict[str, Any], package: dict[str, Any]
) -> dict[str, Any]:
    updated = apply_operations(current, package.get("operations", []))
    now = _timestamp()
    base_revision = current["meta"]["revision"]
    next_revision = base_revision + 1

    change_records = package.get("changeRecords", [])
    if not isinstance(change_records, list):
        raise ApplyPatchError("changeRecords must be an array.")
    updated.setdefault("changes", []).extend(copy.deepcopy(change_records))
    _append_if_present(updated.setdefault("reviews", []), package.get("reviewDraft"), "reviewDraft")

    update_batch = package.get("updateBatchDraft")
    if update_batch not in (None, {}):
        if not isinstance(update_batch, dict):
            raise ApplyPatchError("updateBatchDraft must be an object.")
        update_batch = copy.deepcopy(update_batch)
        update_batch["revisionFrom"] = base_revision
        update_batch["revisionTo"] = next_revision
        update_batch.setdefault("createdAt", now)
        update_batch.setdefault("status", "applied")
        updated.setdefault("updateBatches", []).append(update_batch)
        updated.setdefault("meta", {})["latestUpdateBatchId"] = update_batch.get("id")

    if "guidanceDraft" in package and package.get("guidanceDraft") not in (None, {}):
        if not isinstance(package["guidanceDraft"], dict):
            raise ApplyPatchError("guidanceDraft must be an object.")
        updated["guidance"] = copy.deepcopy(package["guidanceDraft"])

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
            f"Another Panorama update holds lock {lock_path.name}."
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
        raise ApplyPatchError("Update package must be a JSON object.")
    original_text = _read_exact(source)
    original_presentation_hash = compute_presentation_hash(original_text)
    current = extract_data(source)
    expected_revision = package.get("baseRevision")
    expected_hash = package.get("baseDataHash")
    actual_revision = current.get("meta", {}).get("revision")
    actual_hash = compute_data_hash(current)
    if expected_revision != actual_revision:
        raise RevisionConflictError(
            f"Base revision mismatch: package={expected_revision!r}, current={actual_revision!r}."
        )
    if expected_hash != actual_hash:
        raise RevisionConflictError(
            f"Base data hash mismatch: package={expected_hash!r}, current={actual_hash!r}."
        )

    # The brief requires a backup before mutation.  The original remains intact
    # even if a later patch or validation step fails.
    backup = create_backup(source)
    updated = compose_updated_data(current, package)
    report = validate_data(updated, schema_path, base_dir=source.parent)
    if report.errors:
        messages = "\n".join(issue.render() for issue in report.errors)
        raise ApplyPatchError(f"Updated data failed validation:\n{messages}")

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
                "Presentation Layer hash changed while staging the update."
            )
        if _read_exact(source) != original_text:
            raise RevisionConflictError(
                "Target Panorama changed after the Base Revision/Data Hash check; "
                "the approved package was not applied."
            )
        try:
            atomic_write(source, staged_text)
            final_text = _read_exact(source)
            if final_text != staged_text:
                raise RevisionConflictError(
                    "Target Panorama changed during commit; the concurrent state was preserved."
                )
            if compute_presentation_hash(final_text) != original_presentation_hash:
                raise ApplyPatchError(
                    "Presentation Layer hash changed after atomic write."
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
    parser = argparse.ArgumentParser(
        description="Apply an approved revision-guarded Panorama update package."
    )
    parser.add_argument("html", type=Path, help="Target Panorama HTML")
    parser.add_argument("patch", type=Path, help="Pending update package JSON")
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
        print(f"ERROR CONFLICT: {exc}", file=sys.stderr)
        return 1
    except (ApplyPatchError, PanoramaIOError, ValidationRuntimeError) as exc:
        print(f"ERROR APPLY: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR FILE: {exc}", file=sys.stderr)
        return 2

    print(f"Applied revision: {updated['meta']['revision']}")
    print(f"Data SHA-256: {compute_data_hash(updated)}")
    print(f"Backup: {backup}")
    for warning in warnings:
        print(warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
