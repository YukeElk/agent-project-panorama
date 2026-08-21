"""Compile a pending source-element to module mapping proposal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from source_module_mapping import SourceModuleMappingError, compile_mapping_proposal


def main(argv: list[str] | None = None) -> int:
    parser = ChineseArgumentParser(description="生成只读、待评审的 Source Element → Module 候选映射。")
    parser.add_argument("source_observation", type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"输出已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        observation = json.loads(args.source_observation.read_text(encoding="utf-8"))
        proposal = compile_mapping_proposal(observation, generated_at=args.generated_at)
        atomic_write(args.output, json.dumps(proposal, ensure_ascii=False, indent=2) + "\n")
    except (OSError, json.JSONDecodeError, SourceModuleMappingError, ValueError) as exc:
        print(f"模块映射提案生成失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": proposal["status"],
        "proposalId": proposal["proposalId"],
        "mappingProposalHash": proposal["integrity"]["mappingProposalHash"],
        "candidateCount": len(proposal["candidates"]),
        "unmappedSourceElementCount": len(proposal["unmappedSourceElementIds"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
