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

VERIFICATION_DIRECTORY_NAMES = {
    "test",
    "tests",
    "eval",
    "evals",
    "evaluation",
    "evaluations",
    "benchmark",
    "benchmarks",
    "fixture",
    "fixtures",
}

MACHINE_STATE_STEMS = {
    "dashboard",
    "status",
    "status-report",
    "health-report",
    "build-status",
    "release-status",
    "runtime-status",
    "system-status",
}

OPERATIONAL_PATH_SIGNALS = {
    "system",
    "_system",
    "90-system",
    "state",
    "_state",
    "status",
    "registry",
    "registries",
    "audit",
    "logs",
    "reports",
    "runtime",
    "health",
    "metadata",
    "generated",
    "tasks",
    "approvals",
    "retrieval",
}

EXPLICIT_KNOWLEDGE_OPERATIONAL_BOUNDARIES = {
    "90-system",
    "_state",
    "_system",
    "system",
}

KNOWLEDGE_OPERATIONAL_SEMANTIC_SIGNALS = (
    OPERATIONAL_PATH_SIGNALS - EXPLICIT_KNOWLEDGE_OPERATIONAL_BOUNDARIES
)

OPERATIONAL_STRUCTURED_FORMATS = {"json", "jsonl", "yaml", "toml"}
# Content probing is intentionally limited to bounded HTML prefixes because
# Managed/Legacy marker detection requires it. Other evidence stays metadata-only.
PROBE_FORMATS = {"html"}

