"""Prepare an immutable V0.6+ multi-view candidate and machine receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from panorama_cli import ChineseArgumentParser
from validate_panorama import ValidationRuntimeError, load_panorama
from verified_delivery import VerifiedDeliveryError, _read_json, prepare_delivery


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="生成不替换 last-good 的 Verified Delivery Candidate。"
    )
    parser.add_argument("model", type=Path)
    parser.add_argument("--view", type=Path, action="append", required=True, dest="views")
    parser.add_argument("--view-set", type=Path, help="可选的已验证 Guided View Set JSON")
    parser.add_argument("--panorama", type=Path, help="Explain Pack 绑定的 Panorama Core")
    parser.add_argument("--explain-pack", type=Path, help="可选的已验证 Explain Pack JSON")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--browser-evidence", type=Path)
    parser.add_argument("--generated-at", help="受控测试/重放用 RFC 3339 时间")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if (args.panorama is None) != (args.explain_pack is None):
            raise VerifiedDeliveryError("--panorama 与 --explain-pack 必须同时提供。")
        receipt, created = prepare_delivery(
            args.project_root,
            _read_json(args.model),
            [_read_json(path) for path in args.views],
            view_set=_read_json(args.view_set) if args.view_set else None,
            panorama=load_panorama(args.panorama)[0] if args.panorama else None,
            explain_pack=_read_json(args.explain_pack) if args.explain_pack else None,
            browser_evidence=(
                _read_json(args.browser_evidence) if args.browser_evidence else None
            ),
            generated_at=args.generated_at,
        )
    except (ValidationRuntimeError, VerifiedDeliveryError) as exc:
        print(f"Verified Delivery Candidate 失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "prepared",
                "created": created,
                "deliveryId": receipt["deliveryId"],
                "artifact": receipt["artifact"],
                "gates": receipt["gates"],
                "receiptHash": receipt["integrity"]["receiptHash"],
                "lastGoodModified": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
