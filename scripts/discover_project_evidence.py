"""Build a read-only, secret-safe inventory of project evidence candidates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser


EXCLUDED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
    "evals",
}

SOURCE_ROOT_NAMES = {
    "src",
    "app",
    "apps",
    "lib",
    "libs",
    "packages",
    "services",
    "server",
    "client",
}

KNOWLEDGE_BASE_DIRECTORY_NAMES = {
    "kb",
    "knowledge",
    "knowledge-base",
    "knowledge_base",
    "vault",
    "wiki",
}

FORMAT_BY_SUFFIX = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".ini": "ini",
    ".cfg": "config",
    ".conf": "config",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".sh": "shell",
    ".ps1": "powershell",
}

STRUCTURED_FORMATS = {"json", "jsonl", "yaml", "toml", "xml", "ini", "config"}
NARRATIVE_FORMATS = {"markdown", "rst", "text", "html"}
CODE_FORMATS = {
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "csharp",
    "shell",
    "powershell",
}
# Content probing is intentionally limited to bounded HTML prefixes because
# Managed/Legacy marker detection requires it. Other evidence stays metadata-only.
PROBE_FORMATS = {"html"}

MANAGED_MARKERS = ("PANORAMA_DATA_START", "<script", "project-panorama-data")
LEGACY_CUES = (
    "panorama",
    "overview",
    "architecture",
    "dashboard",
    "project-guide",
    "project_guide",
    "status dashboard",
    "project guide",
    "system overview",
)


def is_within_root(path: Path, root: Path) -> bool:
    """Return True only when a resolved path stays inside the resolved root."""

    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _format(path: Path) -> str:
    name = path.name.lower()
    if name in {"dockerfile", "makefile", "procfile"}:
        return name
    if name.startswith(".env"):
        return "env"
    return FORMAT_BY_SUFFIX.get(path.suffix.lower(), "unknown")


def _secret_risk(relative: Path) -> bool:
    name = relative.name.lower()
    parts = {part.lower() for part in relative.parts}
    if name == ".env" or name.startswith(".env."):
        return True
    if relative.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".jks"}:
        return True
    return bool(
        {"secrets", "credentials", ".secrets"} & parts
        or any(token in name for token in ("secret", "credential", "private-key"))
    )


def _knowledge_base_hint(relative: Path) -> bool:
    return bool(
        {part.lower() for part in relative.parts[:-1]}
        & KNOWLEDGE_BASE_DIRECTORY_NAMES
    )


def _read_probe(path: Path, *, limit: int = 131_072) -> str:
    """Read a bounded text prefix. Callers must exclude secret-risk paths first."""

    with path.open("rb") as handle:
        payload = handle.read(limit)
    return payload.decode("utf-8", errors="replace")


def _classify(relative: Path, file_format: str) -> tuple[str, list[str]]:
    lower_path = relative.as_posix().lower()
    name = relative.name.lower()
    stem = relative.stem.lower()
    parts = {part.lower() for part in relative.parts}
    signals: list[str] = []

    if (
        "test" in parts
        or "tests" in parts
        or stem.startswith("test_")
        or stem.endswith("_test")
        or any(token in lower_path for token in ("benchmark", "acceptance", "spec"))
    ):
        signals.append("test-path-or-name")
        return "test", signals

    if (
        stem.startswith("adr")
        or "decision" in stem
        or "decisions" in parts
        or "adr" in parts
        or "accepted-proposal" in lower_path
    ):
        signals.append("decision-record-name")
        return "decision", signals

    if any(token in lower_path for token in ("architecture", "topology", "component-model", "system-design")):
        signals.append("architecture-name")
        return "architecture", signals

    if (
        name in {"dockerfile", "procfile"}
        or any(token in lower_path for token in ("docker-compose", "compose.yaml", "compose.yml", "kubernetes", "k8s", "deployment", "runtime", "service"))
    ):
        signals.append("runtime-or-deployment-name")
        return "runtime", signals

    if any(token in stem for token in ("state", "status", "registry", "snapshot", "manifest", "lock")):
        signals.append("structured-state-name")
        return "state", signals

    if name.startswith("readme") or any(
        token in stem for token in ("overview", "guide", "handbook", "manual", "brief")
    ):
        signals.append("narrative-name")
        return "narrative", signals

    if file_format in STRUCTURED_FORMATS or name in {
        "dockerfile",
        "makefile",
        "procfile",
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
    }:
        signals.append("configuration-or-structured-format")
        return "config", signals

    if file_format in NARRATIVE_FORMATS:
        signals.append("narrative-format")
        return "narrative", signals

    signals.append("unclassified-evidence")
    return "unknown", signals


def _is_candidate(relative: Path, file_format: str) -> bool:
    lower_path = relative.as_posix().lower()
    name = relative.name.lower()
    if _secret_risk(relative):
        return True
    if file_format in STRUCTURED_FORMATS | NARRATIVE_FORMATS:
        return True
    if name in {
        "dockerfile",
        "makefile",
        "procfile",
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
    }:
        return True
    return file_format in CODE_FORMATS and any(
        token in lower_path for token in ("/test", "tests/", "benchmark", "acceptance", "spec")
    )


def _panorama_hints(relative: Path, text: str) -> tuple[bool, bool, str]:
    file_format = _format(relative)
    if file_format not in NARRATIVE_FORMATS:
        return False, False, "none"
    lowered = text.lower() if file_format == "html" else ""
    managed = file_format == "html" and all(
        marker.lower() in lowered for marker in MANAGED_MARKERS
    )
    if managed:
        return True, False, "managed"
    cue_text = f"{relative.as_posix().lower()}\n{lowered[:32_768]}"
    legacy = any(cue in cue_text for cue in LEGACY_CUES)
    return False, legacy, "legacy" if legacy else "none"


def _generated_hint(relative: Path, text: str) -> bool:
    lowered_path = f"/{relative.as_posix().lower()}"
    lowered_content = text[:4_096].lower()
    return (
        "/generated/" in lowered_path
        or relative.name.lower().startswith("generated-")
        or ".generated." in relative.name.lower()
        or any(
            token in lowered_content
            for token in (
                "generated by",
                "auto-generated",
                "autogenerated",
                "do not edit",
            )
        )
    )


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _repository_metadata(root: Path) -> dict[str, Any]:
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--porcelain")
    top_level = []
    source_roots = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if entry.name.lower() in EXCLUDED_DIRECTORIES or not is_within_root(entry, root):
            continue
        entry_type = "directory" if entry.is_dir() else "file"
        top_level.append({"path": entry.name, "type": entry_type})
        if entry.is_dir() and entry.name.lower() in SOURCE_ROOT_NAMES:
            source_roots.append(entry.name)
    return {
        "root": str(root),
        "git": {
            "isRepository": head is not None,
            "head": head,
            "branch": branch,
            "dirty": bool(status) if status is not None else None,
        },
        "topLevelEntries": top_level,
        "sourceRoots": source_roots,
    }


def discover_project_evidence(
    project_root: str | os.PathLike[str], *, max_files: int = 5_000
) -> dict[str, Any]:
    """Return metadata for evidence candidates without modifying the project."""

    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"项目根目录不是目录：{root}")
    if max_files < 1:
        raise ValueError("max_files 必须大于 0。")

    candidates: list[dict[str, Any]] = []
    skipped_outside = 0
    skipped_unreadable = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(dirpath)
        safe_directories = []
        for name in sorted(dirnames):
            child = directory / name
            if name.lower() in EXCLUDED_DIRECTORIES or child.is_symlink():
                continue
            if is_within_root(child, root):
                safe_directories.append(name)
            else:
                skipped_outside += 1
        dirnames[:] = safe_directories

        for name in sorted(filenames):
            path = directory / name
            if path.is_symlink() or not is_within_root(path, root):
                skipped_outside += 1
                continue
            try:
                relative = path.relative_to(root)
                file_format = _format(relative)
                if not _is_candidate(relative, file_format):
                    continue
                stat = path.stat()
            except (OSError, ValueError):
                skipped_unreadable += 1
                continue

            secret_risk = _secret_risk(relative)
            knowledge_base_hint = _knowledge_base_hint(relative)
            probe = ""
            if (
                not secret_risk
                and not knowledge_base_hint
                and file_format in PROBE_FORMATS
            ):
                try:
                    probe = _read_probe(path)
                except OSError:
                    skipped_unreadable += 1

            kind, signals = _classify(relative, file_format)
            managed, legacy, panorama_kind = _panorama_hints(relative, probe)
            candidates.append(
                {
                    "path": relative.as_posix(),
                    "kind": kind,
                    "format": file_format,
                    "modifiedAt": datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "sizeBytes": stat.st_size,
                    "generatedHint": _generated_hint(relative, probe),
                    "managedPanoramaHint": managed,
                    "legacyPanoramaHint": legacy,
                    "panoramaKind": panorama_kind,
                    "secretRisk": secret_risk,
                    "knowledgeBaseHint": knowledge_base_hint,
                    "classificationSignals": signals,
                }
            )
            if len(candidates) >= max_files:
                truncated = True
                break
        if truncated:
            break

    candidates.sort(key=lambda item: (item["kind"], item["path"].lower()))
    return {
        "repository": _repository_metadata(root),
        "candidates": candidates,
        "scan": {
            "candidateCount": len(candidates),
            "maxFiles": max_files,
            "truncated": truncated,
            "skippedOutsideRoot": skipped_outside,
            "skippedUnreadable": skipped_unreadable,
            "contentPolicy": "Secret-risk and knowledge-base files are metadata-only; no values are read.",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="只读发现项目证据候选；不判断哪个来源一定是真相。"
    )
    parser.add_argument("project_root", type=Path, help="待检查的项目根目录")
    parser.add_argument(
        "--max-files",
        type=int,
        default=5_000,
        help="最多返回的候选文件数量（默认 5000）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        result = discover_project_evidence(args.project_root, max_files=args.max_files)
    except (OSError, ValueError) as exc:
        print(f"证据发现错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
