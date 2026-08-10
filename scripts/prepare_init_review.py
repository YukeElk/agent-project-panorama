"""Prepare and prevalidate the exact INIT Preview that a user may approve."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from materialize_init import (
    InitMaterializationError,
    _assert_empty_initial_history,
    _bind_initial_review,
    _canonical_json,
    _materialize_changes,
    _sanitize_evidence_inventory,
    _timestamp,
    assert_release_semantics,
    compute_preview_hash,
)
from observe_project_runtime import source_snapshot
from panorama_cli import ChineseArgumentParser
from validate_panorama import validate_data


DRAFT_VERSION = "draft-init-model.v0.1"
PREPARED_VERSION = "prepared-init-review.v0.1"
REQUIRED_PREVIEW_FIELDS = {
    "generatedAt",
    "sourceSnapshot",
    "panoramaData",
    "reviewSubjectRefs",
    "changeIntents",
    "evidenceInventory",
    "guidance",
    "updateSummaryItems",
    "changeLevel",
    "reviewSummary",
    "reviewDecisionIds",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_new(path: Path, value: str) -> None:
    if path.exists():
        raise InitMaterializationError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".staged"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(value, encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InitMaterializationError(f"{field} must be a non-empty string list")
    return value


def _prevalidate_preview(
    preview: dict[str, Any], schema_path: Path, *, base_dir: Path
) -> dict[str, Any]:
    missing = REQUIRED_PREVIEW_FIELDS - set(preview)
    if missing:
        raise InitMaterializationError(
            f"draft Preview missing fields: {sorted(missing)}"
        )
    preview_at = _timestamp(preview.get("generatedAt"), "preview.generatedAt")
    source_data = preview.get("panoramaData")
    if not isinstance(source_data, dict):
        raise InitMaterializationError("preview.panoramaData must be an object")
    data = copy.deepcopy(source_data)
    _assert_empty_initial_history(data)
    assert_release_semantics(data)

    guidance = preview.get("guidance")
    if not isinstance(guidance, dict) or not 2 <= len(guidance.get("options", [])) <= 3:
        raise InitMaterializationError("draft Preview Guidance must contain 2-3 options")
    data["guidance"] = copy.deepcopy(guidance)
    _sanitize_evidence_inventory(preview.get("evidenceInventory", []))
    _require_string_list(preview.get("updateSummaryItems"), "updateSummaryItems")

    subject_refs = preview.get("reviewSubjectRefs")
    if not isinstance(subject_refs, list) or not subject_refs:
        raise InitMaterializationError("reviewSubjectRefs must not be empty")
    changes = _materialize_changes(
        preview.get("changeIntents", []),
        suffix="PREPARECHECK",
        review_id="REV-PREPARE-CHECK",
        approved_at=preview_at,
        preview_hash="0" * 64,
    )
    review = {
        "id": "REV-PREPARE-CHECK",
        "type": "panorama_update",
        "subjectRefs": copy.deepcopy(subject_refs),
        "status": "pending",
        "impactLevel": preview.get("changeLevel"),
        "requestedBy": preview.get("requestedBy", "ai"),
        "requestedAt": preview_at,
        "reviewedBy": "",
        "reviewedAt": None,
        "summary": preview.get("reviewSummary"),
        "comments": "PREPARE-only validation record.",
        "decisionIds": copy.deepcopy(preview.get("reviewDecisionIds")),
        "changeIds": [item["id"] for item in changes],
        "extensions": {},
    }
    data["reviews"] = [review]
    data["changes"] = changes
    _bind_initial_review(data, subject_refs, review["id"])
    report = validate_data(data, schema_path, base_dir=base_dir)
    if report.errors:
        raise InitMaterializationError(
            "draft Preview prevalidation failed: "
            + "; ".join(item.render() for item in report.errors[:5])
        )
    return report.to_dict()


def prepare_init_review(
    draft: dict[str, Any],
    schema_path: Path,
    *,
    base_dir: Path,
    current_source_snapshot: dict[str, Any],
    prepared_at: str | None = None,
) -> dict[str, Any]:
    """Return a frozen prepared artifact only after full prevalidation succeeds."""

    if draft.get("draftVersion") != DRAFT_VERSION:
        raise InitMaterializationError(f"draftVersion must be {DRAFT_VERSION}")
    preview = draft.get("preview")
    if not isinstance(preview, dict):
        raise InitMaterializationError("draft.preview must be an object")
    expected_snapshot = preview.get("sourceSnapshot")
    if not isinstance(expected_snapshot, dict):
        raise InitMaterializationError("preview.sourceSnapshot is required")
    if _canonical_json(expected_snapshot) != _canonical_json(current_source_snapshot):
        raise InitMaterializationError(
            "project source snapshot changed before PREPARE; regenerate the draft"
        )
    validation = _prevalidate_preview(preview, schema_path, base_dir=base_dir)
    prepared_time = _timestamp(prepared_at or _now(), "preparedAt")
    generated_time = _timestamp(preview.get("generatedAt"), "preview.generatedAt")
    if datetime.fromisoformat(prepared_time.replace("Z", "+00:00")) < datetime.fromisoformat(
        generated_time.replace("Z", "+00:00")
    ):
        raise InitMaterializationError("preparedAt cannot precede Preview generation")
    frozen_preview = copy.deepcopy(preview)
    preview_hash = compute_preview_hash(frozen_preview)
    return {
        "preparedVersion": PREPARED_VERSION,
        "preparedAt": prepared_time,
        "preview": frozen_preview,
        "previewHash": preview_hash,
        "sourceSnapshot": copy.deepcopy(expected_snapshot),
        "validation": validation,
        "approvalState": "awaiting_user_approval",
    }


def _labels(items: Any, *keys: str) -> str:
    if not isinstance(items, list) or not items:
        return "- 无"
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = next((item.get(key) for key in keys if item.get(key)), "未命名")
        lines.append(f"- {label}")
    return "\n".join(lines) or "- 无"


def render_prepared_preview(
    prepared: dict[str, Any], *, artifact_name: str = "prepared-init-review.json"
) -> str:
    preview = prepared["preview"]
    data = preview["panoramaData"]
    project = data.get("project", {})
    versions = data.get("architecture", {}).get("versions", [])
    current_id = data.get("architecture", {}).get("currentVersionId")
    target_id = data.get("architecture", {}).get("targetVersionId")
    current = [item for item in versions if item.get("id") == current_id]
    target = [item for item in versions if item.get("id") == target_id]
    batch_attention = preview.get("attentionCandidates", [])
    guidance = preview.get("guidance", {})
    hash_value = prepared["previewHash"]
    return f"""# INIT Preview

