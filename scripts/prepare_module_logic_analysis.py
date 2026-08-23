"""Prepare a bounded, metadata-only manifest for module-logic analysis.

The manifest is the allow-list for a later Codex transient read.  This step
never persists source bodies and never executes project code.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from panorama_cli import ChineseArgumentParser
from panorama_io import (
    PanoramaIOError,
    atomic_write,
    compute_canonical_hash,
    compute_data_hash,
    extract_data,
)
from source_topology import validate_source_observation
from validate_panorama import validate_data


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "module-logic-analysis-manifest.schema.v0.1.json"
PRODUCER = {"id": "panorama-module-logic-preparer", "version": "0.1.0"}
MAX_FILES = 256
MAX_BYTES = 16 * 1024 * 1024


class ModuleLogicAnalysisError(ValueError):
    """Raised when a trustworthy bounded analysis manifest cannot be made."""


def load_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModuleLogicAnalysisError(f"无法读取 JSON {source}：{exc}") from exc
    if not isinstance(value, dict):
        raise ModuleLogicAnalysisError(f"JSON {source} 必须是对象")
    return value


def load_panorama(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.suffix.casefold() == ".json":
        return load_json_object(source)
    try:
        return extract_data(source)
    except (OSError, PanoramaIOError) as exc:
        raise ModuleLogicAnalysisError(f"无法读取 Panorama {source}：{exc}") from exc


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_errors(value: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for error in sorted(
        _schema_validator().iter_errors(value),
        key=lambda item: (
            tuple(str(part) for part in item.absolute_path),
            item.message,
        ),
    ):
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        result.append(f"{pointer or '/'}: {error.message}")
    return result


def _without_integrity(value: dict[str, Any]) -> dict[str, Any]:
    projected = deepcopy(value)
    projected.pop("integrity", None)
    return projected


def compute_manifest_semantic_hash(manifest: dict[str, Any]) -> str:
    return compute_canonical_hash(_without_integrity(manifest))


def compute_manifest_id(manifest: dict[str, Any]) -> str:
    projected = _without_integrity(manifest)
    projected.pop("manifestId", None)
    return "MLM-" + compute_canonical_hash(projected)[:24].upper()


def _normalized_code_path(value: Any) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        return ""
    if "\\" in text:
        raise ModuleLogicAnalysisError("Module codePath 必须使用项目相对 POSIX 路径")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ModuleLogicAnalysisError("Module codePath 不能是绝对路径或包含路径逃逸")
    return path.as_posix()


def _in_scope(path: str, code_path: str) -> bool:
    if not code_path:
        return False
    return path == code_path or path.startswith(code_path.rstrip("/") + "/")


def validate_manifest(
    manifest: dict[str, Any],
    panorama: dict[str, Any] | None = None,
    source_observation: dict[str, Any] | None = None,
) -> list[str]:
    if not isinstance(manifest, dict):
        return ["/: Analysis Manifest 必须是 JSON 对象"]
    errors = _schema_errors(manifest)
    if errors:
        return sorted(set(errors))
    if manifest.get("manifestId") != compute_manifest_id(manifest):
        errors.append("/manifestId: 与 Manifest 语义不匹配")
    if manifest.get("integrity", {}).get(
        "semanticHash"
    ) != compute_manifest_semantic_hash(manifest):
        errors.append("/integrity/semanticHash: 与 Manifest 语义不匹配")
    paths: set[str] = set()
    total_bytes = 0
    for index, item in enumerate(manifest.get("files", [])):
        path = item.get("path")
        if path in paths:
            errors.append(f"/files/{index}/path: 重复路径 {path!r}")
        paths.add(path)
        total_bytes += int(item.get("size", 0))
    scope = manifest.get("scope", {})
    if scope.get("filesIncluded") != len(manifest.get("files", [])):
        errors.append("/scope/filesIncluded: 与 files 数量不一致")
    if scope.get("bytesIncluded") != total_bytes:
        errors.append("/scope/bytesIncluded: 与 files 大小总和不一致")
    if scope.get("status") == "complete" and (
        scope.get("truncated")
        or manifest.get("informationGaps")
        or manifest.get("sourceObservationBinding", {}).get("coverage") != "complete"
        or manifest.get("sourceObservationBinding", {}).get("currentness") != "current"
    ):
        errors.append("/scope/status: 存在截断/缺口/不完整源时不能声明 complete")

    if panorama is not None:
        project = panorama.get("project", {})
        meta = panorama.get("meta", {})
        binding = manifest.get("projectBinding", {})
        if binding.get("projectId") != project.get("id"):
            errors.append("/projectBinding/projectId: 与 Panorama 不匹配")
        if binding.get("panoramaSchemaVersion") != panorama.get("schemaVersion"):
            errors.append("/projectBinding/panoramaSchemaVersion: 与 Panorama 不匹配")
        if binding.get("revision") != meta.get("revision"):
            errors.append("/projectBinding/revision: 与 Panorama 不匹配")
        if binding.get("dataHash") != compute_data_hash(panorama):
            errors.append("/projectBinding/dataHash: 与 Panorama 不匹配")
        modules = {
            item.get("id"): item
            for item in panorama.get("architecture", {}).get("modules", [])
            if isinstance(item, dict)
        }
        module = modules.get(manifest.get("moduleBinding", {}).get("moduleId"))
        if module is None:
            errors.append("/moduleBinding/moduleId: 未绑定正式 Module")
        elif manifest.get("moduleBinding", {}).get(
            "moduleDigest"
        ) != compute_canonical_hash(module):
            errors.append("/moduleBinding/moduleDigest: 与正式 Module 不匹配")

    if source_observation is not None:
        binding = manifest.get("sourceObservationBinding", {})
        if binding.get("observationId") != source_observation.get("observationId"):
            errors.append("/sourceObservationBinding/observationId: 与 Source Observation 不匹配")
        if binding.get("semanticHash") != source_observation.get("integrity", {}).get(
            "semanticHash"
        ):
            errors.append("/sourceObservationBinding/semanticHash: 与 Source Observation 不匹配")
        if binding.get("observedAt") != source_observation.get("observedAt"):
            errors.append("/sourceObservationBinding/observedAt: 与 Source Observation 不匹配")
        inventory = {
            item.get("path"): item
            for item in source_observation.get("inventory", {}).get("files", [])
            if isinstance(item, dict)
        }
        for index, item in enumerate(manifest.get("files", [])):
            source_item = inventory.get(item.get("path"))
            if source_item is None or any(
                item.get(field) != source_item.get(field)
                for field in ("kind", "language", "size", "sha256", "parseStatus")
            ):
                errors.append(f"/files/{index}: 与 Source Observation Inventory 不匹配")
    return sorted(set(errors))


def prepare_manifest(
    panorama: dict[str, Any],
    source_observation: dict[str, Any],
    *,
    module_id: str,
    prepared_at: str,
    max_files: int = MAX_FILES,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    report = validate_data(panorama)
    if report.errors:
        raise ModuleLogicAnalysisError(
            "Panorama Formal Validation 失败："
            + "; ".join(issue.render() for issue in report.errors[:10])
        )
    source_errors = validate_source_observation(source_observation)
    if source_errors:
        raise ModuleLogicAnalysisError(
            "Source Observation 无效：" + "; ".join(source_errors[:10])
        )
    if source_observation.get("projectBinding", {}).get(
        "projectId"
    ) != panorama.get("project", {}).get("id"):
        raise ModuleLogicAnalysisError("Source Observation 与 Panorama Project 不匹配")
    if not 1 <= max_files <= MAX_FILES:
        raise ModuleLogicAnalysisError(f"max_files 必须介于 1 和 {MAX_FILES}")
    if not 1 <= max_bytes <= MAX_BYTES:
        raise ModuleLogicAnalysisError(f"max_bytes 必须介于 1 和 {MAX_BYTES}")

    modules = {
        item.get("id"): item
        for item in panorama.get("architecture", {}).get("modules", [])
        if isinstance(item, dict)
    }
    module = modules.get(module_id)
    if module is None:
        raise ModuleLogicAnalysisError(f"未知正式 Module：{module_id}")
    code_path = _normalized_code_path(module.get("codePath"))
    inventory = sorted(
        [
            item
            for item in source_observation.get("inventory", {}).get("files", [])
            if isinstance(item, dict) and _in_scope(str(item.get("path", "")), code_path)
        ],
        key=lambda item: str(item.get("path", "")),
    )
    included: list[dict[str, Any]] = []
    included_bytes = 0
    truncated = False
    for item in inventory:
        item_size = int(item.get("size", 0))
        if len(included) >= max_files or included_bytes + item_size > max_bytes:
            truncated = True
            continue
        included.append(
            {
                field: deepcopy(item.get(field))
                for field in (
                    "path",
                    "kind",
                    "language",
                    "size",
                    "sha256",
                    "parseStatus",
                )
            }
        )
        included_bytes += item_size

    source_binding = source_observation.get("sourceBinding", {})
    gaps: list[str] = []
    if not code_path:
        gaps.append("module_code_path_not_declared")
    if not inventory:
        gaps.append("module_scope_has_no_source_inventory")
    if truncated:
        gaps.append("module_scope_truncated_by_analysis_limits")
    if source_binding.get("coverage") != "complete":
        gaps.append("source_observation_coverage_partial")
    if source_binding.get("currentness") != "current":
        gaps.append("source_observation_currentness_unknown")
    if any(item.get("parseStatus") in {"partial", "failed"} for item in included):
        gaps.append("module_scope_contains_partial_or_failed_parse")
    scope_status = (
        "blocked"
        if not code_path or not inventory
        else "partial"
        if gaps
        else "complete"
    )
    manifest: dict[str, Any] = {
        "formatVersion": "module-logic-analysis-manifest.v0.1",
        "preparedAt": prepared_at,
        "producer": PRODUCER,
        "projectBinding": {
            "projectId": panorama["project"]["id"],
            "panoramaSchemaVersion": panorama["schemaVersion"],
            "revision": panorama["meta"]["revision"],
            "dataHash": compute_data_hash(panorama),
        },
        "moduleBinding": {
            "moduleId": module_id,
            "moduleDigest": compute_canonical_hash(module),
            "codePath": code_path,
        },
        "sourceObservationBinding": {
            "observationId": source_observation["observationId"],
            "semanticHash": source_observation["integrity"]["semanticHash"],
            "observedAt": source_observation["observedAt"],
            "mode": source_binding["mode"],
            "gitHead": source_binding.get("gitHead"),
            "contentDigest": source_binding["contentDigest"],
            "coverage": source_binding["coverage"],
            "currentness": source_binding["currentness"],
        },
        "scope": {
            "status": scope_status,
            "filesMatched": len(inventory),
            "filesIncluded": len(included),
            "bytesIncluded": included_bytes,
            "maxFiles": max_files,
            "maxBytes": max_bytes,
            "truncated": truncated,
        },
        "files": included,
        "informationGaps": sorted(set(gaps)),
        "privacyBoundary": {
            "sourceBodiesPersisted": False,
            "projectCodeExecuted": False,
            "networkRequired": False,
            "allowedTransientReads": "manifest_files_only",
            "candidateOutputContract": "module-logic-observation-candidate.v0.1",
        },
        "extensions": {},
    }
    manifest["manifestId"] = compute_manifest_id(manifest)
    manifest["integrity"] = {
        "semanticHash": compute_manifest_semantic_hash(manifest),
        "hashScope": "manifest_without_integrity",
    }
    errors = validate_manifest(manifest, panorama, source_observation)
    if errors:
        raise ModuleLogicAnalysisError("Analysis Manifest 无效：" + "; ".join(errors[:10]))
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="为单个正式 Module 生成不含源码正文的有界 Codex 分析清单。"
    )
    parser.add_argument("panorama", type=Path)
    parser.add_argument("source_observation", type=Path)
    parser.add_argument("--module-id", required=True)
    parser.add_argument("--prepared-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"分析清单已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        manifest = prepare_manifest(
            load_panorama(args.panorama),
            load_json_object(args.source_observation),
            module_id=args.module_id,
            prepared_at=args.prepared_at,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
        atomic_write(
            args.output,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    except (OSError, ValueError, ModuleLogicAnalysisError) as exc:
        print(f"模块逻辑分析准备失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "manifestId": manifest["manifestId"],
                "semanticHash": manifest["integrity"]["semanticHash"],
                "moduleId": manifest["moduleBinding"]["moduleId"],
                "scopeStatus": manifest["scope"]["status"],
                "filesIncluded": manifest["scope"]["filesIncluded"],
                "sourceBodiesPersisted": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_BYTES",
    "MAX_FILES",
    "ModuleLogicAnalysisError",
    "compute_manifest_id",
    "compute_manifest_semantic_hash",
    "load_json_object",
    "load_panorama",
    "prepare_manifest",
    "validate_manifest",
]
