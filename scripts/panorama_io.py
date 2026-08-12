"""Low-level I/O helpers for a single-file Project Panorama.

Only the JSON payload inside the stable Panorama data anchor may change during
ordinary data updates.  This module deliberately has no dependency on the
renderer or on third-party packages.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA_START_MARKER = "<!-- PANORAMA_DATA_START -->"
DATA_END_MARKER = "<!-- PANORAMA_DATA_END -->"
DATA_SCRIPT_ID = "project-panorama-data"
PRESENTATION_SENTINEL = "__PANORAMA_DATA_PAYLOAD__"

_SCRIPT_RE = re.compile(
    r"<script\s+"
    r"(?=[^>]*\bid\s*=\s*([\"'])project-panorama-data\1)"
    r"(?=[^>]*\btype\s*=\s*([\"'])application/json\2)"
    r"[^>]*>(?P<payload>.*?)</script\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)


class PanoramaIOError(ValueError):
    """Raised when the stable Panorama data anchor is missing or malformed."""


def _read_text(path: Path) -> str:
    """Read UTF-8 without newline translation so presentation bytes stay stable."""

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError as exc:
        raise PanoramaIOError(f"Panorama 不是有效 UTF-8 文件：{path}") from exc


def _locate_payload(html_text: str) -> tuple[int, int]:
    """Return the JSON payload span after validating the complete stable anchor."""

    if html_text.count(DATA_START_MARKER) != 1:
        raise PanoramaIOError(
            f"必须且只能有一个 {DATA_START_MARKER!r} 标记。"
        )
    if html_text.count(DATA_END_MARKER) != 1:
        raise PanoramaIOError(f"必须且只能有一个 {DATA_END_MARKER!r} 标记。")

    start_marker_at = html_text.index(DATA_START_MARKER)
    end_marker_at = html_text.index(DATA_END_MARKER)
    if end_marker_at <= start_marker_at:
        raise PanoramaIOError("Panorama 数据标记顺序错误。")

    anchor_start = start_marker_at + len(DATA_START_MARKER)
    anchor_text = html_text[anchor_start:end_marker_at]
    matches = list(_SCRIPT_RE.finditer(anchor_text))
    if len(matches) != 1:
        raise PanoramaIOError(
            "数据锚点中必须且只能有一个 "
            '<script id="project-panorama-data" type="application/json"> 区块。'
        )

    match = matches[0]
    if anchor_text[: match.start()].strip() or anchor_text[match.end() :].strip():
        raise PanoramaIOError("Panorama 数据标记内部存在非预期内容。")

    payload_start = anchor_start + match.start("payload")
    payload_end = anchor_start + match.end("payload")
    return payload_start, payload_end


def _json_for_html(data: dict[str, Any], newline: str) -> str:
    """Serialize data for an application/json script without a closing-tag hazard."""

    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    rendered = re.sub(r"</script", r"<\\/script", rendered, flags=re.IGNORECASE)
    if newline != "\n":
        rendered = rendered.replace("\n", newline)
    return f"{newline}{rendered}{newline}"


def extract_data(html_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Extract and decode the embedded Panorama JSON object."""

    path = Path(html_path)
    html_text = _read_text(path)
    payload_start, payload_end = _locate_payload(html_text)
    payload = html_text[payload_start:payload_end].strip()
    if not payload:
        raise PanoramaIOError("Panorama 数据 payload 为空。")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PanoramaIOError(
            f"{DATA_SCRIPT_ID} 中的 JSON 无效，位于第 {exc.lineno} 行、"
            f"第 {exc.colno} 列：{exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise PanoramaIOError("Panorama 数据 payload 必须是 JSON 对象。")
    return data


def compute_data_hash(data: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 hash of the logical JSON data."""

    if not isinstance(data, dict):
        raise TypeError("Panorama 数据必须是字典。")
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def compute_canonical_hash(value: Any) -> str:
    """Return SHA-256 for any canonical JSON-compatible value."""

    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def compute_presentation_hash(html_text: str) -> str:
    """Hash every HTML byte except the replaceable JSON payload."""

    payload_start, payload_end = _locate_payload(html_text)
    presentation = (
        html_text[:payload_start] + PRESENTATION_SENTINEL + html_text[payload_end:]
    )
    return hashlib.sha256(presentation.encode("utf-8")).hexdigest()


def atomic_write(path: str | os.PathLike[str], text: str) -> None:
    """Atomically replace *path* with UTF-8 text using a sibling temp file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, target)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def replace_data(
    html_path: str | os.PathLike[str],
    data: dict[str, Any],
    output_path: str | os.PathLike[str] | None = None,
) -> Path:
    """Replace only the embedded JSON payload and preserve presentation exactly."""

    if not isinstance(data, dict):
        raise TypeError("Panorama 数据必须是字典。")

    source = Path(html_path)
    target = Path(output_path) if output_path is not None else source
    original = _read_text(source)
    before_hash = compute_presentation_hash(original)
    payload_start, payload_end = _locate_payload(original)
    newline = "\r\n" if "\r\n" in original else "\n"
    replacement = _json_for_html(data, newline)
    updated = original[:payload_start] + replacement + original[payload_end:]

    # Re-parse before writing, catching unsafe or malformed serialization.
    new_start, new_end = _locate_payload(updated)
    decoded = json.loads(updated[new_start:new_end])
    if decoded != data:
        raise PanoramaIOError("序列化后的 Panorama 数据无法精确往返。")
    after_hash = compute_presentation_hash(updated)
    if after_hash != before_hash:
        raise PanoramaIOError("数据替换过程中 Presentation Layer 发生变化。")

    atomic_write(target, updated)
    return target


def create_backup(html_path: str | os.PathLike[str]) -> Path:
    """Create a timestamped sibling backup and return its path."""

    source = Path(html_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = source.with_name(f"{source.stem}.backup-{stamp}{source.suffix}")
    shutil.copy2(source, backup)
    return backup


__all__ = [
    "DATA_END_MARKER",
    "DATA_SCRIPT_ID",
    "DATA_START_MARKER",
    "PanoramaIOError",
    "atomic_write",
    "compute_canonical_hash",
    "compute_data_hash",
    "compute_presentation_hash",
    "create_backup",
    "extract_data",
    "replace_data",
]
