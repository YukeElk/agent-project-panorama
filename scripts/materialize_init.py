"""Deterministically materialize an approved INIT Preview into Panorama data."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable

from observe_project_runtime import source_snapshot
from panorama_cli import ChineseArgumentParser
from validate_panorama import ValidationIssue, validate_data


PREPARED_VERSION = "prepared-init-review.v0.1"
APPROVAL_VERSION = "init-approval.v0.1"
APPROVAL_METHOD = "explicit_hash_confirmation"
APPROVAL_TIME_SOURCE = "approval_recorder_clock"
PREPARED_FIELDS = {
    "preparedVersion",
    "preparedAt",
    "preview",
    "previewHash",
    "sourceSnapshot",
    "validation",
    "approvalState",
}
APPROVAL_FIELDS = {
    "approvalVersion",
    "status",
    "previewHash",
    "approvedBy",
    "approvalRecordedAt",
    "approvalMethod",
    "approvalTimeSource",
}
FINDING_CLASSES = {
    "PROJECT_GAP",
    "PANORAMA_EVIDENCE_GAP",
    "PANORAMA_MODEL_GAP",
    "CONTROL_BLOCKER",
    "INFORMATIONAL",
}
PROVENANCE_VALUES = {
    "observed_now",
    "generated_current_state",
    "current_test",
    "historical_test",
    "approved_design",
    "narrative_reference",
    "inference",
}
ACCESS_CLASSES = {
    "PUBLIC_PROJECT_EVIDENCE",
    "PROJECT_OPERATIONAL_METADATA",
    "PROJECT_CONTENT",
    "SECRET",
    "EXTERNAL_PRIVATE_DATA",
}

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

ATTENTION_TITLES = {
    ("VERIFICATION_GAP", "PANORAMA_EVIDENCE_GAP"): "验证证据映射不完整",
    ("VERIFICATION_GAP", "PROJECT_GAP"): "项目验证缺口",
    ("TRANSITION_RISK", "CONTROL_BLOCKER"): "架构迁移阻塞",
    ("DEPLOYMENT_DRIFT", "CONTROL_BLOCKER"): "发布或部署阻塞",
    ("STAGE_ENTRY_GAP", "CONTROL_BLOCKER"): "阶段准入阻塞",
    ("STAGE_EXIT_GAP", "CONTROL_BLOCKER"): "阶段退出阻塞",
}

PRIORITY_BY_CODE = {
    "STAGE_ENTRY_GAP": 1,
    "STAGE_EXIT_GAP": 1,
    "DEPLOYMENT_DRIFT": 2,
    "TRANSITION_RISK": 2,
    "ARCHITECTURE_GAP": 2,
    "VERIFICATION_GAP": 3,
    "REVIEW_GAP": 4,
    "RESOURCE_RISK": 5,
    "MISSING_REFERENCE_PATH": 6,
}


class InitMaterializationError(RuntimeError):
    """An approval, source-snapshot, model, or validation failure."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_preview_hash(preview: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(preview).encode("utf-8")).hexdigest()


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InitMaterializationError(f"{field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InitMaterializationError(f"{field} is not an ISO timestamp") from exc
    if parsed.utcoffset() is None:
        raise InitMaterializationError(f"{field} must include a UTC offset")
    return value


def _issue_dict(issue: ValidationIssue | dict[str, Any]) -> dict[str, Any]:
    if isinstance(issue, ValidationIssue):
        return issue.to_dict()
    return copy.deepcopy(issue)


def _evidence_for_entities(
    evidence_inventory: list[dict[str, Any]],
    related_entities: Iterable[dict[str, str]],
    *,
    fact_class: str | None = None,
) -> list[dict[str, Any]]:
    targets = {
        (item.get("type"), item.get("id"))
        for item in related_entities
        if isinstance(item, dict)
    }
    matched = []
    for evidence in evidence_inventory:
        if not isinstance(evidence, dict):
            continue
        if fact_class and evidence.get("factClass") != fact_class:
            continue
        if evidence.get("accessClass") not in {
            "PUBLIC_PROJECT_EVIDENCE",
            "PROJECT_OPERATIONAL_METADATA",
        }:
            continue
        if evidence.get("provenance") not in {
            "observed_now",
            "generated_current_state",
            "current_test",
            "historical_test",
        }:
            continue
        evidence_entities = {
            (item.get("type"), item.get("id"))
            for item in evidence.get("relatedEntities", [])
            if isinstance(item, dict)
        }
        if targets & evidence_entities:
            matched.append(evidence)
    return matched


def _blocked_transition(
    data: dict[str, Any], related_entities: list[dict[str, str]]
) -> bool:
    transition_ids = {
        item.get("id")
        for item in related_entities
        if item.get("type") == "transition"
    }
    return any(
        item.get("id") in transition_ids and item.get("state") == "blocked"
        for item in data.get("architecture", {}).get("transitions", [])
        if isinstance(item, dict)
    )


def classify_finding(
    issue: ValidationIssue | dict[str, Any],
    evidence_inventory: list[dict[str, Any]],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Classify one actual validator finding without changing its severity."""

    item = _issue_dict(issue)
    code = item.get("code", "")
    severity = item.get("severity", "warning")
    related = item.get("relatedEntities", [])
    evidence = _evidence_for_entities(
        evidence_inventory, related, fact_class="VERIFICATION"
    )
    if item.get("level") == "INFO" or severity == "info":
        classification = "INFORMATIONAL"
    elif code == "VERIFICATION_GAP":
        classification = (
            "PANORAMA_EVIDENCE_GAP" if evidence else "PROJECT_GAP"
        )
    elif code in {"STAGE_ENTRY_GAP", "STAGE_EXIT_GAP"}:
        classification = "CONTROL_BLOCKER"
    elif code == "TRANSITION_RISK" and _blocked_transition(data, related):
        classification = "CONTROL_BLOCKER"
    elif code == "DEPLOYMENT_DRIFT" and severity in {"high", "critical"}:
        classification = "CONTROL_BLOCKER"
    elif code in {"BROKEN_REFERENCE", "BROKEN_ENTITY_REF", "CURRENT_STAGE_MISMATCH"}:
        classification = "PANORAMA_MODEL_GAP"
    elif code in {"MISSING_REFERENCE_PATH"}:
        classification = "PANORAMA_EVIDENCE_GAP"
    elif code in {"ARCHITECTURE_GAP", "IMPLEMENTATION_DRIFT"}:
        classification = "PANORAMA_MODEL_GAP"
    elif code == "GIT_SECRET_RISK":
        classification = "CONTROL_BLOCKER"
    else:
        classification = "PROJECT_GAP"
    if classification not in FINDING_CLASSES:
        raise InitMaterializationError(f"unsupported finding class: {classification}")
    return {
        **item,
        "classification": classification,
        "priority": PRIORITY_BY_CODE.get(code, 7),
        "evidencePaths": sorted(
            {
                evidence_item.get("path")
                for evidence_item in evidence
                if isinstance(evidence_item.get("path"), str)
            }
        ),
    }


def _attention_group(item: dict[str, Any]) -> tuple[str, str]:
    code = item["code"]
    classification = item["classification"]
    if code == "VERIFICATION_GAP":
        return "verification", classification
    if classification == "CONTROL_BLOCKER":
        if code in {"STAGE_ENTRY_GAP", "STAGE_EXIT_GAP"}:
            return "stage", classification
        if code in {"DEPLOYMENT_DRIFT"}:
            return "release-deployment", classification
        if code in {"ARCHITECTURE_GAP", "TRANSITION_RISK"}:
            return "architecture-transition", classification
    return code.lower(), classification


def reconcile_findings(
    issues: Iterable[ValidationIssue | dict[str, Any]],
    evidence_inventory: list[dict[str, Any]],
    data: dict[str, Any],
) -> dict[str, Any]:
    classified = [
        classify_finding(issue, evidence_inventory, data) for issue in issues
    ]
    actionable = [
        item
        for item in classified
        if item.get("severity") in {"high", "critical"}
        and item["classification"] != "INFORMATIONAL"
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in actionable:
        groups.setdefault(_attention_group(item), []).append(item)

    severity_rank = {"info": 0, "warning": 1, "high": 2, "critical": 3}
    attention_with_priority = []
    for _, items in groups.items():
        items.sort(
            key=lambda item: (
                item["priority"],
                item["code"],
                item.get("message", ""),
            )
        )
        first = items[0]
        related = {
            (entity.get("type"), entity.get("id")): {
                "type": entity.get("type"),
                "id": entity.get("id"),
                **(
                    {"label": entity.get("label")}
                    if isinstance(entity.get("label"), str)
                    else {}
                ),
            }
            for item in items
            for entity in item.get("relatedEntities", [])
            if isinstance(entity, dict)
            and isinstance(entity.get("type"), str)
            and isinstance(entity.get("id"), str)
        }
        severity = max(
            (item["severity"] for item in items),
            key=lambda value: severity_rank.get(value, 0),
        )
        title = ATTENTION_TITLES.get(
            (first["code"], first["classification"]),
            f"{first['classification']} · {first['code']}",
        )
        messages = [item.get("message", "") for item in items[:3]]
        suffix = "" if len(items) <= 3 else f"；另有 {len(items) - 3} 项"
        attention_with_priority.append(
            (
                first["priority"],
                {
                    "type": RULE_TO_ATTENTION.get(first["code"], "other"),
                    "severity": severity,
                    "title": title,
                    "summary": f"{len(items)} 个 {first['classification']} Finding："
                    + "；".join(messages)
                    + suffix,
                    "relatedEntities": [
                        related[key] for key in sorted(related)
                    ],
                },
            )
        )
    attention = [
        item
        for _, item in sorted(
            attention_with_priority,
            key=lambda pair: (pair[0], pair[1]["title"]),
        )
    ]
    if actionable and not attention:
        raise InitMaterializationError(
            "High/Critical findings exist but CONTROL Attention is empty"
        )
    return {
        "formalFindingCount": len(classified),
        "formalHighCriticalCount": len(actionable),
        "attentionCount": len(attention),
        "classifiedFindings": classified,
        "attentionItems": attention,
    }


def _write_reconciliation(
    batch: dict[str, Any], reconciliation: dict[str, Any]
) -> None:
    batch["attentionItems"] = copy.deepcopy(reconciliation["attentionItems"])
    extensions = batch.setdefault("extensions", {})
    extensions["findingReconciliation"] = copy.deepcopy(
        reconciliation["classifiedFindings"]
    )
    extensions["formalFindingCount"] = reconciliation["formalFindingCount"]
    extensions["formalHighCriticalCount"] = reconciliation[
        "formalHighCriticalCount"
    ]
    extensions["attentionClusterCount"] = reconciliation["attentionCount"]


def _reconciliation_matches(
    batch: dict[str, Any], reconciliation: dict[str, Any]
) -> bool:
    extensions = batch.get("extensions", {})
    return bool(
        _canonical_json(batch.get("attentionItems", []))
        == _canonical_json(reconciliation["attentionItems"])
        and _canonical_json(extensions.get("findingReconciliation", []))
        == _canonical_json(reconciliation["classifiedFindings"])
        and extensions.get("formalFindingCount")
        == reconciliation["formalFindingCount"]
        and extensions.get("formalHighCriticalCount")
        == reconciliation["formalHighCriticalCount"]
        and extensions.get("attentionClusterCount")
        == reconciliation["attentionCount"]
    )


def _find_entity(data: dict[str, Any], entity_type: str, entity_id: str) -> dict[str, Any] | None:
    paths = {
        "requirement": ("requirements",),
        "architecture_version": ("architecture", "versions"),
        "module": ("architecture", "modules"),
        "stage": ("stages",),
        "decision": ("decisions",),
        "acceptance": ("acceptanceCriteria",),
    }
    path = paths.get(entity_type)
    if path is None:
        return None
    value: Any = data
    for token in path:
        value = value.get(token, []) if isinstance(value, dict) else []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and item.get("id") == entity_id:
            return item
    return None


def _bind_initial_review(
    data: dict[str, Any], subject_refs: list[dict[str, str]], review_id: str
) -> None:
    for ref in subject_refs:
        entity_type = ref.get("type")
        if entity_type == "intent":
            data["intent"]["reviewId"] = review_id
            continue
        entity = _find_entity(data, str(entity_type), str(ref.get("id")))
        if entity is None:
            continue
        if entity_type == "module":
            review_ids = entity.setdefault("reviewIds", [])
            if review_id not in review_ids:
                review_ids.append(review_id)
        elif entity_type in {
            "requirement",
            "architecture_version",
            "stage",
            "decision",
            "acceptance",
        }:
            entity["reviewId"] = review_id


def _materialize_changes(
    change_intents: list[dict[str, Any]],
    *,
    suffix: str,
    review_id: str,
    approved_at: str,
    preview_hash: str,
) -> list[dict[str, Any]]:
    if not change_intents:
        raise InitMaterializationError("approved Preview must contain semantic changeIntents")
    required = {
        "category",
        "impactLevel",
        "summary",
        "reason",
        "source",
        "entityRefs",
        "beforeSummary",
        "afterSummary",
        "significant",
        "referenceIds",
    }
    records = []
    for index, intent in enumerate(change_intents, start=1):
        missing = required - set(intent) if isinstance(intent, dict) else required
        if missing:
            raise InitMaterializationError(
                f"changeIntents[{index - 1}] missing fields: {sorted(missing)}"
            )
        records.append(
            {
                "id": f"CHG-INIT-{suffix}-{index:02d}",
                "occurredAt": approved_at,
                "category": intent["category"],
                "impactLevel": intent["impactLevel"],
                "summary": intent["summary"],
                "reason": intent["reason"],
                "source": intent["source"],
                "entityRefs": copy.deepcopy(intent["entityRefs"]),
                "beforeSummary": intent["beforeSummary"],
                "afterSummary": intent["afterSummary"],
                "reviewId": review_id,
                "significant": intent["significant"],
                "architectureVersionId": intent.get("architectureVersionId"),
                "referenceIds": copy.deepcopy(intent["referenceIds"]),
                "extensions": {
                    **copy.deepcopy(intent.get("extensions", {})),
                    "initPreviewHash": preview_hash,
                },
            }
        )
    return records


def assert_release_semantics(data: dict[str, Any]) -> None:
    """Reject a candidate/planned Release being presented as current."""

    current_id = data.get("project", {}).get("currentReleaseId")
    if not current_id:
        return
    release = next(
        (
            item
            for item in data.get("releases", [])
            if isinstance(item, dict) and item.get("id") == current_id
        ),
        None,
    )
    if release is not None and release.get("status") != "deployed":
        raise InitMaterializationError(
            "candidate/planned release cannot be currentReleaseId; "
            "correct the draft before approval"
        )


def _sanitize_evidence_inventory(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_items = []
    for item in items:
        if not isinstance(item, dict):
            raise InitMaterializationError("evidenceInventory items must be objects")
        access_class = item.get("accessClass")
        provenance = item.get("provenance")
        if access_class not in ACCESS_CLASSES:
            raise InitMaterializationError(f"invalid evidence accessClass: {access_class}")
        if provenance not in PROVENANCE_VALUES:
            raise InitMaterializationError(f"invalid evidence provenance: {provenance}")
        safe_items.append(
            {
                key: copy.deepcopy(item[key])
                for key in (
                    "path",
                    "accessClass",
                    "factClass",
                    "provenance",
                    "referenceId",
                    "relatedEntities",
                    "contentHash",
                    "observedAt",
                )
                if key in item
            }
        )
    return safe_items


def _attach_reference_provenance(
    data: dict[str, Any], evidence_inventory: list[dict[str, Any]]
) -> None:
    references = {
        item.get("id"): item
        for item in data.get("references", [])
        if isinstance(item, dict)
    }
    for evidence in evidence_inventory:
        reference = references.get(evidence.get("referenceId"))
        if reference is None:
            continue
        reference.setdefault("extensions", {})["evidenceProvenance"] = {
            key: copy.deepcopy(evidence[key])
            for key in ("accessClass", "provenance", "contentHash", "observedAt")
            if key in evidence
        }


def _assert_empty_initial_history(data: dict[str, Any]) -> None:
    if data.get("meta", {}).get("revision") != 0:
        raise InitMaterializationError("INIT panoramaData meta.revision must be 0")
    if data.get("meta", {}).get("latestUpdateBatchId") is not None:
        raise InitMaterializationError("INIT panoramaData already has a latest UpdateBatch")
    for collection in ("reviews", "changes", "updateBatches"):
        if data.get(collection):
            raise InitMaterializationError(
                f"INIT panoramaData must start with an empty {collection} collection"
            )


def semantic_projection(data: dict[str, Any]) -> dict[str, Any]:
    """Remove only the records and bindings derived during INIT materialization."""

    projected = copy.deepcopy(data)
    meta = projected.get("meta", {})
    for key in ("revision", "updatedAt", "latestUpdateBatchId"):
        meta.pop(key, None)
    projected["reviews"] = []
    projected["changes"] = []
    projected["updateBatches"] = []
    intent = projected.get("intent")
    if isinstance(intent, dict):
        intent.pop("reviewId", None)
    for collection in (
        "requirements",
        "stages",
        "decisions",
        "acceptanceCriteria",
    ):
        for item in projected.get(collection, []):
            if isinstance(item, dict):
                item.pop("reviewId", None)
    for item in projected.get("architecture", {}).get("versions", []):
        if isinstance(item, dict):
            item.pop("reviewId", None)
    for item in projected.get("architecture", {}).get("modules", []):
        if isinstance(item, dict):
            item.pop("reviewIds", None)
    extensions = projected.get("extensions")
    if isinstance(extensions, dict):
        extensions.pop("initMaterialization", None)
        if not extensions:
            projected.pop("extensions", None)
    for reference in projected.get("references", []):
        if not isinstance(reference, dict):
            continue
        reference_extensions = reference.get("extensions")
        if isinstance(reference_extensions, dict):
            reference_extensions.pop("evidenceProvenance", None)
            if not reference_extensions:
                reference.pop("extensions", None)
    return projected


def materialize_prepared_init(
    prepared: dict[str, Any],
    approval: dict[str, Any],
    schema_path: Path,
    *,
    base_dir: Path,
    current_source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if set(prepared) != PREPARED_FIELDS:
        raise InitMaterializationError(
            "prepared artifact fields do not match prepared-init-review.v0.1"
        )
    if prepared.get("preparedVersion") != PREPARED_VERSION:
        raise InitMaterializationError(f"preparedVersion must be {PREPARED_VERSION}")
    if prepared.get("approvalState") != "awaiting_user_approval":
        raise InitMaterializationError("prepared artifact is not awaiting user approval")
    preview = prepared.get("preview")
    if not isinstance(preview, dict):
        raise InitMaterializationError("prepared artifact requires a Preview object")
    if not isinstance(approval, dict):
        raise InitMaterializationError("a separate approval artifact is required")
    if set(approval) != APPROVAL_FIELDS:
        raise InitMaterializationError(
            "approval artifact fields do not match init-approval.v0.1"
        )
    validation = prepared.get("validation")
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        raise InitMaterializationError("prepared artifact has no successful prevalidation")
    calculated_hash = compute_preview_hash(preview)
    prepared_hash = prepared.get("previewHash")
    approval_hash = approval.get("previewHash")
    if not calculated_hash == prepared_hash == approval_hash:
        raise InitMaterializationError(
            "approval hash binding failed: recomputed, prepared, and approval hashes must match"
        )
    if approval.get("approvalVersion") != APPROVAL_VERSION:
        raise InitMaterializationError(f"approvalVersion must be {APPROVAL_VERSION}")
    if approval.get("status") != "approved":
        raise InitMaterializationError("INIT Preview approval is not approved")
    if approval.get("approvalMethod") != APPROVAL_METHOD:
        raise InitMaterializationError(
            f"approvalMethod must be {APPROVAL_METHOD}"
        )
    if approval.get("approvalTimeSource") != APPROVAL_TIME_SOURCE:
        raise InitMaterializationError(
            f"approvalTimeSource must be {APPROVAL_TIME_SOURCE}"
        )
    approved_by = approval.get("approvedBy")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise InitMaterializationError("approval.approvedBy must come from the user action")
    if approved_by != approved_by.strip():
        raise InitMaterializationError("approval.approvedBy must be normalized")
    preview_at = _timestamp(preview.get("generatedAt"), "preview.generatedAt")
    prepared_at = _timestamp(prepared.get("preparedAt"), "preparedAt")
    approved_at = _timestamp(
        approval.get("approvalRecordedAt"), "approval.approvalRecordedAt"
    )
    if datetime.fromisoformat(approved_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        prepared_at.replace("Z", "+00:00")
    ):
        raise InitMaterializationError("approval time cannot precede PREPARE")
    if datetime.fromisoformat(prepared_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        preview_at.replace("Z", "+00:00")
    ):
        raise InitMaterializationError("PREPARE time cannot precede Preview generation")
    expected_snapshot = preview.get("sourceSnapshot")
    if not isinstance(expected_snapshot, dict):
        raise InitMaterializationError("preview.sourceSnapshot is required")
    if _canonical_json(prepared.get("sourceSnapshot")) != _canonical_json(expected_snapshot):
        raise InitMaterializationError(
            "prepared sourceSnapshot does not match the approved Preview"
        )
    if _canonical_json(expected_snapshot) != _canonical_json(current_source_snapshot):
        raise InitMaterializationError(
            "project source snapshot changed after Preview; regenerate and reapprove"
        )

    source_data = preview.get("panoramaData")
    if not isinstance(source_data, dict):
        raise InitMaterializationError("preview.panoramaData must be an object")
    data = copy.deepcopy(source_data)
    _assert_empty_initial_history(data)
    assert_release_semantics(data)
    guidance = preview.get("guidance")
    if not isinstance(guidance, dict) or not 2 <= len(guidance.get("options", [])) <= 3:
        raise InitMaterializationError("approved Preview Guidance must contain 2–3 options")
    data["guidance"] = copy.deepcopy(guidance)
    approved_projection = semantic_projection(data)
    evidence_inventory = _sanitize_evidence_inventory(
        preview.get("evidenceInventory", [])
    )
    _attach_reference_provenance(data, evidence_inventory)

    suffix = calculated_hash[:12].upper()
    review_id = f"REV-INIT-{suffix}"
    update_id = f"UPD-INIT-{suffix}"
    subject_refs = copy.deepcopy(preview.get("reviewSubjectRefs", []))
    if not isinstance(subject_refs, list) or not subject_refs:
        raise InitMaterializationError("reviewSubjectRefs must not be empty")
    changes = _materialize_changes(
        preview.get("changeIntents", []),
        suffix=suffix,
        review_id=review_id,
        approved_at=approved_at,
        preview_hash=calculated_hash,
    )
    review = {
        "id": review_id,
        "type": "panorama_update",
        "subjectRefs": subject_refs,
        "status": "approved",
        "impactLevel": preview.get("changeLevel", "project"),
        "requestedBy": preview.get("requestedBy", "ai"),
        "requestedAt": preview_at,
        "reviewedBy": approved_by,
        "reviewedAt": approved_at,
        "summary": preview.get(
            "reviewSummary", "Approved Managed Panorama Initialization"
        ),
        "comments": f"Approved INIT Preview bound to {calculated_hash}.",
        "decisionIds": copy.deepcopy(preview.get("reviewDecisionIds", [])),
        "changeIds": [item["id"] for item in changes],
        "extensions": {
            "approvalBinding": {
                "approvedHash": calculated_hash,
                "previewHash": calculated_hash,
                "sourceSnapshot": copy.deepcopy(expected_snapshot),
                "approvedBy": approved_by,
                "approvalRecordedAt": approved_at,
                "approvalMethod": APPROVAL_METHOD,
                "approvalTimeSource": APPROVAL_TIME_SOURCE,
            }
        },
    }
    data["reviews"] = [review]
    data["changes"] = changes
    _bind_initial_review(data, subject_refs, review_id)

    summary_items = copy.deepcopy(preview.get("updateSummaryItems", []))
    if not isinstance(summary_items, list) or not summary_items:
        raise InitMaterializationError("updateSummaryItems must not be empty")
    option_ids = [
        item["id"]
        for item in guidance.get("options", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    current_stage = data["project"]["currentStageId"]
    batch = {
        "id": update_id,
        "revisionFrom": 0,
        "revisionTo": 1,
        "periodStart": preview_at,
        "periodEnd": approved_at,
        "createdAt": approved_at,
        "status": "applied",
        "reviewId": review_id,
        "changeIds": [item["id"] for item in changes],
        "summaryItems": summary_items,
        "attentionItems": [],
        "nextFocusOptionIds": option_ids,
        "projectStageBefore": current_stage,
        "projectStageAfter": current_stage,
        "changeLevel": preview.get("changeLevel", "project"),
        "extensions": {
            "materializationType": "approved_managed_panorama_initialization",
            "findingReconciliation": [],
            "formalFindingCount": 0,
            "formalHighCriticalCount": 0,
            "attentionClusterCount": 0,
        },
    }
    data["updateBatches"] = [batch]
    data["meta"]["revision"] = 1
    data["meta"]["updatedAt"] = approved_at
    data["meta"]["latestUpdateBatchId"] = update_id
    data.setdefault("extensions", {})["initMaterialization"] = {
        "preparedVersion": PREPARED_VERSION,
        "approvalVersion": APPROVAL_VERSION,
        "previewHash": calculated_hash,
        "sourceSnapshot": copy.deepcopy(expected_snapshot),
        "evidenceInventory": evidence_inventory,
        "reviewId": review_id,
        "updateBatchId": update_id,
    }

    final_report = None
    reconciliation = None
    for _ in range(2):
        report = validate_data(data, schema_path, base_dir=base_dir)
        if report.errors:
            raise InitMaterializationError(
                "materialized Panorama is invalid: "
                + "; ".join(item.render() for item in report.errors[:5])
            )
        candidate = reconcile_findings(report.issues, evidence_inventory, data)
        _write_reconciliation(batch, candidate)
        settled_report = validate_data(data, schema_path, base_dir=base_dir)
        if settled_report.errors:
            raise InitMaterializationError(
                "materialized Panorama is invalid after Finding reconciliation: "
                + "; ".join(item.render() for item in settled_report.errors[:5])
            )
        settled = reconcile_findings(
            settled_report.issues, evidence_inventory, data
        )
        if _reconciliation_matches(batch, settled):
            final_report = settled_report
            reconciliation = settled
            break
    if final_report is None or reconciliation is None:
        raise InitMaterializationError(
            "final Validator findings did not stabilize after 2 reconciliation passes"
        )
    if not _reconciliation_matches(batch, reconciliation):
        raise InitMaterializationError(
            "final Validator findings do not match stored reconciliation and CONTROL Attention"
        )
    if _canonical_json(semantic_projection(data)) != _canonical_json(
        approved_projection
    ):
        raise InitMaterializationError(
            "materialization changed approved semantic content outside derived records"
        )
    return {
        "data": data,
        "materialization": {
            "previewHash": calculated_hash,
            "reviewId": review_id,
            "changeIds": [item["id"] for item in changes],
            "updateBatchId": update_id,
            "formalHighCriticalCount": reconciliation["formalHighCriticalCount"],
            "attentionCount": len(batch["attentionItems"]),
        },
        "validation": final_report.to_dict(),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InitMaterializationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InitMaterializationError("INIT artifact must be a JSON object")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise InitMaterializationError(
            "output already exists; INIT materialization never overwrites it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".staged"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将已批准 INIT Preview 确定性物化为首版 Panorama Data。"
    )
    parser.add_argument("prepared", type=Path)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "schema"
        / "panorama.schema.v0.1.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.project_root is None or args.output is None:
            raise InitMaterializationError(
                "--project-root and --output are required for materialization"
            )
        root = args.project_root.resolve(strict=True)
        result = materialize_prepared_init(
            _read_json(args.prepared),
            _read_json(args.approval),
            args.schema,
            base_dir=root,
            current_source_snapshot=source_snapshot(root),
        )
        _write_json_atomic(args.output, result["data"])
    except (OSError, ValueError, InitMaterializationError) as exc:
        print(f"INIT Materialization 错误：{exc}", file=sys.stderr)
        return 2
    print(f"已物化：{args.output}")
    print(f"Preview Hash：{result['materialization']['previewHash']}")
    print(f"Initial Review：{result['materialization']['reviewId']}")
    print(f"Initial UpdateBatch：{result['materialization']['updateBatchId']}")
    print(f"CONTROL Attention：{result['materialization']['attentionCount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
