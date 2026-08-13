"""Bounded, read-only Agent adapter for Architecture Studio.

The adapter copies only a deliberately reduced review bundle into a fresh
system-temporary directory.  The Codex read-only sandbox prevents writes, but
this adapter does not claim OS-enforced filesystem, network, or process-tree
isolation.  Agent output is advisory only: it cannot approve or apply a
proposal.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from observe_project_runtime import git_observation, source_snapshot
from panorama_io import atomic_write, compute_canonical_hash


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SCHEMA = ROOT / "schema" / "architecture-review-output.schema.v0.1.json"
REVIEW_ENVELOPE_FORMAT = "panorama-architecture-agent-review-envelope.v0.1"
MAX_BUNDLE_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 600


class StudioAgentError(RuntimeError):
    """The optional Agent adapter could not produce a trustworthy result."""


def _candidate_executables(
    explicit: str | os.PathLike[str] | None,
    *,
    allow_discovery: bool,
) -> list[Path]:
    candidates: list[Path] = []
    # An explicit executable is a security/configuration boundary, not a hint.
    # Never hide a bad operator-supplied path by falling back to an environment
    # variable, PATH, or a home-directory convention.
    if explicit is not None:
        candidates.append(Path(explicit))
    elif allow_discovery:
        configured = os.environ.get("PANORAMA_CODEX_CLI")
        if configured:
            candidates.append(Path(configured))
        discovered = shutil.which("codex")
        if discovered:
            candidates.append(Path(discovered))
        if os.name == "nt":
            candidates.append(
                Path.home()
                / ".codex"
                / "plugins"
                / ".plugin-appserver"
                / "codex.exe"
            )
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = os.path.normcase(str(item.resolve(strict=False)))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def locate_codex_cli(
    explicit: str | os.PathLike[str] | None = None,
    *,
    allow_discovery: bool = True,
) -> Path | None:
    """Return a Codex CLI that answers a bounded probe.

    If ``explicit`` is not ``None``, only that exact path is considered. The
    discovery switch applies only when no explicit executable was supplied.
    """

    for candidate in _candidate_executables(
        explicit, allow_discovery=allow_discovery
    ):
        if not candidate.is_file():
            continue
        try:
            probe = subprocess.run(
                [str(candidate), "--version"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate.resolve()
    return None


def codex_capability(
    explicit: str | os.PathLike[str] | None = None,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    executable = locate_codex_cli(explicit, allow_discovery=not strict)
    if executable is None:
        return {"available": False, "authenticated": False, "version": None}
    try:
        login = subprocess.run(
            [str(executable), "login", "status"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=12, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": True, "authenticated": False, "version": None}
    return {
        "available": True,
        "authenticated": login.returncode == 0,
        # Probe output is intentionally discarded rather than accumulated in
        # memory. The Bridge only needs availability/authentication status.
        "version": None,
    }


def _safe_panorama_projection(data: dict[str, Any]) -> dict[str, Any]:
    """Exclude credential/resource bodies while retaining architecture evidence."""

    architecture = data.get("architecture", {})
    return {
        "schemaVersion": data.get("schemaVersion"),
        "meta": {
            "revision": data.get("meta", {}).get("revision"),
            "templateVersion": data.get("meta", {}).get("templateVersion"),
            "updatedAt": data.get("meta", {}).get("updatedAt"),
        },
        "project": copy.deepcopy(data.get("project", {})),
        "intent": copy.deepcopy(data.get("intent", {})),
        "stages": copy.deepcopy(data.get("stages", [])),
        "requirements": copy.deepcopy(data.get("requirements", [])),
        "architecture": {
            "layers": copy.deepcopy(architecture.get("layers", [])),
            "modules": copy.deepcopy(architecture.get("modules", [])),
            "connections": copy.deepcopy(architecture.get("connections", [])),
            "transitions": copy.deepcopy(architecture.get("transitions", [])),
            "versions": copy.deepcopy(architecture.get("versions", [])),
        },
        "decisions": copy.deepcopy(data.get("decisions", [])),
        "risks": copy.deepcopy(data.get("risks", [])),
        "acceptanceCriteria": copy.deepcopy(data.get("acceptanceCriteria", [])),
        "gates": copy.deepcopy(data.get("gates", [])),
        "sourceBinding": copy.deepcopy(data.get("sourceBinding")),
    }


def build_review_bundle(
    panorama_data: dict[str, Any],
    session: dict[str, Any],
    candidate: dict[str, Any],
    formal_validation: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    project_observation: dict[str, Any] | None = None
    if project_root is not None:
        root = project_root.resolve(strict=True)
        snapshot = source_snapshot(root)
        git = git_observation(root)
        # Only repository state metadata crosses the review boundary. No
        # tracked/untracked paths, diffs, commit messages, process output, or
        # source file contents are included.
        project_observation = {
            "git": {
                "isRepository": git.get("isRepository"),
                "head": git.get("head"),
                "branch": git.get("branch"),
                "dirty": git.get("dirty"),
                "statusEntryCount": git.get("statusEntryCount"),
                "statusHash": git.get("statusHash"),
                "provenance": git.get("provenance"),
            },
            "sourceSnapshotHash": compute_canonical_hash(snapshot),
            "sourceSnapshotSummary": {
                "mode": snapshot.get("mode"),
                "gitDirty": snapshot.get("gitDirty"),
                "gitStatusEntryCount": snapshot.get("gitStatusEntryCount"),
                "worktreeFileCount": snapshot.get("worktreeFileCount"),
                "worktreeMetadataTruncated": snapshot.get("worktreeMetadataTruncated"),
                "fileCount": snapshot.get("fileCount"),
                "truncated": snapshot.get("truncated"),
            },
        }
    bundle = {
        "format": "panorama-architecture-review-input.v0.1",
        "formalPanorama": _safe_panorama_projection(panorama_data),
        "sessionBinding": copy.deepcopy(session.get("projectBinding", {})),
        "scope": copy.deepcopy(session.get("scope", {})),
        "candidate": copy.deepcopy(candidate),
        "candidateSemanticHash": compute_canonical_hash(
            {
                key: value
                for key, value in candidate.items()
                if key not in {"layout", "layoutOperations", "browserValidation"}
            }
        ),
        "formalValidation": copy.deepcopy(formal_validation),
        "projectObservation": project_observation,
        "privacyBoundary": {
            "projectContentIncluded": False,
            "sourceFileContentsIncluded": False,
            "credentialValuesIncluded": False,
            "externalStoresRead": False,
            "inputBundleBoundary": "bounded_panorama_and_repository_metadata_only",
            "hostIsolation": {
                "codexSandbox": "read_only",
                "filesystemIsolation": "not_enforced",
                "networkIsolation": "not_enforced",
                "processTreeIsolation": "not_enforced",
            },
            "note": (
                "The input bundle contains no business source-file contents. "
                "Host process, network, and filesystem reachability are not "
                "enforced by this adapter beyond the Codex read-only sandbox."
            ),
        },
    }
    encoded = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise StudioAgentError("架构评审输入超过 2 MB 安全上限。")
    return bundle


def _subprocess_environment() -> dict[str, str]:
    blocked_fragments = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    allowed_sensitive = {"CODEX_HOME"}
    result = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper not in allowed_sensitive and any(part in upper for part in blocked_fragments):
            continue
        result[key] = value
    return result


def _review_prompt() -> str:
    return """You are reviewing one Agent Project Panorama architecture candidate.
