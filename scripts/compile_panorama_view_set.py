"""Compile validated single-scope Panorama View IRs into a guided View Set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from panorama_view_ir import PanoramaViewIRError
from panorama_view_set import build_view_set


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PanoramaViewIRError(f"JSON 输入必须是 object：{path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将同一 Model 的单 scope View IR 编译为引导式 View Set。"
    )
    parser.add_argument("model", type=Path, help="Panorama Model IR JSON")
    parser.add_argument("--view", type=Path, action="append", required=True, dest="views")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"View Set 输出已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        view_set = build_view_set(_load(args.model), [_load(path) for path in args.views])
        atomic_write(args.output, json.dumps(view_set, ensure_ascii=False, indent=2) + "\n")
    except (OSError, UnicodeError, json.JSONDecodeError, PanoramaViewIRError) as exc:
        print(f"View Set 编译失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "compiled",
                "output": str(args.output),
                "viewSetId": view_set["viewSetId"],
                "semanticHash": view_set["integrity"]["semanticHash"],
                "chapterCount": len(view_set["chapters"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
