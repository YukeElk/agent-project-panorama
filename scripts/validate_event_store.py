"""Validate or explicitly recover a V0.5 Engineering Event Store head."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from event_store import DEFAULT_SCHEMA, EventStoreError, recover_store_head, validate_store
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="校验 V0.5 Engineering Event Store。")
    parser.add_argument("store", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--recover-head",
        action="store_true",
        help="仅在 Event Chain 完整时确定性重建 stream-head.json",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        recovered = recover_store_head(args.store, schema_path=args.schema) if args.recover_head else None
        report = validate_store(args.store, schema_path=args.schema)
        if recovered is not None:
            report["recoveredHead"] = recovered
    except (OSError, ValueError, EventStoreError) as exc:
        print(f"Event Store 错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["valid"]:
        print(f"Event Store 有效：{report['eventCount']} events")
    else:
        for error in report["errors"]:
            print(f"错误 EVENT_STORE: {error}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
