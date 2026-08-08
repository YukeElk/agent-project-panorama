"""Embed a Panorama JSON document into the stable Single-HTML template."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from panorama_cli import ChineseArgumentParser
from panorama_io import compute_data_hash, extract_data, replace_data
from validate_panorama import validate_data


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="生成 local-first 的 Single-HTML Project Panorama。"
    )
    parser.add_argument("--template", required=True, type=Path, help="HTML 模板")
    parser.add_argument("--data", required=True, type=Path, help="Panorama JSON 数据")
    parser.add_argument("--output", required=True, type=Path, help="输出 HTML 路径")
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "schema" / "panorama.schema.v0.1.json",
    )
    parser.add_argument(
        "--allow-invalid",
        action="store_true",
        help="即使校验报告错误也生成调试输出。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with args.data.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Panorama 数据必须是 JSON 对象。")
    before = validate_data(
        data,
        args.schema,
        base_dir=args.data.parent,
        source_path=args.data,
    )
    if before.errors and not args.allow_invalid:
        for issue in before.errors:
            print(issue.render(), file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, stage_name = tempfile.mkstemp(
        dir=args.output.parent,
        prefix=f".{args.output.name}.",
        suffix=".staged",
    )
    os.close(descriptor)
    staged = Path(stage_name)
    staged.unlink(missing_ok=True)
    try:
        replace_data(args.template, data, staged)
        generated = extract_data(staged)
        after = validate_data(
            generated,
            args.schema,
            base_dir=args.output.parent,
            source_path=staged,
        )
        if after.errors and not args.allow_invalid:
            for issue in after.errors:
                print(issue.render(), file=sys.stderr)
            return 1
        os.replace(staged, args.output)
    finally:
        staged.unlink(missing_ok=True)
    output = args.output
    print(f"已生成：{output}")
    print(f"数据 SHA-256：{compute_data_hash(data)}")
    for issue in before.warnings:
        print(issue.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
