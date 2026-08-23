"""Materialize a Codex module-logic candidate as a safe bound observation."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from module_logic_observation import (
    ModuleLogicObservationError,
    materialize_observation,
    validate_module_logic_observation,
)
from prepare_module_logic_analysis import (
    ModuleLogicAnalysisError,
    load_json_object,
    load_panorama,
    validate_manifest,
)


PRODUCER = {
    "id": "panorama-module-logic-materializer",
    "version": "0.1.0",
    "contractVersion": "module-logic-observation.v0.1",
}
_CANDIDATE_KEYS = {
    "filesReadPaths",
    "evidencePins",
    "nodes",
    "edges",
    "boundaryPorts",
    "externalReferences",
    "unresolved",
    "transformationLoss",
    "informationGaps",
    "extensions",
}
_EVIDENCE_COLLECTIONS = (
    "nodes",
    "edges",
    "boundaryPorts",
    "externalReferences",
    "unresolved",
    "transformationLoss",
)


class ModuleLogicMaterializationError(ValueError):
    """Raised when a candidate cannot be promoted to a safe observation."""


def _candidate_shape(candidate: dict[str, Any]) -> None:
    unknown = sorted(set(candidate) - _CANDIDATE_KEYS)
    missing = sorted(_CANDIDATE_KEYS - set(candidate))
    if unknown:
        raise ModuleLogicMaterializationError(
            "Candidate 含未允许字段：" + ", ".join(unknown)
        )
    if missing:
        raise ModuleLogicMaterializationError(
            "Candidate 缺少字段：" + ", ".join(missing)
        )
    if not isinstance(candidate.get("filesReadPaths"), list):
        raise ModuleLogicMaterializationError("filesReadPaths 必须是数组")


def _require_source_evidence(candidate: dict[str, Any]) -> None:
    source_pin_ids = {
        pin.get("evidenceId")
        for pin in candidate.get("evidencePins", [])
        if isinstance(pin, dict) and pin.get("kind") == "source_location"
    }
    for collection in _EVIDENCE_COLLECTIONS:
        for index, item in enumerate(candidate.get(collection, [])):
            if not isinstance(item, dict):
                continue
            if not source_pin_ids.intersection(item.get("evidencePinIds", [])):
                raise ModuleLogicMaterializationError(
                    f"/{collection}/{index}: 至少需要一个本次 Manifest 内的 Source Evidence Pin"
                )


def materialize_candidate(
    panorama: dict[str, Any],
    manifest: dict[str, Any],
    candidate: dict[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    manifest_errors = validate_manifest(manifest, panorama)
    if manifest_errors:
        raise ModuleLogicMaterializationError(
            "Analysis Manifest 无效：" + "; ".join(manifest_errors[:10])
        )
    if manifest.get("scope", {}).get("status") == "blocked":
        raise ModuleLogicMaterializationError(
            "Analysis Manifest scope=blocked，不能物化模块逻辑"
        )
    if not isinstance(candidate, dict):
        raise ModuleLogicMaterializationError("Candidate 必须是 JSON 对象")
    _candidate_shape(candidate)

    manifest_files = {
        item["path"]: item
        for item in manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    read_paths = candidate.get("filesReadPaths", [])
    if len(read_paths) != len(set(read_paths)):
        raise ModuleLogicMaterializationError("filesReadPaths 不能重复")
    unknown_paths = sorted(set(read_paths) - set(manifest_files))
    if unknown_paths:
        raise ModuleLogicMaterializationError(
            "Candidate 声明读取了 Manifest 之外的路径："
            + ", ".join(unknown_paths[:10])
        )

    for index, pin in enumerate(candidate.get("evidencePins", [])):
        if not isinstance(pin, dict):
            continue
        if pin.get("kind") != "source_location":
            continue
        path = pin.get("ref")
        source_file = manifest_files.get(path)
        if source_file is None:
            raise ModuleLogicMaterializationError(
                f"/evidencePins/{index}/ref: 不属于 Analysis Manifest"
            )
        if path not in read_paths:
            raise ModuleLogicMaterializationError(
                f"/evidencePins/{index}/ref: 未在 filesReadPaths 中声明瞬时读取"
            )
        if pin.get("digest") != source_file.get("sha256"):
            raise ModuleLogicMaterializationError(
                f"/evidencePins/{index}/digest: 与 Manifest 文件摘要不匹配"
            )
    _require_source_evidence(candidate)

    all_files_read = set(read_paths) == set(manifest_files)
    manifest_complete = manifest.get("scope", {}).get("status") == "complete"
    gaps = set(str(item) for item in manifest.get("informationGaps", []))
    gaps.update(str(item) for item in candidate.get("informationGaps", []))
    if not all_files_read:
        gaps.add("module_analysis_files_not_all_read")
    coverage_status = (
        "complete"
        if manifest_complete
        and all_files_read
        and not gaps
        and not candidate.get("unresolved")
        and not candidate.get("transformationLoss")
        else "partial"
    )
    if coverage_status != "complete" and not gaps:
        gaps.add("module_analysis_coverage_partial")

    read_files = [manifest_files[path] for path in read_paths]
    source_binding = manifest["sourceObservationBinding"]
    observation_candidate: dict[str, Any] = {
        "formatVersion": "module-logic-observation.v0.1",
        "generatedAt": generated_at,
        "producer": PRODUCER,
        "projectBinding": deepcopy(manifest["projectBinding"]),
        "moduleBinding": {
            "moduleId": manifest["moduleBinding"]["moduleId"],
            "moduleDigest": manifest["moduleBinding"]["moduleDigest"],
        },
        "sourceBinding": {
            "mode": source_binding["mode"],
            "gitHead": source_binding.get("gitHead"),
            "sourceContentDigest": source_binding["contentDigest"],
            "coverage": coverage_status,
            "currentness": (
                "current"
                if coverage_status == "complete"
                and source_binding.get("currentness") == "current"
                else "recorded_as_of"
            ),
        },
        "asOf": source_binding["observedAt"],
        "evidencePins": deepcopy(candidate["evidencePins"]),
        "nodes": deepcopy(candidate["nodes"]),
        "edges": deepcopy(candidate["edges"]),
        "boundaryPorts": deepcopy(candidate["boundaryPorts"]),
        "externalReferences": deepcopy(candidate["externalReferences"]),
        "coverage": {
            "status": coverage_status,
            "filesConsidered": len(manifest_files),
            "filesReadTransiently": len(read_paths),
            "supportedLanguages": sorted(
                {
                    str(item.get("language"))
                    for item in read_files
                    if item.get("kind") == "source"
                }
            ),
            "unsupportedLanguages": [],
        },
        "unresolved": deepcopy(candidate["unresolved"]),
        "transformationLoss": deepcopy(candidate["transformationLoss"]),
        "informationGaps": sorted(gaps),
        "extensions": {
            **deepcopy(candidate["extensions"]),
            "analysisManifestBinding": {
                "manifestId": manifest["manifestId"],
                "semanticHash": manifest["integrity"]["semanticHash"],
            },
        },
    }
    try:
        observation = materialize_observation(observation_candidate)
    except ModuleLogicObservationError as exc:
        raise ModuleLogicMaterializationError(str(exc)) from exc
    errors = validate_module_logic_observation(observation, panorama)
    if errors:
        raise ModuleLogicMaterializationError(
            "Module Logic Observation 无效：" + "; ".join(errors[:10])
        )
    return observation


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="把 Codex 结构化候选物化为源码/Manifest/Panorama 绑定的只读 Observation。"
    )
    parser.add_argument("panorama", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"Observation 已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        observation = materialize_candidate(
            load_panorama(args.panorama),
            load_json_object(args.manifest),
            load_json_object(args.candidate),
            generated_at=args.generated_at,
        )
        atomic_write(
            args.output,
            json.dumps(observation, ensure_ascii=False, indent=2) + "\n",
        )
    except (
        OSError,
        ValueError,
        ModuleLogicAnalysisError,
        ModuleLogicMaterializationError,
    ) as exc:
        print(f"模块逻辑 Observation 物化失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "observationId": observation["observationId"],
                "semanticHash": observation["integrity"]["semanticHash"],
                "moduleId": observation["moduleBinding"]["moduleId"],
                "coverage": observation["coverage"]["status"],
                "currentness": observation["sourceBinding"]["currentness"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ModuleLogicMaterializationError",
    "materialize_candidate",
]
