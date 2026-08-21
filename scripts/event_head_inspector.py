"""Read-only Event Head classification for Panorama V0.5.1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from event_store import DEFAULT_SCHEMA, EventStoreError, inspect_head_state
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="只读检查 Engineering Event Store 的 Head 状态。"
    )
    parser.add_argument("store", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = inspect_head_state(args.store, schema_path=args.schema)
    except (OSError, ValueError, EventStoreError) as exc:
        print(f"Event Head Inspector 错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Head 状态：{report['status']}")
        print(f"Event 数量：{report['eventCount']}")
        print(f"可由 Policy 恢复：{'是' if report['recoverable'] else '否'}")
        for error in report["errors"]:
            print(f"说明：{error}")
    return 0 if report["status"] in {"current", "head_missing", "head_behind"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
