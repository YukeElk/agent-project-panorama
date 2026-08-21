"""Compile a validated Panorama Model IR into a bounded View IR profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from panorama_view_ir import PanoramaViewIRError, compile_module_view_ir


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将 Model IR 确定性编译为只读 Architecture View IR。"
    )
    parser.add_argument("input", type=Path, help="Panorama Model IR JSON")
    parser.add_argument("--profile", choices=["module"], default="module")
    parser.add_argument(
        "--scope",
        choices=["current", "target", "historical"],
        action="append",
        dest="scopes",
        help="可重复指定；默认 current",
    )
    parser.add_argument("--output", type=Path, required=True, help="新 View IR JSON")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖既有候选文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"View IR 输出已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        model = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(model, dict):
            raise PanoramaViewIRError("Model IR 输入必须是 JSON 对象。")
        view = compile_module_view_ir(
            model, architecture_scopes=args.scopes or ["current"]
        )
        atomic_write(
            args.output,
            json.dumps(view, ensure_ascii=False, indent=2) + "\n",
        )
    except (OSError, UnicodeError, json.JSONDecodeError, PanoramaViewIRError) as exc:
        print(f"View IR 编译失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "compiled",
                "output": str(args.output),
                "viewId": view["viewId"],
                "semanticHash": view["integrity"]["semanticHash"],
                "layoutHash": view["integrity"]["layoutHash"],
                "nodeCount": len(view["nodes"]),
                "edgeCount": len(view["edges"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
