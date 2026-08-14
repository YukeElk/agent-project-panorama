"""Loopback-only HTTP bridge for the Panorama Architecture Studio.

The browser remains an untrusted draft editor.  This bridge owns persistence,
formal validation, advisory Agent review, exact-hash approval recording, and
the existing backup/validate/atomic-apply transaction.  It never accepts a
filesystem path, executable, or argv from an HTTP request.
"""

from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Any
from urllib.parse import urlsplit
import webbrowser

from apply_patch import (
    ApplyPatchError,
    apply_update_package,
    compute_proposal_hash,
    parse_proposal_artifact,
)
from continuous_observation import ObservationError, reconcile_html
from observe_project_runtime import git_observation, source_snapshot
from panorama_io import (
    PanoramaIOError,
    compute_canonical_hash,
    compute_data_hash,
    compute_presentation_hash,
    extract_data,
)
from import_verification_receipt import (
    PREVIEW_FORMAT as VERIFICATION_PREVIEW_FORMAT,
    VerificationReceiptError,
    build_receipt_preview,
    build_receipt_proposal,
)
from propose_update import build_proposal
from schema_support import schema_for_data
from studio_agent import (
    StudioAgentError,
    build_review_bundle,
    codex_capability,
    locate_codex_cli,
    review_with_codex,
)
from studio_approval import StudioApprovalError, record_studio_approval
from studio_session import (
    SessionConflictError,
    StudioSessionError,
    create_session,
    formal_validate_candidate,
    load_session,
    save_session,
    semantic_hash,
)


BRIDGE_VERSION = "0.4.0"
MAX_BODY_BYTES = 1024 * 1024
MAX_JOBS = 32
API_PREFIX = "/api/v1"
APPROVAL_PHRASE = "\u6279\u51c6 Studio Proposal {proposal_hash}"
RECEIPT_APPROVAL_PHRASE = "\u6279\u51c6 Receipt Proposal {proposal_hash}"
SAFE_ARTIFACT_ID = re.compile(r"^[A-Z][A-Z0-9._-]{1,127}$")
META_CSP_RE = re.compile(
    r"\s*<meta\s+http-equiv=[\"']Content-Security-Policy[\"'][^>]*>",
    re.IGNORECASE,
)
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
MAX_STUDIO_SOURCE_FILES = 20_000
MAX_STUDIO_SOURCE_BYTES = 128 * 1024 * 1024


class StudioBridgeError(RuntimeError):
    """A safe, user-facing bridge failure."""

    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ARTIFACT_ID.fullmatch(value):
        raise StudioBridgeError("INVALID_ID", f"{label} is invalid")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise StudioBridgeError("INVALID_ID", f"{label} is unsafe")
    return value


def _path_is_link_or_reparse(path: Path) -> bool:
    """Detect POSIX links and Windows junction/reparse points without following."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return path.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _ensure_safe_directory_tree(project_root: Path, directory: Path) -> None:
    """Create/check a Panorama-owned directory without traversing redirects."""

    root = project_root.resolve(strict=True)
    try:
        relative = directory.absolute().relative_to(root)
    except ValueError as exc:
        raise StudioBridgeError(
            "UNSAFE_WORK_PATH", "Studio work path escapes project root", 500
        ) from exc
    if _path_is_link_or_reparse(root):
        raise StudioBridgeError(
            "UNSAFE_WORK_PATH", "project root is a link or reparse point", 500
        )
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            if _path_is_link_or_reparse(cursor):
                raise StudioBridgeError(
                    "UNSAFE_WORK_PATH",
                    "Studio work path contains a link or reparse point",
                    500,
                )
            if not cursor.is_dir():
                raise StudioBridgeError(
                    "UNSAFE_WORK_PATH", "Studio work path is not a directory", 500
                )
        else:
            cursor.mkdir()
            if _path_is_link_or_reparse(cursor) or not cursor.is_dir():
                raise StudioBridgeError(
                    "UNSAFE_WORK_PATH", "Studio work directory is unsafe", 500
                )
    try:
        cursor.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise StudioBridgeError(
            "UNSAFE_WORK_PATH", "Studio work path escapes project root", 500
        ) from exc


def _studio_owned_source_path(relative: Path, panorama_relative: Path | None) -> bool:
    parts = {part.lower() for part in relative.parts[:-1]}
    name = relative.name.lower()
    return bool(
        relative == panorama_relative
        or ".git" in parts
        or ".panorama-work" in parts
        or (name.startswith(".") and name.endswith(".panorama.lock"))
        or (".backup-" in name and name.endswith(".html"))
        or name.endswith(".local.html")
    )


def _studio_source_paths(project_root: Path) -> tuple[list[Path], str, str | None]:
    """Enumerate Git tracked/untracked source, or a bounded filesystem fallback."""

    try:
        result = subprocess.run(
            [
                "git", "-C", str(project_root), "ls-files", "-z",
                "--cached", "--others", "--exclude-standard",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0:
        values = [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]
        return sorted({Path(value) for value in values}, key=lambda item: item.as_posix()), "git", None

    paths: list[Path] = []
    unsafe = False
    for dirpath, dirnames, filenames in os.walk(project_root, topdown=True, followlinks=False):
        safe_dirs = []
        for name in sorted(dirnames):
            child = Path(dirpath) / name
            if name.lower() in {".git", ".panorama-work"}:
                continue
            if _path_is_link_or_reparse(child):
                unsafe = True
                continue
            safe_dirs.append(name)
        dirnames[:] = safe_dirs
        for name in sorted(filenames):
            paths.append((Path(dirpath) / name).relative_to(project_root))
            if len(paths) > MAX_STUDIO_SOURCE_FILES:
                return paths, "filesystem", "file_limit_exceeded"
    return paths, "filesystem", "unsafe_link_skipped" if unsafe else None


def _studio_source_digest(project_root: Path, panorama: Path) -> dict[str, Any]:
    """Hash bounded source contents; metadata-only snapshots are insufficient."""

    root = project_root.resolve(strict=True)
    try:
        panorama_relative = panorama.resolve(strict=True).relative_to(root)
    except ValueError:
        panorama_relative = None
    paths, mode, incomplete_reason = _studio_source_paths(root)
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for relative in paths:
        if relative.is_absolute() or ".." in relative.parts:
            incomplete_reason = incomplete_reason or "unsafe_path"
            continue
        if _studio_owned_source_path(relative, panorama_relative):
            continue
        source = root / relative
        if _path_is_link_or_reparse(source):
            incomplete_reason = incomplete_reason or "unsafe_link_skipped"
            continue
        try:
            resolved = source.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                incomplete_reason = incomplete_reason or "non_file_entry"
                continue
            size = resolved.stat().st_size
            if file_count + 1 > MAX_STUDIO_SOURCE_FILES:
                incomplete_reason = "file_limit_exceeded"
                break
            if total_bytes + size > MAX_STUDIO_SOURCE_BYTES:
                incomplete_reason = "byte_limit_exceeded"
                break
            path_bytes = relative.as_posix().encode("utf-8", errors="surrogateescape")
            digest.update(len(path_bytes).to_bytes(8, "big"))
            digest.update(path_bytes)
            digest.update(size.to_bytes(8, "big"))
            with resolved.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            file_count += 1
            total_bytes += size
        except (OSError, ValueError):
            incomplete_reason = incomplete_reason or "source_read_failed"
    return {
        "algorithm": "sha256-path-size-content-v1",
        "mode": mode,
        "contentHash": digest.hexdigest(),
        "fileCount": file_count,
        "totalBytes": total_bytes,
        "coverageComplete": incomplete_reason is None,
        "incompleteReason": incomplete_reason,
    }


def _formal_source_status(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "unbound"
    if value.get("extensions", {}).get("migrationRequiresInitialSync") is True:
        return "uninitialized"
    mode = value.get("mode")
    snapshot = value.get("sourceSnapshotHash")
    if snapshot in {None, "0" * 64}:
        return "uninitialized"
    if mode == "git" and value.get("gitHead") is None:
        return "uninitialized"
    return "bound"


def _atomic_json(path: Path, value: dict[str, Any], *, write_once: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if write_once:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise StudioBridgeError(
                "ARTIFACT_EXISTS", f"artifact already exists: {path.name}", 409
            ) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(rendered, encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioBridgeError("ARTIFACT_READ_FAILED", f"cannot read {label}", 500) from exc
    if not isinstance(value, dict):
        raise StudioBridgeError("ARTIFACT_INVALID", f"{label} is not a JSON object", 500)
    return value


def _candidate(session: dict[str, Any], candidate_id: str | None) -> dict[str, Any]:
    wanted = candidate_id or session.get("activeCandidateId")
    for item in session.get("candidates", []):
        if isinstance(item, dict) and item.get("candidateId") == wanted:
            return item
    raise StudioBridgeError("CANDIDATE_NOT_FOUND", "candidate does not exist", 404)


def _nonce_html(source: str, nonce: str) -> str:
    """Create a response-only CSP variant without touching canonical bytes."""

    result = META_CSP_RE.sub("", source, count=1)
    result = re.sub(r"<style(?![^>]*\bnonce=)", f'<style nonce="{nonce}"', result)
    result = re.sub(r"<script(?![^>]*\bnonce=)", f'<script nonce="{nonce}"', result)
    return result


def _launcher_html(nonce: str) -> str:
    """Return a project-data-free launcher for fragment capability bootstrap."""

    # The bearer value is read from the URL fragment and sent only in the
    # dedicated request header.  The protected document is rewritten with the
    # launcher's nonce because fetch() response CSP headers do not become the
    # active document policy after document replacement.
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Panorama Architecture Studio</title>
<style nonce="{nonce}">body{{font:16px system-ui,sans-serif;margin:3rem;max-width:48rem}}p{{color:#475569}}</style>
</head><body><h1>Architecture Studio</h1><p id="status">正在验证本机启动能力……</p>
<script nonce="{nonce}">(() => {{
  "use strict";
  const status = document.getElementById("status");
  const params = new URLSearchParams(location.hash.slice(1));
  const capability = params.get("cap") || "";
  if (!capability) {{ status.textContent = "启动链接缺少 capability；请重新启动 Bridge。"; return; }}
  fetch("/studio/document", {{
    method: "GET", cache: "no-store", credentials: "omit",
    headers: {{"X-Panorama-Capability": capability}}
  }}).then(response => {{
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.text();
  }}).then(source => {{
    const activeNonce = {json.dumps(nonce)};
    source = source
      .replace(/<style(?:\\s+nonce=["'][^"']*["'])?/gi, '<style nonce="' + activeNonce + '"')
      .replace(/<script(?:\\s+nonce=["'][^"']*["'])?/gi, '<script nonce="' + activeNonce + '"');
    // Preserve the fragment until the trusted Renderer consumes it into
    // memory and immediately removes it from the address bar.
    document.open(); document.write(source); document.close();
  }}).catch(() => {{ status.textContent = "无法载入受保护的 Studio 文档；请重新启动 Bridge。"; }});
}})();</script></body></html>"""


