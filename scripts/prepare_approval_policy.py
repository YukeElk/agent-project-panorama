"""Prepare exact Approval Policy semantics for human hash confirmation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from approval_policy import ApprovalPolicyError, prepare_policy
from approval_policy_cli import read_object, write_once
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="准备待精确 Hash 批准的 V0.5 Approval Policy。")
    parser.add_argument("semantics", type=Path, help="不含 approvalBinding 的 Policy 语义")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        prepared = prepare_policy(read_object(args.semantics, "Policy semantics"))
        write_once(args.output, prepared)
    except (OSError, ValueError, ApprovalPolicyError) as exc:
        print(f"Approval Policy Prepare 错误：{exc}", file=sys.stderr)
        return 2
    print(f"已准备 Policy：{args.output}")
    print(f"Policy SHA-256：{prepared['policyHash']}")
    print("批准门禁：请仅确认以上精确 Hash；任何语义变化都必须重新准备。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
