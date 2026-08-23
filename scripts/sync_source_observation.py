"""Synchronize the latest derived multistack source observation sidecars."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from source_topology import (
    SourceTopologyError,
    extract_source_topology,
    validate_source_observation,
)


class SourceSyncError(RuntimeError):
    """The latest sidecar cannot be safely reconciled."""


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _load_latest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise SourceSyncError("latest Source Observation 必须是普通文件。")
    value = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_source_observation(value)
    if errors:
        raise SourceSyncError("latest Source Observation 无效：" + "; ".join(errors[:10]))
    return value


def sync_source_observation(
    project_root: Path,
    *,
    project_id: str,
    observed_at: str,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    target = Path(output_dir) if output_dir is not None else root / ".panorama-work" / "source"
    if not target.is_absolute():
        target = root / target
    target = target.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SourceSyncError("Source Sidecar 输出目录必须位于 project root 内。") from exc
    target.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise SourceSyncError("Source Sidecar 输出目录不能是 symlink。")

    lock = target / ".source-observation.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SourceSyncError("Source Observation 同步正在运行。") from exc
    try:
        os.close(descriptor)
        latest_path = target / "latest.observation.json"
        previous = _load_latest(latest_path)
        bundle = extract_source_topology(
            root, project_id=project_id, observed_at=observed_at
        )
        observation = bundle["observation"]
        same_source = bool(
            previous
            and previous["projectBinding"] == observation["projectBinding"]
            and previous["sourceBinding"] == observation["sourceBinding"]
            and previous["extensions"]["adapterRegistry"]
            == observation["extensions"]["adapterRegistry"]
        )
        if same_source:
            return {
                "status": "match",
                "changed": False,
                "previousObservationId": previous["observationId"],
                "observationId": previous["observationId"],
                "observationHash": previous["integrity"]["semanticHash"],
                "sourceBinding": previous["sourceBinding"],
                "latestPath": str(latest_path),
            }

        identity = (
            f"{observation['observationId']}."
            f"{observation['integrity']['semanticHash'][:16]}"
        )
        immutable_observation = target / f"{identity}.observation.json"
        immutable_receipt = target / f"{identity}.receipt.json"
        immutable_loss = target / f"{identity}.loss.json"
        for path, value in (
            (immutable_observation, observation),
            (immutable_receipt, bundle["receipt"]),
            (immutable_loss, bundle["lossReport"]),
        ):
            if path.exists():
                if path.read_text(encoding="utf-8") != _json_text(value):
                    raise SourceSyncError(f"Immutable Source Sidecar 已存在但内容不同：{path.name}")
            else:
                atomic_write(path, _json_text(value))
        atomic_write(target / "latest.receipt.json", _json_text(bundle["receipt"]))
        atomic_write(target / "latest.loss.json", _json_text(bundle["lossReport"]))
        atomic_write(latest_path, _json_text(observation))
        return {
            "status": "initialized" if previous is None else "changed",
            "changed": True,
            "previousObservationId": previous.get("observationId") if previous else None,
            "observationId": observation["observationId"],
            "observationHash": observation["integrity"]["semanticHash"],
            "sourceBinding": observation["sourceBinding"],
            "latestPath": str(latest_path),
            "immutablePaths": [
                str(immutable_observation), str(immutable_receipt), str(immutable_loss)
            ],
        }
    finally:
        lock.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="同步最新多语言 Source Observation Sidecar。")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--observed-at", help="可重算使用的 ISO 时间")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = sync_source_observation(
            args.project_root,
            project_id=args.project_id,
            observed_at=args.observed_at or datetime.now(timezone.utc).isoformat(),
            output_dir=args.output_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError, SourceTopologyError, SourceSyncError) as exc:
        print(f"Source Observation 同步错误：{exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Source Observation：{result['status']} / {result['observationId']}")
        print(f"Latest：{result['latestPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
