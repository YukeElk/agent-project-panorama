"""Preview or execute one policy-governed Verified Delivery promotion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import approval_policy
from panorama_cli import ChineseArgumentParser
from verified_delivery_policy_adapter import (
    VerifiedDeliveryPolicyAdapterError,
    execute,
    preview,
)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="按 Approval Policy 晋升精确 Verified Delivery Candidate。")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("delivery_id")
    parser.add_argument("policy_id")
    parser.add_argument("--preview", action="store_true", help="只读预检，不分配 Policy Use")
    parser.add_argument("--at", help="受控测试/重放用 RFC 3339 时间")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = (
            preview(args.project_root, args.delivery_id, args.policy_id, previewed_at=args.at)
            if args.preview
            else execute(args.project_root, args.delivery_id, args.policy_id, executed_at=args.at)
        )
    except (VerifiedDeliveryPolicyAdapterError, approval_policy.ApprovalPolicyError, OSError) as exc:
        print(f"Verified Delivery Promotion 失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
