"""Record one explicit approval for an immutable prepared INIT Preview."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from materialize_init import (
    InitMaterializationError,
    _canonical_json,
    _timestamp,
    compute_preview_hash,
)
from panorama_cli import ChineseArgumentParser


PREPARED_VERSION = "prepared-init-review.v0.1"
APPROVAL_VERSION = "init-approval.v0.1"
APPROVAL_METHOD = "explicit_hash_confirmation"
APPROVAL_TIME_SOURCE = "approval_recorder_clock"
PREPARED_FIELDS = {
    "preparedVersion",
    "preparedAt",
    "preview",
    "previewHash",
    "sourceSnapshot",
    "validation",
    "approvalState",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InitMaterializationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InitMaterializationError("prepared INIT review must be a JSON object")
    return value


def record_init_approval(
    prepared: dict[str, Any],
    *,
    approved_hash: str,
    approved_by: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    if set(prepared) != PREPARED_FIELDS:
        raise InitMaterializationError(
            "prepared artifact fields do not match prepared-init-review.v0.1"
        )
    if prepared.get("preparedVersion") != PREPARED_VERSION:
        raise InitMaterializationError(f"preparedVersion must be {PREPARED_VERSION}")
    if prepared.get("approvalState") != "awaiting_user_approval":
        raise InitMaterializationError("prepared artifact is not awaiting user approval")
    validation = prepared.get("validation")
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        raise InitMaterializationError("prepared artifact has no successful prevalidation")
    preview = prepared.get("preview")
    if not isinstance(preview, dict):
        raise InitMaterializationError("prepared.preview must be an object")
    recomputed = compute_preview_hash(preview)
    if _canonical_json(prepared.get("sourceSnapshot")) != _canonical_json(
        preview.get("sourceSnapshot")
    ):
        raise InitMaterializationError(
            "prepared sourceSnapshot does not match its Preview"
        )
    if prepared.get("previewHash") != recomputed:
        raise InitMaterializationError("prepared previewHash does not match its Preview")
    if approved_hash != recomputed:
        raise InitMaterializationError("explicit approved hash does not match prepared previewHash")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise InitMaterializationError("approvedBy must identify the approving user")
    approval_time = _timestamp(recorded_at or _now(), "approvalRecordedAt")
    prepared_time = _timestamp(prepared.get("preparedAt"), "preparedAt")
    if datetime.fromisoformat(approval_time.replace("Z", "+00:00")) < datetime.fromisoformat(
        prepared_time.replace("Z", "+00:00")
    ):
        raise InitMaterializationError("approvalRecordedAt cannot precede preparedAt")
    return {
        "approvalVersion": APPROVAL_VERSION,
        "status": "approved",
        "previewHash": recomputed,
        "approvedBy": approved_by.strip(),
        "approvalRecordedAt": approval_time,
        "approvalMethod": APPROVAL_METHOD,
        "approvalTimeSource": APPROVAL_TIME_SOURCE,
    }


def _write_once(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise InitMaterializationError("approval output already exists; approval is write-once")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".staged"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="记录一次与精确 Preview Hash 绑定的 INIT 批准。")
    parser.add_argument("prepared", type=Path)
    parser.add_argument("--approved-hash", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        approval = record_init_approval(
            _read_json(args.prepared),
            approved_hash=args.approved_hash,
            approved_by=args.approved_by,
        )
        _write_once(args.output, approval)
    except (OSError, ValueError, InitMaterializationError) as exc:
        print(f"INIT Approval 错误：{exc}", file=sys.stderr)
        return 2
    print(f"已记录批准：{args.output}")
    print(f"Approved Preview SHA-256：{approval['previewHash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