## Project
- {project.get('name', project.get('id', '未命名项目'))}

## Stage
- {project.get('currentStageId', '未设置')}

## Current Focus
- {project.get('currentFocus', '未设置')}

## Current Architecture
{_labels(current, 'label', 'id')}

## Target Architecture
{_labels(target, 'label', 'id')}

## Transitions
{_labels(data.get('architecture', {}).get('transitions', []), 'summary', 'id')}

## Modules
{_labels(data.get('architecture', {}).get('modules', []), 'name', 'id')}

## Decisions
{_labels(data.get('decisions', []), 'title', 'summary', 'id')}

## Risks
{_labels(data.get('risks', []), 'title', 'summary', 'id')}

## Verification / Runtime
{_labels(preview.get('evidenceInventory', []), 'path', 'factClass')}

## Attention Candidates
{_labels(batch_attention, 'title', 'summary', 'id')}

## Next Focus
{_labels(guidance.get('options', []), 'title', 'label', 'id')}

---
Approval Binding
Preview SHA-256: {hash_value}
Prepared Artifact: {artifact_name}
Status: Awaiting explicit hash approval
---
"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InitMaterializationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InitMaterializationError("draft INIT model must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="预校验并冻结待批准的 INIT Preview。")
    parser.add_argument("draft", type=Path, nargs="?")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preview-markdown", type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "schema"
        / "panorama.schema.v0.1.json",
    )
    parser.add_argument("--print-source-snapshot", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.project_root is None:
            raise InitMaterializationError("--project-root is required")
        root = args.project_root.resolve(strict=True)
        snapshot = source_snapshot(root)
        if args.print_source_snapshot:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0
        if args.draft is None or args.output is None or args.preview_markdown is None:
            raise InitMaterializationError(
                "draft, --output and --preview-markdown are required for PREPARE"
            )
        if args.output.exists() or args.preview_markdown.exists():
            raise InitMaterializationError(
                "PREPARE outputs already exist; regenerate to new paths"
            )
        prepared = prepare_init_review(
            _read_json(args.draft),
            args.schema,
            base_dir=root,
            current_source_snapshot=snapshot,
        )
        _write_new(
            args.output,
            json.dumps(prepared, ensure_ascii=False, indent=2) + "\n",
        )
        _write_new(
            args.preview_markdown,
            render_prepared_preview(prepared, artifact_name=args.output.name),
        )
    except (OSError, ValueError, InitMaterializationError) as exc:
        print(f"INIT PREPARE 错误：{exc}", file=sys.stderr)
        return 2
    print(f"已准备：{args.output}")
    print(f"Review Preview：{args.preview_markdown}")
    print(f"Preview SHA-256：{prepared['previewHash']}")
    print(f"批准命令语义：批准 INIT Preview {prepared['previewHash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
