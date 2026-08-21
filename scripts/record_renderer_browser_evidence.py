"""Seal bounded browser measurements against exact Renderer artifact bytes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from verified_delivery import VerifiedDeliveryError, _read_json, build_browser_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="记录 hash-bound Renderer Browser Evidence。")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("measurement", type=Path, help="包含 viewports/console/limitations 的 JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checked-at", help="受控测试/重放用 RFC 3339 时间")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists():
        print(f"Browser Evidence 已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        if args.artifact.is_symlink() or not args.artifact.is_file():
            raise VerifiedDeliveryError("Renderer Artifact 缺失或是不安全链接。")
        measurement = _read_json(args.measurement)
        expected = {"viewports", "console", "limitations"}
        if set(measurement) != expected:
            raise VerifiedDeliveryError(
                f"Browser Measurement 字段不匹配：{sorted(set(measurement) ^ expected)}"
            )
        evidence = build_browser_evidence(
            args.artifact.read_bytes(),
            measurement["viewports"],
            console=measurement["console"],
            checked_at=args.checked_at,
            limitations=measurement["limitations"],
        )
        atomic_write(
            args.output, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
        )
    except (OSError, VerifiedDeliveryError) as exc:
        print(f"Browser Evidence 记录失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "output": str(args.output),
                "evidenceId": evidence["evidenceId"],
                "evidenceHash": evidence["integrity"]["evidenceHash"],
                "visualReview": evidence["visualReview"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
