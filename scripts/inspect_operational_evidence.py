"""Safely inspect bounded project-owned operational metadata."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any

from discover_project_evidence import (
    _format,
    _generated_hint,
    _knowledge_base_hint,
    _machine_state_hint,
    _secret_risk,
    _verification_path_or_name,
    evidence_access_class,
    is_within_root,
)
from panorama_cli import ChineseArgumentParser


REDACTED = "***REDACTED***"
MAX_STRUCTURED_BYTES = 262_144
MAX_MARKDOWN_BYTES = 65_536
MAX_FACT_DEPTH = 12
MAX_FACT_ITEMS = 100

SECRET_KEY_TOKENS = {
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "session",
}

CONTENT_KEYS = {
    "article",
    "body",
    "content",
    "description",
    "document",
    "email",
    "message",
    "note",
    "notes",
    "prompt",
    "raw",
    "source_text",
    "summary_text",
    "text",
    "title",
}

SAFE_OPERATIONAL_KEYS = {
    "approval",
    "approvals",
    "approved",
    "available",
    "branch",
    "count",
    "created",
    "current",
    "cursor",
    "dirty",
    "enabled",
    "entries",
    "errors",
    "events",
    "failed",
    "health",
    "head",
    "id",
    "ids",
    "items",
    "lint",
    "metadata",
    "passed",
    "progress",
    "records",
    "registered",
    "relationships",
    "result",
    "results",
    "retrieval",
    "run",
    "runs",
    "state",
    "status",
    "tasks",
    "test",
    "tests",
    "timestamp",
    "timestamps",
    "type",
    "updated",
    "version",
    "warnings",
}


class OperationalInspectionError(RuntimeError):
    """A safe parser or bounded-inspection failure."""


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _pointer(path: str, key: str | int) -> str:
    token = str(key).replace("~", "~0").replace("/", "~1")
    return f"{path}/{token}" if path else f"/{token}"


def _normalized_key(key: Any) -> str:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key).strip())
    return re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")


def _secret_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    parts = set(normalized.split("_"))
    return bool(
        normalized in SECRET_KEY_TOKENS
        or normalized.endswith(("_token", "_secret", "_password", "_key"))
        or parts
        & {
            "password",
            "passwd",
            "token",
            "secret",
            "credential",
            "authorization",
            "cookie",
            "session",
        }
    )


def redact_secret_values(
    value: Any,
    *,
    path: str = "",
    redactions: list[str] | None = None,
    depth: int = 0,
) -> tuple[Any, list[str]]:
    """Recursively redact secret-shaped keys without exposing their values."""

    found = redactions if redactions is not None else []
    if depth > MAX_FACT_DEPTH:
        return "[TRUNCATED_DEPTH]", found
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_FACT_ITEMS:
                result["_truncatedItems"] = len(value) - MAX_FACT_ITEMS
                break
            child_path = _pointer(path, key)
            if _secret_key(key):
                result[str(key)] = REDACTED
                found.append(child_path)
            else:
                result[str(key)], found = redact_secret_values(
                    item,
                    path=child_path,
                    redactions=found,
                    depth=depth + 1,
                )
        return result, found
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value[:MAX_FACT_ITEMS]):
            child, found = redact_secret_values(
                item,
                path=_pointer(path, index),
                redactions=found,
                depth=depth + 1,
            )
            result.append(child)
        if len(value) > MAX_FACT_ITEMS:
            result.append({"_truncatedItems": len(value) - MAX_FACT_ITEMS})
        return result, found
    if isinstance(value, (datetime, date)):
        return value.isoformat(), found
    if isinstance(value, float) and not math.isfinite(value):
        return "[NON_FINITE_NUMBER]", found
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value, found
    return f"[UNSUPPORTED_TYPE:{type(value).__name__}]", found


def _safe_operational_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if normalized in SAFE_OPERATIONAL_KEYS:
        return True
    return normalized.endswith(
        (
            "_at",
            "_count",
            "_id",
            "_ids",
            "_state",
            "_status",
            "_time",
            "_timestamp",
            "_version",
        )
    )


def extract_operational_facts(
    value: Any,
    *,
    path: str = "",
    omitted: list[str] | None = None,
    depth: int = 0,
) -> tuple[Any, list[str]]:
    """Keep status/ID/time/relationship facts and omit likely content bodies."""

    omitted_paths = omitted if omitted is not None else []
    if depth > MAX_FACT_DEPTH:
        return "[TRUNCATED_DEPTH]", omitted_paths
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_FACT_ITEMS:
                result["_truncatedItems"] = len(value) - MAX_FACT_ITEMS
                break
            normalized = _normalized_key(key)
            child_path = _pointer(path, key)
            if item == REDACTED:
                result[str(key)] = REDACTED
                continue
            if normalized in CONTENT_KEYS:
                omitted_paths.append(child_path)
                continue
            child, omitted_paths = extract_operational_facts(
                item,
                path=child_path,
                omitted=omitted_paths,
                depth=depth + 1,
            )
            if _safe_operational_key(key):
                result[str(key)] = child
            elif isinstance(item, (dict, list)) and child not in ({}, []):
                result[str(key)] = child
            elif item not in (None, "", [], {}):
                omitted_paths.append(child_path)
        return result, omitted_paths
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value[:MAX_FACT_ITEMS]):
            child, omitted_paths = extract_operational_facts(
                item,
                path=_pointer(path, index),
                omitted=omitted_paths,
                depth=depth + 1,
            )
            if child not in ({}, []):
                result.append(child)
        if len(value) > MAX_FACT_ITEMS:
            result.append({"_truncatedItems": len(value) - MAX_FACT_ITEMS})
        return result, omitted_paths
    if isinstance(value, str):
        return value[:512] + ("…" if len(value) > 512 else ""), omitted_paths
    return value, omitted_paths


def _yaml_load(text: str) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise OperationalInspectionError(
            "YAML inspection requires PyYAML; the inspector will not guess-parse it."
        ) from exc

    class NoAliasSafeLoader(yaml.SafeLoader):
        def compose_node(self, parent: Any, index: Any) -> Any:
            if self.check_event(yaml.AliasEvent):
                raise yaml.YAMLError("YAML aliases are not accepted by the bounded inspector")
            return super().compose_node(parent, index)

    try:
        return yaml.load(text, Loader=NoAliasSafeLoader)
    except yaml.YAMLError as exc:
        raise OperationalInspectionError(f"invalid or unsupported YAML: {exc}") from exc


def _toml_load(text: str) -> Any:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef,import-not-found]
        except ImportError as exc:
            raise OperationalInspectionError(
                "TOML inspection requires tomllib (Python 3.11+) or tomli."
            ) from exc
    try:
        return tomllib.loads(text)
    except Exception as exc:
        raise OperationalInspectionError(f"invalid TOML: {exc}") from exc


def _parse_structured(file_format: str, text: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        if file_format == "json":
            return json.loads(text, parse_constant=reject_constant)
        if file_format == "jsonl":
            return [
                json.loads(line, parse_constant=reject_constant)
                for line in text.splitlines()
                if line.strip()
            ]
        if file_format == "yaml":
            return _yaml_load(text)
        if file_format == "toml":
            return _toml_load(text)
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
        raise OperationalInspectionError(f"invalid {file_format}: {exc}") from exc
    raise OperationalInspectionError(f"unsupported operational format: {file_format}")


def _frontmatter(text: str) -> tuple[Any | None, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in {"---", "..."}:
            return _yaml_load("\n".join(lines[1:index])), "\n".join(lines[index + 1 :])
    raise OperationalInspectionError("generated Markdown frontmatter is not terminated")


def _markdown_status_facts(text: str) -> tuple[dict[str, Any], list[str], list[str]]:
    metadata, body = _frontmatter(text)
    redactions: list[str] = []
    omitted: list[str] = []
    facts: dict[str, Any] = {}
    if metadata is not None:
        redacted, redactions = redact_secret_values(metadata, path="/frontmatter")
        selected, omitted = extract_operational_facts(
            redacted, path="/frontmatter"
        )
        facts["frontmatter"] = selected

    status_lines = []
    for line_number, line in enumerate(body.splitlines()[:200], start=1):
        candidate = line.strip().lstrip("#-* ").strip()
        if ":" not in candidate:
            continue
        key, value = (part.strip() for part in candidate.split(":", 1))
        if _secret_key(key):
            status_lines.append({"key": key, "value": REDACTED})
            redactions.append(f"/statusLines/{line_number}/{key}")
        elif _safe_operational_key(key):
            status_lines.append({"key": key, "value": value[:512]})
        else:
            omitted.append(f"/markdown/line/{line_number}")
        if len(status_lines) >= 40:
            break
    facts["statusLines"] = status_lines
    return facts, sorted(set(redactions)), sorted(set(omitted))


def _summarize(facts: Any) -> dict[str, Any]:
    ids: list[str] = []
    timestamps: list[str] = []
    statuses: Counter[str] = Counter()

    def visit(value: Any, key: str = "") -> None:
        normalized = _normalized_key(key)
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and value != REDACTED:
            if normalized == "id" or normalized.endswith("_id"):
                ids.append(value)
            if normalized in {"status", "state", "result"} or normalized.endswith(
                ("_status", "_state")
            ):
                statuses[value] += 1
            if normalized.endswith(("_at", "_time", "_timestamp")):
                timestamps.append(value)

    visit(facts)
    return {
        "idCount": len(set(ids)),
        "ids": sorted(set(ids))[:50],
        "statusCounts": dict(sorted(statuses.items())),
        "timestamps": sorted(set(timestamps))[:50],
    }


def _provenance(relative: Path) -> str:
    lowered_parts = {part.lower() for part in relative.parts[:-1]}
    if _machine_state_hint(relative) and (
        "generated" in lowered_parts or _generated_hint(relative, "")
    ):
        return "generated_current_state"
    if _verification_path_or_name(relative) or lowered_parts & {
        "reports",
        "test-results",
        "test_reports",
    }:
        return "historical_test"
    return "observed_now"


def _base_result(path: str, access_class: str, file_format: str) -> dict[str, Any]:
    return {
        "path": path,
        "accessClass": access_class,
        "format": file_format,
        "safeToRead": False,
        "facts": {},
        "summary": {},
        "redactions": [],
        "omittedFields": [],
        "warnings": [],
        "contentHash": None,
        "contentHashScope": "none",
        "observedAt": None,
        "provenance": None,
        "truncated": False,
    }


def inspect_operational_evidence(
    project_root: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
    *,
    max_structured_bytes: int = MAX_STRUCTURED_BYTES,
    max_markdown_bytes: int = MAX_MARKDOWN_BYTES,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Inspect one candidate without escaping the project or exposing content."""

    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    supplied = Path(relative_path)
    if supplied.is_absolute():
        result = _base_result(str(supplied), "EXTERNAL_PRIVATE_DATA", _format(supplied))
        result["warnings"].append("Absolute and external paths are never inspected automatically.")
        return result

    lexical = root / supplied
    relative = Path(supplied.as_posix())
    file_format = _format(relative)
    access_class = evidence_access_class(relative, file_format)
    result = _base_result(relative.as_posix(), access_class, file_format)

    if lexical.is_symlink():
        result["warnings"].append("Symbolic links are not inspected.")
        return result
    try:
        resolved = lexical.resolve(strict=True)
    except OSError:
        result["warnings"].append("Candidate does not exist or is unreadable.")
        return result
    if not is_within_root(resolved, root):
        result["accessClass"] = "EXTERNAL_PRIVATE_DATA"
        result["warnings"].append("Resolved path is outside the project root.")
        return result
    if not resolved.is_file():
        result["warnings"].append("Candidate is not a regular file.")
        return result
    if _secret_risk(relative):
        result["accessClass"] = "SECRET"
        result["warnings"].append("Secret-risk paths remain metadata-only.")
        return result
    if access_class == "PROJECT_CONTENT" or _knowledge_base_hint(relative) and access_class != "PROJECT_OPERATIONAL_METADATA":
        result["accessClass"] = "PROJECT_CONTENT"
        result["warnings"].append("Project knowledge/content is not operational metadata.")
        return result
    if access_class != "PROJECT_OPERATIONAL_METADATA":
        result["warnings"].append("The operational inspector only reads qualified operational candidates.")
        return result

    limit = max_markdown_bytes if file_format == "markdown" else max_structured_bytes
    before = resolved.stat()
    with resolved.open("rb") as handle:
        payload = handle.read(limit + 1)
    after = resolved.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        result["warnings"].append("Candidate changed during inspection; facts were discarded.")
        return result
    truncated = len(payload) > limit
    inspected = payload[:limit]
    result["truncated"] = truncated
    result["contentHash"] = "sha256:" + hashlib.sha256(inspected).hexdigest()
    result["contentHashScope"] = "bounded_prefix" if truncated else "complete_file"
    result["observedAt"] = observed_at or _now()
    result["provenance"] = _provenance(relative)

    try:
        text = inspected.decode("utf-8")
    except UnicodeDecodeError:
        result["warnings"].append("Operational metadata must be UTF-8.")
        return result
    if truncated and file_format != "markdown":
        result["warnings"].append("Structured metadata exceeds the bounded read limit.")
        return result

    try:
        if file_format == "markdown":
            facts, redactions, omitted = _markdown_status_facts(text)
        else:
            parsed = _parse_structured(file_format, text)
            redacted, redactions = redact_secret_values(parsed)
            facts, omitted = extract_operational_facts(redacted)
    except OperationalInspectionError as exc:
        result["warnings"].append(str(exc))
        return result

    result["safeToRead"] = True
    result["facts"] = facts
    result["summary"] = _summarize(facts)
    result["redactions"] = sorted(set(redactions))
    result["omittedFields"] = sorted(set(omitted))
    if truncated:
        result["warnings"].append("Generated status was extracted from a bounded prefix.")
    if omitted:
        result["warnings"].append("Non-operational content fields were omitted.")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="安全、受限地读取一个项目自有 Operational Metadata Candidate。"
    )
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--path", required=True, dest="relative_path")
    parser.add_argument("--max-structured-bytes", type=int, default=MAX_STRUCTURED_BYTES)
    parser.add_argument("--max-markdown-bytes", type=int, default=MAX_MARKDOWN_BYTES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = inspect_operational_evidence(
            args.project_root,
            args.relative_path,
            max_structured_bytes=args.max_structured_bytes,
            max_markdown_bytes=args.max_markdown_bytes,
        )
    except (OSError, ValueError) as exc:
        print(f"Operational Evidence 错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["safeToRead"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
