"""Assess project evidence against one declarative Standard Pack."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from panorama_cli import ChineseArgumentParser
from panorama_io import extract_data
from standard_pack import StandardPackError, assess_standard_pack, load_document
from observe_project_runtime import git_observation


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="基于项目规范评估 Panorama 与项目证据。")
    parser.add_argument("panorama", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--standard", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        panorama = extract_data(args.panorama)
        git = git_observation(args.project_root.resolve(strict=True))
        result = assess_standard_pack(
            load_document(args.standard),
            args.project_root,
            panorama,
            source_commit=git.get("head"),
            assessed_at=datetime.now(timezone.utc).isoformat(),
        )
    except (OSError, ValueError, StandardPackError) as exc:
        print(f"规范评估错误：{exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"规范评估已写入：{args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
