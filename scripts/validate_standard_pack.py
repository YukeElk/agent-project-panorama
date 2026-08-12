"""Validate a declarative Panorama Standard Pack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from panorama_cli import ChineseArgumentParser
from standard_pack import (
    DEFAULT_SCHEMA,
    StandardPackError,
    load_document,
    standard_pack_hash,
    validate_standard_pack,
)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="校验项目规范 Standard Pack。")
    parser.add_argument("input", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pack = load_document(args.input)
        errors = validate_standard_pack(pack, args.schema)
        result = {
            "valid": not errors,
            "standardPackHash": standard_pack_hash(pack),
            "errors": errors,
        }
    except (OSError, StandardPackError) as exc:
        print(f"规范包错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif errors:
        for error in errors:
            print(f"错误 STANDARD_PACK_SCHEMA: {error}")
    else:
        print(f"Standard Pack 有效：{result['standardPackHash']}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
