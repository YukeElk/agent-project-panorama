"""Materialize and optionally activate an approved V0.5 Approval Policy."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from approval_policy import ApprovalPolicyError, install_policy, materialize_policy
from approval_policy_cli import read_object, write_once
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="物化精确批准的 Approval Policy。")
    parser.add_argument("prepared", type=Path)
    parser.add_argument("approval", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--store", type=Path, default=None, help="同时激活到 Policy Store")
    return parser


def _publish_or_match(path: Path, policy: dict) -> bool:
    if path.exists():
        if read_object(path, "Active Policy") != policy:
            raise ApprovalPolicyError(f"输出已存在且内容冲突：{path}")
        return False
    try:
        write_once(path, policy)
    except ApprovalPolicyError:
        if path.exists() and read_object(path, "Active Policy") == policy:
            return False
        raise
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        policy = materialize_policy(
            read_object(args.prepared, "Prepared Policy"),
            read_object(args.approval, "Policy Approval"),
        )
        # Publish the exact lifecycle artifact first. If activation then fails,
        # an identical retry can safely reuse it without hiding the failure.
        _publish_or_match(args.output, policy)
        if args.store is not None:
            install_policy(args.store, policy)
    except (OSError, ValueError, ApprovalPolicyError) as exc:
        print(f"Approval Policy Materialize 错误：{exc}", file=sys.stderr)
        return 2
    print(f"已物化 Policy：{args.output}")
    if args.store is not None:
        print(f"已激活 Policy Store：{args.store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
