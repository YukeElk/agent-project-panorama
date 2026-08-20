"""Explicitly revoke one exact active Approval Policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from approval_policy import ApprovalPolicyError, revoke_policy
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="按 Policy ID + Hash 显式撤销 Approval Policy。")
    parser.add_argument("store", type=Path)
    parser.add_argument("policy_id")
    parser.add_argument("--policy-hash", required=True)
    parser.add_argument("--revoked-by", required=True)
    parser.add_argument(
        "--reason-class",
        default="user_request",
        choices=["user_request", "scope_change", "producer_compromise", "project_change", "safety_incident", "other"],
    )
    parser.add_argument("--summary", default="Explicit policy revocation.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        revocation, created = revoke_policy(
            args.store,
            args.policy_id,
            policy_hash=args.policy_hash,
            revoked_by=args.revoked_by,
            reason_class=args.reason_class,
            summary=args.summary,
        )
    except (OSError, ValueError, ApprovalPolicyError) as exc:
        print(f"Approval Policy Revoke 错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"created": created, "revocation": revocation}, ensure_ascii=False, indent=2))
    else:
        print(f"Policy 已撤销：{args.policy_id}（{'new' if created else 'idempotent'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
