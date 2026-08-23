"""Bounded, read-only source topology extraction for Panorama V0.6.

The extractor records file metadata and typed dependency candidates.  It never
persists source bodies, promotes files to formal Modules, or treats static
imports as observed runtime calls or sequence order.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from panorama_io import compute_canonical_hash
from stack_adapters import (
    LANGUAGE_ADAPTERS,
    MANIFEST_ADAPTERS,
    configuration_kind,
    dependency_stack_matches,
    filename_stack_match,
    manifest_name_map,
    manifest_suffix,
    registry_projection,
    scan_language_source,
    scan_multistack_manifest,
    source_suffix_map,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVATION_SCHEMA = ROOT / "schema" / "source-topology-observation.schema.v0.2.json"
LEGACY_OBSERVATION_SCHEMA = ROOT / "schema" / "source-topology-observation.schema.v0.1.json"
RECEIPT_SCHEMA = ROOT / "schema" / "extraction-receipt.schema.v0.1.json"
LOSS_SCHEMA = ROOT / "schema" / "transformation-loss-report.schema.v0.1.json"
PRODUCER = {"id": "panorama-source-extractor", "version": "0.85.0"}

MAX_FILES = 20_000
MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_DEPTH = 32

IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".panorama-work",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".venv",
    "venv",
    "env",
    "evals",
}
SECRET_DIRECTORIES = {".ssh", ".gnupg", "secrets", "credentials"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
SOURCE_SUFFIXES = source_suffix_map()
SOURCE_LIKE_UNSUPPORTED_SUFFIXES = {
    ".dart", ".ex", ".exs", ".fs", ".fsx", ".lua", ".m", ".mm", ".r", ".sol",
}
MANIFEST_NAMES = manifest_name_map()
CONFIGURATION_SUFFIXES = {
    ".properties": ("configuration", "properties"),
}
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class SourceTopologyError(ValueError):
    """The requested extraction cannot produce a trustworthy observation."""


def _schema_validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_errors(value: dict[str, Any], path: Path) -> list[str]:
    errors = sorted(
        _schema_validator(path).iter_errors(value),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    rendered: list[str] = []
    for error in errors:
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        rendered.append(f"{pointer or '/'}: {error.message}")
    return rendered


def _derived_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{compute_canonical_hash(value)[:24].upper()}"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _secret_risk(relative: Path) -> bool:
    lowered_parts = [part.casefold() for part in relative.parts]
    name = relative.name.casefold()
    if any(part in SECRET_DIRECTORIES for part in lowered_parts[:-1]):
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if relative.suffix.casefold() in SECRET_SUFFIXES:
        return True
    return name in {
        "id_rsa",
        "id_ed25519",
        "credentials.json",
        "service-account.json",
    }


def _supported_kind(relative: Path) -> tuple[str, str] | None:
    by_name = MANIFEST_NAMES.get(relative.name.casefold())
    if by_name is not None:
        return by_name
    by_manifest_suffix = manifest_suffix(relative)
    if by_manifest_suffix is not None:
        return by_manifest_suffix
    by_configuration = CONFIGURATION_SUFFIXES.get(relative.suffix.casefold())
    if by_configuration is not None:
        return by_configuration
    by_multistack_configuration = configuration_kind(relative)
    if by_multistack_configuration is not None:
        return by_multistack_configuration
    return SOURCE_SUFFIXES.get(relative.suffix.casefold())


def _git_inventory(root: Path) -> tuple[str | None, list[Path] | None]:
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
            return None, None
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-c", "-o", "--exclude-standard", "-z"],
            check=False,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None, None
    if head.returncode != 0 or listed.returncode != 0:
        return None, None
    paths = [Path(os.fsdecode(item)) for item in listed.stdout.split(b"\0") if item]
    return head.stdout.strip(), paths


def _filesystem_inventory(root: Path) -> tuple[list[Path], int]:
    paths: list[Path] = []
    ignored = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        kept_directories: list[str] = []
        for name in sorted(directories):
            child = directory_path / name
            relative = child.relative_to(root)
            if name.casefold() in IGNORED_DIRECTORIES or _secret_risk(relative / "x"):
                ignored += 1
                continue
            if _is_link_or_reparse(child):
                ignored += 1
                continue
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(files):
            paths.append((directory_path / name).relative_to(root))
    return paths, ignored


def _inventory(root: Path) -> tuple[list[dict[str, Any]], dict[str, int], str | None]:
    git_head, candidates = _git_inventory(root)
    ignored = 0
    if candidates is None:
        candidates, ignored = _filesystem_inventory(root)

    inventory: list[dict[str, Any]] = []
    unsupported = 0
    unsupported_source = 0
    secret_risk = 0
    total_bytes = 0
    supported_discovered = 0
    for relative in sorted(set(candidates), key=lambda path: path.as_posix()):
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceTopologyError(f"仓库清单包含越界路径：{relative}")
        if len(relative.parts) > MAX_DEPTH:
            raise SourceTopologyError(f"源码路径深度超过 {MAX_DEPTH}：{relative.as_posix()}")
        if any(part.casefold() in IGNORED_DIRECTORIES for part in relative.parts[:-1]):
            ignored += 1
            continue
        if _secret_risk(relative):
            secret_risk += 1
            continue
        kind = _supported_kind(relative)
        if kind is None:
            unsupported += 1
            if relative.suffix.casefold() in SOURCE_LIKE_UNSUPPORTED_SUFFIXES:
                unsupported_source += 1
            continue
        supported_discovered += 1
        if supported_discovered > MAX_FILES:
            raise SourceTopologyError(f"源码候选超过 {MAX_FILES} 个文件。")
        lexical = root / relative
        if _is_link_or_reparse(lexical):
            raise SourceTopologyError(f"拒绝读取 in-scope link/reparse path：{relative.as_posix()}")
        try:
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise SourceTopologyError(f"源码路径越出 project root：{relative.as_posix()}") from exc
        if not resolved.is_file():
            raise SourceTopologyError(f"源码候选不是普通文件：{relative.as_posix()}")
        size = resolved.stat().st_size
        if size > MAX_FILE_BYTES:
            raise SourceTopologyError(
                f"源码文件超过 {MAX_FILE_BYTES} bytes：{relative.as_posix()}"
            )
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise SourceTopologyError(f"源码总量超过 {MAX_TOTAL_BYTES} bytes。")
        body = resolved.read_bytes()
        if len(body) != size:
            raise SourceTopologyError(f"读取期间源码大小发生变化：{relative.as_posix()}")
        inventory.append(
            {
                "path": relative.as_posix(),
                "kind": kind[0],
                "language": kind[1],
                "size": size,
                "sha256": _sha256_bytes(body),
                "parseStatus": "not_applicable",
                "accessClass": "PROJECT_CONTENT",
                "_body": body,
            }
        )
    return inventory, {
        "ignored": ignored,
        "unsupported": unsupported,
        "unsupportedSource": unsupported_source,
        "secretRisk": secret_risk,
        "supportedDiscovered": supported_discovered,
    }, git_head


def _source_digest(inventory: Iterable[dict[str, Any]]) -> str:
    projection = [
        {"path": item["path"], "size": item["size"], "sha256": item["sha256"]}
        for item in inventory
    ]
    return compute_canonical_hash(projection)


def _evidence_pin(
    item: dict[str, Any], *, line: int | None, column: int | None, revision: str
) -> dict[str, Any]:
    identity = {
        "path": item["path"],
        "line": line,
        "column": column,
        "digest": item["sha256"],
    }
    return {
        "evidenceId": _derived_id("SRCEVID", identity),
        "kind": "source_location",
        "ref": item["path"],
        "line": line,
        "column": column,
        "revision": revision,
        "digest": item["sha256"],
        "accessClass": "PROJECT_CONTENT",
        "freshness": "current",
    }


def _file_element(item: dict[str, Any], revision: str) -> dict[str, Any]:
    if item["kind"] == "manifest":
        kind = "manifest"
    elif item["kind"] == "configuration":
        kind = "configuration"
    else:
        kind = "source_file"
    return {
        "id": _derived_id("SRCEL", {"kind": kind, "path": item["path"]}),
        "kind": kind,
        "name": Path(item["path"]).name,
        "path": item["path"],
        "language": item["language"],
        "parseStatus": item["parseStatus"],
        "factStatus": "observed",
        "authority": "observed",
        "confidence": "high",
        "evidencePins": [_evidence_pin(item, line=None, column=None, revision=revision)],
        "attributes": {"size": item["size"], "sha256": item["sha256"]},
    }


def _merge_pin(element: dict[str, Any], pin: dict[str, Any]) -> None:
    if pin["evidenceId"] not in {item["evidenceId"] for item in element["evidencePins"]}:
        element["evidencePins"].append(pin)
        element["evidencePins"].sort(key=lambda item: item["evidenceId"])


def _target_element(
    elements: dict[str, dict[str, Any]],
    *,
    kind: str,
    name: str,
    pin: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    identity = {"kind": kind, "name": name}
    element_id = _derived_id("SRCEL", identity)
    if element_id not in elements:
        elements[element_id] = {
            "id": element_id,
            "kind": kind,
            "name": name,
            "path": None,
            "language": None,
            "parseStatus": "unresolved" if kind == "unresolved_target" else "not_applicable",
            "factStatus": "unknown" if kind == "unresolved_target" else "derived",
            "authority": "unknown" if kind == "unresolved_target" else "inferred",
            "confidence": "unknown" if kind == "unresolved_target" else "medium",
            "evidencePins": [pin],
            "attributes": {"reason": reason} if reason else {},
        }
    else:
        _merge_pin(elements[element_id], pin)
    return elements[element_id]


def _relation(
    *,
    kind: str,
    name: str,
    source_id: str,
    target_id: str,
    resolution: str,
    pin: dict[str, Any],
    fact_status: str,
    authority: str,
    confidence: str,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    identity = {
        "kind": kind,
        "source": source_id,
        "target": target_id,
        "pin": pin["evidenceId"],
        "name": name,
    }
    return {
        "id": _derived_id("SRCREL", identity),
        "kind": kind,
        "name": name,
        "fromElementId": source_id,
        "toElementId": target_id,
        "resolution": resolution,
        "factStatus": fact_status,
        "authority": authority,
        "confidence": confidence,
        "evidencePins": [pin],
        "attributes": attributes,
    }


def _python_module_index(
    inventory: list[dict[str, Any]], file_elements: dict[str, dict[str, Any]]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in inventory:
        if item["language"] != "python":
            continue
        path = Path(item["path"])
        parts = list(path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts:
            result[".".join(parts)] = file_elements[item["path"]]["id"]
    return result


def _type_checking_test(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "TYPE_CHECKING"
        or isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
        and node.attr == "TYPE_CHECKING"
    )


def _is_type_only(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.If) and _type_checking_test(parent.test):
            if any(current is child for child in parent.body):
                return True
        current = parent
    return False


def _resolve_python_spec(
    *,
    source_path: str,
    module: str | None,
    level: int,
    alias: str | None,
    module_index: dict[str, str],
) -> tuple[str | None, str, bool]:
    if level:
        path = Path(source_path)
        package_parts = list(path.parent.parts)
        if path.name == "__init__.py":
            package_parts = list(path.parent.parts)
        remove = max(level - 1, 0)
        if remove > len(package_parts):
            return None, module or alias or "relative", True
        base = package_parts[: len(package_parts) - remove]
        if module:
            base.extend(module.split("."))
        spec = ".".join(base)
        candidates = [f"{spec}.{alias}" if spec and alias else spec]
        if alias:
            candidates.append(spec)
        for candidate in candidates:
            if candidate in module_index:
                return module_index[candidate], candidate, True
        return None, candidates[0] or "relative", True
    spec = module or alias or "unknown"
    candidates = [f"{spec}.{alias}" if module and alias else spec, spec]
    for candidate in candidates:
        if candidate in module_index:
            return module_index[candidate], candidate, False
    root_name = spec.split(".", 1)[0]
    if root_name in module_index:
        return module_index[root_name], root_name, False
    return None, spec, False


def _parse_python(
    item: dict[str, Any],
    *,
    file_elements: dict[str, dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    module_index: dict[str, str],
    revision: str,
    losses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_element = file_elements[item["path"]]
    try:
        text = item["_body"].decode("utf-8")
        tree = ast.parse(text, filename=item["path"], type_comments=True)
    except (UnicodeDecodeError, SyntaxError) as exc:
        item["parseStatus"] = "failed"
        source_element["parseStatus"] = "failed"
        losses.append(
            {
                "kind": "source_file.parse_failed",
                "sourceRef": item["path"],
                "severity": "partial",
                "reason": f"Python parser could not parse this file: {type(exc).__name__}",
                "preservedAs": f"inventory:{item['path']}",
            }
        )
        return []

    item["parseStatus"] = "parsed"
    source_element["parseStatus"] = "parsed"
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    relations: list[dict[str, Any]] = []

    def add_candidate(
        node: ast.AST,
        *,
        module: str | None,
        level: int = 0,
        alias: str | None = None,
        dynamic: bool = False,
        unknown_dynamic: bool = False,
    ) -> None:
        line = getattr(node, "lineno", None)
        column = getattr(node, "col_offset", None)
        pin = _evidence_pin(item, line=line, column=column, revision=revision)
        local_id, spec, relative = _resolve_python_spec(
            source_path=item["path"],
            module=module,
            level=level,
            alias=alias,
            module_index=module_index,
        )
        if unknown_dynamic:
            target = _target_element(
                elements,
                kind="unresolved_target",
                name=f"dynamic import at {item['path']}:{line or 0}",
                pin=pin,
                reason="dynamic_import_expression_not_static",
            )
            resolution = "dynamic"
            specifier: str | None = None
        elif local_id is not None:
            target = elements[local_id]
            resolution = "dynamic" if dynamic else "resolved"
            specifier = spec
        elif relative:
            target = _target_element(
                elements,
                kind="unresolved_target",
                name=spec,
                pin=pin,
                reason="relative_import_target_not_found",
            )
            resolution = "dynamic" if dynamic else "unresolved"
            specifier = spec
        else:
            external_name = spec.split(".", 1)[0]
            target = _target_element(
                elements,
                kind="external_package",
                name=external_name,
                pin=pin,
            )
            resolution = "dynamic" if dynamic else "external"
            specifier = spec
        type_only = _is_type_only(node, parents)
        kind = "dynamic_import" if dynamic else "type_import" if type_only else "import"
        relations.append(
            _relation(
                kind=kind,
                name=(f"{kind}: {specifier}" if specifier else kind),
                source_id=source_element["id"],
                target_id=target["id"],
                resolution=resolution,
                pin=pin,
                fact_status="derived",
                authority="inferred",
                confidence="high" if not unknown_dynamic else "low",
                attributes={
                    "adapterId": "python-ast-imports",
                    "parser": "python_ast",
                    "specifier": specifier,
                    "typeOnly": type_only,
                    "dynamic": dynamic,
                    "runtimeObserved": False,
                    "sequenceOrder": None,
                },
            )
        )
        if resolution in {"unresolved", "dynamic"}:
            losses.append(
                {
                    "kind": "source_relation.dynamic_or_unresolved",
                    "sourceRef": f"{item['path']}:{line or 0}",
                    "severity": "partial" if unknown_dynamic or relative else "informational",
                    "reason": "Static analysis cannot prove the target or runtime behavior.",
                    "preservedAs": f"relation:{relations[-1]['id']}",
                }
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_candidate(node, module=alias.name)
        elif isinstance(node, ast.ImportFrom):
            if not node.names:
                add_candidate(node, module=node.module, level=node.level)
            else:
                for alias in node.names:
                    add_candidate(
                        node,
                        module=node.module,
                        level=node.level,
                        alias=None if alias.name == "*" else alias.name,
                    )
        elif isinstance(node, ast.Call):
            dynamic_name = None
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                dynamic_name = "__import__"
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
            ):
                dynamic_name = "importlib.import_module"
            if dynamic_name is not None:
                value = (
                    node.args[0].value
                    if node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    else None
                )
                add_candidate(
                    node,
                    module=value,
                    dynamic=True,
                    unknown_dynamic=value is None,
                )
    return relations


def _strip_js_comments(text: str) -> str:
    chars = list(text)
    index = 0
    quote: str | None = None
    while index < len(chars):
        char = chars[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "/" and index + 1 < len(chars) and chars[index + 1] == "/":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < len(chars) and chars[index] not in {"\r", "\n"}:
                chars[index] = " "
                index += 1
            continue
        if char == "/" and index + 1 < len(chars) and chars[index + 1] == "*":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index + 1 < len(chars):
                if chars[index] == "*" and chars[index + 1] == "/":
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    break
                if chars[index] not in {"\r", "\n"}:
                    chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


JS_PATTERNS = (
    (
        "type_import",
        re.compile(r"\bimport\s+type\b[^;\n]*?\bfrom\s*(['\"])([^'\"]+)\1"),
    ),
    (
        "dynamic_import",
        re.compile(r"\bimport\s*\(\s*(['\"])([^'\"]+)\1\s*\)"),
    ),
    (
        "import",
        re.compile(r"\brequire\s*\(\s*(['\"])([^'\"]+)\1\s*\)"),
    ),
    (
        "import",
        re.compile(
            r"\b(?:import|export)\s+(?!type\b)(?:[^;\n]*?\bfrom\s*)?(['\"])([^'\"]+)\1"
        ),
    ),
)


def _resolve_js_relative(
    source_path: str, specifier: str, file_elements: dict[str, dict[str, Any]]
) -> str | None:
    if not specifier.startswith("."):
        return None
    # Inventory paths are canonical POSIX project-relative strings.  Path.as_posix()
    # changes separators but deliberately preserves ``..`` segments, so use a
    # lexical POSIX normalization before matching the inventory index.
    base = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), specifier))
    if base == ".." or base.startswith("../") or base.startswith("/"):
        return None
    suffixes = ("", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
    candidates = [base + suffix for suffix in suffixes]
    candidates.extend(f"{base}/index{suffix}" for suffix in suffixes[1:])
    for candidate in candidates:
        normalized = Path(candidate).as_posix()
        if normalized in file_elements:
            return file_elements[normalized]["id"]
    return None


def _external_js_package(specifier: str) -> str:
    if specifier.startswith("@"):
        return "/".join(specifier.split("/")[:2])
    return specifier.split("/", 1)[0]


def _parse_javascript(
    item: dict[str, Any],
    *,
    file_elements: dict[str, dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    revision: str,
    losses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_element = file_elements[item["path"]]
    try:
        text = item["_body"].decode("utf-8")
    except UnicodeDecodeError as exc:
        item["parseStatus"] = "failed"
        source_element["parseStatus"] = "failed"
        losses.append(
            {
                "kind": "source_file.parse_failed",
                "sourceRef": item["path"],
                "severity": "partial",
                "reason": f"JavaScript/TypeScript decoder failed: {type(exc).__name__}",
                "preservedAs": f"inventory:{item['path']}",
            }
        )
        return []
    item["parseStatus"] = "partial"
    source_element["parseStatus"] = "partial"
    cleaned = _strip_js_comments(text)
    matches: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for kind, pattern in JS_PATTERNS:
        for match in pattern.finditer(cleaned):
            span = match.span()
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            occupied.append(span)
            matches.append((span[0], span[1], kind, match.group(2)))
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for start, _, kind, specifier in sorted(matches):
        line = cleaned.count("\n", 0, start) + 1
        identity = (kind, specifier, line)
        if identity in seen:
            continue
        seen.add(identity)
        pin = _evidence_pin(item, line=line, column=None, revision=revision)
        local_id = _resolve_js_relative(item["path"], specifier, file_elements)
        if local_id is not None:
            target = elements[local_id]
            resolution = "dynamic" if kind == "dynamic_import" else "resolved"
        elif specifier.startswith("."):
            target = _target_element(
                elements,
                kind="unresolved_target",
                name=specifier,
                pin=pin,
                reason="relative_import_target_not_found",
            )
            resolution = "dynamic" if kind == "dynamic_import" else "unresolved"
        else:
            target = _target_element(
                elements,
                kind="external_package",
                name=_external_js_package(specifier),
                pin=pin,
            )
            resolution = "dynamic" if kind == "dynamic_import" else "external"
        relations.append(
            _relation(
                kind=kind,
                name=f"{kind}: {specifier}",
                source_id=source_element["id"],
                target_id=target["id"],
                resolution=resolution,
                pin=pin,
                fact_status="derived",
                authority="inferred",
                confidence="medium",
                attributes={
                    "adapterId": "javascript-static-imports",
                    "parser": "bounded_regex",
                    "specifier": specifier,
                    "typeOnly": kind == "type_import",
                    "dynamic": kind == "dynamic_import",
                    "runtimeObserved": False,
                    "sequenceOrder": None,
                },
            )
        )
        if resolution in {"unresolved", "dynamic"}:
            losses.append(
                {
                    "kind": "source_relation.dynamic_or_unresolved",
                    "sourceRef": f"{item['path']}:{line}",
                    "severity": "partial" if resolution == "unresolved" else "informational",
                    "reason": "Static extraction cannot prove the target or runtime behavior.",
                    "preservedAs": f"relation:{relations[-1]['id']}",
                }
            )
    losses.append(
        {
            "kind": "parser.javascript_bounded_regex",
            "sourceRef": item["path"],
            "severity": "informational",
            "reason": (
                "The built-in JS/TS adapter recognizes bounded static import "
                "forms; it is not a full parser."
            ),
            "preservedAs": f"element:{source_element['id']}",
        }
    )
    return relations


SAFE_PROPERTY_KEYS = {
    "database",
    "management.endpoints.web.exposure.include",
    "spring.thymeleaf.mode",
    "spring.datasource.url",
}


def _safe_property_value(key: str, value: str) -> tuple[str | None, str]:
    normalized = value.strip()
    if key not in SAFE_PROPERTY_KEYS:
        return None, "omitted_by_policy"
    if key == "spring.datasource.url":
        match = re.match(r"^(jdbc:[A-Za-z0-9_-]+):", normalized)
        return (match.group(1), "scheme_only") if match else (None, "omitted_by_policy")
    if len(normalized) <= 128 and re.fullmatch(r"[A-Za-z0-9_.*,+-]+", normalized):
        return normalized, "recorded_safe_value"
    return None, "omitted_by_policy"


def _parse_properties(
    item: dict[str, Any],
    *,
    file_elements: dict[str, dict[str, Any]],
    revision: str,
    losses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_element = file_elements[item["path"]]
    try:
        text = item["_body"].decode("utf-8")
    except UnicodeDecodeError as exc:
        item["parseStatus"] = "failed"
        source_element["parseStatus"] = "failed"
        losses.append(
            {
                "kind": "configuration.parse_failed",
                "sourceRef": item["path"],
                "severity": "partial",
                "reason": f"Properties decoder failed: {type(exc).__name__}",
                "preservedAs": f"inventory:{item['path']}",
            }
        )
        return []
    entries = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        match = re.match(r"^([^:=\s]+)\s*[:=]\s*(.*)$", stripped)
        if not match:
            continue
        key = match.group(1)
        safe_value, value_policy = _safe_property_value(key, match.group(2))
        entry = {
            "key": key,
            "line": line_number,
            "valuePolicy": value_policy,
            "evidencePinId": _evidence_pin(
                item, line=line_number, column=None, revision=revision
            )["evidenceId"],
        }
        if safe_value is not None:
            entry["safeValue"] = safe_value
        entries.append(entry)
    item["parseStatus"] = "parsed"
    source_element["parseStatus"] = "parsed"
    source_element["attributes"].update(
        {
            "configurationFormat": "java_properties",
            "entries": entries,
            "runtimeObserved": False,
        }
    )
    for entry in entries:
        _merge_pin(
            source_element,
            _evidence_pin(item, line=entry["line"], column=None, revision=revision),
        )
    return []


JAVA_PACKAGE_PATTERN = re.compile(
    r"(?m)^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;"
)
JAVA_IMPORT_PATTERN = re.compile(
    r"(?m)^\s*import\s+(static\s+)?"
    r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\.\*)?)\s*;"
)
JAVA_TYPE_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:public|protected|private|abstract|final|sealed|non-sealed|static|strictfp|\s)*"
    r"\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)"
)
JAVA_ANNOTATION_PATTERN = re.compile(
    r"(?m)^[ \t]*@([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)"
)


def _java_semantic_attributes(cleaned: str) -> dict[str, Any]:
    """Return bounded declaration metadata without inferring framework behavior."""

    package_match = JAVA_PACKAGE_PATTERN.search(cleaned)
    package = package_match.group(1) if package_match else None
    declarations = []
    for match in JAVA_TYPE_PATTERN.finditer(cleaned):
        declarations.append(
            {
                "kind": match.group(1),
                "name": match.group(2),
                "line": cleaned.count("\n", 0, match.start()) + 1,
            }
        )
    type_annotations: list[dict[str, Any]] = []
    if declarations:
        first_declaration = JAVA_TYPE_PATTERN.search(cleaned)
        prefix = cleaned[: first_declaration.start()] if first_declaration else ""
        for match in JAVA_ANNOTATION_PATTERN.finditer(prefix):
            type_annotations.append(
                {
                    "name": match.group(1),
                    "line": cleaned.count("\n", 0, match.start()) + 1,
                }
            )

    constructor_parameters: list[dict[str, Any]] = []
    for declaration in declarations[:1]:
        constructor_pattern = re.compile(
            rf"(?m)^[ \t]*(?:public|protected|private)?[ \t]+"
            rf"{re.escape(declaration['name'])}[ \t]*\(([^)]*)\)"
        )
        for constructor in constructor_pattern.finditer(cleaned):
            types: list[str] = []
            for parameter in constructor.group(1).split(","):
                normalized = re.sub(r"@[A-Za-z_$][\w$]*(?:\([^)]*\))?", " ", parameter)
                normalized = re.sub(r"\b(final|volatile|transient)\b", " ", normalized)
                tokens = re.findall(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:<[^>]+>)?(?:\[\])?", normalized)
                if len(tokens) >= 2:
                    types.append(re.sub(r"<.*>", "", tokens[-2]).removesuffix("[]"))
            constructor_parameters.append(
                {
                    "line": cleaned.count("\n", 0, constructor.start()) + 1,
                    "parameterTypes": types,
                }
            )
    return {
        "package": package,
        "declarations": declarations,
        "typeAnnotations": type_annotations,
        "constructors": constructor_parameters,
        "semanticBoundary": "declarations_only_not_framework_behavior",
    }


def _strip_java_non_code(text: str) -> str:
    """Preserve line positions while blanking comments, strings and text blocks."""

    chars = list(text)
    index = 0
    state = "code"
    while index < len(chars):
        char = chars[index]
        following = chars[index + 1] if index + 1 < len(chars) else ""
        triple = "".join(chars[index : index + 3])
        if state == "code":
            if char == "/" and following == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and following == "*":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if triple == '\"\"\"':
                chars[index : index + 3] = [" ", " ", " "]
                index += 3
                state = "text_block"
                continue
            if char == '"':
                chars[index] = " "
                index += 1
                state = "string"
                continue
            if char == "'":
                chars[index] = " "
                index += 1
                state = "char"
                continue
            index += 1
            continue
        if state == "line_comment":
            if char in {"\r", "\n"}:
                state = "code"
            else:
                chars[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and following == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "code"
                continue
            if char not in {"\r", "\n"}:
                chars[index] = " "
            index += 1
            continue
        if state == "text_block":
            if triple == '\"\"\"':
                chars[index : index + 3] = [" ", " ", " "]
                index += 3
                state = "code"
                continue
            if char not in {"\r", "\n"}:
                chars[index] = " "
            index += 1
            continue
        if char == "\\":
            chars[index] = " "
            if index + 1 < len(chars):
                if chars[index + 1] not in {"\r", "\n"}:
                    chars[index + 1] = " "
                index += 2
            else:
                index += 1
            continue
        terminator = '"' if state == "string" else "'"
        if char == terminator:
            chars[index] = " "
            index += 1
            state = "code"
            continue
        if char not in {"\r", "\n"}:
            chars[index] = " "
        index += 1
    return "".join(chars)


def _java_type_index(
    inventory: list[dict[str, Any]], file_elements: dict[str, dict[str, Any]]
) -> tuple[dict[str, str], set[str]]:
    candidates: dict[str, list[str]] = {}
    packages: set[str] = set()
    for item in inventory:
        if item["language"] != "java":
            continue
        try:
            cleaned = _strip_java_non_code(item["_body"].decode("utf-8"))
        except UnicodeDecodeError:
            continue
        package_match = JAVA_PACKAGE_PATTERN.search(cleaned)
        package = package_match.group(1) if package_match else ""
        if package:
            packages.add(package)
        if Path(item["path"]).name in {"package-info.java", "module-info.java"}:
            continue
        declarations = JAVA_TYPE_PATTERN.findall(cleaned)
        type_names = [name for _, name in declarations] or [Path(item["path"]).stem]
        for type_name in type_names:
            qualified = f"{package}.{type_name}" if package else type_name
            candidates.setdefault(qualified, []).append(file_elements[item["path"]]["id"])
    return {
        name: element_ids[0]
        for name, element_ids in candidates.items()
        if len(element_ids) == 1
    }, packages


def _external_java_symbol(specifier: str, *, static_import: bool) -> str:
    parts = specifier.removesuffix(".*").split(".")
    if static_import:
        for index, part in enumerate(parts):
            if part and (part[0].isupper() or "$" in part):
                return ".".join(parts[: index + 1])
        if len(parts) > 1:
            return ".".join(parts[:-1])
    return ".".join(parts)


def _resolve_java_import(
    specifier: str,
    *,
    static_import: bool,
    type_index: dict[str, str],
    packages: set[str],
) -> tuple[str | None, str]:
    if specifier.endswith(".*"):
        package = specifier[:-2]
        matches = [target for name, target in type_index.items() if name.rpartition(".")[0] == package]
        return (matches[0], "resolved") if len(matches) == 1 else (None, "unresolved" if package in packages else "external")
    if specifier in type_index:
        return type_index[specifier], "resolved"
    if static_import:
        parts = specifier.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in type_index:
                return type_index[candidate], "resolved"
    package = specifier.rpartition(".")[0]
    if package in packages or any(specifier.startswith(item + ".") for item in packages):
        return None, "unresolved"
    return None, "external"


def _parse_java(
    item: dict[str, Any],
    *,
    file_elements: dict[str, dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    type_index: dict[str, str],
    packages: set[str],
    revision: str,
    losses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_element = file_elements[item["path"]]
    try:
        cleaned = _strip_java_non_code(item["_body"].decode("utf-8"))
    except UnicodeDecodeError as exc:
        item["parseStatus"] = "failed"
        source_element["parseStatus"] = "failed"
        losses.append(
            {
                "kind": "source_file.parse_failed",
                "sourceRef": item["path"],
                "severity": "partial",
                "reason": f"Java decoder failed: {type(exc).__name__}",
                "preservedAs": f"inventory:{item['path']}",
            }
        )
        return []
    item["parseStatus"] = "partial"
    source_element["parseStatus"] = "partial"
    semantic_attributes = _java_semantic_attributes(cleaned)
    source_element["attributes"].update(semantic_attributes)
    for semantic_item in (
        semantic_attributes["declarations"]
        + semantic_attributes["typeAnnotations"]
        + semantic_attributes["constructors"]
    ):
        _merge_pin(
            source_element,
            _evidence_pin(
                item,
                line=semantic_item["line"],
                column=None,
                revision=revision,
            ),
        )
    relations: list[dict[str, Any]] = []
    for match in JAVA_IMPORT_PATTERN.finditer(cleaned):
        static_import = bool(match.group(1))
        specifier = match.group(2)
        line = cleaned.count("\n", 0, match.start()) + 1
        pin = _evidence_pin(item, line=line, column=None, revision=revision)
        local_id, resolution = _resolve_java_import(
            specifier,
            static_import=static_import,
            type_index=type_index,
            packages=packages,
        )
        if local_id is not None:
            target = elements[local_id]
        elif resolution == "unresolved":
            target = _target_element(
                elements,
                kind="unresolved_target",
                name=specifier,
                pin=pin,
                reason=(
                    "java_wildcard_import_not_single_target"
                    if specifier.endswith(".*")
                    else "java_local_import_target_not_found"
                ),
            )
        else:
            target = _target_element(
                elements,
                kind="external_package",
                name=_external_java_symbol(
                    specifier, static_import=static_import
                ),
                pin=pin,
            )
        relations.append(
            _relation(
                kind="import",
                name=f"import: {specifier}",
                source_id=source_element["id"],
                target_id=target["id"],
                resolution=resolution,
                pin=pin,
                fact_status="derived",
                authority="inferred",
                confidence="medium",
                attributes={
                    "adapterId": "java-static-imports",
                    "parser": "bounded_regex",
                    "specifier": specifier,
                    "typeOnly": False,
                    "dynamic": False,
                    "dependencyClass": "static_import" if static_import else "import",
                    "runtimeObserved": False,
                    "sequenceOrder": None,
                },
            )
        )
        if resolution == "unresolved":
            losses.append(
                {
                    "kind": "source_relation.dynamic_or_unresolved",
                    "sourceRef": f"{item['path']}:{line}",
                    "severity": "partial",
                    "reason": "Bounded Java extraction cannot resolve this import to one source file.",
                    "preservedAs": f"relation:{relations[-1]['id']}",
                }
            )
    losses.append(
        {
            "kind": "parser.java_bounded_static_imports",
            "sourceRef": item["path"],
            "severity": "informational",
            "reason": (
                "The built-in Java adapter recognizes package/import declarations; "
                "it is not a full parser and does not infer calls, reflection or generated code."
            ),
            "preservedAs": f"element:{source_element['id']}",
        }
    )
    return relations


def _multistack_symbol_index(
    inventory: list[dict[str, Any]], file_elements: dict[str, dict[str, Any]]
) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}
    for item in inventory:
        language = item["language"]
        if language not in LANGUAGE_ADAPTERS or language in {
            "python", "javascript", "typescript", "java"
        }:
            continue
        scan = scan_language_source(language, item["_body"])
        item["_multistack_scan"] = scan
        element = file_elements[item["path"]]
        adapter = LANGUAGE_ADAPTERS[language]
        element["attributes"].update(
            {
                "adapterId": adapter["id"],
                "parser": adapter["parser"],
                "semanticBoundary": "dependencies_and_package_declarations_only",
                "package": scan.get("package"),
            }
        )
        package = scan.get("package")
        if package:
            keys = {package, f"{package}.{Path(item['path']).stem}"}
            for key in keys:
                index.setdefault((language, key), []).append(element["id"])
    for values in index.values():
        values.sort()
    return index


def _resolve_multistack_target(
    *,
    language: str,
    specifier: str,
    item: dict[str, Any],
    file_elements: dict[str, dict[str, Any]],
    symbol_index: dict[tuple[str, str], list[str]],
    go_module: str | None,
) -> tuple[str | None, str]:
    relative = specifier.startswith(("./", "../"))
    if relative:
        raw = posixpath.normpath(
            posixpath.join(posixpath.dirname(item["path"]), specifier)
        )
        suffixes = LANGUAGE_ADAPTERS[language]["suffixes"]
        candidates = [raw]
        candidates.extend(raw + suffix for suffix in suffixes)
        candidates.extend(posixpath.join(raw, "index" + suffix) for suffix in suffixes)
        for candidate in candidates:
            target = file_elements.get(candidate)
            if target is not None:
                return target["id"], "resolved"
        return None, "unresolved"
    if language == "rust" and specifier.startswith("crate::"):
        parts = specifier.split("::")[1:]
        raw = posixpath.join("src", *parts)
        for candidate in (raw + ".rs", posixpath.join(raw, "mod.rs")):
            target = file_elements.get(candidate)
            if target is not None:
                return target["id"], "resolved"
        return None, "unresolved"
    if language == "go" and go_module and (
        specifier == go_module or specifier.startswith(go_module + "/")
    ):
        directory = specifier[len(go_module):].lstrip("/")
        candidates = sorted(
            element["id"]
            for path, element in file_elements.items()
            if element["language"] == "go"
            and posixpath.dirname(path) == directory
        )
        return (candidates[0], "resolved") if candidates else (None, "unresolved")
    separators = ("::", "\\", ".")
    normalized = specifier.replace("\\", ".").replace("::", ".")
    candidates = symbol_index.get((language, normalized), [])
    if len(candidates) == 1:
        return candidates[0], "resolved"
    for separator in separators:
        if separator in specifier:
            prefix = normalized.rpartition(".")[0]
            candidates = symbol_index.get((language, prefix), [])
            if len(candidates) == 1:
                return candidates[0], "resolved"
    return None, "external"


def _parse_multistack_language(
    item: dict[str, Any],
    *,
    file_elements: dict[str, dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    symbol_index: dict[tuple[str, str], list[str]],
    go_module: str | None,
    revision: str,
    losses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    language = item["language"]
    adapter = LANGUAGE_ADAPTERS[language]
    source_element = file_elements[item["path"]]
    try:
        scan = item.get("_multistack_scan") or scan_language_source(
            language, item["_body"]
        )
    except (UnicodeDecodeError, ValueError, re.error) as exc:
        item["parseStatus"] = "failed"
        source_element["parseStatus"] = "failed"
        losses.append(
            {
                "kind": f"parser.{language}_failed",
                "sourceRef": item["path"],
                "severity": "partial",
                "reason": f"Bounded {language} parser failed: {type(exc).__name__}",
                "preservedAs": f"element:{source_element['id']}",
            }
        )
        return []
    relations: list[dict[str, Any]] = []
    for dependency in scan["dependencies"]:
        specifier = dependency["specifier"]
        line = dependency["line"]
        pin = _evidence_pin(item, line=line, column=dependency.get("column"), revision=revision)
        _merge_pin(source_element, pin)
        local_id, resolution = _resolve_multistack_target(
            language=language,
            specifier=specifier,
            item=item,
            file_elements=file_elements,
            symbol_index=symbol_index,
            go_module=go_module,
        )
        if local_id:
            target = elements[local_id]
        elif resolution == "external":
            target = _target_element(elements, kind="external_package", name=specifier, pin=pin)
        else:
            target = _target_element(
                elements,
                kind="unresolved_target",
                name=specifier,
                pin=pin,
                reason=f"{language}_local_or_dynamic_target_not_resolved",
            )
        dynamic = bool(dependency.get("dynamic"))
        relation_kind = "dynamic_import" if dynamic else "import"
        relations.append(
            _relation(
                kind=relation_kind,
                name=f"{relation_kind}: {specifier}",
                source_id=source_element["id"],
                target_id=target["id"],
                resolution="dynamic" if dynamic else resolution,
                pin=pin,
                fact_status="unknown" if dynamic or resolution == "unresolved" else "derived",
                authority="unknown" if dynamic or resolution == "unresolved" else "inferred",
                confidence="unknown" if dynamic or resolution == "unresolved" else "medium",
                attributes={
                    "adapterId": adapter["id"],
                    "parser": adapter["parser"],
                    "specifier": specifier,
                    "typeOnly": False,
                    "dynamic": dynamic,
                    "dependencyClass": dependency.get("kind", "import"),
                    "runtimeObserved": False,
                    "sequenceOrder": None,
                },
            )
        )
        if dynamic or resolution == "unresolved":
            losses.append(
                {
                    "kind": "source_relation.dynamic_or_unresolved",
                    "sourceRef": f"{item['path']}:{line}",
                    "severity": "partial",
                    "reason": f"Bounded {language} extraction cannot resolve this dependency to one source file.",
                    "preservedAs": f"relation:{relations[-1]['id']}",
                }
            )
    item["parseStatus"] = "partial" if adapter["status"] == "preview" else "parsed"
    source_element["parseStatus"] = item["parseStatus"]
    losses.append(
        {
            "kind": f"parser.{language}_bounded_dependencies",
            "sourceRef": item["path"],
            "severity": "informational" if adapter["status"] == "supported" else "partial",
            "reason": (
                f"The {language} adapter recognizes dependency and package declarations; "
                "it does not infer calls, framework injection, generated code or runtime behavior."
            ),
            "preservedAs": f"element:{source_element['id']}",
        }
    )
    return relations


def _parse_multistack_manifest(
    item: dict[str, Any],
    *,
    file_elements: dict[str, dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    revision: str,
    losses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_element = file_elements[item["path"]]
    try:
        result = scan_multistack_manifest(item["path"], item["language"], item["_body"])
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        item["parseStatus"] = "failed"
        source_element["parseStatus"] = "failed"
        losses.append(
            {
                "kind": "manifest.parse_failed",
                "sourceRef": item["path"],
                "severity": "partial",
                "reason": f"Multistack manifest parse failed: {type(exc).__name__}",
                "preservedAs": f"element:{source_element['id']}",
            }
        )
        return []
    source_element["attributes"].update(
        {
            "adapterId": "multistack-manifest-dependencies",
            "parser": "structured_manifest",
            "manifestFormat": item["language"],
            "metadata": result["metadata"],
            "runtimeObserved": False,
        }
    )
    relations: list[dict[str, Any]] = []
    for dependency in result["dependencies"]:
        pin = _evidence_pin(item, line=dependency["line"], column=None, revision=revision)
        _merge_pin(source_element, pin)
        target = _target_element(
            elements, kind="external_package", name=dependency["specifier"], pin=pin
        )
        relations.append(
            _relation(
                kind="declared_dependency",
                name=f"declared dependency: {dependency['specifier']}",
                source_id=source_element["id"],
                target_id=target["id"],
                resolution="external",
                pin=pin,
                fact_status="declared",
                authority="declared",
                confidence="high",
                attributes={
                    "adapterId": "multistack-manifest-dependencies",
                    "parser": "structured_manifest",
                    "specifier": dependency["specifier"],
                    "dependencyClass": dependency["dependencyClass"],
                    "runtimeObserved": False,
                    "sequenceOrder": None,
                },
            )
        )
    item["parseStatus"] = "parsed"
    source_element["parseStatus"] = "parsed"
    return relations


def _dependency_name(value: str) -> str | None:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", value)
    return match.group(1) if match else None


def _manifest_relation(
    item: dict[str, Any],
    *,
    dependency: str,
    dependency_class: str,
    file_elements: dict[str, dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    revision: str,
    line: int | None = None,
) -> dict[str, Any]:
    pin = _evidence_pin(item, line=line, column=None, revision=revision)
    target = _target_element(
        elements,
        kind="external_package",
        name=dependency,
        pin=pin,
    )
    return _relation(
        kind="declared_dependency",
        name=f"declared dependency: {dependency}",
        source_id=file_elements[item["path"]]["id"],
        target_id=target["id"],
        resolution="external",
        pin=pin,
        fact_status="declared",
        authority="declared",
        confidence="high",
        attributes={
            "adapterId": "manifest-dependencies",
            "parser": item["language"],
            "specifier": dependency,
            "dependencyClass": dependency_class,
            "runtimeObserved": False,
            "sequenceOrder": None,
        },
    )


def _parse_manifest(
    item: dict[str, Any],
    *,
    file_elements: dict[str, dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    revision: str,
    losses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_element = file_elements[item["path"]]
    relations: list[dict[str, Any]] = []
    try:
        text = item["_body"].decode("utf-8")
        if item["language"] == "json":
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("manifest root is not an object")
            for field in (
                "dependencies",
                "optionalDependencies",
                "peerDependencies",
                "devDependencies",
            ):
                values = data.get(field, {})
                if not isinstance(values, dict):
                    continue
                for dependency in sorted(str(name) for name in values):
                    relations.append(
                        _manifest_relation(
                            item,
                            dependency=dependency,
                            dependency_class=field,
                            file_elements=file_elements,
                            elements=elements,
                            revision=revision,
                        )
                    )
        elif item["language"] == "toml":
            try:
                import tomllib
            except ImportError as exc:  # Python 3.10 remains supported.
                raise ValueError("tomllib is unavailable") from exc
            data = tomllib.loads(text)
            project = data.get("project", {}) if isinstance(data, dict) else {}
            if isinstance(project, dict):
                raw_dependencies = project.get("dependencies", [])
                if isinstance(raw_dependencies, list):
                    for raw in raw_dependencies:
                        dependency = _dependency_name(str(raw))
                        if dependency:
                            relations.append(
                                _manifest_relation(
                                    item,
                                    dependency=dependency,
                                    dependency_class="project.dependencies",
                                    file_elements=file_elements,
                                    elements=elements,
                                    revision=revision,
                                )
                            )
                optional = project.get("optional-dependencies", {})
                if isinstance(optional, dict):
                    for group, values in sorted(optional.items()):
                        if not isinstance(values, list):
                            continue
                        for raw in values:
                            dependency = _dependency_name(str(raw))
                            if dependency:
                                relations.append(
                                    _manifest_relation(
                                        item,
                                        dependency=dependency,
                                        dependency_class=f"project.optional-dependencies.{group}",
                                        file_elements=file_elements,
                                        elements=elements,
                                        revision=revision,
                                    )
                                )
        elif item["language"] == "xml":
            root = ET.fromstring(text)
            namespace = ""
            if root.tag.startswith("{"):
                namespace = root.tag.split("}", 1)[0] + "}"
            for dependency_node in root.findall(f".//{namespace}dependencies/{namespace}dependency"):
                group_node = dependency_node.find(f"{namespace}groupId")
                artifact_node = dependency_node.find(f"{namespace}artifactId")
                scope_node = dependency_node.find(f"{namespace}scope")
                if artifact_node is None or not (artifact_node.text or "").strip():
                    continue
                group = (group_node.text or "").strip() if group_node is not None else ""
                artifact = (artifact_node.text or "").strip()
                dependency = f"{group}:{artifact}" if group else artifact
                scope = (scope_node.text or "compile").strip() if scope_node is not None else "compile"
                artifact_pattern = re.compile(
                    rf"<artifactId>\s*{re.escape(artifact)}\s*</artifactId>"
                )
                artifact_match = artifact_pattern.search(text)
                line = text.count("\n", 0, artifact_match.start()) + 1 if artifact_match else None
                relations.append(
                    _manifest_relation(
                        item,
                        dependency=dependency,
                        dependency_class=f"maven.{scope}",
                        file_elements=file_elements,
                        elements=elements,
                        revision=revision,
                        line=line,
                    )
                )
        else:
            raise ValueError(f"unsupported manifest language: {item['language']}")
    except (UnicodeDecodeError, json.JSONDecodeError, ET.ParseError, ValueError) as exc:
        item["parseStatus"] = "failed"
        source_element["parseStatus"] = "failed"
        losses.append(
            {
                "kind": "manifest.parse_failed",
                "sourceRef": item["path"],
                "severity": "partial",
                "reason": f"Manifest parser failed: {type(exc).__name__}",
                "preservedAs": f"inventory:{item['path']}",
            }
        )
        return []
    item["parseStatus"] = "parsed"
    source_element["parseStatus"] = "parsed"
    return relations


def _stack_signal(
    *,
    category: str,
    technology: str,
    source_kind: str,
    fact_status: str,
    authority: str,
    confidence: str,
    adapter_id: str,
    evidence_pin_ids: list[str],
) -> dict[str, Any]:
    identity = {
        "category": category,
        "technology": technology,
        "sourceKind": source_kind,
        "adapterId": adapter_id,
        "evidencePinIds": sorted(set(evidence_pin_ids)),
    }
    return {
        "signalId": _derived_id("STKSIG", identity),
        "category": category,
        "technology": technology,
        "sourceKind": source_kind,
        "factStatus": fact_status,
        "authority": authority,
        "confidence": confidence,
        "adapterId": adapter_id,
        "evidencePinIds": sorted(set(evidence_pin_ids)),
    }


def _build_stack_profile(
    elements: list[dict[str, Any]], relations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for element in elements:
        if element["kind"] == "source_file" and element["language"] in LANGUAGE_ADAPTERS:
            adapter = LANGUAGE_ADAPTERS[element["language"]]
            signals.append(
                _stack_signal(
                    category="language",
                    technology=element["language"],
                    source_kind="language",
                    fact_status="derived",
                    authority="inferred",
                    confidence="high",
                    adapter_id=adapter["id"],
                    evidence_pin_ids=[element["evidencePins"][0]["evidenceId"]],
                )
            )
        filename_match = filename_stack_match(element.get("path") or "")
        if filename_match:
            category, technology = filename_match
            signals.append(
                _stack_signal(
                    category=category,
                    technology=technology,
                    source_kind="filename",
                    fact_status="derived",
                    authority="inferred",
                    confidence="medium",
                    adapter_id="stack-filename-signals",
                    evidence_pin_ids=[element["evidencePins"][0]["evidenceId"]],
                )
            )
    for relation in relations:
        if relation["kind"] != "declared_dependency":
            continue
        specifier = relation["attributes"].get("specifier") or ""
        for category, technology in dependency_stack_matches(specifier):
            signals.append(
                _stack_signal(
                    category=category,
                    technology=technology,
                    source_kind="manifest_dependency",
                    fact_status="declared",
                    authority="declared",
                    confidence="high",
                    adapter_id=relation["attributes"]["adapterId"],
                    evidence_pin_ids=[pin["evidenceId"] for pin in relation["evidencePins"]],
                )
            )
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for signal in signals:
        key = (
            signal["category"], signal["technology"],
            signal["sourceKind"], signal["adapterId"],
        )
        if key not in merged:
            merged[key] = signal
            continue
        evidence = sorted(set(merged[key]["evidencePinIds"] + signal["evidencePinIds"]))
        merged[key] = _stack_signal(
            category=signal["category"], technology=signal["technology"],
            source_kind=signal["sourceKind"], fact_status=signal["factStatus"],
            authority=signal["authority"], confidence=signal["confidence"],
            adapter_id=signal["adapterId"], evidence_pin_ids=evidence,
        )
    return sorted(merged.values(), key=lambda item: item["signalId"])


def _build_monorepo_boundaries(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundaries: list[dict[str, Any]] = []
    for element in elements:
        if element["kind"] != "manifest" or not element.get("path"):
            continue
        path = Path(element["path"])
        name = path.name.casefold()
        if name not in MANIFEST_ADAPTERS and not name.endswith(".csproj"):
            continue
        root = path.parent.as_posix()
        if root == ".":
            root = ""
        kind = "workspace" if name == "package.json" else "project"
        if name == "cargo.toml":
            kind = "crate"
        elif name in {"go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "cmakelists.txt"}:
            kind = "build_root"
        identity = {"kind": kind, "root": root, "path": element["path"]}
        boundaries.append(
            {
                "boundaryId": _derived_id("WRKSP", identity),
                "kind": kind,
                "root": root,
                "name": path.parent.name or path.stem,
                "evidencePinIds": [element["evidencePins"][0]["evidenceId"]],
            }
        )
    return sorted(boundaries, key=lambda item: item["boundaryId"])


def _build_support_matrix(
    adapter_inputs: dict[str, int],
    adapter_outputs: dict[str, int],
    adapter_errors: dict[str, int],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for language, adapter in sorted(LANGUAGE_ADAPTERS.items()):
        count = adapter_inputs.get(language, 0)
        errors = adapter_errors.get(language, 0)
        if count == 0:
            status = "not_applicable"
        elif errors:
            status = "partial"
        else:
            status = adapter["status"]
        gaps = [] if count == 0 else [
            f"{language}_static_dependencies_do_not_prove_runtime_calls",
            f"{language}_reflection_generated_code_and_framework_injection_not_resolved",
        ]
        result.append(
            {
                "adapterId": adapter["id"],
                "language": language,
                "level": adapter["level"],
                "status": status,
                "inputCount": count,
                "outputCount": adapter_outputs.get(language, 0),
                "errorCount": errors,
                "informationGaps": gaps,
            }
        )
    return result


def compute_observation_semantic_hash(observation: dict[str, Any]) -> str:
    value = deepcopy(observation)
    value.pop("integrity", None)
    return compute_canonical_hash(value)


def compute_receipt_hash(receipt: dict[str, Any]) -> str:
    value = deepcopy(receipt)
    value.pop("integrity", None)
    return compute_canonical_hash(value)


def validate_source_observation(observation: dict[str, Any]) -> list[str]:
    schema = (
        LEGACY_OBSERVATION_SCHEMA
        if observation.get("formatVersion") == "panorama-source-topology-observation.v0.1"
        else OBSERVATION_SCHEMA
    )
    errors = _schema_errors(observation, schema)
    if errors:
        return errors
    if observation["integrity"]["semanticHash"] != compute_observation_semantic_hash(
        observation
    ):
        errors.append("/integrity/semanticHash: 与 Source Observation 内容不匹配")
    inventory = {item["path"]: item for item in observation["inventory"]["files"]}
    if len(inventory) != len(observation["inventory"]["files"]):
        errors.append("/inventory/files: 存在重复 path")
    expected_digest = _source_digest(observation["inventory"]["files"])
    if observation["sourceBinding"]["contentDigest"] != expected_digest:
        errors.append("/sourceBinding/contentDigest: 与 Inventory 不匹配")
    element_ids = [item["id"] for item in observation["elements"]]
    relation_ids = [item["id"] for item in observation["relations"]]
    if len(element_ids) != len(set(element_ids)):
        errors.append("/elements: 存在重复稳定 ID")
    if len(relation_ids) != len(set(relation_ids)):
        errors.append("/relations: 存在重复稳定 ID")
    element_set = set(element_ids)
    element_by_id = {item["id"]: item for item in observation["elements"]}
    evidence_ids = {
        pin["evidenceId"]
        for item in observation["elements"]
        for pin in item["evidencePins"]
    }
    revision = observation["sourceBinding"]["gitHead"] or expected_digest
    for owner in observation["elements"] + observation["relations"]:
        for pin in owner["evidencePins"]:
            bound = inventory.get(pin["ref"])
            if bound is None:
                errors.append(f"/{owner['id']}: Evidence ref 不在 Inventory")
                continue
            if pin["digest"] != bound["sha256"]:
                errors.append(f"/{owner['id']}: Evidence digest 与 Inventory 不匹配")
            if pin["revision"] != revision:
                errors.append(f"/{owner['id']}: Evidence revision 与 Source Binding 不匹配")
    for element in observation["elements"]:
        attributes = element["attributes"]
        pin_by_id = {pin["evidenceId"]: pin for pin in element["evidencePins"]}
        pinned_lines = {
            (pin["ref"], pin["line"])
            for pin in element["evidencePins"]
            if pin["line"] is not None
        }
        if element["language"] == "java" and element["kind"] == "source_file":
            if attributes.get("semanticBoundary") != "declarations_only_not_framework_behavior":
                errors.append(f"/{element['id']}: Java semanticBoundary 不匹配")
            for key in ("declarations", "typeAnnotations", "constructors"):
                for semantic_item in attributes.get(key, []):
                    if (element["path"], semantic_item.get("line")) not in pinned_lines:
                        errors.append(f"/{element['id']}: {key} 缺少行级 Evidence Pin")
        if element["language"] == "properties" and element["kind"] == "configuration":
            if attributes.get("configurationFormat") != "java_properties" or attributes.get("runtimeObserved") is not False:
                errors.append(f"/{element['id']}: Properties 边界字段不匹配")
            seen_keys: set[str] = set()
            for entry in attributes.get("entries", []):
                key = entry.get("key")
                if key in seen_keys:
                    errors.append(f"/{element['id']}: Properties key 重复")
                seen_keys.add(key)
                pin = pin_by_id.get(entry.get("evidencePinId"))
                if pin is None or pin.get("line") != entry.get("line"):
                    errors.append(f"/{element['id']}: Properties entry Evidence Pin 不匹配")
                safe_value = entry.get("safeValue")
                value_policy = entry.get("valuePolicy")
                if safe_value is None:
                    if value_policy != "omitted_by_policy":
                        errors.append(f"/{element['id']}: 无安全值时必须 omitted_by_policy")
                elif key not in SAFE_PROPERTY_KEYS:
                    errors.append(f"/{element['id']}: 非白名单 Properties key 持久化了值")
                elif key == "spring.datasource.url":
                    if value_policy != "scheme_only" or not re.fullmatch(r"jdbc:[A-Za-z0-9_-]+", safe_value):
                        errors.append(f"/{element['id']}: datasource URL 只能持久化 jdbc scheme")
                elif value_policy != "recorded_safe_value" or not re.fullmatch(r"[A-Za-z0-9_.*,+-]{1,128}", safe_value):
                    errors.append(f"/{element['id']}: Properties safeValue 不符合白名单标量")
    for relation in observation["relations"]:
        if relation["fromElementId"] not in element_set:
            errors.append(f"/relations/{relation['id']}: 未知 fromElementId")
        if relation["toElementId"] not in element_set:
            errors.append(f"/relations/{relation['id']}: 未知 toElementId")
        attributes = relation["attributes"]
        source = element_by_id.get(relation["fromElementId"])
        expected_adapter: tuple[str, str] | None = None
        if source is not None:
            if source["kind"] == "manifest":
                expected_adapter = (
                    source["attributes"].get("adapterId", "manifest-dependencies"),
                    source["attributes"].get("parser", source["language"]),
                )
            else:
                expected_adapter = {
                    "python": ("python-ast-imports", "python_ast"),
                    "javascript": ("javascript-static-imports", "bounded_regex"),
                    "typescript": ("javascript-static-imports", "bounded_regex"),
                    "java": ("java-static-imports", "bounded_regex"),
                }.get(source["language"])
                if expected_adapter is None and source["language"] in LANGUAGE_ADAPTERS:
                    adapter = LANGUAGE_ADAPTERS[source["language"]]
                    expected_adapter = (adapter["id"], adapter["parser"])
        if expected_adapter is None:
            errors.append(
                f"/relations/{relation['id']}: 来源 Element 没有受支持的 Adapter Binding"
            )
        elif (
            attributes["adapterId"],
            attributes["parser"],
        ) != expected_adapter:
            errors.append(
                f"/relations/{relation['id']}: Adapter/Parser 与来源 Element 不匹配"
            )
        if relation["kind"] == "type_import" and not attributes.get("typeOnly"):
            errors.append(f"/relations/{relation['id']}: type_import 缺少 typeOnly=true")
        if relation["kind"] == "dynamic_import" and not attributes.get("dynamic"):
            errors.append(f"/relations/{relation['id']}: dynamic_import 缺少 dynamic=true")
        if relation["kind"] == "declared_dependency" and (
            relation["factStatus"] != "declared"
            or relation["authority"] != "declared"
        ):
            errors.append(
                f"/relations/{relation['id']}: declared_dependency 权威边界不匹配"
            )
    if observation.get("formatVersion") == "panorama-source-topology-observation.v0.2":
        extensions = observation["extensions"]
        if extensions["adapterRegistry"] != registry_projection():
            errors.append("/extensions/adapterRegistry: 与当前 Multistack Registry 不匹配")
        support_ids = [
            (item["adapterId"], item["language"])
            for item in extensions["supportMatrix"]
        ]
        if len(support_ids) != len(set(support_ids)):
            errors.append("/extensions/supportMatrix: adapterId/language 重复")
        signal_ids = [item["signalId"] for item in extensions["stackProfile"]]
        if len(signal_ids) != len(set(signal_ids)):
            errors.append("/extensions/stackProfile: signalId 重复")
        for signal in extensions["stackProfile"]:
            if not set(signal["evidencePinIds"]).issubset(evidence_ids):
                errors.append(f"/extensions/stackProfile/{signal['signalId']}: Evidence Pin 不存在")
    return errors


def validate_extraction_receipt(
    receipt: dict[str, Any],
    observation: dict[str, Any] | None = None,
    loss_report: dict[str, Any] | None = None,
) -> list[str]:
    errors = _schema_errors(receipt, RECEIPT_SCHEMA)
    if errors:
        return errors
    if receipt["integrity"]["receiptHash"] != compute_receipt_hash(receipt):
        errors.append("/integrity/receiptHash: 与 Extraction Receipt 内容不匹配")
    if observation is not None:
        binding = receipt["outputBinding"]
        if binding["observationId"] != observation.get("observationId"):
            errors.append("/outputBinding/observationId: 与 Observation 不匹配")
        if binding["observationHash"] != observation.get("integrity", {}).get(
            "semanticHash"
        ):
            errors.append("/outputBinding/observationHash: 与 Observation 不匹配")
    if loss_report is not None:
        binding = receipt["outputBinding"]
        if binding["lossReportId"] != loss_report.get("reportId"):
            errors.append("/outputBinding/lossReportId: 与 Loss Report 不匹配")
        if binding["lossReportHash"] != compute_canonical_hash(loss_report):
            errors.append("/outputBinding/lossReportHash: 与 Loss Report 不匹配")
    return errors


def extract_source_topology(
    project_root: Path,
    *,
    project_id: str,
    observed_at: str,
) -> dict[str, dict[str, Any]]:
    raw_root = Path(project_root)
    if not raw_root.exists() or not raw_root.is_dir():
        raise SourceTopologyError("project root 必须是已有目录。")
    if _is_link_or_reparse(raw_root):
        raise SourceTopologyError("project root 不能是 link/reparse path。")
    root = raw_root.resolve(strict=True)
    inventory, excluded, git_head = _inventory(root)
    content_digest = _source_digest(inventory)
    revision = git_head or content_digest
    source_binding = {
        "mode": "git" if git_head else "content_digest",
        "gitHead": git_head,
        "contentDigest": content_digest,
        "coverage": "partial" if excluded["unsupportedSource"] else "complete",
        "currentness": (
            "unknown" if excluded["unsupportedSource"] and git_head is None else "current"
        ),
        "includedFileCount": len(inventory),
        "includedByteCount": sum(item["size"] for item in inventory),
    }
    project_binding = {"projectId": project_id, "rootName": root.name}
    file_elements = {
        item["path"]: _file_element(item, revision) for item in inventory
    }
    elements = {item["id"]: item for item in file_elements.values()}
    module_index = _python_module_index(inventory, file_elements)
    java_type_index, java_packages = _java_type_index(inventory, file_elements)
    multistack_symbol_index = _multistack_symbol_index(inventory, file_elements)
    go_module = None
    for item in inventory:
        if Path(item["path"]).name.casefold() == "go.mod":
            try:
                go_module = scan_multistack_manifest(
                    item["path"], item["language"], item["_body"]
                )["metadata"].get("module")
            except (UnicodeDecodeError, ValueError):
                go_module = None
            break
    losses: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    adapter_keys = set(LANGUAGE_ADAPTERS) | {"manifest", "multistack_manifest", "properties"}
    adapter_inputs = {key: 0 for key in adapter_keys}
    adapter_outputs = {key: 0 for key in adapter_keys}
    adapter_errors = {key: 0 for key in adapter_keys}

    for item in inventory:
        before = len(relations)
        if item["language"] == "python":
            adapter = "python"
            adapter_inputs[adapter] += 1
            relations.extend(
                _parse_python(
                    item,
                    file_elements=file_elements,
                    elements=elements,
                    module_index=module_index,
                    revision=revision,
                    losses=losses,
                )
            )
        elif item["language"] in {"javascript", "typescript"}:
            adapter = item["language"]
            adapter_inputs[adapter] += 1
            relations.extend(
                _parse_javascript(
                    item,
                    file_elements=file_elements,
                    elements=elements,
                    revision=revision,
                    losses=losses,
                )
            )
        elif item["language"] == "java":
            adapter = "java"
            adapter_inputs[adapter] += 1
            relations.extend(
                _parse_java(
                    item,
                    file_elements=file_elements,
                    elements=elements,
                    type_index=java_type_index,
                    packages=java_packages,
                    revision=revision,
                    losses=losses,
                )
            )
        elif item["language"] in LANGUAGE_ADAPTERS:
            adapter = item["language"]
            adapter_inputs[adapter] += 1
            relations.extend(
                _parse_multistack_language(
                    item,
                    file_elements=file_elements,
                    elements=elements,
                    symbol_index=multistack_symbol_index,
                    go_module=go_module,
                    revision=revision,
                    losses=losses,
                )
            )
        elif item["language"] == "properties":
            adapter = "properties"
            adapter_inputs[adapter] += 1
            relations.extend(
                _parse_properties(
                    item,
                    file_elements=file_elements,
                    revision=revision,
                    losses=losses,
                )
            )
        elif item["kind"] == "manifest":
            legacy_manifest = Path(item["path"]).name.casefold() in {
                "package.json", "pyproject.toml", "pom.xml"
            }
            adapter = "manifest" if legacy_manifest else "multistack_manifest"
            adapter_inputs[adapter] += 1
            parser = _parse_manifest if legacy_manifest else _parse_multistack_manifest
            relations.extend(
                parser(
                    item,
                    file_elements=file_elements,
                    elements=elements,
                    revision=revision,
                    losses=losses,
                )
            )
        else:
            item["parseStatus"] = "not_applicable"
            continue
        adapter_outputs[adapter] += len(relations) - before
        if item["parseStatus"] == "failed":
            adapter_errors[adapter] += 1

    relation_by_id = {item["id"]: item for item in relations}
    relations = sorted(relation_by_id.values(), key=lambda item: item["id"])
    adapter_outputs = {key: 0 for key in adapter_keys}
    element_by_id = {item["id"]: item for item in elements.values()}
    for relation in relations:
        source = element_by_id.get(relation["fromElementId"])
        key = None
        if source is not None:
            if source["kind"] == "manifest":
                key = (
                    "multistack_manifest"
                    if source["attributes"].get("adapterId") == "multistack-manifest-dependencies"
                    else "manifest"
                )
            elif source["language"] in LANGUAGE_ADAPTERS:
                key = source["language"]
            elif source["language"] == "properties":
                key = "properties"
        if key is not None:
            adapter_outputs[key] += 1
    public_inventory = []
    for item in inventory:
        public_item = {
            key: value
            for key, value in item.items()
            if key not in {"_body", "_multistack_scan"}
        }
        public_inventory.append(public_item)
    public_inventory.sort(key=lambda item: item["path"])
    information_gaps = [
        "static_dependencies_do_not_prove_runtime_calls_or_sequence_order"
    ]
    if any(item["resolution"] in {"unresolved", "dynamic"} for item in relations):
        information_gaps.append("dynamic_or_unresolved_dependencies_present")
    if adapter_inputs["javascript"] or adapter_inputs["typescript"]:
        information_gaps.append("javascript_typescript_full_parser_not_used")
    if adapter_inputs["java"]:
        information_gaps.extend(
            [
                "java_full_parser_not_used",
                "java_reflection_generated_sources_and_calls_not_resolved",
            ]
        )
    for language, adapter in sorted(LANGUAGE_ADAPTERS.items()):
        if language in {"python", "javascript", "typescript", "java"}:
            continue
        if adapter_inputs[language]:
            information_gaps.append(
                f"{language}_bounded_parser_does_not_resolve_calls_reflection_generated_code_or_framework_injection"
            )
    if any(item["parseStatus"] == "failed" for item in inventory):
        information_gaps.append("source_parse_failures_present")
    if excluded["unsupportedSource"]:
        information_gaps.append("unsupported_source_languages_present")
        if git_head is None:
            information_gaps.append("unsupported_source_snapshot_not_content_bound")
        losses.append(
            {
                "kind": "source_language.unsupported",
                "sourceRef": "$project",
                "severity": "unsupported",
                "reason": (
                    f"{excluded['unsupportedSource']} source-like files use languages "
                    "without a V0.85 extractor adapter and were not read."
                ),
                "preservedAs": "inventory.excluded.unsupported",
            }
        )
    observation_id = _derived_id(
        "SRCOBS",
        {
            "producer": PRODUCER,
            "projectBinding": project_binding,
            "sourceBinding": source_binding,
        },
    )
    sorted_elements = sorted(elements.values(), key=lambda item: item["id"])
    support_matrix = _build_support_matrix(
        adapter_inputs, adapter_outputs, adapter_errors
    )
    observation: dict[str, Any] = {
        "formatVersion": "panorama-source-topology-observation.v0.2",
        "observationId": observation_id,
        "observedAt": observed_at,
        "producer": PRODUCER,
        "projectBinding": project_binding,
        "sourceBinding": source_binding,
        "inventory": {
            "files": public_inventory,
            "excluded": {
                "ignored": excluded["ignored"],
                "unsupported": excluded["unsupported"],
                "secretRisk": excluded["secretRisk"],
            },
            "limits": {
                "maxFiles": MAX_FILES,
                "maxFileBytes": MAX_FILE_BYTES,
                "maxTotalBytes": MAX_TOTAL_BYTES,
                "maxDepth": MAX_DEPTH,
            },
        },
        "elements": sorted_elements,
        "relations": relations,
        "informationGaps": sorted(information_gaps),
        "extensions": {
            "adapterRegistry": registry_projection(),
            "supportMatrix": support_matrix,
            "stackProfile": _build_stack_profile(sorted_elements, relations),
            "monorepoBoundaries": _build_monorepo_boundaries(sorted_elements),
        },
    }
    observation["integrity"] = {
        "hashAlgorithm": "sha256",
        "semanticHash": compute_observation_semantic_hash(observation),
        "hashScope": "observation_without_integrity",
    }
    observation_errors = validate_source_observation(observation)
    if observation_errors:
        raise SourceTopologyError(
            "Source Observation 无效：" + "; ".join(observation_errors[:10])
        )

    unresolved = sum(
        item["resolution"] in {"unresolved", "dynamic"} for item in relations
    )
    parse_failures = sum(item["parseStatus"] == "failed" for item in public_inventory)
    partial = bool(
        parse_failures
        or unresolved
        or excluded["unsupportedSource"]
        or any(
            item["severity"] in {"partial", "unsupported", "blocked"}
            for item in losses
        )
    )
    loss_report: dict[str, Any] = {
        "formatVersion": "panorama-transformation-loss-report.v0.1",
        "reportId": _derived_id(
            "LOSS",
            {
                "observationId": observation_id,
                "observationHash": observation["integrity"]["semanticHash"],
            },
        ),
        "generatedAt": observed_at,
        "adapter": PRODUCER,
        "inputBinding": {
            "format": "repository_source_set",
            "revision": revision,
            "sha256": content_digest,
        },
        "outputBinding": {
            "format": "panorama-source-topology-observation.v0.2",
            "revision": observation_id,
            "sha256": observation["integrity"]["semanticHash"],
        },
        "status": "partial" if partial else "lossless",
        "counts": {
            "input": len(public_inventory),
            "output": len(observation["elements"]) + len(relations),
            "preserved": len(public_inventory) - parse_failures,
            "lost": parse_failures,
            "degraded": unresolved,
            "unknown": sum(item["resolution"] == "dynamic" for item in relations),
        },
        "preservation": {
            "elements": "partial" if parse_failures else "preserved",
            "relationships": "partial" if unresolved else "preserved",
            "views": "not_applicable",
            "order": "not_applicable",
            "timestamps": "preserved",
            "evidence": "preserved",
            "layout": "not_applicable",
            "documentsAndAdrs": "not_applicable",
        },
        "losses": sorted(
            losses, key=lambda item: (item["sourceRef"], item["kind"], item["reason"])
        ),
        "informationGaps": sorted(information_gaps),
        "extensions": {},
    }
    loss_errors = _schema_errors(loss_report, LOSS_SCHEMA)
    if loss_errors:
        raise SourceTopologyError("Loss Report 无效：" + "; ".join(loss_errors[:10]))

    receipt_adapters: dict[str, dict[str, Any]] = {}

    def add_receipt_adapter(
        adapter_id: str,
        version: str,
        keys: list[str],
        *,
        bounded: bool,
    ) -> None:
        target = receipt_adapters.setdefault(
            adapter_id,
            {
                "id": adapter_id,
                "version": version,
                "status": "not_applicable",
                "inputCount": 0,
                "outputCount": 0,
                "errorCount": 0,
                "_bounded": False,
            },
        )
        target["inputCount"] += sum(adapter_inputs[key] for key in keys)
        target["outputCount"] += sum(adapter_outputs[key] for key in keys)
        target["errorCount"] += sum(adapter_errors[key] for key in keys)
        target["_bounded"] = target["_bounded"] or bounded

    add_receipt_adapter("python-ast-imports", "0.1.0", ["python"], bounded=False)
    add_receipt_adapter(
        "javascript-static-imports", "0.1.0",
        ["javascript", "typescript"], bounded=True,
    )
    add_receipt_adapter("java-static-imports", "0.1.0", ["java"], bounded=True)
    add_receipt_adapter("manifest-dependencies", "0.3.0", ["manifest"], bounded=False)
    add_receipt_adapter(
        "multistack-manifest-dependencies", "0.1.0",
        ["multistack_manifest"], bounded=False,
    )
    add_receipt_adapter(
        "java-properties-metadata", "0.1.0", ["properties"], bounded=False
    )
    for language, adapter in sorted(LANGUAGE_ADAPTERS.items()):
        if language in {"python", "javascript", "typescript", "java"}:
            continue
        add_receipt_adapter(
            adapter["id"], adapter["version"], [language], bounded=True
        )

    adapters = []
    for adapter_id in sorted(receipt_adapters):
        item = receipt_adapters[adapter_id]
        count = item["inputCount"]
        item["status"] = (
            "not_applicable"
            if count == 0
            else "partial"
            if item["errorCount"] or item.pop("_bounded")
            else "completed"
        )
        item.pop("_bounded", None)
        adapters.append(item)
    receipt_id = _derived_id(
        "SRCREC",
        {
            "observationId": observation_id,
            "observationHash": observation["integrity"]["semanticHash"],
            "lossReportId": loss_report["reportId"],
        },
    )
    receipt: dict[str, Any] = {
        "formatVersion": "panorama-extraction-receipt.v0.1",
        "receiptId": receipt_id,
        "observedAt": observed_at,
        "producer": PRODUCER,
        "projectBinding": project_binding,
        "inputBinding": {
            "mode": source_binding["mode"],
            "gitHead": git_head,
            "contentDigest": content_digest,
            "fileCount": len(public_inventory),
        },
        "outputBinding": {
            "observationId": observation_id,
            "observationHash": observation["integrity"]["semanticHash"],
            "lossReportId": loss_report["reportId"],
            "lossReportHash": compute_canonical_hash(loss_report),
        },
        "status": "partial" if partial else "completed",
        "counts": {
            "filesDiscovered": excluded["supportedDiscovered"],
            "filesRead": len(public_inventory),
            "elements": len(observation["elements"]),
            "relations": len(relations),
            "unresolvedRelations": unresolved,
            "parseFailures": parse_failures,
            "secretRiskExcluded": excluded["secretRisk"],
        },
        "adapters": adapters,
        "safety": {
            "networkAccessed": False,
            "projectMutated": False,
            "sourceBodiesReadTransiently": bool(public_inventory),
            "sourceBodyPersisted": False,
            "classifiedSecretPathsRead": False,
            "embeddedSecretScan": "not_performed",
            "externalPrivateDataRead": False,
            "symlinkPolicy": "reject_in_scope",
        },
        "extensions": {},
    }
    receipt["integrity"] = {
        "hashAlgorithm": "sha256",
        "receiptHash": compute_receipt_hash(receipt),
        "hashScope": "receipt_without_integrity",
    }
    receipt_errors = validate_extraction_receipt(receipt, observation, loss_report)
    if receipt_errors:
        raise SourceTopologyError(
            "Extraction Receipt 无效：" + "; ".join(receipt_errors[:10])
        )
    return {"observation": observation, "receipt": receipt, "lossReport": loss_report}
