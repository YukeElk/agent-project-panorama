"""Record one explicit, write-once approval for a Studio Proposal artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from apply_patch import (
    ApplyPatchError,
    STUDIO_APPROVAL_METHOD,
    STUDIO_APPROVAL_TIME_SOURCE,
    STUDIO_APPROVAL_VERSION,
    STUDIO_SOURCE_BINDING_FIELDS,
    compute_proposal_hash,
    parse_proposal_artifact,
)
from panorama_cli import ChineseArgumentParser


class StudioApprovalError(RuntimeError):
    """A Studio approval cannot be recorded without weakening its binding."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise StudioApprovalError(f"{field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StudioApprovalError(f"{field} must be an ISO timestamp") from exc
    if parsed.utcoffset() is None:
        raise StudioApprovalError(f"{field} must include a UTC offset")
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioApprovalError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StudioApprovalError(f"{label} must be a JSON object")
    return value


def _normalise_source_binding(
    value: Any, package: dict[str, Any]
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise StudioApprovalError("sourceBinding must be a non-empty object")
    unknown = sorted(set(value).difference(STUDIO_SOURCE_BINDING_FIELDS))
    if unknown:
        raise StudioApprovalError(
            "sourceBinding contains unknown fields: " + ", ".join(unknown)
        )
    binding = dict(value)
    binding.setdefault("baseRevision", package.get("baseRevision"))
    binding.setdefault("baseDataHash", package.get("baseDataHash"))
    if binding.get("baseRevision") != package.get("baseRevision"):
        raise StudioApprovalError(
            "sourceBinding.baseRevision does not match the Proposal"
        )
    if binding.get("baseDataHash") != package.get("baseDataHash"):
        raise StudioApprovalError(
            "sourceBinding.baseDataHash does not match the Proposal"
        )
    return binding


def record_studio_approval(
    proposal_artifact: dict[str, Any],
    *,
    approved_hash: str,
    approved_by: str,
    recorded_at: str | None = None,
    source_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an approval from exact Proposal bytes-as-semantics.

    ``recorded_at`` is injectable for deterministic tests and trusted host
    callers.  The CLI never accepts it, so a Studio page cannot supply its own
    approval time.
    """

    try:
        package, wrapper_binding = parse_proposal_artifact(proposal_artifact)
    except ApplyPatchError as exc:
        raise StudioApprovalError(str(exc)) from exc
    proposal_hash = package.get("proposalHash")
    recomputed = compute_proposal_hash(package)
    if proposal_hash != recomputed:
        raise StudioApprovalError(
            "stored proposalHash does not match current Proposal semantics"
        )
    if not isinstance(approved_hash, str) or approved_hash != recomputed:
        raise StudioApprovalError(
            "explicit approved hash does not match the exact Proposal Hash"
        )
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise StudioApprovalError("approvedBy must identify the approving user")
    if source_binding is not None and wrapper_binding is not None:
        raise StudioApprovalError(
            "sourceBinding must come from exactly one trusted input"
        )
    binding = _normalise_source_binding(
        source_binding if source_binding is not None else wrapper_binding,
        package,
    )
    approval = {
        "approvalVersion": STUDIO_APPROVAL_VERSION,
        "status": "approved",
        "proposalHash": recomputed,
        "approvedBy": approved_by.strip(),
        "approvalRecordedAt": _timestamp(
            recorded_at or _now(), "approvalRecordedAt"
        ),
        "approvalMethod": STUDIO_APPROVAL_METHOD,
        "approvalTimeSource": STUDIO_APPROVAL_TIME_SOURCE,
    }
    if binding is not None:
        approval["sourceBinding"] = binding
    return approval


def _write_once(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise StudioApprovalError(
            "approval output already exists; approval is write-once"
        )
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
            # Hard-link publication is an atomic create-if-absent operation.
            # It closes the exists()/replace() race without overwriting a
            # concurrently recorded approval.
            os.link(temporary, path)
        except FileExistsError as exc:
            raise StudioApprovalError(
                "approval output already exists; approval is write-once"
            ) from exc
        except OSError:
            # Some filesystems do not support hard links.  O_EXCL retains
            # write-once semantics there; partial output is removed on error.
            try:
                output_descriptor = os.open(
                    path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError as exc:
                raise StudioApprovalError(
                    "approval output already exists; approval is write-once"
                ) from exc
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


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="记录一次与精确 Proposal Hash 绑定的 Studio 批准。"
    )
    parser.add_argument("proposal", type=Path, help="Proposal wrapper 或 legacy bare package")
    parser.add_argument("--approved-hash", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--source-binding", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        source_binding = (
            _read_json(args.source_binding, "sourceBinding")
            if args.source_binding is not None
            else None
        )
        approval = record_studio_approval(
            _read_json(args.proposal, "Proposal artifact"),
            approved_hash=args.approved_hash,
            approved_by=args.approved_by,
            source_binding=source_binding,
        )
        _write_once(args.output, approval)
    except (OSError, ValueError, StudioApprovalError) as exc:
        print(f"Studio Approval 错误：{exc}", file=sys.stderr)
        return 2
    print(f"已记录批准：{args.output}")
    print(f"Approved Proposal SHA-256：{approval['proposalHash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
