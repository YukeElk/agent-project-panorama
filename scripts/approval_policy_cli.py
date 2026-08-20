"""Shared, write-once JSON helpers for Approval Policy lifecycle CLIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from approval_policy import ApprovalPolicyError


def read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovalPolicyError(f"无法读取 {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ApprovalPolicyError(f"{label} 必须是 JSON 对象。")
    return value


def write_once(path: Path, value: dict[str, Any]) -> None:
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
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ApprovalPolicyError(f"输出已存在；生命周期制品禁止覆盖：{path}") from exc
        except OSError:
            try:
                output_descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                raise ApprovalPolicyError(f"输出已存在；生命周期制品禁止覆盖：{path}") from exc
            try:
                with os.fdopen(output_descriptor, "wb") as output:
                    output.write(temporary.read_bytes())
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                path.unlink(missing_ok=True)
                raise
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["read_object", "write_once"]
