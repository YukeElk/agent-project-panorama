"""Read a Project Panorama and print its stable orientation summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from panorama_io import extract_data


def _by_id(items: list[dict[str, Any]], entity_id: str | None) -> dict[str, Any] | None:
    if entity_id is None:
        return None
    return next((item for item in items if item.get("id") == entity_id), None)


def _display(entity: dict[str, Any] | None, *fields: str) -> str:
    if entity is None:
        return "Unknown"
    for field in fields:
        value = entity.get(field)
        if value:
            if field == "id":
                return str(value)
            return f"{value} ({entity.get('id', 'no-id')})"
    return str(entity.get("id", "Unknown"))


def _contains_embedded_secret(data: dict[str, Any]) -> bool:
    for resource in data.get("resources", []):
        credentials = resource.get("access", {}).get("credentials", {})
        if credentials.get("mode") == "embedded":
            return True
    return False


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

    return [
        ("Schema Version", str(data.get("schemaVersion", "Unknown"))),
        ("Revision", str(data.get("meta", {}).get("revision", "Unknown"))),
        ("Project", _display(project, "name")),
        ("Current Stage", _display(stage, "name")),
        ("Current Architecture", _display(current_arch, "label")),
        ("Target Architecture", _display(target_arch, "label")),
        ("Current Release", _display(release, "version", "name")),
        ("Latest Update Batch", _display(latest_update, "id")),
        (
            "Embedded Secret",
            "Present" if _contains_embedded_secret(data) else "Not present",
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a Project Panorama HTML.")
    parser.add_argument("html", type=Path, help="Panorama HTML path")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete embedded JSON instead of the orientation summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = extract_data(args.html)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    for label, value in orientation_summary(data):
        print(f"{label}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
