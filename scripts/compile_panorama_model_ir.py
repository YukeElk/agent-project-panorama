"""Compile a valid Panorama Core JSON/HTML into read-only Model IR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from panorama_view_ir import PanoramaViewIRError, load_and_compile_model_ir
from validate_panorama import ValidationRuntimeError


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将正式 Panorama Core 确定性编译为只读 Model IR。"
    )
    parser.add_argument("input", type=Path, help="Panorama JSON 或 Single HTML")
    parser.add_argument("--output", type=Path, required=True, help="新 Model IR JSON")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖既有候选文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"Model IR 输出已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        model = load_and_compile_model_ir(args.input)
        atomic_write(
            args.output,
            json.dumps(model, ensure_ascii=False, indent=2) + "\n",
        )
    except (OSError, ValueError, ValidationRuntimeError, PanoramaViewIRError) as exc:
        print(f"Model IR 编译失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "compiled",
                "output": str(args.output),
                "modelId": model["modelId"],
                "semanticHash": model["integrity"]["semanticHash"],
                "entityCount": len(model["entities"]),
                "relationCount": len(model["relations"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
