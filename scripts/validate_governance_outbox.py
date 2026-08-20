"""Validate or explicitly recover the governed Apply Event Outbox."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from governance_outbox import (
    GovernanceOutboxError,
    resolve_governance_outbox,
    validate_outbox_store,
)
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="校验或恢复 V0.5 Governance Event Outbox。")
    parser.add_argument("store", type=Path)
    parser.add_argument("--recover", type=Path, default=None, help="要恢复的精确 Outbox JSON")
    parser.add_argument("--panorama", type=Path, default=None)
    parser.add_argument("--event-store", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        recovered = None
        if args.recover is not None:
            if args.panorama is None or args.event_store is None:
                raise GovernanceOutboxError("--recover 必须同时提供 --panorama 与 --event-store。")
            recovered = resolve_governance_outbox(
                args.recover,
                panorama_path=args.panorama,
                event_store_path=args.event_store,
            )
        report = validate_outbox_store(
            args.store, event_store_path=args.event_store
        )
        if recovered is not None:
            report["recovered"] = {
                "transactionId": recovered["transactionId"],
                "status": recovered["status"],
            }
    except (OSError, ValueError, GovernanceOutboxError) as exc:
        print(f"Governance Outbox 错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["valid"]:
        print(f"Governance Outbox Store 有效：{report['outboxCount']} records")
        if report["recoveryRequired"]:
            print("状态：recovery_required")
    else:
        for error in report["errors"]:
            print(f"错误 GOVERNANCE_OUTBOX: {error}")
    return 0 if report["valid"] and not report["recoveryRequired"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
