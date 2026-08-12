"""Read a Project Panorama and print its stable orientation summary."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import extract_data


REDACTED = "***REDACTED***"


def _by_id(items: list[dict[str, Any]], entity_id: str | None) -> dict[str, Any] | None:
    if entity_id is None:
        return None
    return next((item for item in items if item.get("id") == entity_id), None)


def _display(entity: dict[str, Any] | None, *fields: str) -> str:
    if entity is None:
        return "未知"
    for field in fields:
        value = entity.get(field)
        if value:
            if field == "id":
                return str(value)
            return f"{value} ({entity.get('id', '无 ID')})"
    return str(entity.get("id", "未知"))


def _contains_embedded_secret(data: dict[str, Any]) -> bool:
    for resource in data.get("resources", []):
        credentials = resource.get("access", {}).get("credentials", {})
        if credentials.get("mode") == "embedded":
            return True
    return False


def redact_embedded_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with only Embedded credential values redacted."""

    redacted = copy.deepcopy(data)
    for resource in redacted.get("resources", []):
        if not isinstance(resource, dict):
            continue
        access = resource.get("access", {})
        credentials = access.get("credentials", {}) if isinstance(access, dict) else {}
        if not isinstance(credentials, dict) or credentials.get("mode") != "embedded":
            continue
        fields = credentials.get("fields", {})
        if isinstance(fields, dict):
            credentials["fields"] = {key: REDACTED for key in fields}
    return redacted


def load_data(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".html", ".htm"}:
        return extract_data(path)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Panorama 数据必须是 JSON 对象。")
    return data


def orientation_summary(data: dict[str, Any]) -> list[tuple[str, str]]:
    project = data.get("project", {})
    architecture = data.get("architecture", {})
    stage = _by_id(data.get("stages", []), project.get("currentStageId"))
    current_arch = _by_id(
        architecture.get("versions", []), architecture.get("currentVersionId")
    )
    target_arch = _by_id(
        architecture.get("versions", []), architecture.get("targetVersionId")
    )
    release = _by_id(data.get("releases", []), project.get("currentReleaseId"))
    latest_update = _by_id(
        data.get("updateBatches", []), data.get("meta", {}).get("latestUpdateBatchId")
    )

    summary = [
        ("Schema 版本", str(data.get("schemaVersion", "未知"))),
        ("修订号", str(data.get("meta", {}).get("revision", "未知"))),
        ("项目", _display(project, "name")),
        ("当前阶段", _display(stage, "name")),
        ("当前架构", _display(current_arch, "label")),
        ("目标架构", _display(target_arch, "label")),
        ("当前发布", _display(release, "version", "name")),
        ("最近更新批次", _display(latest_update, "id")),
        (
            "内嵌凭据",
            "存在" if _contains_embedded_secret(data) else "不存在",
        ),
    ]
    binding = data.get("sourceBinding")
    if isinstance(binding, dict):
        summary.extend(
            [
                ("自动追踪 HEAD", str(binding.get("gitHead") or "等待首次同步")),
                ("最近观察批次", str(binding.get("lastObservationBatchId") or "无")),
                ("事实来源记录", str(len(data.get("factProvenance", [])))),
                ("规范评估", str(len(data.get("standardAssessments", [])))),
            ]
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="检查 Project Panorama HTML。")
    parser.add_argument("html", type=Path, help="Panorama HTML 路径")
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出完整内嵌 JSON，而不是项目定位摘要。",
    )
    parser.add_argument(
        "--unsafe-include-secrets",
        action="store_true",
        help="以明文输出内嵌凭据值（必须同时使用 --json）。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = load_data(args.html)
    if args.unsafe_include_secrets and not args.json:
        print("错误：--unsafe-include-secrets 必须与 --json 同时使用。", file=sys.stderr)
        return 2
    if args.json:
        if args.unsafe_include_secrets:
            print(
                "警告：正在以明文输出内嵌凭据。",
                file=sys.stderr,
            )
            output = data
        else:
            output = redact_embedded_secrets(data)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    for label, value in orientation_summary(data):
        print(f"{label}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
