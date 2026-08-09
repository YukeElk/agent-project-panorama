"""Perform explicit, read-only local runtime and verification observations."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable

from panorama_cli import ChineseArgumentParser


MAX_MANIFEST_BYTES = 65_536
MAX_PROJECT_FILES = 20_000
SAFE_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_OBSERVATION_NAME = re.compile(r"^[A-Za-z0-9_.:/\\ -]{1,160}$")
BLOCKED_EXECUTABLES = {
    "bash",
    "cmd",
    "curl",
    "docker",
    "npm",
    "pip",
    "pip3",
    "pnpm",
    "powershell",
    "pwsh",
    "sh",
    "wget",
    "yarn",
}
BLOCKED_ARGUMENT_TOKENS = {
    "build",
    "download",
    "install",
    "publish",
    "push",
    "upload",
}


class ObservationError(RuntimeError):
    """An unsafe request or observation failure."""


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int = 10,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        shell=False,
    )


def git_observation(root: Path) -> dict[str, Any]:
    try:
        head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    except (OSError, subprocess.SubprocessError):
        return {
            "isRepository": False,
            "head": None,
            "branch": None,
            "dirty": None,
            "statusEntryCount": None,
            "statusHash": None,
            "provenance": "observed_now",
        }
    if head.returncode != 0:
        return {
            "isRepository": False,
            "head": None,
            "branch": None,
            "dirty": None,
            "statusEntryCount": None,
            "statusHash": None,
            "provenance": "observed_now",
        }
    branch = _run(["git", "branch", "--show-current"], cwd=root)
    status = _run(["git", "status", "--porcelain=v1", "-z"], cwd=root)
    status_text = status.stdout if status.returncode == 0 else ""
    entries = [item for item in status_text.split("\0") if item]
    return {
        "isRepository": True,
        "head": head.stdout.strip(),
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "dirty": bool(entries),
        "statusEntryCount": len(entries),
        "statusHash": hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
        "provenance": "observed_now",
    }


def source_snapshot(root: Path) -> dict[str, Any]:
    git = git_observation(root)
    records, truncated = _project_metadata_records(root)
    canonical = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    metadata_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if git["isRepository"]:
        return {
            "mode": "git",
            "gitHead": git["head"],
            "gitBranch": git["branch"],
            "gitDirty": git["dirty"],
            "gitStatusEntryCount": git["statusEntryCount"],
            "gitStatusHash": git["statusHash"],
            "worktreeFileCount": len(records),
            "worktreeMetadataTruncated": truncated,
            "worktreeMetadataHash": metadata_hash,
        }
    return {
        "mode": "filesystem_metadata",
        "fileCount": len(records),
        "truncated": truncated,
        "metadataHash": metadata_hash,
    }


def _project_metadata_records(root: Path) -> tuple[list[list[Any]], bool]:
    records: list[list[Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in {".git", "__pycache__", ".pytest_cache"}
            and not (Path(dirpath) / name).is_symlink()
        ]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            try:
                stat = path.stat()
                records.append(
                    [path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns]
                )
            except (OSError, ValueError):
                continue
            if len(records) >= MAX_PROJECT_FILES:
                return records, True
    return records, False


def _running_process_names() -> set[str]:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            shell=False,
        )
        if result.returncode != 0:
            return set()
        return {
            row[0].lower()
            for row in csv.reader(result.stdout.splitlines())
            if row
        }
    result = subprocess.run(
        ["ps", "-eo", "comm="],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        shell=False,
    )
    return (
        {Path(line.strip()).name.lower() for line in result.stdout.splitlines() if line.strip()}
        if result.returncode == 0
        else set()
    )


def _scheduler_status(name: str) -> str:
    if not SAFE_OBSERVATION_NAME.fullmatch(name):
        return "error"
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", name, "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                shell=False,
            )
            return "registered" if result.returncode == 0 else "not_detected"
        result = subprocess.run(
            ["systemctl", "is-enabled", name],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            shell=False,
        )
        if result.returncode == 0:
            return "registered"
        return "not_detected" if result.returncode in {1, 3, 4} else "error"
    except (OSError, subprocess.SubprocessError):
        return "error"


def _dependency_status(module_name: str) -> str:
    if not SAFE_MODULE_NAME.fullmatch(module_name):
        return "error"
    try:
        return "available" if importlib.util.find_spec(module_name) is not None else "unavailable"
    except (ImportError, AttributeError, ValueError):
        return "error"


def _safe_environment() -> dict[str, str]:
    allowed = {
        "LANG",
        "LC_ALL",
        "PATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def _validate_test_command(command: dict[str, Any], root: Path) -> tuple[list[str], Path, int]:
    if command.get("safe") is not True:
        raise ObservationError("test command is not explicitly marked safe")
    if command.get("networkAccess") != "none":
        raise ObservationError("test command must declare networkAccess=none")
    if command.get("writesProject") is not False:
        raise ObservationError("test command must declare writesProject=false")
    if command.get("installsDependencies") is not False:
        raise ObservationError("test command must declare installsDependencies=false")
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or len(argv) > 64 or not all(
        isinstance(item, str) and 0 < len(item) <= 1_000 for item in argv
    ):
        raise ObservationError("test argv must be a bounded string array")
    executable = Path(argv[0]).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable in BLOCKED_EXECUTABLES:
        raise ObservationError(f"test executable is not allowed: {executable}")
    lowered_args = [item.strip().lower() for item in argv[1:]]
    if any(
        token in BLOCKED_ARGUMENT_TOKENS or "://" in token
        for token in lowered_args
    ):
        raise ObservationError("test argv contains an install/build/network operation")
    working_directory = root / str(command.get("workingDirectory", "."))
    resolved_cwd = working_directory.resolve(strict=True)
    try:
        resolved_cwd.relative_to(root)
    except ValueError as exc:
        raise ObservationError("test working directory escapes project root") from exc
    timeout = command.get("timeoutSeconds", 60)
    if not isinstance(timeout, int) or timeout < 1 or timeout > 300:
        raise ObservationError("test timeout must be between 1 and 300 seconds")
    return argv, resolved_cwd, timeout


def _load_test_command(root: Path, manifest_path: str, command_id: str) -> dict[str, Any]:
    supplied = Path(manifest_path)
    if supplied.is_absolute():
        raise ObservationError("test manifest must be project-relative")
    path = (root / supplied).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ObservationError("test manifest escapes project root") from exc
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ObservationError("test manifest is missing, linked, or too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    commands = payload.get("commands", []) if isinstance(payload, dict) else []
    for command in commands:
        if isinstance(command, dict) and command.get("id") == command_id:
            return command
    raise ObservationError(f"test command is not declared: {command_id}")


def _execute_safe_test(
    root: Path, manifest_path: str, command_id: str
) -> dict[str, Any]:
    command = _load_test_command(root, manifest_path, command_id)
    argv, cwd, timeout = _validate_test_command(command, root)
    before, before_truncated = _project_metadata_records(root)
    if before_truncated:
        raise ObservationError("project is too large for a no-write verification snapshot")
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=_safe_environment(),
            shell=False,
        )
        status = "passed" if result.returncode == 0 else "failed"
        exit_code: int | None = result.returncode
        stdout_bytes = None
        stderr_bytes = None
    except subprocess.TimeoutExpired:
        status = "timeout"
        exit_code = None
        stdout_bytes = 0
        stderr_bytes = 0
    after, after_truncated = _project_metadata_records(root)
    modified = before != after or after_truncated
    if modified:
        status = "project_modified"
    return {
        "id": command_id,
        "status": status,
        "exitCode": exit_code,
        "durationMs": int((time.monotonic() - started) * 1_000),
        "stdoutBytes": stdout_bytes,
        "stderrBytes": stderr_bytes,
        "projectModified": modified,
        "provenance": "current_test",
        "outputPolicy": "stdout/stderr content is not returned",
    }


def observe_project_runtime(
    project_root: str | os.PathLike[str],
    *,
    dependencies: list[str] | None = None,
    process_names: list[str] | None = None,
    scheduler_names: list[str] | None = None,
    check_paths: list[str] | None = None,
    test_manifest: str | None = None,
    test_id: str | None = None,
    observed_at: str | None = None,
    process_provider: Callable[[], set[str]] = _running_process_names,
    scheduler_provider: Callable[[str], str] = _scheduler_status,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    running = process_provider() if process_names else set()
    processes = []
    for name in process_names or []:
        if not SAFE_OBSERVATION_NAME.fullmatch(name):
            status = "error"
        else:
            status = "running" if name.lower() in running else "not_detected"
        processes.append({"name": name, "status": status, "provenance": "observed_now"})

    files = []
    for supplied in check_paths or []:
        relative = Path(supplied)
        if relative.is_absolute():
            files.append({"path": supplied, "status": "external_private_data"})
            continue
        resolved = (root / relative).resolve(strict=False)
        try:
            resolved.relative_to(root)
            status = "present" if resolved.exists() else "not_detected"
        except ValueError:
            status = "external_private_data"
        files.append({"path": relative.as_posix(), "status": status})

    verification = []
    if bool(test_manifest) != bool(test_id):
        raise ObservationError("test_manifest and test_id must be provided together")
    if test_manifest and test_id:
        verification.append(_execute_safe_test(root, test_manifest, test_id))

    return {
        "projectRoot": str(root),
        "observedAt": observed_at or _now(),
        "git": git_observation(root),
        "dependencies": [
            {
                "name": name,
                "status": _dependency_status(name),
                "provenance": "observed_now",
            }
            for name in dependencies or []
        ],
        "processes": processes,
        "schedulers": [
            {
                "name": name,
                "status": scheduler_provider(name),
                "provenance": "observed_now",
            }
            for name in scheduler_names or []
        ],
        "files": files,
        "verification": verification,
        "safety": {
            "networkUsed": False,
            "dependenciesInstalled": False,
            "processArgumentsRead": False,
            "secretValuesRead": False,
            "testOutputReturned": False,
        },
        "semantics": [
            "not_detected is not absent",
            "available is not running",
            "historical_test is not current_test",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="只读观察当前本地 Runtime / Verification。")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--dependency", action="append", default=[])
    parser.add_argument("--process-name", action="append", default=[])
    parser.add_argument("--scheduler-name", action="append", default=[])
    parser.add_argument("--check-path", action="append", default=[])
    parser.add_argument("--test-manifest")
    parser.add_argument("--test-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = observe_project_runtime(
            args.project_root,
            dependencies=args.dependency,
            process_names=args.process_name,
            scheduler_names=args.scheduler_name,
            check_paths=args.check_path,
            test_manifest=args.test_manifest,
            test_id=args.test_id,
        )
    except (OSError, ValueError, ObservationError, json.JSONDecodeError) as exc:
        print(f"Runtime Observation 错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