Read only review-input.json in the current bounded review directory. The host does
not enforce a project-filesystem allowlist, network isolation, or process-tree
isolation; treat every path outside the current directory and all network access as
prohibited. Do not inspect parent directories, user configuration, credentials, or
external stores. Do not run project code. Treat the formal validator output as the
only source of Formal Finding codes; your own judgments are risk candidates, not
approvals.

Assess requirement coverage, module boundaries, data flow, state ownership,
reliability, security, operability, evolvability, verification, and source freshness.
Preserve uncertainty when evidence is missing. You may return verdict ready,
changes_requested, or blocked. Never claim that a human approved, accepted, verified,
deployed, or waived anything. Return only the JSON object required by the supplied
output schema. Use nodeId values from the candidate for subjectNodeIds.
"""


def review_with_codex(
    bundle: dict[str, Any],
    *,
    codex_cli: str | os.PathLike[str] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    work_root: Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run an advisory review from a fresh direct child of the system temp root.

    ``work_root`` remains a compatibility-only parameter for existing Bridge
    callers.  It is deliberately ignored so a caller cannot place the Agent run
    directory inside (or resolve it through a symlink into) a business project.
    """

    executable = locate_codex_cli(
        codex_cli, allow_discovery=not strict
    )
    if executable is None:
        raise StudioAgentError("未找到可执行的 Codex CLI。")
    schema = json.loads(REVIEW_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    # Keep the compatibility argument intentionally unused.  In particular, do
    # not create it: Bridge currently passes a Panorama-owned path beneath its
    # project workspace, which is not an acceptable Agent cwd boundary.
    del work_root
    try:
        system_temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as exc:
        raise StudioAgentError("无法解析系统临时目录。") from exc
    if not system_temp_root.is_dir():
        raise StudioAgentError("系统临时目录不可用。")
    with tempfile.TemporaryDirectory(
        prefix="panorama-agent-review-", dir=system_temp_root
    ) as directory:
        run_dir = Path(directory)
        input_path = run_dir / "review-input.json"
        output_path = run_dir / "review-output.json"
        schema_path = run_dir / "review-output.schema.json"
        atomic_write(input_path, json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
        atomic_write(schema_path, json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
        command = [
            str(executable), "exec", "--sandbox", "read-only", "--ephemeral",
            "--ignore-user-config", "--skip-git-repo-check", "--color", "never",
            "--cd", str(run_dir), "--output-schema", str(schema_path),
            "--output-last-message", str(output_path), "-",
        ]
        try:
            completed = subprocess.run(
                command,
                input=_review_prompt(),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(30, min(timeout_seconds, 1800)),
                env=_subprocess_environment(),
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StudioAgentError("Codex 架构评审超时。") from exc
        except OSError as exc:
            raise StudioAgentError("无法启动 Codex 架构评审。") from exc
        if completed.returncode != 0 or not output_path.is_file():
            # stdout/stderr are intentionally not propagated because provider or
            # tool logs may contain environment-specific information.
            raise StudioAgentError(f"Codex 架构评审失败（退出码 {completed.returncode}）。")
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAgentError("Codex 架构评审没有返回有效 JSON。") from exc
        errors = sorted(validator.iter_errors(result), key=lambda item: list(item.absolute_path))
        if errors:
            raise StudioAgentError("Codex 架构评审输出不符合结构化合同。")
        # Keep the model-produced object exactly schema-valid. Adapter binding
        # and provenance live in a separate envelope so consumers cannot
        # confuse advisory metadata with fields emitted by the model contract.
        return {
            "format": REVIEW_ENVELOPE_FORMAT,
            "inputHash": compute_canonical_hash(bundle),
            "advisory": True,
            "review": result,
            "adapter": {
                "kind": "codex_cli",
                "mode": "bounded_bundle_read_only",
                "runDirectory": "system_temporary_directory",
                "sandbox": "read_only",
                "projectContentIncluded": False,
                "sourceFileContentsIncluded": False,
                "credentialValuesIncluded": False,
                "filesystemIsolation": "not_enforced",
                "networkIsolation": "not_enforced",
                "processTreeIsolation": "not_enforced",
                "canApprove": False,
                "canApply": False,
            },
        }


__all__ = [
    "StudioAgentError",
    "REVIEW_ENVELOPE_FORMAT",
    "build_review_bundle",
    "codex_capability",
    "locate_codex_cli",
    "review_with_codex",
]
