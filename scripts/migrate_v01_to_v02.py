"""Create a V0.2 Continuous Observation Panorama without overwriting V0.1."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from panorama_cli import ChineseArgumentParser
from observation_policy import default_policy, policy_hash
from panorama_io import extract_data, replace_data
from validate_panorama import load_panorama, validate_data


ROOT = Path(__file__).resolve().parents[1]


def migrate_data(data: dict[str, Any], *, migrated_at: str) -> dict[str, Any]:
    if data.get("schemaVersion") != "0.1":
        raise ValueError("迁移输入必须使用 Schema 0.1。")
    migrated = copy.deepcopy(data)
    migrated["schemaVersion"] = "0.2"
    migrated["observationPolicy"] = default_policy(migrated_at)
    migrated["sourceBinding"] = {
        "mode": "git",
        "gitHead": None,
        "gitBranch": None,
        "sourceSnapshotHash": "0" * 64,
        "observedAt": None,
        "lastObservationBatchId": None,
        "extensions": {"migrationRequiresInitialSync": True},
    }
    migrated["factProvenance"] = []
    migrated["currentArchitectureSnapshots"] = []
    migrated["standardAssessments"] = []
    migrated["observationBatches"] = []
    migrated.setdefault("extensions", {})["schemaMigration"] = {
        "from": "0.1",
        "to": "0.2",
        "migratedAt": migrated_at,
        "governedSemanticsPreserved": True,
    }
    return migrated


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="将 Panorama Schema 0.1 迁移为 V0.2 新文件。")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template", type=Path, default=ROOT / "templates" / "panorama.html")
    parser.add_argument("--migrated-at", help="测试或可重现迁移使用的 ISO 时间")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.resolve(strict=False) == args.input.resolve(strict=False):
        print("迁移错误：V0.2 必须输出新文件，不得覆盖 V0.1。", file=sys.stderr)
        return 1
    try:
        data, base_dir = load_panorama(args.input)
        before = validate_data(data, base_dir=base_dir, source_path=args.input)
        if before.errors:
            raise ValueError("V0.1 输入校验失败，拒绝迁移。")
        migrated = migrate_data(
            data,
            migrated_at=args.migrated_at or datetime.now(timezone.utc).isoformat(),
        )
        after = validate_data(migrated, base_dir=args.output.parent, source_path=args.output)
        if after.errors:
            raise ValueError("V0.2 输出校验失败：\n" + "\n".join(i.render() for i in after.errors))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.suffix.lower() in {".html", ".htm"}:
            replace_data(args.template, migrated, args.output)
            if extract_data(args.output) != migrated:
                raise ValueError("迁移 HTML 无法精确往返。")
        else:
            args.output.write_text(
                json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    except (OSError, ValueError) as exc:
        print(f"迁移错误：{exc}", file=sys.stderr)
        return 1
    print(f"V0.2 已生成：{args.output}")
    print(f"Observation Policy Hash：{migrated['observationPolicy']['policyHash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
