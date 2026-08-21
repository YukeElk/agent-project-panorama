"""Extract a bounded Source Topology Observation and its receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from source_topology import SourceTopologyError, extract_source_topology


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="只读提取源码拓扑候选、Extraction Receipt 与 Loss Report。"
    )
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--observed-at",
        required=True,
        help="RFC 3339 时间；作为显式 as-of 输入以保持结果可重算",
    )
    parser.add_argument("--observation-output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument("--loss-output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    outputs = [args.observation_output, args.receipt_output, args.loss_output]
    existing = [str(path) for path in outputs if path.exists()]
    if existing and not args.overwrite:
        print("源码抽取输出已存在：" + ", ".join(existing), file=sys.stderr)
        return 2
    try:
        bundle = extract_source_topology(
            args.project_root,
            project_id=args.project_id,
            observed_at=args.observed_at,
        )
        for path, key in zip(outputs, ("observation", "receipt", "lossReport")):
            atomic_write(
                path,
                json.dumps(bundle[key], ensure_ascii=False, indent=2) + "\n",
            )
    except (OSError, ValueError, SourceTopologyError) as exc:
        print(f"源码拓扑抽取失败：{exc}", file=sys.stderr)
        return 2
    observation = bundle["observation"]
    receipt = bundle["receipt"]
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "observationId": observation["observationId"],
                "observationHash": observation["integrity"]["semanticHash"],
                "receiptId": receipt["receiptId"],
                "files": receipt["counts"]["filesRead"],
                "elements": receipt["counts"]["elements"],
                "relations": receipt["counts"]["relations"],
                "unresolvedRelations": receipt["counts"]["unresolvedRelations"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
