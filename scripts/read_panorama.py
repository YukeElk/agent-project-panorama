"""Read a Project Panorama and print its stable orientation summary."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from panorama_io import extract_data


REDACTED = "***REDACTED***"


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
        raise ValueError("Panorama data must be a JSON object.")
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
    parser.add_argument(
        "--unsafe-include-secrets",
        action="store_true",
        help="Emit Embedded credential values in plaintext (requires --json).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = load_data(args.html)
    if args.unsafe_include_secrets and not args.json:
        print("ERROR: --unsafe-include-secrets requires --json.", file=sys.stderr)
        return 2
    if args.json:
        if args.unsafe_include_secrets:
            print(
                "WARNING: Embedded secrets are being emitted in plaintext.",
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
