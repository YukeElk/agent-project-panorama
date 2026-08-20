"""Record one validated Engineering Event request into a project-local store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from event_store import (
    DEFAULT_SCHEMA,
    EventStoreError,
    load_request,
    record_request,
)
from panorama_cli import ChineseArgumentParser


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="记录一条 V0.5 Engineering Event。")
    parser.add_argument("request", type=Path, help="panorama-engineering-event-request.v0.1 JSON")
    parser.add_argument("--store", type=Path, required=True, help="项目本地 event-store/v0.1 目录")
    parser.add_argument("--project-root", type=Path, help="仅用于验证 relative_path 不越界；不读取文件正文")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--recorded-at", help="测试/受控重放用 UTC RFC 3339 时间；默认使用 Recorder Clock")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        request = load_request(args.request)
        event, created = record_request(
            args.store,
            request,
            schema_path=args.schema,
            recorded_at=args.recorded_at,
            project_root=args.project_root,
        )
    except (OSError, ValueError, EventStoreError) as exc:
        print(f"Engineering Event 错误：{exc}", file=sys.stderr)
        return 2
    result = {
        "created": created,
        "eventId": event["eventId"],
        "sequence": event["stream"]["sequence"],
        "eventHash": event["integrity"]["eventHash"],
        "recordedAt": event["recordedAt"],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        action = "已记录" if created else "幂等命中"
        print(f"{action}：{result['eventId']}")
        print(f"Sequence：{result['sequence']}")
        print(f"Event SHA-256：{result['eventHash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
