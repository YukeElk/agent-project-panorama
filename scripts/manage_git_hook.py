"""Install and remove non-destructive Continuous Observation Git hooks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write


HOOKS = ("post-commit", "post-merge", "post-checkout", "post-rewrite")
MARKER = "# AGENT_PROJECT_PANORAMA_V02_MANAGED_HOOK"


class HookError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=5
    )
    if result.returncode != 0:
        raise HookError(result.stderr.strip() or "Git 命令失败。")
    return result.stdout.strip()


def hook_directory(root: Path) -> Path:
    configured_result = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
    )
    configured = configured_result.stdout.strip() if configured_result.returncode == 0 else ""
    if configured:
        supplied = Path(configured)
        return supplied if supplied.is_absolute() else (root / supplied).resolve()
    common = Path(_git(root, "rev-parse", "--git-common-dir"))
    common = common if common.is_absolute() else (root / common).resolve()
    return common / "hooks"


def _script(dispatcher: Path, root: Path, hook: str) -> str:
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        f"exec \"{sys.executable.replace(os.sep, '/')}\" \"{dispatcher.as_posix()}\" "
        f"--project-root \"{root.as_posix()}\" --hook {hook}\n"
    )


def _relative_to_root(root: Path, supplied: Path) -> str:
    resolved = supplied.resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise HookError(f"Continuous Observation 制品必须位于项目根内：{supplied}") from exc


def install(root: Path, panorama: Path, standards: list[Path] | None = None) -> list[Path]:
    root = root.resolve(strict=True)
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        raise HookError("目标不是 Git Worktree。")
    directory = hook_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    dispatcher = Path(__file__).resolve().with_name("panorama_hook_dispatch.py")
    panorama_relative = _relative_to_root(root, panorama)
    if not (root / panorama_relative).is_file():
        raise HookError(f"Panorama 不存在：{panorama_relative}")
    standard_relatives = [_relative_to_root(root, path) for path in standards or []]
    for relative in standard_relatives:
        if not (root / relative).is_file():
            raise HookError(f"Standard Pack 不存在：{relative}")
    config = {
        "formatVersion": "continuous-observation-hook.v0.2",
        "panorama": panorama_relative,
        "standards": standard_relatives,
    }
    config_path = root / ".panorama-work" / "continuous-observation.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    targets = []
    for hook in HOOKS:
        target = directory / hook
        if target.exists():
            text = target.read_text(encoding="utf-8", errors="replace")
            if MARKER not in text:
                raise HookError(f"已有非 Panorama Hook，拒绝覆盖：{target}")
        atomic_write(target, _script(dispatcher, root, hook))
        try:
            target.chmod(target.stat().st_mode | 0o111)
        except OSError:
            pass
        targets.append(target)
    return targets


def uninstall(root: Path) -> list[Path]:
    root = root.resolve(strict=True)
    directory = hook_directory(root)
    removed = []
    for hook in HOOKS:
        target = directory / hook
        if target.exists() and MARKER in target.read_text(encoding="utf-8", errors="replace"):
            target.unlink()
            removed.append(target)
    (root / ".panorama-work" / "continuous-observation.json").unlink(missing_ok=True)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="安装或卸载 Panorama Continuous Observation Git Hooks。")
    parser.add_argument("action", choices=["install", "uninstall", "status"])
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--panorama", type=Path, help="install 时必填，项目根内的 V0.2 Panorama")
    parser.add_argument("--standard", type=Path, action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "install":
            if args.panorama is None:
                raise HookError("install 必须提供 --panorama。")
            paths = install(args.project_root, args.panorama, args.standard)
            for path in paths:
                print(f"已安装：{path}")
        elif args.action == "uninstall":
            paths = uninstall(args.project_root)
            for path in paths:
                print(f"已卸载：{path}")
        else:
            directory = hook_directory(args.project_root.resolve(strict=True))
            for hook in HOOKS:
                target = directory / hook
                managed = target.exists() and MARKER in target.read_text(encoding="utf-8", errors="replace")
                print(f"{hook}: {'managed' if managed else ('external' if target.exists() else 'missing')}")
    except (OSError, subprocess.SubprocessError, HookError) as exc:
        print(f"Git Hook 错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
