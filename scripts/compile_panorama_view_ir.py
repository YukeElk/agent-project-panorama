"""Compile a validated Panorama Model IR into a bounded View IR profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from panorama_view_ir import (
    PanoramaViewIRError,
    compile_dependency_dataflow_view_ir,
    compile_deployment_runtime_view_ir,
    compile_evolution_risk_view_ir,
    compile_lifecycle_view_ir,
    compile_module_view_ir,
    compile_sequence_view_ir,
)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将 Model IR 确定性编译为有界、只读的 View IR。"
    )
    parser.add_argument("input", type=Path, help="Panorama Model IR JSON")
    parser.add_argument(
        "--profile",
        choices=[
            "module",
            "dependency_dataflow",
            "deployment_runtime",
            "sequence",
            "lifecycle",
            "evolution_risk",
        ],
        default="module",
    )
    parser.add_argument(
        "--scope",
        choices=["current", "target", "transition", "historical"],
        action="append",
        dest="scopes",
        help="可重复指定；默认 current",
    )
    parser.add_argument(
        "--root-entity",
        action="append",
        dest="root_entity_ids",
        help="Dependency/Data Flow 精确根实体；可重复指定",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        help="Dependency/Data Flow 根实体遍历深度；默认 2",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        help="Dependency/Data Flow 最大节点数；默认 30",
    )
    parser.add_argument(
        "--environment",
        action="append",
        dest="environments",
        help="Deployment/Runtime 环境过滤；可重复指定",
    )
    parser.add_argument(
        "--correlation-id",
        action="append",
        dest="correlation_ids",
        help="Sequence Event correlation 过滤；可重复指定",
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
        default_scope = (
            "historical"
            if args.profile in {"sequence", "lifecycle", "evolution_risk"}
            else "current"
        )
        common = {"architecture_scopes": args.scopes or [default_scope]}
        if args.profile == "module":
            if (
                args.root_entity_ids
                or args.max_depth is not None
                or args.max_nodes is not None
                or args.environments
                or args.correlation_ids
            ):
                raise PanoramaViewIRError("module profile 不接受 focus/environment 参数。")
            view = compile_module_view_ir(model, **common)
        elif args.profile == "dependency_dataflow":
            if args.environments:
                raise PanoramaViewIRError(
                    "dependency_dataflow profile 不接受 environment 参数。"
                )
            if args.correlation_ids:
                raise PanoramaViewIRError(
                    "dependency_dataflow profile 不接受 correlation 参数。"
                )
            view = compile_dependency_dataflow_view_ir(
                model,
                **common,
                root_entity_ids=args.root_entity_ids,
                max_depth=args.max_depth if args.max_depth is not None else 2,
                max_nodes=args.max_nodes if args.max_nodes is not None else 30,
            )
        elif args.profile == "deployment_runtime":
            if (
                args.root_entity_ids
                or args.max_depth is not None
                or args.max_nodes is not None
                or args.correlation_ids
            ):
                raise PanoramaViewIRError(
                    "deployment_runtime profile 不接受 dependency focus 参数。"
                )
            view = compile_deployment_runtime_view_ir(
                model,
                **common,
                environments=args.environments,
            )
        else:
            if (
                args.root_entity_ids
                or args.max_depth is not None
                or args.max_nodes is not None
                or args.environments
            ):
                raise PanoramaViewIRError(
                    f"{args.profile} profile 不接受 dependency/environment 参数。"
                )
            event_compilers = {
                "sequence": compile_sequence_view_ir,
                "lifecycle": compile_lifecycle_view_ir,
                "evolution_risk": compile_evolution_risk_view_ir,
            }
            view = event_compilers[args.profile](
                model, **common, correlation_ids=args.correlation_ids
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