class StudioBridge:
    """State and trusted operations shared by the HTTP handlers."""

    def __init__(
        self,
        panorama: Path,
        project_root: Path,
        *,
        codex_cli: Path | None = None,
        agent_timeout: int = 600,
    ) -> None:
        self.panorama = panorama.resolve(strict=True)
        self.project_root = project_root.resolve(strict=True)
        if not self.panorama.is_file() or self.panorama.suffix.lower() not in {
            ".html",
            ".htm",
        }:
            raise ValueError("panorama must be an existing HTML file")
        if not self.project_root.is_dir():
            raise ValueError("project root must be an existing directory")
        # Parse eagerly so invalid input never reaches a listening socket.
        extract_data(self.panorama)
        self.capability = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        if codex_cli is not None:
            trusted_codex = locate_codex_cli(codex_cli, allow_discovery=False)
            if trusted_codex is None:
                raise StudioBridgeError(
                    "AGENT_EXECUTABLE_INVALID",
                    "the explicitly configured Codex executable is unavailable",
                    400,
                )
            self.codex_cli = trusted_codex
        else:
            self.codex_cli = None
        self.agent_timeout = max(30, min(int(agent_timeout), 1800))
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="panorama-studio-agent"
        )
        self.jobs: dict[str, dict[str, Any]] = {}
        self.jobs_lock = threading.Lock()
        self.project_lock = threading.RLock()
        self.server: ThreadingHTTPServer | None = None
        self.host = ""
        self.origin = ""
        self.work = self.project_root / ".panorama-work" / "studio"
        self.sessions = self.work / "sessions"
        self.proposals = self.work / "proposals"
        self.approvals = self.work / "approvals"
        self.validations = self.work / "validations"
        self.reviews = self.work / "reviews"
        self.receipts = self.work / "verification-receipts"
        for directory in (
            self.work,
            self.sessions,
            self.proposals,
            self.approvals,
            self.validations,
            self.reviews,
            self.receipts,
        ):
            _ensure_safe_directory_tree(self.project_root, directory)
        # Evidence Inspector compares the current source with an immutable
        # Bridge-start baseline.  Only hashes and coverage metadata leave the
        # process; source bytes are never returned to the browser.
        self.evidence_live_baseline = self._live_baseline()

    def _safe_artifact_parent(self, path: Path) -> None:
        """Recheck containment and redirect-free ancestry immediately before I/O."""

        try:
            path.absolute().relative_to(self.work.absolute())
        except ValueError as exc:
            raise StudioBridgeError(
                "UNSAFE_WORK_PATH", "Studio artifact escapes work root", 500
            ) from exc
        _ensure_safe_directory_tree(self.project_root, path.parent)

    def _save_session(
        self, session: dict[str, Any], *, expected_revision: int
    ) -> dict[str, Any]:
        self._safe_artifact_parent(
            self.sessions / f"{_safe_id(session.get('sessionId'), 'sessionId')}.json"
        )
        return save_session(
            self.project_root, session, expected_revision=expected_revision
        )

    def _write_artifact(
        self, path: Path, value: dict[str, Any], *, write_once: bool = False
    ) -> None:
        self._safe_artifact_parent(path)
        _atomic_json(path, value, write_once=write_once)

    def _read_artifact(self, path: Path, label: str) -> dict[str, Any]:
        self._safe_artifact_parent(path)
        if _path_is_link_or_reparse(path) or not path.is_file():
            raise StudioBridgeError(
                "UNSAFE_WORK_PATH", f"{label} is not a regular local artifact", 500
            )
        try:
            path.resolve(strict=True).relative_to(self.work.resolve(strict=True))
        except ValueError as exc:
            raise StudioBridgeError(
                "UNSAFE_WORK_PATH", f"{label} escapes Studio work root", 500
            ) from exc
        return _read_json(path, label)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def bind(self, port: int) -> ThreadingHTTPServer:
        bridge = self

        class IPv4LoopbackServer(ThreadingHTTPServer):
            address_family = socket.AF_INET
            daemon_threads = True
            allow_reuse_address = False

        class Handler(StudioRequestHandler):
            app = bridge

        server = IPv4LoopbackServer(("127.0.0.1", port), Handler)
        actual_port = int(server.server_address[1])
        self.host = f"127.0.0.1:{actual_port}"
        self.origin = f"http://{self.host}"
        self.server = server
        return server

    def _data(self) -> dict[str, Any]:
        return extract_data(self.panorama)

    def _schema(self, data: dict[str, Any]) -> Path:
        return schema_for_data(data)

    def _source_observation(self) -> dict[str, Any]:
        snapshot = source_snapshot(self.project_root)
        git = git_observation(self.project_root)
        return {
            "gitHead": git.get("head"),
            "gitBranch": git.get("branch"),
            "gitDirty": git.get("dirty"),
            "sourceSnapshotHash": compute_canonical_hash(snapshot),
        }

    def _live_baseline(self) -> dict[str, Any]:
        observation = self._source_observation()
        return {
            "capturedAt": _now(),
            "gitHead": observation.get("gitHead"),
            "studioSourceDigest": _studio_source_digest(
                self.project_root, self.panorama
            ),
        }

    def _evidence_source_freshness(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return disclosure-only live source comparison for the Renderer."""

        formal = data.get("sourceBinding")
        formal = formal if isinstance(formal, dict) else {}
        formal_status = _formal_source_status(formal)
        actual_observation = self._source_observation()
        actual_live = self._live_baseline()
        baseline_live = self.evidence_live_baseline
        baseline_digest = baseline_live.get("studioSourceDigest", {})
        actual_digest = actual_live.get("studioSourceDigest", {})
        reasons: list[str] = []

        if formal_status == "unbound":
            reasons.append("formal_source_unbound")
        elif formal_status == "uninitialized":
            reasons.append("formal_source_uninitialized")
        else:
            if formal.get("mode") == "git" and formal.get("gitHead") != actual_observation.get("gitHead"):
                reasons.append("git_head_changed")
            if formal.get("sourceSnapshotHash") != actual_observation.get("sourceSnapshotHash"):
                reasons.append("source_snapshot_changed")

        if baseline_live.get("gitHead") != actual_live.get("gitHead"):
            reasons.append("bridge_baseline_git_changed")
        if (
            baseline_digest.get("contentHash") != actual_digest.get("contentHash")
            or baseline_digest.get("fileCount") != actual_digest.get("fileCount")
            or baseline_digest.get("totalBytes") != actual_digest.get("totalBytes")
        ):
            reasons.append("source_content_changed")

        coverage_complete = bool(
            baseline_digest.get("coverageComplete")
            and actual_digest.get("coverageComplete")
        )
        if not coverage_complete:
            reasons.append("coverage_incomplete")

        reasons = list(dict.fromkeys(reasons))
        if not coverage_complete:
            status = "incomplete"
        elif formal_status in {"unbound", "uninitialized"}:
            status = "uninitialized"
        elif reasons:
            status = "drift"
        else:
            status = "match"

        return {
            "status": status,
            "checkedAt": _now(),
            "reasons": reasons,
            "formalSourceBindingStatus": formal_status,
            "formalBinding": {
                "mode": formal.get("mode"),
                "gitHead": formal.get("gitHead"),
                "gitBranch": formal.get("gitBranch"),
                "sourceSnapshotHash": formal.get("sourceSnapshotHash"),
                "observedAt": formal.get("observedAt"),
                "lastObservationBatchId": formal.get("lastObservationBatchId"),
            },
            "actualObservation": actual_observation,
            "contentDigest": {
                "algorithm": actual_digest.get("algorithm"),
                "baselineHash": baseline_digest.get("contentHash"),
                "actualHash": actual_digest.get("contentHash"),
                "fileCount": actual_digest.get("fileCount"),
                "totalBytes": actual_digest.get("totalBytes"),
                "coverageComplete": coverage_complete,
                "incompleteReason": actual_digest.get("incompleteReason")
                or baseline_digest.get("incompleteReason"),
            },
        }

    def _assert_live_source(
        self,
        session: dict[str, Any],
        current: dict[str, Any],
        *,
        require_complete: bool,
    ) -> dict[str, Any]:
        baseline = session.get("liveBaseline")
        if not isinstance(baseline, dict):
            raise StudioBridgeError(
                "SOURCE_BASELINE_MISSING",
                "Studio session has no immutable live source baseline",
                409,
            )
        expected_digest = baseline.get("studioSourceDigest")
        actual = self._live_baseline()
        actual_digest = actual["studioSourceDigest"]
        if require_complete and (
            not isinstance(expected_digest, dict)
            or not expected_digest.get("coverageComplete")
            or not actual_digest.get("coverageComplete")
        ):
            raise StudioBridgeError(
                "SOURCE_COVERAGE_INCOMPLETE",
                "Studio source content coverage is incomplete",
                409,
                {"baseline": expected_digest, "actual": actual_digest},
            )
        for field in ("gitHead",):
            if baseline.get(field) != actual.get(field):
                raise StudioBridgeError(
                    "SOURCE_DRIFT", f"project source changed ({field})", 409
                )
        if not isinstance(expected_digest, dict) or (
            expected_digest.get("contentHash") != actual_digest.get("contentHash")
            or expected_digest.get("fileCount") != actual_digest.get("fileCount")
            or expected_digest.get("totalBytes") != actual_digest.get("totalBytes")
            or expected_digest.get("coverageComplete")
            != actual_digest.get("coverageComplete")
        ):
            raise StudioBridgeError(
                "SOURCE_DRIFT", "project source content changed", 409
            )
        formal = current.get("sourceBinding")
        formal = formal if isinstance(formal, dict) else {}
        formal_status = _formal_source_status(formal)
        observed = self._source_observation()
        binding = session.get("projectBinding", {})
        if formal_status == "uninitialized":
            raise StudioBridgeError(
                "FORMAL_SOURCE_UNINITIALIZED",
                "formal Panorama requires initial fact synchronization",
                409,
            )
        if formal_status == "bound":
            fields = ["sourceSnapshotHash"]
            if formal.get("mode") == "git":
                fields.insert(0, "gitHead")
            for key in fields:
                expected = formal.get(key)
                if not isinstance(expected, str) or expected != observed.get(key):
                    raise StudioBridgeError(
                        "FORMAL_SOURCE_DRIFT",
                        f"formal Panorama sourceBinding does not match live source ({key})",
                        409,
                    )
                if binding.get(key) != expected:
                    raise StudioBridgeError(
                        "FORMAL_SOURCE_DRIFT",
                        f"Session formal source binding changed ({key})",
                        409,
                    )
        return {
            "formalSourceBinding": formal_status,
            "liveBaseline": copy.deepcopy(baseline),
            "actual": actual,
        }

    def health(self) -> dict[str, Any]:
        agent = (
            codex_capability(self.codex_cli, strict=True)
            if self.codex_cli is not None
            else {
                "available": False,
                "authenticated": False,
                "version": None,
                "reason": "codex_cli_not_enabled_at_startup",
            }
        )
        return {
            "version": BRIDGE_VERSION,
            "csrfToken": self.csrf,
            "mode": "loopback",
            "features": {
                "sessions": True,
                "formalValidation": True,
                "agentReview": bool(agent.get("available")),
                "proposal": True,
                "proposals": True,
                "exactHashApproval": True,
                "approvals": True,
                "apply": True,
                "factRefresh": True,
                "factsRefresh": True,
                "verificationReceiptPreview": True,
                "verificationReceiptProposal": True,
            },
            "agent": agent,
        }

    def context(self) -> dict[str, Any]:
        data = self._data()
        text = self.panorama.read_text(encoding="utf-8")
        sessions = []
        if self.sessions.is_dir():
            for path in sorted(self.sessions.glob("SESSION-*.json"))[-20:]:
                try:
                    # Filename must round-trip through the same ID/path checks
                    # as the API. Unsafe redirect artifacts are ignored without
                    # reading their target or disclosing it in Context.
                    value = self.get_session(path.stem)
                except (StudioBridgeError, StudioSessionError):
                    continue
                sessions.append(
                    {
                        "sessionId": value.get("sessionId"),
                        "sessionRevision": value.get("sessionRevision"),
                        "status": value.get("status"),
                        "activeCandidateId": value.get("activeCandidateId"),
                        "updatedAt": value.get("updatedAt"),
                    }
                )
        return {
            "project": {
                "id": data.get("project", {}).get("id"),
                "name": data.get("project", {}).get("name"),
            },
            "panorama": {
                "name": self.panorama.name,
                "schemaVersion": data.get("schemaVersion"),
                "templateVersion": data.get("meta", {}).get("templateVersion"),
                "revision": data.get("meta", {}).get("revision"),
                "dataHash": compute_data_hash(data),
                "presentationHash": compute_presentation_hash(text),
            },
            "sourceObservation": self._source_observation(),
            "sourceFreshness": self._evidence_source_freshness(data),
            "sessions": sessions,
        }

    def create_session(self) -> dict[str, Any]:
        with self.project_lock:
            # studio_session keeps formal binding and observed HEAD separate.
            session = create_session(self._data(), self.project_root)
            session["mode"] = "bridge"
            session["liveBaseline"] = self._live_baseline()
            # V0.1 may have no formal sourceBinding. This is disclosed and the
            # immutable liveBaseline remains the authoritative drift anchor.
            current = self._data()
            session["formalSourceBindingStatus"] = _formal_source_status(
                current.get("sourceBinding")
            )
            if session["formalSourceBindingStatus"] == "uninitialized":
                session["status"] = "stale"
            else:
                self._assert_live_source(session, current, require_complete=False)
            return self._save_session(session, expected_revision=0)

    def get_session(self, session_id: str) -> dict[str, Any]:
        safe_id = _safe_id(session_id, "sessionId")
        path = self.sessions / f"{safe_id}.json"
        self._safe_artifact_parent(path)
        if _path_is_link_or_reparse(path) or not path.is_file():
            raise StudioBridgeError(
                "UNSAFE_WORK_PATH", "Session is not a regular local artifact", 500
            )
        return load_session(self.project_root, safe_id)

    @staticmethod
    def _strip_formal_payloads(session: dict[str, Any], disk: dict[str, Any]) -> None:
        # Browser exports may carry only immutable pointers/summaries, never a
        # Proposal package or Approval artifact.
        for key in ("reviewArtifact", "proposalArtifact"):
            incoming = session.get(key)
            if incoming is not None:
                if not isinstance(incoming, dict) or any(
                    field in incoming
                    for field in (
                        "operations",
                        "proposal",
                        "approval",
                        "changeRecords",
                        "reviewDraft",
                        "updateBatchDraft",
                    )
                ):
                    raise StudioBridgeError(
                        "SESSION_FORMAL_PAYLOAD",
                        f"{key} may contain only an artifact pointer/summary",
                    )
            session[key] = copy.deepcopy(disk.get(key))
        for key in ("approvalArtifact", "approvalArtifacts", "proposalArtifacts"):
            session.pop(key, None)

    def put_session(
        self, session_id: str, expected_revision: Any, session: Any
    ) -> dict[str, Any]:
        if not isinstance(expected_revision, int) or not isinstance(session, dict):
            raise StudioBridgeError(
                "INVALID_SESSION_SAVE", "expectedRevision and session are required"
            )
        safe_session_id = _safe_id(session_id, "sessionId")
        if session.get("sessionId") != safe_session_id:
            raise StudioBridgeError("SESSION_ID_MISMATCH", "sessionId does not match URL")
        with self.project_lock:
            disk = self.get_session(safe_session_id)
            if expected_revision != disk.get("sessionRevision"):
                raise SessionConflictError(
                    f"expected session revision {expected_revision}, "
                    f"current revision is {disk.get('sessionRevision')}"
                )
            incoming = copy.deepcopy(session)
            self._strip_formal_payloads(incoming, disk)
            if incoming.get("projectBinding") != disk.get("projectBinding"):
                raise StudioBridgeError(
                    "PROJECT_BINDING_IMMUTABLE", "projectBinding cannot be changed", 409
                )
            incoming["mode"] = "bridge"
            incoming["sourceObservation"] = copy.deepcopy(
                disk.get("sourceObservation")
            )
            incoming["liveBaseline"] = copy.deepcopy(disk.get("liveBaseline"))
            incoming["formalSourceBindingStatus"] = disk.get(
                "formalSourceBindingStatus"
            )
            comparable_incoming = copy.deepcopy(incoming)
            comparable_disk = copy.deepcopy(disk)
            for value in (comparable_incoming, comparable_disk):
                for key in ("sessionRevision", "updatedAt", "clientValidation"):
                    value.pop(key, None)
            if comparable_incoming == comparable_disk:
                return disk
            return self._save_session(incoming, expected_revision=expected_revision)

    def _formal(
        self, session_id: str, candidate_id: str | None
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        session = self.get_session(session_id)
        candidate = _candidate(session, candidate_id)
        current = self._data()
        self._assert_live_source(session, current, require_complete=False)
        result = formal_validate_candidate(
            current,
            session,
            candidate["candidateId"],
            self._schema(current),
            self.panorama.parent,
            self.panorama,
        )
        return current, session, candidate, result

    def formal_validation(
        self, session_id: str, candidate_id: str | None
    ) -> dict[str, Any]:
        with self.project_lock:
            _, session, candidate, result = self._formal(session_id, candidate_id)
            validation_id = (
                "VALIDATION-" + result["semanticHash"][:24].upper()
            )
            artifact = {
                "format": "panorama-studio-formal-validation.v0.1",
                "validationId": validation_id,
                "sessionId": session["sessionId"],
                "candidateId": candidate["candidateId"],
                # Saving the formalValidation pointer advances Session once;
                # bind the disk artifact to that final revision up front.
                "sessionRevision": session["sessionRevision"] + 1,
                "semanticHash": result["semanticHash"],
                "validatedAt": _now(),
                "validation": result["validation"],
                "operationCount": len(result["operations"]),
            }
            self._write_artifact(
                self.validations / f"{validation_id}.json", artifact
            )
            session["formalValidation"] = {
                key: copy.deepcopy(artifact[key])
                for key in (
                    "validationId",
                    "candidateId",
                    "semanticHash",
                    "validatedAt",
                    "validation",
                    "operationCount",
                )
            }
            saved = self._save_session(
                session, expected_revision=session["sessionRevision"]
            )
            if saved["sessionRevision"] != artifact["sessionRevision"]:
                raise StudioBridgeError(
                    "SESSION_BINDING_INVALID",
                    "Formal Validation did not bind the final Session Revision",
                    500,
                )
            return artifact

    def create_review_job(
        self, session_id: str, candidate_id: str | None
    ) -> dict[str, Any]:
        if self.codex_cli is None:
            raise StudioBridgeError(
                "AGENT_UNAVAILABLE",
                "Codex CLI review was not enabled when the bridge started",
                409,
            )
        capability = codex_capability(self.codex_cli, strict=True)
        if not capability.get("available") or not capability.get("authenticated"):
            raise StudioBridgeError(
                "AGENT_UNAVAILABLE",
                "Codex CLI is unavailable or not authenticated",
                409,
            )
        with self.project_lock:
            current, session, candidate, result = self._formal(
                session_id, candidate_id
            )
            source_state = self._assert_live_source(
                session, current, require_complete=True
            )
            bundle = build_review_bundle(
                current,
                session,
                candidate,
                result["validation"],
                project_root=self.project_root,
            )
        with self.jobs_lock:
            if len(self.jobs) >= MAX_JOBS:
                raise StudioBridgeError("JOB_LIMIT", "review job limit reached", 429)
            job_id = "JOB-" + secrets.token_hex(12).upper()
            job = {
                "jobId": job_id,
                "status": "queued",
                "sessionId": session["sessionId"],
                "candidateId": candidate["candidateId"],
                "semanticHash": semantic_hash(candidate),
                "createdAt": _now(),
            }
            self.jobs[job_id] = job
        self.executor.submit(self._run_review_job, job_id, bundle)
        return copy.deepcopy(job)

    def _run_review_job(self, job_id: str, bundle: dict[str, Any]) -> None:
        with self.jobs_lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["startedAt"] = _now()
        try:
            result = review_with_codex(
                bundle,
                codex_cli=self.codex_cli,
                timeout_seconds=self.agent_timeout,
                work_root=self.work / "agent-runs",
                strict=True,
            )
            review_id = "REVIEW-" + result["inputHash"][:24].upper()
            model = result["review"]
            artifact = {
                "format": "panorama-studio-agent-review.v0.1",
                "reviewId": review_id,
                "completedAt": _now(),
                "result": result,
            }
            self._write_artifact(self.reviews / f"{review_id}.json", artifact)
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "succeeded",
                        "completedAt": artifact["completedAt"],
                        "result": {
                            "reviewId": review_id,
                            "verdict": model.get("verdict"),
                            "summary": model.get("summary"),
                            "findings": copy.deepcopy(model.get("findings", [])),
                            "inputHash": result.get("inputHash"),
                            "semanticHash": self.jobs[job_id].get("semanticHash"),
                        },
                    }
                )
        except Exception as exc:  # bounded worker; never return provider logs
            message = str(exc) if isinstance(exc, StudioAgentError) else "review failed"
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "completedAt": _now(),
                        "error": {"code": "AGENT_REVIEW_FAILED", "message": message},
                    }
                )

    def get_job(self, job_id: str) -> dict[str, Any]:
        safe_job_id = _safe_id(job_id, "jobId")
        with self.jobs_lock:
            if safe_job_id not in self.jobs:
                raise StudioBridgeError("JOB_NOT_FOUND", "review job does not exist", 404)
            return copy.deepcopy(self.jobs[safe_job_id])

    @staticmethod
    def _receipt_hash(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise StudioBridgeError("INVALID_RECEIPT_HASH", "receiptHash is invalid")
        return value

    def _receipt_preview_path(self, receipt_hash: str) -> Path:
        digest = self._receipt_hash(receipt_hash)
        return self.receipts / f"RECEIPT-{digest.upper()}.json"

    def preview_verification_receipt(self, body: dict[str, Any]) -> dict[str, Any]:
        receipt = body.get("receipt")
        if not isinstance(receipt, dict) or set(body) != {"receipt"}:
            raise StudioBridgeError(
                "INVALID_RECEIPT", "body must contain only a receipt object"
            )
        with self.project_lock:
            current = self._data()
            preview = build_receipt_preview(
                current,
                receipt,
                project_root=self.project_root,
                panorama_path=self.panorama,
                panorama_schema=self._schema(current),
            )
            path = self._receipt_preview_path(preview["receiptHash"])
            if path.exists():
                existing = self._read_artifact(path, "Verification Receipt Preview")
                if existing != preview:
                    raise StudioBridgeError(
                        "RECEIPT_PREVIEW_CONFLICT",
                        "stored Receipt Preview differs from the current preview",
                        409,
                    )
            else:
                self._write_artifact(path, preview, write_once=True)
            return {
                "preview": preview,
                "artifact": str(path.relative_to(self.project_root)),
            }

    def create_verification_receipt_proposal(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        if set(body) != {"receiptHash"}:
            raise StudioBridgeError(
                "INVALID_RECEIPT_PROPOSAL", "body must contain only receiptHash"
            )
        receipt_hash = self._receipt_hash(body.get("receiptHash"))
        with self.project_lock:
            preview = self._read_artifact(
                self._receipt_preview_path(receipt_hash),
                "Verification Receipt Preview",
            )
            if preview.get("format") != VERIFICATION_PREVIEW_FORMAT:
                raise StudioBridgeError(
                    "INVALID_RECEIPT_PREVIEW", "stored Receipt Preview format is invalid"
                )
            current = self._data()
            wrapper = build_receipt_proposal(
                current,
                preview,
                project_root=self.project_root,
                panorama_path=self.panorama,
                panorama_schema=self._schema(current),
            )
            package = wrapper["proposal"]
            observation = self._source_observation()
            actual = self._live_baseline()
            receipt_binding = package["reviewDraft"]["extensions"][
                "verificationReceiptBinding"
            ]
            receipt_binding.update(
                {
                    "gitHead": observation.get("gitHead"),
                    "sourceSnapshotHash": observation.get("sourceSnapshotHash"),
                    "studioSourceDigest": copy.deepcopy(
                        actual.get("studioSourceDigest")
                    ),
                }
            )
            package["updateBatchDraft"]["extensions"][
                "verificationReceiptBinding"
            ] = copy.deepcopy(receipt_binding)
            package["proposalHash"] = compute_proposal_hash(package)
            package["approval"]["proposalHash"] = package["proposalHash"]
            proposal_id = "PROPOSAL-VR-" + package["proposalHash"][:20].upper()
            proposal_path = self.proposals / f"{proposal_id}.json"
            if proposal_path.exists():
                existing = self._read_artifact(proposal_path, "Receipt Proposal")
                if existing != wrapper:
                    raise StudioBridgeError(
                        "PROPOSAL_ID_COLLISION", "Receipt Proposal ID collision", 409
                    )
            else:
                self._write_artifact(proposal_path, wrapper, write_once=True)
            return {
                "kind": "verification_receipt",
                "proposalId": proposal_id,
                "proposalHash": package["proposalHash"],
                "status": "pending",
                "receiptId": preview["receiptId"],
                "receiptHash": receipt_hash,
                "confirmationPhrase": RECEIPT_APPROVAL_PHRASE.format(
                    proposal_hash=package["proposalHash"]
                ),
                "binding": copy.deepcopy(receipt_binding),
                "artifact": str(proposal_path.relative_to(self.project_root)),
                "operations": copy.deepcopy(package["operations"]),
                "affectedEntities": copy.deepcopy(
                    wrapper["facts"]["affectedEntities"]
                ),
                "validation": copy.deepcopy(wrapper["validation"]),
            }

    def create_proposal(self, body: dict[str, Any]) -> dict[str, Any]:
        session_id = _safe_id(body.get("sessionId"), "sessionId")
        candidate_id = body.get("candidateId")
        if candidate_id is not None:
            candidate_id = _safe_id(candidate_id, "candidateId")
        summary = body.get("summary")
        reason = body.get("reason")
        if not isinstance(summary, str) or not summary.strip():
            raise StudioBridgeError("INVALID_PROPOSAL", "summary is required")
        if not isinstance(reason, str) or not reason.strip():
            raise StudioBridgeError("INVALID_PROPOSAL", "reason is required")
        change_level = body.get("changeLevel", "architecture")
        if change_level not in {
            "local",
            "module",
            "architecture",
            "deployment",
            "project",
        }:
            raise StudioBridgeError("INVALID_PROPOSAL", "changeLevel is invalid")
        with self.project_lock:
            current, session, candidate, result = self._formal(
                session_id, candidate_id
            )
            source_state = self._assert_live_source(
                session, current, require_complete=True
            )
            if not result["validation"].get("valid"):
                raise StudioBridgeError(
                    "FORMAL_VALIDATION_FAILED",
                    "candidate must pass formal validation before Proposal",
                    422,
                    result["validation"],
                )
            observation = self._source_observation()
            binding = {
                "sessionId": session["sessionId"],
                # Persisting the Proposal pointer below advances this once.
                "sessionRevision": session["sessionRevision"] + 1,
                "candidateId": candidate["candidateId"],
                "semanticHash": semantic_hash(candidate),
                "projectId": current.get("project", {}).get("id"),
                "baseRevision": current.get("meta", {}).get("revision"),
                "baseDataHash": compute_data_hash(current),
                "gitHead": observation.get("gitHead"),
                "sourceSnapshotHash": observation.get("sourceSnapshotHash"),
                "formalSourceBindingStatus": source_state["formalSourceBinding"],
                "studioSourceDigest": copy.deepcopy(
                    source_state["actual"]["studioSourceDigest"]
                ),
            }
            candidate_update = {
                "operations": result["operations"],
                "summary": summary.strip(),
                "reason": reason.strip(),
                "beforeSummary": "Current Panorama target architecture",
                "afterSummary": candidate.get("label", candidate["candidateId"]),
                "changeLevel": change_level,
                "createdAt": _now(),
            }
            wrapper = build_proposal(
                current,
                candidate_update,
                self._schema(current),
                base_dir=self.panorama.parent,
                source_path=self.panorama,
            )
            package = wrapper["proposal"]
            for field in ("reviewDraft", "updateBatchDraft"):
                extensions = package[field].setdefault("extensions", {})
                extensions["architectureStudioBinding"] = copy.deepcopy(binding)
            package["proposalHash"] = compute_proposal_hash(package)
            package["approval"] = {
                "status": "pending",
                "approvedBy": "",
                "approvedAt": None,
                "proposalHash": package["proposalHash"],
            }
            formal_source = current.get("sourceBinding")
            formal_source = formal_source if isinstance(formal_source, dict) else {}
            wrapper["sourceBinding"] = {
                "projectId": current.get("project", {}).get("id"),
                "schemaVersion": str(current.get("schemaVersion", "")),
                "templateVersion": str(
                    current.get("meta", {}).get("templateVersion", "")
                ),
                "baseRevision": package["baseRevision"],
                "baseDataHash": package["baseDataHash"],
                "gitHead": formal_source.get("gitHead"),
                "sourceSnapshotHash": formal_source.get("sourceSnapshotHash"),
            }
            proposal_id = "PROPOSAL-" + package["proposalHash"][:24].upper()
            proposal_path = self.proposals / f"{proposal_id}.json"
            if proposal_path.exists():
                existing = self._read_artifact(proposal_path, "Proposal")
                if existing != wrapper:
                    raise StudioBridgeError(
                        "PROPOSAL_ID_COLLISION", "Proposal ID collision", 409
                    )
            else:
                self._write_artifact(proposal_path, wrapper, write_once=True)
            session["proposalArtifact"] = {
                "proposalId": proposal_id,
                "proposalHash": package["proposalHash"],
                "status": "pending",
                "summary": summary.strip(),
            }
            saved_session = self._save_session(
                session, expected_revision=session["sessionRevision"]
            )
            if saved_session["sessionRevision"] != binding["sessionRevision"]:
                raise StudioBridgeError(
                    "SESSION_BINDING_INVALID",
                    "Proposal did not bind the final Session Revision",
                    500,
                )
            return {
                "proposalId": proposal_id,
                "proposalHash": package["proposalHash"],
                "status": "pending",
                "summary": summary.strip(),
                "sessionRevision": saved_session["sessionRevision"],
                "confirmationPhrase": APPROVAL_PHRASE.format(
                    proposal_hash=package["proposalHash"]
                ),
                "binding": binding,
                "artifact": str(proposal_path.relative_to(self.project_root)),
                # These are the exact propose_update.py outputs.  The Renderer
                # projects them without recomputing a second diff or affected
                # entity set in the browser.
                "operations": copy.deepcopy(package["operations"]),
                "affectedEntities": copy.deepcopy(
                    wrapper["facts"]["affectedEntities"]
                ),
                "validation": copy.deepcopy(wrapper["validation"]),
            }

    def _proposal_path(self, proposal_id: str) -> Path:
        return self.proposals / f"{_safe_id(proposal_id, 'proposalId')}.json"

    def record_approval(self, body: dict[str, Any]) -> dict[str, Any]:
        proposal_id = _safe_id(body.get("proposalId"), "proposalId")
        approved_hash = body.get("approvedHash")
        approved_by = body.get("approvedBy")
        confirmation = body.get("confirmation")
        if not isinstance(approved_hash, str) or not re.fullmatch(
            r"[a-f0-9]{64}", approved_hash
        ):
            raise StudioBridgeError("INVALID_APPROVAL", "approvedHash is invalid")
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise StudioBridgeError("INVALID_APPROVAL", "approvedBy is required")
        with self.project_lock:
            wrapper = self._read_artifact(
                self._proposal_path(proposal_id), "Proposal"
            )
            package, _ = parse_proposal_artifact(wrapper)
            if package.get("proposalHash") != approved_hash:
                raise StudioBridgeError(
                    "APPROVAL_HASH_MISMATCH",
                    "approvedHash is not the selected Proposal Hash",
                    409,
                )
            binding = self._assert_governance_proposal_binding(
                package, self._data()
            )
            receipt_proposal = "receiptHash" in binding
            expected_confirmation = (
                RECEIPT_APPROVAL_PHRASE if receipt_proposal else APPROVAL_PHRASE
            ).format(proposal_hash=approved_hash)
            if not isinstance(confirmation, str) or not hmac.compare_digest(
                confirmation.encode("utf-8"), expected_confirmation.encode("utf-8")
            ):
                raise StudioBridgeError(
                    "CONFIRMATION_MISMATCH",
                    "exact confirmation phrase does not match the Proposal Hash",
                )
            approval = record_studio_approval(
                wrapper,
                approved_hash=approved_hash,
                approved_by=approved_by,
            )
            approval_id = "APPROVAL-" + approved_hash[:24].upper()
            approval_path = self.approvals / f"{approval_id}.json"
            self._write_artifact(approval_path, approval, write_once=True)
            return {
                "approvalId": approval_id,
                "proposalId": proposal_id,
                "proposalHash": approved_hash,
                "approvedBy": approval["approvedBy"],
                "recordedAt": approval["approvalRecordedAt"],
                "status": "approved",
                "artifact": str(approval_path.relative_to(self.project_root)),
            }

    @staticmethod
    def _studio_binding(package: dict[str, Any]) -> dict[str, Any]:
        review = package.get("reviewDraft", {})
        batch = package.get("updateBatchDraft", {})
        review_binding = review.get("extensions", {}).get(
            "architectureStudioBinding"
        )
        batch_binding = batch.get("extensions", {}).get(
            "architectureStudioBinding"
        )
        if not isinstance(review_binding, dict) or review_binding != batch_binding:
            raise StudioBridgeError(
                "STUDIO_BINDING_INVALID", "Proposal Studio binding is missing", 409
            )
        return review_binding

    def _assert_proposal_session_binding(
        self, package: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        binding = self._studio_binding(package)
        session = self.get_session(
            _safe_id(binding.get("sessionId"), "sessionId")
        )
        if session.get("sessionRevision") != binding.get("sessionRevision"):
            raise StudioBridgeError(
                "SESSION_DRIFT",
                "Studio Session Revision changed after Proposal",
                409,
            )
        pointer = session.get("proposalArtifact")
        if not isinstance(pointer, dict) or pointer.get("proposalHash") != package.get(
            "proposalHash"
        ):
            raise StudioBridgeError(
                "SESSION_DRIFT",
                "Session no longer points to this exact Proposal",
                409,
            )
        candidate = _candidate(
            session, _safe_id(binding.get("candidateId"), "candidateId")
        )
        if semantic_hash(candidate) != binding.get("semanticHash"):
            raise StudioBridgeError(
                "SESSION_DRIFT",
                "Studio candidate semantics changed after Proposal",
                409,
            )
        source_state = self._assert_live_source(
            session, current, require_complete=True
        )
        if binding.get("studioSourceDigest") != source_state["actual"].get(
            "studioSourceDigest"
        ):
            raise StudioBridgeError(
                "SOURCE_DRIFT",
                "Proposal source content binding no longer matches",
                409,
            )
        return binding

    @staticmethod
    def _receipt_binding(package: dict[str, Any]) -> dict[str, Any] | None:
        review = package.get("reviewDraft", {})
        batch = package.get("updateBatchDraft", {})
        review_binding = review.get("extensions", {}).get(
            "verificationReceiptBinding"
        )
        batch_binding = batch.get("extensions", {}).get(
            "verificationReceiptBinding"
        )
        if review_binding is None and batch_binding is None:
            return None
        if not isinstance(review_binding, dict) or review_binding != batch_binding:
            raise StudioBridgeError(
                "RECEIPT_BINDING_INVALID",
                "Proposal Verification Receipt binding is missing or inconsistent",
                409,
            )
        return review_binding

    def _assert_receipt_proposal_binding(
        self, package: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        binding = self._receipt_binding(package)
        if binding is None:
            raise StudioBridgeError(
                "RECEIPT_BINDING_INVALID", "Receipt Proposal binding is missing", 409
            )
        if binding.get("baseRevision") != current.get("meta", {}).get("revision"):
            raise StudioBridgeError(
                "PANORAMA_CONFLICT", "Panorama Revision changed after Receipt Proposal", 409
            )
        if binding.get("baseDataHash") != compute_data_hash(current):
            raise StudioBridgeError(
                "PANORAMA_CONFLICT", "Panorama Data Hash changed after Receipt Proposal", 409
            )
        if binding.get("operationsHash") != compute_canonical_hash(
            package.get("operations", [])
        ):
            raise StudioBridgeError(
                "RECEIPT_BINDING_INVALID", "Receipt Proposal operations changed", 409
            )
        receipt_hash = self._receipt_hash(binding.get("receiptHash"))
        preview = self._read_artifact(
            self._receipt_preview_path(receipt_hash), "Verification Receipt Preview"
        )
        if (
            preview.get("receiptHash") != receipt_hash
            or preview.get("receiptId") != binding.get("receiptId")
            or preview.get("referenceId") != binding.get("referenceId")
            or preview.get("status") != "ready"
        ):
            raise StudioBridgeError(
                "RECEIPT_BINDING_INVALID", "Receipt Preview no longer matches Proposal", 409
            )
        observation = self._source_observation()
        for field in ("gitHead", "sourceSnapshotHash"):
            if binding.get(field) != observation.get(field):
                raise StudioBridgeError(
                    "SOURCE_DRIFT", f"project source changed after Receipt Proposal ({field})", 409
                )
        actual_digest = self._live_baseline().get("studioSourceDigest")
        if binding.get("studioSourceDigest") != actual_digest:
            raise StudioBridgeError(
                "SOURCE_DRIFT", "project source content changed after Receipt Proposal", 409
            )
        if not isinstance(actual_digest, dict) or not actual_digest.get(
            "coverageComplete"
        ):
            raise StudioBridgeError(
                "SOURCE_COVERAGE_INCOMPLETE",
                "Receipt Proposal source coverage is incomplete",
                409,
            )
        return binding

    def _assert_governance_proposal_binding(
        self, package: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        if self._receipt_binding(package) is not None:
            return self._assert_receipt_proposal_binding(package, current)
        return self._assert_proposal_session_binding(package, current)

    def apply(self, body: dict[str, Any]) -> dict[str, Any]:
        proposal_id = _safe_id(body.get("proposalId"), "proposalId")
        requested_hash = body.get("proposalHash")
        requested_approval_id = body.get("approvalId")
        with self.project_lock:
            wrapper = self._read_artifact(
                self._proposal_path(proposal_id), "Proposal"
            )
            package, _ = parse_proposal_artifact(wrapper)
            proposal_hash = package.get("proposalHash")
            if not isinstance(proposal_hash, str):
                raise StudioBridgeError("PROPOSAL_INVALID", "Proposal Hash is missing", 409)
            approval_id = "APPROVAL-" + proposal_hash[:24].upper()
            if requested_hash is not None and requested_hash != proposal_hash:
                raise StudioBridgeError(
                    "PROPOSAL_HASH_MISMATCH",
                    "requested Proposal Hash is not the stored Proposal Hash",
                    409,
                )
            if requested_approval_id is not None and requested_approval_id != approval_id:
                raise StudioBridgeError(
                    "APPROVAL_ID_MISMATCH",
                    "requested Approval does not bind this Proposal",
                    409,
                )
            approval = self._read_artifact(
                self.approvals / f"{approval_id}.json", "Approval"
            )
            binding = self._assert_governance_proposal_binding(
                package, self._data()
            )
            actual = self._source_observation()
            for field in ("gitHead", "sourceSnapshotHash"):
                if binding.get(field) != actual.get(field):
                    raise StudioBridgeError(
                        "SOURCE_DRIFT",
                        f"project source changed after Proposal ({field})",
                        409,
                    )
            before_presentation = compute_presentation_hash(
                self.panorama.read_text(encoding="utf-8")
            )
            backup, updated, warnings = apply_update_package(
                self.panorama,
                package,
                self._schema(self._data()),
                approval,
            )
            after_presentation = compute_presentation_hash(
                self.panorama.read_text(encoding="utf-8")
            )
            if before_presentation != after_presentation:
                raise StudioBridgeError(
                    "PRESENTATION_CHANGED",
                    "Presentation Layer changed during Apply",
                    500,
                )
            return {
                "proposalId": proposal_id,
                "proposalHash": proposal_hash,
                "revision": updated.get("meta", {}).get("revision"),
                "dataHash": compute_data_hash(updated),
                "backup": str(backup),
                "backupPath": str(backup),
                "panorama": str(self.panorama),
                "presentationHash": after_presentation,
                "warnings": warnings,
            }

    def refresh_facts(self) -> dict[str, Any]:
        with self.project_lock:
            current = self._data()
            if str(current.get("schemaVersion")) != "0.2":
                raise StudioBridgeError(
                    "FACT_REFRESH_REQUIRES_V02",
                    "fact refresh is available only for Schema 0.2",
                    409,
                )
            changed, backup, updated = reconcile_html(
                self.panorama,
                self.project_root,
                standard_paths=[],
                fact_package=None,
            )
            return {
                "changed": changed,
                "revision": updated.get("meta", {}).get("revision"),
                "dataHash": compute_data_hash(updated),
                "backup": str(backup) if backup is not None else None,
            }


class StudioRequestHandler(BaseHTTPRequestHandler):
    """Strict same-origin JSON API and CSP-hardened HTML responder."""

    app: StudioBridge
    protocol_version = "HTTP/1.1"
    server_version = "PanoramaStudioBridge"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        # Do not log capability-bearing browser URLs or request headers.
        return

    def _peer_and_host(self) -> None:
        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except ValueError as exc:
            raise StudioBridgeError("FORBIDDEN_PEER", "invalid peer", 403) from exc
        if peer.version != 4 or not peer.is_loopback:
            raise StudioBridgeError("FORBIDDEN_PEER", "loopback IPv4 is required", 403)
        if self.headers.get("Host") != self.app.host:
            raise StudioBridgeError("INVALID_HOST", "Host header is not allowed", 403)

    def _cap(self) -> None:
        supplied = self.headers.get("X-Panorama-Capability", "")
        if not hmac.compare_digest(supplied, self.app.capability):
            raise StudioBridgeError("UNAUTHORIZED", "capability is invalid", 401)

    def _mutation_gate(self) -> None:
        self._cap()
        csrf = self.headers.get("X-Panorama-CSRF", "")
        if not hmac.compare_digest(csrf, self.app.csrf):
            raise StudioBridgeError("CSRF_REJECTED", "CSRF token is invalid", 403)
        if self.headers.get("Origin") != self.app.origin:
            raise StudioBridgeError("ORIGIN_REJECTED", "Origin is not allowed", 403)
        if self.headers.get("Transfer-Encoding") is not None:
            raise StudioBridgeError(
                "TRANSFER_ENCODING_REJECTED", "Transfer-Encoding is not allowed", 400
            )
        if self.headers.get("Content-Encoding") is not None:
            raise StudioBridgeError(
                "CONTENT_ENCODING_REJECTED", "Content-Encoding is not allowed", 400
            )
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type.lower() != "application/json":
            raise StudioBridgeError(
                "CONTENT_TYPE_REJECTED", "Content-Type must be application/json", 415
            )

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            raise StudioBridgeError("LENGTH_REQUIRED", "Content-Length is required", 411)
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            raise StudioBridgeError("BODY_TOO_LARGE", "request body exceeds 1 MiB", 413)
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StudioBridgeError("INVALID_JSON", "request body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise StudioBridgeError("INVALID_JSON", "request body must be a JSON object")
        return value

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()

    def _json(self, status: int, data: Any) -> None:
        payload = _json_bytes({"ok": True, "data": data})
        self._headers(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _error(self, error: StudioBridgeError) -> None:
        body: dict[str, Any] = {
            "ok": False,
            "error": {"code": error.code, "message": str(error)},
        }
        if error.details is not None:
            body["error"]["details"] = error.details
        payload = _json_bytes(body)
        self._headers(error.status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _dispatch(self, method: str) -> None:
        self._peer_and_host()
        path = urlsplit(self.path).path
        if method == "GET" and path == "/studio/":
            nonce = secrets.token_urlsafe(24)
            payload = _launcher_html(nonce).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self' data:; "
                f"style-src-elem 'self' 'nonce-{nonce}'; "
                "style-src-attr 'unsafe-inline'; "
                f"script-src-elem 'self' 'nonce-{nonce}'; "
                "script-src-attr 'none'; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(payload)
            return
        if method == "GET" and path == "/studio/document":
            self._cap()
            nonce = secrets.token_urlsafe(24)
            source = self.app.panorama.read_text(encoding="utf-8")
            payload = _nonce_html(source, nonce).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self' data:; "
                f"style-src-elem 'self' 'nonce-{nonce}'; "
                "style-src-attr 'unsafe-inline'; "
                f"script-src-elem 'self' 'nonce-{nonce}'; "
                "script-src-attr 'none'; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(payload)
            return
        if not path.startswith(API_PREFIX + "/"):
            raise StudioBridgeError("NOT_FOUND", "route does not exist", 404)
        self._cap()
        if method != "GET":
            self._mutation_gate()
            body = self._body()
        else:
            body = {}

        if method == "GET" and path == f"{API_PREFIX}/health":
            self._json(200, self.app.health())
        elif method == "GET" and path == f"{API_PREFIX}/context":
            self._json(200, self.app.context())
        elif method == "POST" and path == f"{API_PREFIX}/sessions":
            if body:
                raise StudioBridgeError("UNEXPECTED_FIELDS", "session creation body must be empty")
            self._json(201, {"session": self.app.create_session()})
        elif method == "GET" and re.fullmatch(
            rf"{API_PREFIX}/sessions/[A-Z][A-Z0-9._-]{{1,127}}", path
        ):
            session_id = path.rsplit("/", 1)[1]
            self._json(200, {"session": self.app.get_session(session_id)})
        elif method == "PUT" and re.fullmatch(
            rf"{API_PREFIX}/sessions/[A-Z][A-Z0-9._-]{{1,127}}", path
        ):
            session_id = path.rsplit("/", 1)[1]
            saved = self.app.put_session(
                session_id, body.get("expectedRevision"), body.get("session")
            )
            self._json(200, {"session": saved})
        elif method == "POST" and path == f"{API_PREFIX}/formal-validations":
            session_id = _safe_id(body.get("sessionId"), "sessionId")
            candidate_id = body.get("candidateId")
            if candidate_id is not None:
                candidate_id = _safe_id(candidate_id, "candidateId")
            self._json(
                200,
                {
                    "artifact": self.app.formal_validation(
                        session_id, candidate_id
                    )
                },
            )
        elif method == "POST" and path == f"{API_PREFIX}/review-jobs":
            session_id = _safe_id(body.get("sessionId"), "sessionId")
            candidate_id = body.get("candidateId")
            if candidate_id is not None:
                candidate_id = _safe_id(candidate_id, "candidateId")
            self._json(
                202,
                {"job": self.app.create_review_job(session_id, candidate_id)},
            )
        elif method == "GET" and re.fullmatch(
            rf"{API_PREFIX}/jobs/[A-Z][A-Z0-9._-]{{1,127}}", path
        ):
            self._json(200, {"job": self.app.get_job(path.rsplit("/", 1)[1])})
        elif method == "POST" and path == f"{API_PREFIX}/proposals":
            self._json(201, {"proposal": self.app.create_proposal(body)})
        elif method == "POST" and path == f"{API_PREFIX}/verification-receipts/preview":
            self._json(200, self.app.preview_verification_receipt(body))
        elif method == "POST" and path == f"{API_PREFIX}/verification-receipts/proposals":
            self._json(
                201,
                {
                    "proposal": self.app.create_verification_receipt_proposal(
                        body
                    )
                },
            )
        elif method == "POST" and path == f"{API_PREFIX}/approvals":
            self._json(201, {"approval": self.app.record_approval(body)})
        elif method == "POST" and path == f"{API_PREFIX}/apply":
            self._json(200, {"apply": self.app.apply(body)})
        elif method == "POST" and path == f"{API_PREFIX}/facts/refresh":
            if body:
                raise StudioBridgeError(
                    "UNEXPECTED_FIELDS", "fact refresh does not accept paths or facts"
                )
            self._json(200, {"refresh": self.app.refresh_facts()})
        else:
            raise StudioBridgeError("NOT_FOUND", "route does not exist", 404)

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._dispatch("GET")
        except StudioBridgeError as exc:
            self._error(exc)
        except (
            ApplyPatchError,
            ObservationError,
            PanoramaIOError,
            SessionConflictError,
            StudioApprovalError,
            StudioAgentError,
            StudioSessionError,
            VerificationReceiptError,
            ValueError,
        ) as exc:
            self._error(StudioBridgeError("OPERATION_FAILED", str(exc), 409))
        except Exception:
            self._error(StudioBridgeError("INTERNAL_ERROR", "bridge operation failed", 500))

    def do_POST(self) -> None:  # noqa: N802
        self._mutation("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._mutation("PUT")

    def _mutation(self, method: str) -> None:
        try:
            self._dispatch(method)
        except StudioBridgeError as exc:
            self._error(exc)
        except (
            ApplyPatchError,
            ObservationError,
            PanoramaIOError,
            SessionConflictError,
            StudioApprovalError,
            StudioAgentError,
            StudioSessionError,
            VerificationReceiptError,
            ValueError,
        ) as exc:
            self._error(StudioBridgeError("OPERATION_FAILED", str(exc), 409))
        except Exception:
            self._error(StudioBridgeError("INTERNAL_ERROR", "bridge operation failed", 500))

    def do_OPTIONS(self) -> None:  # noqa: N802
        # Deliberately no CORS preflight support.
        try:
            self._peer_and_host()
            self._error(StudioBridgeError("CORS_DISABLED", "CORS is not supported", 405))
        except StudioBridgeError as exc:
            self._error(exc)

    def _method_not_allowed(self) -> None:
        try:
            self._peer_and_host()
            self._error(
                StudioBridgeError(
                    "METHOD_NOT_ALLOWED", "HTTP method is not supported", 405
                )
            )
        except StudioBridgeError as exc:
            self._error(exc)

    do_DELETE = _method_not_allowed  # type: ignore[assignment]
    do_PATCH = _method_not_allowed  # type: ignore[assignment]
    do_TRACE = _method_not_allowed  # type: ignore[assignment]
    do_CONNECT = _method_not_allowed  # type: ignore[assignment]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the loopback-only Panorama Architecture Studio Bridge."
    )
    parser.add_argument("panorama", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument(
        "--codex-cli",
        type=Path,
        default=None,
        help="Explicit trusted Codex executable; HTTP clients cannot change it.",
    )
    parser.add_argument("--agent-timeout", type=int, default=600)
    parser.add_argument(
        "--url-file",
        type=Path,
        default=None,
        help=(
            "Write the launch URL (including its capability fragment) to this "
            "explicit path; protect and delete the file after use."
        ),
    )
    return parser


def _write_url_file(path: Path, url: str) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("--url-file already exists; refusing to overwrite") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(url + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(target, 0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("error: --port must be between 0 and 65535", file=sys.stderr)
        return 2
    app: StudioBridge | None = None
    server: ThreadingHTTPServer | None = None
    try:
        app = StudioBridge(
            args.panorama,
            args.project_root,
            codex_cli=args.codex_cli,
            agent_timeout=args.agent_timeout,
        )
        server = app.bind(args.port)
        url = f"{app.origin}/studio/#cap={app.capability}"
        if args.url_file is not None:
            _write_url_file(args.url_file, url)
            print(
                "Launch URL file contains a bearer capability; permissions are "
                "0600 where supported. Delete it after use.",
                file=sys.stderr,
            )
        print(url, flush=True)
        if args.open_browser:
            webbrowser.open(url, new=2)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, PanoramaIOError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.server_close()
        if app is not None:
            app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
