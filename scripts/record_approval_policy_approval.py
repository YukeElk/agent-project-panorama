"""Record explicit confirmation of one prepared Approval Policy hash."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from approval_policy import ApprovalPolicyError, record_policy_approval
from approval_policy_cli import read_object, write_once
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="记录一次与精确 Policy Hash 绑定的批准。")
    parser.add_argument("prepared", type=Path)
    parser.add_argument("--approved-hash", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        approval = record_policy_approval(
            read_object(args.prepared, "Prepared Policy"),
            confirmed_policy_hash=args.approved_hash,
            approved_by=args.approved_by,
        )
        write_once(args.output, approval)
    except (OSError, ValueError, ApprovalPolicyError) as exc:
        print(f"Approval Policy Approval 错误：{exc}", file=sys.stderr)
        return 2
    print(f"已记录批准：{args.output}")
    print(f"Approved Policy SHA-256：{approval['policyHash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
