"""Reconcile a module-logic manifest/observation with current source state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write, compute_canonical_hash, compute_data_hash
from module_logic_observation import validate_module_logic_observation
from prepare_module_logic_analysis import (
    ModuleLogicAnalysisError,
    load_json_object,
    load_panorama,
    validate_manifest,
)
from source_topology import validate_source_observation


class ModuleLogicReconcileError(ValueError):
    """Raised when reconciliation inputs are malformed rather than stale."""


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & 0x400)


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _module(panorama: dict[str, Any], module_id: Any) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in panorama.get("architecture", {}).get("modules", [])
            if isinstance(item, dict) and item.get("id") == module_id
        ),
        None,
    )


def reconcile(
    panorama: dict[str, Any],
    manifest: dict[str, Any],
    observation: dict[str, Any],
    latest_source_observation: dict[str, Any],
    *,
    project_root: str | Path,
    checked_at: str,
) -> dict[str, Any]:
    manifest_errors = validate_manifest(manifest)
    if manifest_errors:
        raise ModuleLogicReconcileError(
            "Analysis Manifest 本身无效：" + "; ".join(manifest_errors[:10])
        )
    observation_errors = validate_module_logic_observation(observation)
    if observation_errors:
        raise ModuleLogicReconcileError(
            "Module Logic Observation 本身无效："
            + "; ".join(observation_errors[:10])
        )
    source_errors = validate_source_observation(latest_source_observation)
    if source_errors:
        raise ModuleLogicReconcileError(
            "Latest Source Observation 无效：" + "; ".join(source_errors[:10])
        )

    stale_reasons: list[str] = []
    incomplete_reasons: list[str] = []
    unknown_reasons: list[str] = []
    project_binding = manifest["projectBinding"]
    module_binding = manifest["moduleBinding"]
    if project_binding.get("projectId") != panorama.get("project", {}).get("id"):
        stale_reasons.append("panorama_project_changed")
    if project_binding.get("panoramaSchemaVersion") != panorama.get("schemaVersion"):
        stale_reasons.append("panorama_schema_changed")
    if project_binding.get("revision") != panorama.get("meta", {}).get("revision"):
        stale_reasons.append("panorama_revision_changed")
    if project_binding.get("dataHash") != compute_data_hash(panorama):
        stale_reasons.append("panorama_data_hash_changed")
    module = _module(panorama, module_binding.get("moduleId"))
    if module is None:
        stale_reasons.append("formal_module_missing")
    elif module_binding.get("moduleDigest") != compute_canonical_hash(module):
        stale_reasons.append("formal_module_changed")

    latest_binding = latest_source_observation.get("sourceBinding", {})
    bound_source = manifest["sourceObservationBinding"]
    if latest_source_observation.get("projectBinding", {}).get(
        "projectId"
    ) != project_binding.get("projectId"):
        stale_reasons.append("source_observation_project_changed")
    for field, reason in (
        ("contentDigest", "source_content_digest_changed"),
        ("gitHead", "source_git_head_changed"),
    ):
        if bound_source.get(field) != latest_binding.get(field):
            stale_reasons.append(reason)
    if latest_source_observation.get("observationId") != bound_source.get(
        "observationId"
    ):
        stale_reasons.append("source_observation_id_changed")
    if latest_source_observation.get("integrity", {}).get(
        "semanticHash"
    ) != bound_source.get("semanticHash"):
        stale_reasons.append("source_observation_hash_changed")
    if latest_binding.get("coverage") != "complete":
        incomplete_reasons.append("latest_source_coverage_partial")
    if latest_binding.get("currentness") != "current":
        unknown_reasons.append("latest_source_currentness_unknown")
    if manifest.get("scope", {}).get("status") != "complete":
        incomplete_reasons.append("analysis_manifest_scope_not_complete")
    if observation.get("coverage", {}).get("status") != "complete":
        incomplete_reasons.append("module_logic_observation_coverage_not_complete")
    if observation.get("informationGaps"):
        incomplete_reasons.append("module_logic_observation_has_information_gaps")

    root = Path(project_root).resolve(strict=True)
    latest_inventory = {
        item.get("path"): item
        for item in latest_source_observation.get("inventory", {}).get("files", [])
        if isinstance(item, dict)
    }
    checked_files = 0
    for item in manifest.get("files", []):
        relative = item["path"]
        latest_item = latest_inventory.get(relative)
        if latest_item is None or any(
            latest_item.get(field) != item.get(field)
            for field in ("kind", "language", "size", "sha256", "parseStatus")
        ):
            stale_reasons.append(f"latest_inventory_changed:{relative}")
        lexical = root / Path(relative)
        try:
            if _is_link_or_reparse(lexical):
                stale_reasons.append(f"unsafe_or_missing_source_path:{relative}")
                continue
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(root)
            body = resolved.read_bytes()
        except (OSError, ValueError):
            stale_reasons.append(f"unsafe_or_missing_source_path:{relative}")
            continue
        checked_files += 1
        if len(body) != item.get("size") or hashlib.sha256(body).hexdigest() != item.get(
            "sha256"
        ):
            stale_reasons.append(f"source_file_changed:{relative}")

    live_git_head = _git_head(root)
    if bound_source.get("mode") == "git":
        if live_git_head is None:
            unknown_reasons.append("live_git_head_unavailable")
        elif live_git_head != bound_source.get("gitHead"):
            stale_reasons.append("live_git_head_changed")

    if stale_reasons:
        status = "stale"
    elif incomplete_reasons:
        status = "incomplete"
    elif unknown_reasons:
        status = "unknown"
    else:
        status = "match"
    result: dict[str, Any] = {
        "formatVersion": "module-logic-reconciliation.v0.1",
        "checkedAt": checked_at,
        "status": status,
        "manifestBinding": {
            "manifestId": manifest["manifestId"],
            "semanticHash": manifest["integrity"]["semanticHash"],
        },
        "observationBinding": {
            "observationId": observation["observationId"],
            "semanticHash": observation["integrity"]["semanticHash"],
        },
        "latestSourceBinding": {
            "observationId": latest_source_observation["observationId"],
            "semanticHash": latest_source_observation["integrity"]["semanticHash"],
            "gitHead": latest_binding.get("gitHead"),
            "contentDigest": latest_binding.get("contentDigest"),
        },
        "reasons": sorted(
            set(stale_reasons + incomplete_reasons + unknown_reasons)
        ),
        "checks": {
            "filesExpected": len(manifest.get("files", [])),
            "filesReadTransiently": checked_files,
            "sourceBodiesPersisted": False,
            "projectCodeExecuted": False,
            "networkRequired": False,
        },
    }
    result["integrity"] = {
        "semanticHash": compute_canonical_hash(result),
        "hashScope": "reconciliation_without_integrity",
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="比较 Panorama/Module/Source/File Hash，返回 match/stale/incomplete/unknown。"
    )
    parser.add_argument("panorama", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("observation", type=Path)
    parser.add_argument("latest_source_observation", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--checked-at", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output and args.output.exists() and not args.overwrite:
        print(f"Reconciliation 已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        result = reconcile(
            load_panorama(args.panorama),
            load_json_object(args.manifest),
            load_json_object(args.observation),
            load_json_object(args.latest_source_observation),
            project_root=args.project_root,
            checked_at=args.checked_at,
        )
        if args.output:
            atomic_write(
                args.output,
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            )
    except (
        OSError,
        ValueError,
        ModuleLogicAnalysisError,
        ModuleLogicReconcileError,
    ) as exc:
        print(f"模块逻辑对账失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "match" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ModuleLogicReconcileError", "reconcile"]
