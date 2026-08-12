"""Fast Git Hook dispatcher for Continuous Observation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def dispatch_event(project_root: Path, hook: str, observed_at: str | None = None) -> Path:
    root = project_root.resolve(strict=True)
    work = root / ".panorama-work" / "events"
    work.mkdir(parents=True, exist_ok=True)
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    event = {
        "formatVersion": "panorama-git-event.v0.2",
        "hook": hook,
        "gitHead": head,
        "gitBranch": branch,
        "observedAt": observed_at or datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    token = (head or "NOHEAD")[:16]
    target = work / f"{token}-{hook}.json"
    atomic_write(target, json.dumps(event, ensure_ascii=False, indent=2) + "\n")
    return target


def build_worker_argv(project_root: Path) -> tuple[list[str], Path] | None:
    """Return a validated one-shot Worker argv and log path."""

    root = project_root.resolve(strict=True)
    config_path = root / ".panorama-work" / "continuous-observation.json"
    if not config_path.is_file():
        return None
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("formatVersion") != "continuous-observation-hook.v0.2":
        raise ValueError("Continuous Observation Hook 配置版本不受支持。")
    panorama = (root / config["panorama"]).resolve(strict=False)
    panorama.relative_to(root)
    argv = [
        sys.executable,
        str(Path(__file__).resolve().with_name("continuous_observation.py")),
        str(panorama),
        "--project-root",
        str(root),
    ]
    for supplied in config.get("standards", []):
        standard = (root / supplied).resolve(strict=False)
        standard.relative_to(root)
        argv.extend(["--standard", str(standard)])
    return argv, root / ".panorama-work" / "logs" / "continuous-observation.log"


def wake_worker(project_root: Path) -> bool:
    """Start a detached one-shot reconciler when an installed config exists."""

    root = project_root.resolve(strict=True)
    built = build_worker_argv(root)
    if built is None:
        return False
    argv, log_path = built
    log_dir = log_path.parent
    log_dir.mkdir(parents=True, exist_ok=True)
    flags = 0
    kwargs: dict[str, object] = {"cwd": root, "shell": False}
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    with log_path.open("ab") as log:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="记录 Git 事件供 Panorama 补偿同步消费。")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--hook", choices=["post-commit", "post-merge", "post-checkout", "post-rewrite"], required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        target = dispatch_event(args.project_root, args.hook)
        started = wake_worker(args.project_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Panorama Hook 入队失败（不影响 Git）：{exc}", file=sys.stderr)
        return 0
    print(f"Panorama 观察事件：{target}")
    if started:
        print("Continuous Observation Worker 已在后台启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
