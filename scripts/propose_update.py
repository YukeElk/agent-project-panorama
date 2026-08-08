"""Build a deterministic, non-mutating Panorama update proposal."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from apply_patch import apply_operations, compose_updated_data, compute_proposal_hash
from panorama_cli import ChineseArgumentParser
from panorama_io import compute_data_hash, extract_data
from validate_panorama import ValidationIssue, validate_data


RULE_TO_ATTENTION = {
    "ARCHITECTURE_GAP": "architecture_gap",
    "SCOPE_DRIFT": "scope_drift",
    "IMPLEMENTATION_DRIFT": "implementation_drift",
    "REVIEW_GAP": "review_required",
    "VERIFICATION_GAP": "verification_gap",
    "BASELINE_DRIFT": "baseline_drift",
    "DEPLOYMENT_DRIFT": "deployment_drift",
    "TRANSITION_RISK": "transition_risk",
    "RESOURCE_RISK": "resource_risk",
    "STAGE_ENTRY_GAP": "stage_exit_gap",
    "STAGE_EXIT_GAP": "stage_exit_gap",
    "GIT_SECRET_RISK": "other",
}

FINDING_TITLES = {
    "ARCHITECTURE_GAP": "架构缺口",
    "SCOPE_DRIFT": "范围漂移",
    "IMPLEMENTATION_DRIFT": "实现漂移",
    "REVIEW_GAP": "评审缺口",
    "VERIFICATION_GAP": "验证缺口",
    "BASELINE_DRIFT": "基线漂移",
    "DEPLOYMENT_DRIFT": "部署漂移",
    "TRANSITION_RISK": "迁移风险",
    "RESOURCE_RISK": "资源风险",
    "STAGE_ENTRY_GAP": "阶段准入缺口",
    "STAGE_EXIT_GAP": "阶段退出缺口",
    "GIT_SECRET_RISK": "Git 凭据风险",
}


def _pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def json_diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    """Return a deterministic minimal-enough add/replace/remove subset."""

    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        operations: list[dict[str, Any]] = []
        for key in sorted(before.keys() - after.keys(), reverse=True):
            operations.append({"op": "remove", "path": f"{path}/{_pointer(key)}"})
        for key in sorted(after.keys() - before.keys()):
            operations.append(
                {"op": "add", "path": f"{path}/{_pointer(key)}", "value": copy.deepcopy(after[key])}
            )
        for key in sorted(before.keys() & after.keys()):
            operations.extend(json_diff(before[key], after[key], f"{path}/{_pointer(key)}"))
        return operations
    if not path:
        raise ValueError("Panorama 提案不支持替换根对象。")
    return [{"op": "replace", "path": path, "value": copy.deepcopy(after)}]


def _stable_suffix(candidate: dict[str, Any]) -> str:
    canonical = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10].upper()


ENTITY_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "requirement": ("requirements",),
    "module": ("architecture", "modules"),
    "connection": ("architecture", "connections"),
    "architecture_version": ("architecture", "versions"),
    "stage": ("stages",),
    "release": ("releases",),
    "deployment": ("deployments",),
    "resource": ("resources",),
    "decision": ("decisions",),
    "risk": ("risks",),
    "acceptance": ("acceptanceCriteria",),
    "gate": ("gates",),
    "reference": ("references",),
}


def _collection(data: dict[str, Any], path: tuple[str, ...]) -> list[Any]:
    value: Any = data
    for token in path:
        if not isinstance(value, dict):
            return []
        value = value.get(token, [])
    return value if isinstance(value, list) else []


def _entity_registry(data: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    registry: dict[str, dict[str, dict[str, Any]]] = {}
    for entity_type, collection_path in ENTITY_COLLECTIONS.items():
        registry[entity_type] = {
            item["id"]: item
            for item in _collection(data, collection_path)
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
    return registry


def _pointer_parts(pointer: Any) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return []
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _path_entity_refs(
    data: dict[str, Any], operation: dict[str, Any]
) -> set[tuple[str, str]]:
    """Resolve entity identities from a JSON Pointer and structured value."""

    parts = _pointer_parts(operation.get("path"))
    refs: set[tuple[str, str]] = set()
    for entity_type, collection_path in ENTITY_COLLECTIONS.items():
        prefix = list(collection_path)
        if parts[: len(prefix)] != prefix:
            continue
        items = _collection(data, collection_path)
        if len(parts) > len(prefix):
            try:
                index = int(parts[len(prefix)])
            except (TypeError, ValueError):
                index = -1
            if 0 <= index < len(items) and isinstance(items[index], dict):
                entity_id = items[index].get("id")
                if isinstance(entity_id, str):
                    refs.add((entity_type, entity_id))
        if len(parts) > len(prefix):
            value = operation.get("value")
            if isinstance(value, dict) and isinstance(value.get("id"), str):
                refs.add((entity_type, value["id"]))
        break
    return refs


def affected_entity_refs(
    current: dict[str, Any], preview: dict[str, Any], operations: list[dict[str, Any]]
) -> list[dict[str, str]]:
    current_registry = _entity_registry(current)
    preview_registry = _entity_registry(preview)
    refs: set[tuple[str, str]] = set()

    # Registry comparison detects additions, removals, and modifications without
    # mistaking an ID-shaped substring in free text for an entity reference.
    for entity_type in ENTITY_COLLECTIONS:
        before = current_registry[entity_type]
        after = preview_registry[entity_type]
        for entity_id in before.keys() | after.keys():
            if before.get(entity_id) != after.get(entity_id):
                refs.add((entity_type, entity_id))

    # Pointer/value resolution preserves the primary entity identity for indexed
    # operations (especially remove and add /- operations) in both states.
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if operation.get("op") in {"remove", "replace"}:
            refs.update(_path_entity_refs(current, operation))
        if operation.get("op") in {"add", "replace"}:
            refs.update(_path_entity_refs(preview, operation))

    if not refs:
        return [{"type": "project", "id": current["project"]["id"]}]
    return [{"type": entity_type, "id": entity_id} for entity_type, entity_id in sorted(refs)]


def attention_from_findings(findings: list[ValidationIssue]) -> list[dict[str, Any]]:
    """Convert every high/critical rule finding without hiding it."""

    result = []
    for issue in findings:
        if issue.severity not in {"high", "critical"} or issue.code not in RULE_TO_ATTENTION:
            continue
        result.append(
            {
                "type": RULE_TO_ATTENTION[issue.code],
                "severity": issue.severity,
                "title": FINDING_TITLES[issue.code],
                "summary": issue.message,
                "relatedEntities": copy.deepcopy(list(issue.related_entities)),
            }
        )
    return result


def build_proposal(
    current: dict[str, Any],
    candidate: dict[str, Any],
    schema_path: Path,
    *,
    base_dir: Path,
    source_path: Path | None = None,
) -> dict[str, Any]:
    if "schemaVersion" in candidate:
        operations = json_diff(current, candidate)
        supplied: dict[str, Any] = {}
    else:
        supplied = candidate
        operations = copy.deepcopy(candidate.get("operations", []))
    suffix = _stable_suffix(candidate)
    timestamp = candidate.get("createdAt") or current.get("meta", {}).get("updatedAt")
    level = candidate.get("changeLevel", "local")
    summary = candidate.get("summary", "评审拟议的 Panorama 数据变更。")
    operation_preview = apply_operations(current, operations)
    refs = affected_entity_refs(current, operation_preview, operations)
    change_id = f"CHG-PROP-{suffix}"
    review_id = f"REV-PROP-{suffix}"
    update_id = f"UPD-PROP-{suffix}"
    change_records = copy.deepcopy(supplied.get("changeRecords")) if "changeRecords" in supplied else [{
        "id": change_id, "occurredAt": timestamp, "category": "other", "impactLevel": level,
        "summary": summary, "reason": candidate.get("reason", "候选更新已提交评审。"),
        "source": "ai", "entityRefs": refs, "beforeSummary": candidate.get("beforeSummary", "当前 Panorama 状态"),
        "afterSummary": candidate.get("afterSummary", summary), "reviewId": review_id,
        "significant": level != "local", "architectureVersionId": None, "referenceIds": [], "extensions": {},
    }]
    review = copy.deepcopy(supplied.get("reviewDraft")) if "reviewDraft" in supplied else {
        "id": review_id, "type": "panorama_update", "subjectRefs": refs, "status": "pending",
        "impactLevel": level, "requestedBy": "ai", "requestedAt": timestamp,
        "reviewedBy": "", "reviewedAt": None, "summary": summary,
        "comments": "只有绑定此 proposalHash 的批准才有效。", "decisionIds": [],
        "changeIds": [item["id"] for item in change_records], "extensions": {},
    }
    if not isinstance(review, dict):
        raise ValueError("reviewDraft 必须是 JSON 对象。")
    review["status"] = "pending"
    review["reviewedBy"] = ""
    review["reviewedAt"] = None
    guidance = copy.deepcopy(supplied.get("guidanceDraft", current.get("guidance", {})))
    focus_ids = [item.get("id") for item in guidance.get("options", []) if isinstance(item, dict)]
    batch = copy.deepcopy(supplied.get("updateBatchDraft")) if "updateBatchDraft" in supplied else {
        "id": update_id, "revisionFrom": current["meta"]["revision"], "revisionTo": current["meta"]["revision"] + 1,
        "periodStart": timestamp, "periodEnd": timestamp, "createdAt": timestamp, "status": "approved",
        "reviewId": review["id"], "changeIds": [item["id"] for item in change_records],
        "summaryItems": [summary], "attentionItems": [], "nextFocusOptionIds": focus_ids,
        "projectStageBefore": current["project"]["currentStageId"], "projectStageAfter": current["project"]["currentStageId"],
        "changeLevel": level, "extensions": {},
    }
    package = {
        "baseRevision": current["meta"]["revision"], "baseDataHash": compute_data_hash(current),
        "operations": operations, "changeRecords": change_records, "reviewDraft": review,
        "updateBatchDraft": batch, "guidanceDraft": guidance,
    }
    preview = compose_updated_data(current, package)
    report = validate_data(preview, schema_path, base_dir=base_dir, source_path=source_path)
    generated_attention = attention_from_findings(report.issues)
    existing_attention = batch.setdefault("attentionItems", [])
    existing_keys = {(item.get("type"), item.get("summary")) for item in existing_attention}
    existing_attention.extend(
        item for item in generated_attention if (item["type"], item["summary"]) not in existing_keys
    )
    package["proposalHash"] = compute_proposal_hash(package)
    package["approval"] = {
        "status": "pending", "approvedBy": "", "approvedAt": None,
        "proposalHash": package["proposalHash"],
    }
    return {
        "proposal": package,
        "facts": {
            "baseRevision": package["baseRevision"], "baseDataHash": package["baseDataHash"],
            "operationCount": len(operations), "affectedEntities": refs,
        },
        "validation": report.to_dict(),
    }


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = ChineseArgumentParser(description="创建不修改源文件的 Panorama 更新提案。")
    parser.add_argument("panorama", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--schema", type=Path, default=root / "schema" / "panorama.schema.v0.1.json")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        current = extract_data(args.panorama)
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise ValueError("候选更新必须是 JSON 对象。")
        result = build_proposal(current, candidate, args.schema, base_dir=args.panorama.parent, source_path=args.panorama)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
            print(f"提案已写入：{args.output}")
        else:
            print(rendered)
        return 1 if not result["validation"]["valid"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"提案错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
