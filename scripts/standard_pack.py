"""Load, validate, and deterministically assess project Standard Packs."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

from panorama_io import compute_canonical_hash, compute_data_hash


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schema" / "standard-pack.schema.v0.1.json"


class StandardPackError(RuntimeError):
    """A Standard Pack or evaluator is invalid or unsafe."""


def load_document(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ModuleNotFoundError as exc:
                raise StandardPackError("读取 YAML Standard Pack 需要 PyYAML。") from exc
            value = yaml.safe_load(text)
        else:
            value = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StandardPackError(f"无法读取 Standard Pack {source}：{exc}") from exc
    if not isinstance(value, dict):
        raise StandardPackError("Standard Pack 必须是对象。")
    return value


def validate_standard_pack(
    pack: dict[str, Any], schema_path: str | Path = DEFAULT_SCHEMA
) -> list[str]:
    try:
        import jsonschema
    except ModuleNotFoundError as exc:
        raise StandardPackError("校验 Standard Pack 需要 jsonschema。") from exc
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(pack), key=lambda item: list(item.path))
    messages = [
        "/" + "/".join(str(part) for part in error.absolute_path) + ": " + error.message
        for error in errors
    ]
    ids = [rule.get("id") for rule in pack.get("rules", []) if isinstance(rule, dict)]
    if len(ids) != len(set(ids)):
        messages.append("/rules: Rule ID 必须唯一。")
    return messages


def standard_pack_hash(pack: dict[str, Any]) -> str:
    return compute_canonical_hash(pack)


def _safe_relative(root: Path, supplied: str) -> tuple[Path, str]:
    relative = Path(supplied)
    if relative.is_absolute() or ".." in relative.parts:
        raise StandardPackError(f"规范路径必须位于项目根内：{supplied!r}")
    candidate = root / relative
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StandardPackError(f"规范路径越出项目根：{supplied!r}") from exc
    if candidate.is_symlink():
        raise StandardPackError(f"规范评估拒绝符号链接：{supplied!r}")
    return candidate, relative.as_posix()


def _pointer_value(document: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, document
    if not pointer.startswith("/"):
        return False, None
    value = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list):
            try:
                value = value[int(token)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, value


def _git_tracked(root: Path, relative: str) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode == 0


def evaluate_rule(
    rule: dict[str, Any], project_root: Path, panorama: dict[str, Any]
) -> tuple[str, str, list[str]]:
    evaluator = rule["evaluator"]
    evaluator_type = evaluator["type"]
    if evaluator_type == "manual":
        return "unknown", "该规则需要人工或外部证据，未自动判定。", []
    if evaluator_type in {"path_exists", "path_absent"}:
        candidate, relative = _safe_relative(project_root, evaluator["path"])
        exists = candidate.exists()
        passed = exists if evaluator_type == "path_exists" else not exists
        verb = "存在" if exists else "未检测到"
        return ("pass" if passed else "fail", f"路径 {relative} {verb}。", [relative])
    if evaluator_type == "path_glob_exists":
        pattern = evaluator["pattern"]
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise StandardPackError(f"Glob 必须位于项目根内：{pattern!r}")
        matches = []
        for candidate in project_root.glob(pattern):
            try:
                relative = candidate.resolve(strict=False).relative_to(project_root)
            except ValueError:
                continue
            if not candidate.is_symlink():
                matches.append(relative.as_posix())
        matches.sort()
        return (
            "pass" if matches else "fail",
            f"Glob {pattern!r} 匹配 {len(matches)} 个安全路径。",
            matches[:20],
        )
    if evaluator_type == "git_tracked":
        _, relative = _safe_relative(project_root, evaluator["path"])
        tracked = _git_tracked(project_root, relative)
        if tracked is None:
            return "unknown", "无法读取 Git tracked 状态。", [relative]
        return (
            "pass" if tracked else "fail",
            f"路径 {relative} {'已' if tracked else '未'}被 Git 跟踪。",
            [relative],
        )
    if evaluator_type in {"panorama_pointer_exists", "panorama_pointer_equals"}:
        exists, value = _pointer_value(panorama, evaluator["pointer"])
        if evaluator_type == "panorama_pointer_exists":
            passed = exists
        else:
            passed = exists and value == evaluator.get("expected")
        return (
            "pass" if passed else "fail",
            f"Panorama Pointer {evaluator['pointer']} "
            + ("满足规则。" if passed else "不满足规则。"),
            [evaluator["pointer"]],
        )
    raise StandardPackError(f"不支持的声明式 Evaluator：{evaluator_type}")


def assess_standard_pack(
    pack: dict[str, Any],
    project_root: str | Path,
    panorama: dict[str, Any],
    *,
    source_commit: str | None,
    assessed_at: str,
) -> dict[str, Any]:
    errors = validate_standard_pack(pack)
    if errors:
        raise StandardPackError("Standard Pack 校验失败：\n" + "\n".join(errors))
    root = Path(project_root).resolve(strict=True)
    results = []
    for rule in pack["rules"]:
        status, message, evidence = evaluate_rule(rule, root, panorama)
        results.append(
            {
                "ruleId": rule["id"],
                "category": rule["category"],
                "status": status,
                "severity": rule["severity"],
                "message": message,
                "evidence": evidence,
                "relatedEntities": copy.deepcopy(rule.get("relatedEntities", [])),
            }
        )
    pack_hash = standard_pack_hash(pack)
    suffix = compute_canonical_hash(
        [pack["id"], pack["version"], pack_hash, source_commit, assessed_at]
    )[:16].upper()
    return {
        "id": f"STD-ASSESS-{suffix}",
        "standardPackId": pack["id"],
        "standardPackVersion": pack["version"],
        "standardPackHash": pack_hash,
        "sourceCommit": source_commit,
        "panoramaDataHash": compute_data_hash(panorama),
        "assessedAt": assessed_at,
        "results": results,
        "extensions": {},
    }


__all__ = [
    "DEFAULT_SCHEMA",
    "StandardPackError",
    "assess_standard_pack",
    "load_document",
    "standard_pack_hash",
    "validate_standard_pack",
]
