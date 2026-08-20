"""Validate or deterministically recover a V0.5 Approval Policy Store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from approval_policy import ApprovalPolicyError, recover_pending, validate_policy_store
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="校验 V0.5 Approval Policy Store。")
    parser.add_argument("store", type=Path)
    parser.add_argument("--recover-policy", default=None, help="仅从已存在的 durable receipt 恢复指定 pending ledger")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        recovered = recover_pending(args.store, args.recover_policy) if args.recover_policy else None
        report = validate_policy_store(args.store)
        if recovered is not None:
            report["recovery"] = recovered
    except (OSError, ValueError, ApprovalPolicyError) as exc:
        print(f"Approval Policy Store 错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["valid"] and report["recoveryRequired"]:
        print(
            "Approval Policy Store 结构有效但不可继续："
            f"{report['policyCount']} policies，{report['receiptCount']} receipts，"
            "存在 pending/recovery-required"
        )
    elif report["valid"]:
        print(f"Approval Policy Store 有效：{report['policyCount']} policies，{report['receiptCount']} receipts")
    else:
        for error in report["errors"]:
            print(f"错误 APPROVAL_POLICY_STORE: {error}")
    return 0 if report["valid"] and not report["recoveryRequired"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
