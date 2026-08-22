"""Compile Panorama Core + Model/View/View Set into an Explain Pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_explain_pack import PanoramaExplainPackError, build_explain_pack
from panorama_io import atomic_write
from validate_panorama import ValidationRuntimeError, load_panorama


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PanoramaExplainPackError(f"JSON 输入无效 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise PanoramaExplainPackError(f"JSON 输入必须是 object：{path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将已验证 Panorama Core、Model/View IR 与 Guided View Set 编译为逐步讲解包。"
    )
    parser.add_argument("panorama", type=Path, help="Panorama JSON 或 Single HTML")
    parser.add_argument("model", type=Path, help="Panorama Model IR JSON")
    parser.add_argument("--view", type=Path, action="append", required=True, dest="views")
    parser.add_argument("--view-set", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"Explain Pack 输出已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        panorama, _ = load_panorama(args.panorama)
        pack = build_explain_pack(
            panorama,
            _load(args.model),
            [_load(path) for path in args.views],
            _load(args.view_set),
        )
        atomic_write(args.output, json.dumps(pack, ensure_ascii=False, indent=2) + "\n")
    except (ValidationRuntimeError, PanoramaExplainPackError) as exc:
        print(f"Explain Pack 编译失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "compiled",
                "output": str(args.output),
                "explainPackId": pack["explainPackId"],
                "semanticHash": pack["integrity"]["semanticHash"],
                "storyCount": len(pack["stories"]),
                "stepCount": sum(len(story["steps"]) for story in pack["stories"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