MANAGED_MARKERS = ("PANORAMA_DATA_START", "<script", "project-panorama-data")
LEGACY_CUES = (
    "panorama",
    "overview",
    "architecture",
    "project-guide",
    "project_guide",
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


def _knowledge_operational_parts(relative: Path) -> tuple[str, ...]:
    """Return path parts after the last knowledge root, excluding the file name."""

    directories = tuple(part.lower() for part in relative.parts[:-1])
    knowledge_indexes = [
        index
        for index, part in enumerate(directories)
        if part in KNOWLEDGE_BASE_DIRECTORY_NAMES
    ]
    if not knowledge_indexes:
        return ()
    return directories[knowledge_indexes[-1] + 1 :]


def _knowledge_operational_boundary(relative: Path) -> bool:
    return bool(
        set(_knowledge_operational_parts(relative))
        & EXPLICIT_KNOWLEDGE_OPERATIONAL_BOUNDARIES
    )


def _knowledge_operational_semantic(relative: Path) -> bool:
    parts = set(_knowledge_operational_parts(relative))
    normalized_stem = relative.stem.lower().replace("_", "-")
    return bool(
        parts & KNOWLEDGE_OPERATIONAL_SEMANTIC_SIGNALS
        or normalized_stem in MACHINE_STATE_STEMS
        or normalized_stem in {"registry", "audit", "metadata"}
    )


def _path_at_or_below(path: Path, boundary: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(boundary.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _executing_skill_root() -> Path:
    """Resolve the installed Skill root from this production script."""

    return Path(__file__).resolve(strict=True).parents[1]


def _verification_path_or_name(relative: Path) -> bool:
    directory_parts = {part.lower() for part in relative.parts[:-1]}
    stem = relative.stem.lower()
    name = relative.name.lower()
    return bool(
        directory_parts & VERIFICATION_DIRECTORY_NAMES
        or stem.startswith(("test_", "eval_", "benchmark_"))
        or stem.endswith(("_test", "_eval", "_benchmark"))
        or ".test." in name
        or ".spec." in name
    )


def _machine_state_hint(relative: Path) -> bool:
    normalized_stem = relative.stem.lower().replace("_", "-")
    return normalized_stem in MACHINE_STATE_STEMS


def _operational_metadata_hint(relative: Path, file_format: str) -> bool:
    """Require both a semantic/path signal and a safely inspectable format."""

    raw_parts = {part.lower() for part in relative.parts[:-1]}
    normalized_parts = {part.replace("_", "-") for part in raw_parts}
    parts = raw_parts | normalized_parts
    path_signal = bool(parts & OPERATIONAL_PATH_SIGNALS)
    machine_signal = _machine_state_hint(relative)
    if _knowledge_base_hint(relative):
        qualified_knowledge_signal = (
            _knowledge_operational_boundary(relative)
            and _knowledge_operational_semantic(relative)
        )
        if file_format in OPERATIONAL_STRUCTURED_FORMATS:
            return qualified_knowledge_signal
        if file_format == "markdown":
            return qualified_knowledge_signal and machine_signal
        return False
    if file_format in OPERATIONAL_STRUCTURED_FORMATS:
        return path_signal or machine_signal
    if file_format == "markdown":
        return path_signal and machine_signal
    return False


def evidence_access_class(relative: Path, file_format: str) -> str:
    """Classify discovery access without reading file contents."""

    if _secret_risk(relative):
        return "SECRET"
    if _knowledge_base_hint(relative):
        if _operational_metadata_hint(relative, file_format):
            return "PROJECT_OPERATIONAL_METADATA"
        return "PROJECT_CONTENT"
    if _operational_metadata_hint(relative, file_format):
        return "PROJECT_OPERATIONAL_METADATA"
    return "PUBLIC_PROJECT_EVIDENCE"


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

    if _verification_path_or_name(relative) or any(
        token in lower_path for token in ("acceptance", "spec")
    ):
        signals.append("verification-path-or-name")
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

    if _machine_state_hint(relative):
        signals.append("status-report-name")
        return "state", signals

    if any(token in lower_path for token in ("architecture", "topology", "component-model", "system-design")):
        signals.append("architecture-name")
        return "architecture", signals

    if (
        name in {"dockerfile", "procfile"}
        or any(token in lower_path for token in ("docker-compose", "compose.yaml", "compose.yml", "kubernetes", "k8s", "deployment", "runtime", "service"))
    ):
        signals.append("runtime-or-deployment-name")
        return "runtime", signals

    if file_format in STRUCTURED_FORMATS and any(
        token in stem
        for token in ("state", "status", "registry", "snapshot", "manifest", "lock")
    ):
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
    return file_format in CODE_FORMATS and (
        _verification_path_or_name(relative)
        or any(token in lower_path for token in ("acceptance", "spec"))
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


def _implementation_scope_paths(relative: Path) -> list[Path]:
    if len(relative.parts) == 1:
        return [Path(".")]
    scopes = [Path(relative.parts[0])]
    if len(relative.parts) >= 3:
        scopes.append(Path(relative.parts[0]) / relative.parts[1])
    return scopes


def _record_implementation_metadata(
    scopes: dict[str, dict[str, Any]], relative: Path, file_format: str
) -> None:
    if _verification_path_or_name(relative):
        return
    is_code = file_format in CODE_FORMATS
    for scope in _implementation_scope_paths(relative):
        key = scope.as_posix()
        item = scopes.setdefault(
            key,
            {
                "path": key,
                "totalFileCount": 0,
                "codePaths": set(),
                "languages": set(),
                "directCodeFileCount": 0,
            },
        )
        item["totalFileCount"] += 1
        if not is_code:
            continue
        item["codePaths"].add(relative.as_posix())
        item["languages"].add(file_format)
        if len(relative.parts) == len(scope.parts) + 1 or key == ".":
            item["directCodeFileCount"] += 1


def _qualified_implementation_root(item: dict[str, Any]) -> bool:
    code_count = len(item["codePaths"])
    total_count = item["totalFileCount"]
    ratio = code_count / total_count if total_count else 0.0
    path = item["path"]
    if path == ".":
        return code_count >= 2
    if len(Path(path).parts) == 1 and path.lower() in SOURCE_ROOT_NAMES:
        return code_count >= 1
    return code_count >= 2 and ratio >= 0.5


def _implementation_roots(scopes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    qualified = {
        path: item
        for path, item in scopes.items()
        if _qualified_implementation_root(item)
    }
    selected: list[dict[str, Any]] = []
    top_paths = sorted(
        (
            path
            for path in scopes
            if path == "." or len(Path(path).parts) == 1
        ),
        key=str.lower,
    )
    for top_path in top_paths:
        top_item = scopes[top_path]
        children = sorted(
            (
                item
                for path, item in qualified.items()
                if len(Path(path).parts) == 2
                and Path(path).parts[0] == top_path
            ),
            key=lambda item: item["path"].lower(),
        )
        if top_path not in qualified:
            selected.extend(children)
        elif len(children) >= 2 and top_item["directCodeFileCount"] == 0:
            selected.extend(children)
        else:
            selected.append(top_item)

    roots: list[dict[str, Any]] = []
    for item in selected[:25]:
        path = item["path"]
        code_paths = sorted(item["codePaths"], key=str.lower)
        code_count = len(code_paths)
        total_count = item["totalFileCount"]
        signals = []
        if path == ".":
            signals.append("root-level-code")
        elif len(Path(path).parts) == 1 and path.lower() in SOURCE_ROOT_NAMES:
            signals.append("conventional-source-root")
        if code_count >= 2 and code_count / total_count >= 0.5:
            signals.append("code-density")
        if len(Path(path).parts) == 2:
            signals.append("second-level-scope")
        roots.append(
            {
                "path": path,
                "codeFileCount": code_count,
                "languages": sorted(item["languages"]),
                "samplePaths": code_paths[:5],
                "signals": signals,
            }
        )
    return roots


def _repository_metadata(
    root: Path,
    *,
    implementation_roots: list[dict[str, Any]],
    excluded_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--porcelain")
    top_level = []
    source_roots = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if (
            entry.name.lower() in EXCLUDED_DIRECTORIES
            or not is_within_root(entry, root)
            or any(_path_at_or_below(entry, boundary) for boundary in excluded_paths)
        ):
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
        "implementationRoots": implementation_roots,
    }


def discover_project_evidence(
    project_root: str | os.PathLike[str],
    *,
    max_files: int = 5_000,
    self_skill_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return metadata for evidence candidates without modifying the project."""

    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"项目根目录不是目录：{root}")
    if max_files < 1:
        raise ValueError("max_files 必须大于 0。")

    skill_root = (
        Path(self_skill_root).resolve(strict=False)
        if self_skill_root
        else _executing_skill_root()
    )
    self_oracle = (skill_root / "evals").resolve(strict=False)
    excluded_self_oracles: tuple[Path, ...] = ()
    if self_oracle.is_dir() and _path_at_or_below(self_oracle, root):
        excluded_self_oracles = (self_oracle,)

    candidates: list[dict[str, Any]] = []
    implementation_scopes: dict[str, dict[str, Any]] = {}
    skipped_outside = 0
    skipped_unreadable = 0
    truncated = False

    walk_entries = () if any(_path_at_or_below(root, item) for item in excluded_self_oracles) else os.walk(
        root, topdown=True, followlinks=False
    )
    for dirpath, dirnames, filenames in walk_entries:
        directory = Path(dirpath)
        safe_directories = []
        for name in sorted(dirnames):
            child = directory / name
            if (
                name.lower() in EXCLUDED_DIRECTORIES
                or child.is_symlink()
                or any(_path_at_or_below(child, item) for item in excluded_self_oracles)
            ):
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
                _record_implementation_metadata(
                    implementation_scopes, relative, file_format
                )
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
            machine_state_hint = _machine_state_hint(relative)
            access_class = evidence_access_class(relative, file_format)
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
                    "machineStateHint": machine_state_hint,
                    "operationalMetadataHint": (
                        access_class == "PROJECT_OPERATIONAL_METADATA"
                    ),
                    "accessClass": access_class,
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
    implementation_roots = _implementation_roots(implementation_scopes)
    excluded_relative_paths = [
        item.relative_to(root).as_posix() for item in excluded_self_oracles
    ]
    return {
        "repository": _repository_metadata(
            root,
            implementation_roots=implementation_roots,
            excluded_paths=excluded_self_oracles,
        ),
        "candidates": candidates,
        "scan": {
            "candidateCount": len(candidates),
            "maxFiles": max_files,
            "truncated": truncated,
            "skippedOutsideRoot": skipped_outside,
            "skippedUnreadable": skipped_unreadable,
            "excludedSelfOraclePaths": excluded_relative_paths,
            "contentPolicy": (
                "Secret-risk and knowledge-base project-content files are metadata-only; "
                "qualified operational candidates require the bounded inspector before values are read."
            ),
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
