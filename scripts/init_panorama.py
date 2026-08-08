"""Embed a Panorama JSON document into the stable Single-HTML template."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from panorama_io import compute_data_hash, replace_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a local-first Single-HTML Project Panorama."
    )
    parser.add_argument("--template", required=True, type=Path, help="HTML template")
    parser.add_argument("--data", required=True, type=Path, help="Panorama JSON data")
    parser.add_argument("--output", required=True, type=Path, help="Output HTML path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with args.data.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Panorama data must be a JSON object.")
    output = replace_data(args.template, data, args.output)
    print(f"Generated: {output}")
    print(f"Data SHA-256: {compute_data_hash(data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
