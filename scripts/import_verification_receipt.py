"""Validate and project a redacted external verification receipt.

The adapter never executes a test and never changes Gate, Acceptance, Review,
Approval, or Waiver state.  It produces a deterministic mapping preview and,
only when requested, a normal Panorama Proposal wrapper for the existing
exact-hash approval/apply transaction.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from apply_patch import apply_operations
from panorama_cli import ChineseArgumentParser
from panorama_io import (
    atomic_write,
    compute_canonical_hash,
    compute_data_hash,
    extract_data,
)
from propose_update import build_proposal
from schema_support import schema_for_data
from validate_panorama import validate_data


RECEIPT_FORMAT = "panorama-verification-receipt.v0.1"
PREVIEW_FORMAT = "panorama-verification-import-preview.v0.1"
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 20
POLICY_HASH = compute_canonical_hash(
    {"kind": "verification_receipt_import", "version": "0.1"}
)
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "private_key",
    "client_secret",
    "access_key",
}
SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|client[_-]?secret)"
        r"\s*[:=]\s*(?!\[?redacted\]?|removed\b)\S+"
    ),
)


class VerificationReceiptError(RuntimeError):
    """A receipt cannot be safely mapped to the selected Panorama."""


def receipt_schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "schema" / "verification-receipt.schema.v0.1.json"


def compute_receipt_hash(receipt: dict[str, Any]) -> str:
    """Hash receipt semantics while excluding the self-referential hash field."""

    value = copy.deepcopy(receipt)
    producer = value.get("producer")
    if isinstance(producer, dict):
        producer.pop("receiptHash", None)
    return compute_canonical_hash(value)


def _json_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_NESTING_DEPTH:
        return depth
    if isinstance(value, dict):
        return max([depth] + [_json_depth(item, depth + 1) for item in value.values()])
    if isinstance(value, list):
        return max([depth] + [_json_depth(item, depth + 1) for item in value])
    return depth


def _validate_shape(receipt: dict[str, Any], schema_path: Path | None = None) -> None:
    path = schema_path or receipt_schema_path()
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationReceiptError(f"无法读取 Receipt Schema：{exc}") from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(receipt),
        key=lambda issue: list(issue.absolute_path),
    )
    if errors:
        rendered = []
        for issue in errors[:20]:
            pointer = "/" + "/".join(str(part) for part in issue.absolute_path)
            rendered.append(f"{pointer or '/'}: {issue.message}")
        raise VerificationReceiptError("Receipt Schema 无效：" + "；".join(rendered))
    if _json_depth(receipt) > MAX_NESTING_DEPTH:
        raise VerificationReceiptError("Receipt 嵌套深度超过 20 层。")


def _validate_redaction(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in SENSITIVE_KEYS:
                raise VerificationReceiptError(f"Receipt 包含敏感字段键：{path}.{key}")
            _validate_redaction(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_redaction(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in SENSITIVE_PATTERNS:
            if pattern.search(value):
                raise VerificationReceiptError(f"Receipt 疑似包含敏感明文：{path}")


def _unique_ids(items: list[dict[str, Any]], field: str, label: str) -> set[str]:
    values = [item.get(field) for item in items]
    if len(values) != len(set(values)):
        raise VerificationReceiptError(f"Receipt {label} ID 必须唯一。")
    return {str(item) for item in values}


def _validate_semantics(receipt: dict[str, Any], project_root: Path) -> dict[str, str]:
    evidence = receipt["evidence"]
    evidence_ids = _unique_ids(evidence, "evidenceId", "Evidence")
    _unique_ids(receipt["verifications"], "verificationId", "Verification")
    finding_ids: list[str] = []
    evidence_status: dict[str, str] = {}
    root = project_root.resolve(strict=True)
    for item in evidence:
        evidence_id = item["evidenceId"]
        location_type = item["locationType"]
        location = item["location"]
        if location_type == "relative_path":
            pure = PurePosixPath(location.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts:
                raise VerificationReceiptError(
                    f"Evidence {evidence_id} relative_path 试图逃逸 project root。"
                )
            candidate = root.joinpath(*pure.parts)
            if candidate.exists():
                if candidate.is_symlink():
                    raise VerificationReceiptError(
                        f"Evidence {evidence_id} 不允许使用符号链接。"
                    )
                try:
                    candidate.resolve(strict=True).relative_to(root)
                except ValueError as exc:
                    raise VerificationReceiptError(
                        f"Evidence {evidence_id} 路径越出 project root。"
                    ) from exc
                evidence_status[evidence_id] = "available" if candidate.is_file() else "unknown"
            else:
                evidence_status[evidence_id] = "missing"
        elif location_type == "url":
            parsed = urlsplit(location)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise VerificationReceiptError(
                    f"Evidence {evidence_id} URL 必须是无凭据的 HTTPS URL。"
                )
            evidence_status[evidence_id] = "available"
        else:
            if not (location.startswith("receipt:") or location.startswith("urn:")):
                raise VerificationReceiptError(
                    f"Evidence {evidence_id} inline_note 只能保存 receipt:/urn: 引用。"
                )
            evidence_status[evidence_id] = "available"

    for verification in receipt["verifications"]:
        unknown = sorted(set(verification["evidenceIds"]).difference(evidence_ids))
        if unknown:
            raise VerificationReceiptError(
                f"Verification {verification['verificationId']} 引用了未知 Evidence：{unknown}"
            )
        for finding in verification["findings"]:
            finding_ids.append(finding["findingId"])
            unknown_finding = sorted(set(finding["evidenceIds"]).difference(evidence_ids))
            if unknown_finding:
                raise VerificationReceiptError(
                    f"External Finding {finding['findingId']} 引用了未知 Evidence：{unknown_finding}"
                )
    if len(finding_ids) != len(set(finding_ids)):
        raise VerificationReceiptError("Receipt External Finding ID 必须唯一。")
    return evidence_status


def validate_receipt(
    receipt: dict[str, Any], project_root: Path, *, schema_path: Path | None = None
) -> dict[str, str]:
    if not isinstance(receipt, dict):
        raise VerificationReceiptError("Receipt 必须是 JSON 对象。")
    _validate_shape(receipt, schema_path)
    _validate_redaction(receipt)
    expected_hash = compute_receipt_hash(receipt)
    if receipt["producer"]["receiptHash"] != expected_hash:
        raise VerificationReceiptError("Receipt Hash 与当前 Receipt 语义不一致。")
    return _validate_semantics(receipt, project_root)


def _binding_status(current: dict[str, Any], receipt: dict[str, Any]) -> str:
    binding = receipt["projectBinding"]
    if binding["projectId"] != current.get("project", {}).get("id"):
        raise VerificationReceiptError("Receipt projectId 与 Panorama 不匹配。")
    if binding["panoramaRevision"] != current.get("meta", {}).get("revision"):
        raise VerificationReceiptError("Receipt Panorama Revision 已过期。")
    if binding["panoramaDataHash"] != compute_data_hash(current):
        raise VerificationReceiptError("Receipt Panorama Data Hash 已过期。")
    formal = current.get("sourceBinding")
    formal = formal if isinstance(formal, dict) else {}
    formal_snapshot = formal.get("sourceSnapshotHash")
    uninitialized = bool(
        formal.get("extensions", {}).get("migrationRequiresInitialSync")
        or not formal.get("observedAt")
        or formal_snapshot == "0" * 64
    )
    if current.get("schemaVersion") == "0.2" and not uninitialized:
        if binding.get("gitHead") != formal.get("gitHead"):
            raise VerificationReceiptError("Receipt gitHead 与 Panorama Source Binding 不匹配。")
        if binding.get("sourceSnapshotHash") != formal_snapshot:
            raise VerificationReceiptError(
                "Receipt sourceSnapshotHash 与 Panorama Source Binding 不匹配。"
            )
        return "formal_source_match"
    if binding.get("gitHead") is not None or binding.get("sourceSnapshotHash") is not None:
        return "receipt_declared_source"
    return "panorama_only"


def _receipt_reference(
    current: dict[str, Any],
    receipt: dict[str, Any],
    receipt_hash: str,
    reference_id: str,
    evidence_status: dict[str, str],
) -> dict[str, Any]:
    evidence = receipt["evidence"]
    locations = {(item["locationType"], item["location"]) for item in evidence}
    if len(locations) == 1:
        location_type, location = next(iter(locations))
    else:
        location_type, location = "inline_note", f"receipt:{receipt_hash}"
    statuses = {evidence_status[item["evidenceId"]] for item in evidence}
    status = "available" if statuses == {"available"} else "missing" if "missing" in statuses else "unknown"
    gate_ids = sorted(
        {item for verification in receipt["verifications"] for item in verification["gateIds"]}
    )
    acceptance_ids = sorted(
        {
            item
            for verification in receipt["verifications"]
            for item in verification["acceptanceCriteriaIds"]
        }
    )
    related = [{"type": "gate", "id": item, "label": "Gate"} for item in gate_ids]
    related.extend(
        {"type": "acceptance", "id": item, "label": "Acceptance"}
        for item in acceptance_ids
    )
    return {
        "id": reference_id,
        "type": "test_report",
        "title": f"Verification Receipt {receipt['receiptId']}",
        "locationType": location_type,
        "location": location,
        "status": status,
        "sensitive": False,
        "relatedEntities": related,
        "notes": "外部验证收据的脱敏投影；不代表 Gate passed 或 Acceptance accepted。",
        "updatedAt": receipt["producer"]["generatedAt"],
        "extensions": {
            "verificationReceipt": copy.deepcopy(receipt),
            "verificationReceiptHash": receipt_hash,
            "mappingMode": "evidence_candidate_only",
        },
    }


def _find_index(items: list[dict[str, Any]], entity_id: str, label: str) -> int:
    for index, item in enumerate(items):
        if item.get("id") == entity_id:
            return index
    raise VerificationReceiptError(f"Receipt 映射了未知 {label}：{entity_id}")


def _primary_operations(
    current: dict[str, Any], reference: dict[str, Any]
) -> list[dict[str, Any]]:
    reference_id = reference["id"]
    operations: list[dict[str, Any]] = [
        {"op": "add", "path": "/references/-", "value": reference}
    ]
    receipt = reference["extensions"]["verificationReceipt"]
    gate_ids = sorted(
        {item for verification in receipt["verifications"] for item in verification["gateIds"]}
    )
    acceptance_ids = sorted(
        {
            item
            for verification in receipt["verifications"]
            for item in verification["acceptanceCriteriaIds"]
        }
    )
    if not gate_ids and not acceptance_ids:
        raise VerificationReceiptError("Receipt 至少要映射一个 Gate 或 Acceptance。")
    for gate_id in gate_ids:
        index = _find_index(current.get("gates", []), gate_id, "Gate")
        if reference_id not in current["gates"][index].get("evidenceReferenceIds", []):
            operations.append(
                {
                    "op": "add",
                    "path": f"/gates/{index}/evidenceReferenceIds/-",
                    "value": reference_id,
                }
            )
    for acceptance_id in acceptance_ids:
        index = _find_index(
            current.get("acceptanceCriteria", []), acceptance_id, "Acceptance"
        )
        if reference_id not in current["acceptanceCriteria"][index].get(
            "evidenceReferenceIds", []
        ):
            operations.append(
                {
                    "op": "add",
                    "path": f"/acceptanceCriteria/{index}/evidenceReferenceIds/-",
                    "value": reference_id,
                }
            )
    return operations


def _receipt_history(current: dict[str, Any], receipt_hash: str) -> tuple[dict[str, Any] | None, str]:
    for reference in current.get("references", []):
        extension = reference.get("extensions", {}) if isinstance(reference, dict) else {}
        if extension.get("verificationReceiptHash") == receipt_hash:
            return reference, "duplicate"
    return None, "new"


def _with_v02_provenance(
    current: dict[str, Any], receipt: dict[str, Any], receipt_hash: str,
    primary: list[dict[str, Any]], reference_id: str
) -> list[dict[str, Any]]:
    if current.get("schemaVersion") != "0.2":
        return primary
    suffix = receipt_hash[:16].upper()
    batch_id = f"OBS-VR-{suffix}"
    observed_at = receipt["producer"]["generatedAt"]
    provenance: list[dict[str, Any]] = []
    for index, operation in enumerate(primary):
        provenance.append(
            {
                "id": f"PROV-VR-{suffix}-{index + 1}",
                "path": (
                    f"/references/{len(current.get('references', []))}"
                    if operation["path"] == "/references/-"
                    else operation["path"].removesuffix("/-")
                ),
                "authority": "declared",
                "confidence": "medium",
                "evidence": [reference_id, f"receipt:{receipt_hash}"],
                "sourceCommit": receipt["projectBinding"].get("gitHead"),
                "observedAt": observed_at,
                "observationBatchId": batch_id,
                "extensions": {
                    "factClass": "VERIFICATION",
                    "verificationReceiptHash": receipt_hash,
                },
            }
        )
    batch = {
        "id": batch_id,
        "revisionFrom": current["meta"]["revision"],
        "revisionTo": current["meta"]["revision"] + 1,
        "sourceCommitFrom": current.get("sourceBinding", {}).get("gitHead"),
        "sourceCommitTo": receipt["projectBinding"].get("gitHead"),
        "policyHash": POLICY_HASH,
        "evidenceSnapshotHash": receipt_hash,
        "standardAssessmentIds": [],
        "operations": copy.deepcopy(primary),
        "provenanceIds": [item["id"] for item in provenance],
        "conflicts": [],
        "findings": [],
        "validationResult": {"valid": True, "errors": 0, "warnings": 0},
        "observedAt": observed_at,
        "status": "applied",
        "extensions": {
            "kind": "verification_receipt_import",
            "verificationReceiptHash": receipt_hash,
            "externalFindingsAreFormal": False,
        },
    }
    result = list(primary)
    result.extend({"op": "add", "path": "/factProvenance/-", "value": item} for item in provenance)
    result.append({"op": "add", "path": "/observationBatches/-", "value": batch})
    return result


def build_receipt_preview(
    current: dict[str, Any],
    receipt: dict[str, Any],
    *,
    project_root: Path,
    panorama_path: Path | None = None,
    receipt_schema: Path | None = None,
    panorama_schema: Path | None = None,
) -> dict[str, Any]:
    evidence_status = validate_receipt(receipt, project_root, schema_path=receipt_schema)
    receipt_hash = receipt["producer"]["receiptHash"]
    existing, disposition = _receipt_history(current, receipt_hash)
    binding_status = (
        "previously_imported"
        if existing is not None
        else _binding_status(current, receipt)
    )
    reference_id = existing.get("id") if existing else f"REF-VR-{receipt_hash[:16].upper()}"
    if existing:
        operations: list[dict[str, Any]] = []
        report = validate_data(
            current,
            panorama_schema or schema_for_data(current),
            base_dir=project_root,
            source_path=panorama_path,
        )
    else:
        if any(item.get("id") == reference_id for item in current.get("references", [])):
            raise VerificationReceiptError("Receipt Reference ID 与已有实体冲突。")
        reference = _receipt_reference(
            current, receipt, receipt_hash, reference_id, evidence_status
        )
        primary = _primary_operations(current, reference)
        operations = _with_v02_provenance(
            current, receipt, receipt_hash, primary, reference_id
        )
        preview_data = apply_operations(current, operations)
        preview_data["meta"]["revision"] = current["meta"]["revision"] + 1
        preview_data["meta"]["updatedAt"] = receipt["producer"]["generatedAt"]
        report = validate_data(
            preview_data,
            panorama_schema or schema_for_data(current),
            base_dir=project_root,
            source_path=panorama_path,
        )
        if report.errors:
            raise VerificationReceiptError("Receipt Mapping Preview 未通过 Panorama Formal Validator。")
        # Persist exact validation counts in the Receipt-owned Observation Batch.
        for operation in operations:
            value = operation.get("value")
            if operation.get("path") == "/observationBatches/-" and isinstance(value, dict):
                value["validationResult"] = {
                    "valid": not report.errors,
                    "errors": len(report.errors),
                    "warnings": len(report.warnings),
                }
        preview_data = apply_operations(current, operations)
        preview_data["meta"]["revision"] = current["meta"]["revision"] + 1
        preview_data["meta"]["updatedAt"] = receipt["producer"]["generatedAt"]
        report = validate_data(
            preview_data,
            panorama_schema or schema_for_data(current),
            base_dir=project_root,
            source_path=panorama_path,
        )
        if report.errors:
            raise VerificationReceiptError("Receipt Mapping Preview 最终校验失败。")

    gate_ids = sorted(
        {item for verification in receipt["verifications"] for item in verification["gateIds"]}
    )
    acceptance_ids = sorted(
        {
            item
            for verification in receipt["verifications"]
            for item in verification["acceptanceCriteriaIds"]
        }
    )
    return {
        "format": PREVIEW_FORMAT,
        "status": "duplicate" if disposition == "duplicate" else "ready",
        "experimental": True,
        "receiptId": receipt["receiptId"],
        "receiptHash": receipt_hash,
        "referenceId": reference_id,
        "binding": {
            "status": binding_status,
            "projectId": current.get("project", {}).get("id"),
            "baseRevision": current.get("meta", {}).get("revision"),
            "baseDataHash": compute_data_hash(current),
            "gitHead": receipt["projectBinding"].get("gitHead"),
            "sourceSnapshotHash": receipt["projectBinding"].get("sourceSnapshotHash"),
            "environment": receipt["projectBinding"]["environment"],
        },
        "mapping": {
            "gateIds": gate_ids,
            "acceptanceCriteriaIds": acceptance_ids,
            "operationCount": len(operations),
            "operations": operations,
            "changesGovernanceStatus": False,
        },
        "verifications": copy.deepcopy(receipt["verifications"]),
        "evidence": copy.deepcopy(receipt["evidence"]),
        "isolation": copy.deepcopy(receipt["isolation"]),
        "redaction": copy.deepcopy(receipt["redaction"]),
        "validation": report.to_dict(),
        "boundary": {
            "executesTests": False,
            "setsGatePassed": False,
            "acceptsAcceptance": False,
            "createsApproval": False,
            "externalFindingsAreFormal": False,
        },
    }


def build_receipt_proposal(
    current: dict[str, Any],
    preview: dict[str, Any],
    *,
    project_root: Path,
    panorama_path: Path | None = None,
    panorama_schema: Path | None = None,
) -> dict[str, Any]:
    if preview.get("format") != PREVIEW_FORMAT or preview.get("status") != "ready":
        raise VerificationReceiptError("只有 ready Receipt Preview 可以生成 Proposal。")
    binding = preview.get("binding", {})
    if binding.get("baseRevision") != current.get("meta", {}).get("revision"):
        raise VerificationReceiptError("Receipt Preview Revision 已过期。")
    if binding.get("baseDataHash") != compute_data_hash(current):
        raise VerificationReceiptError("Receipt Preview Data Hash 已过期。")
    candidate = {
        "operations": copy.deepcopy(preview["mapping"]["operations"]),
        "summary": f"导入 Verification Receipt {preview['receiptId']} 的脱敏证据映射。",
        "reason": "保留外部验证结果、隔离声明与证据映射，不推进治理状态。",
        "changeLevel": "local",
        "createdAt": next(
            (
                item.get("completedAt")
                for item in preview.get("verifications", [])
                if item.get("completedAt")
            ),
            current.get("meta", {}).get("updatedAt"),
        ),
    }
    wrapper = build_proposal(
        current,
        candidate,
        panorama_schema or schema_for_data(current),
        base_dir=project_root,
        source_path=panorama_path,
    )
    if not wrapper["validation"]["valid"]:
        raise VerificationReceiptError("Receipt Proposal 未通过 Panorama Formal Validator。")
    package = wrapper["proposal"]
    receipt_binding = {
        "receiptId": preview["receiptId"],
        "receiptHash": preview["receiptHash"],
        "referenceId": preview["referenceId"],
        "baseRevision": package["baseRevision"],
        "baseDataHash": package["baseDataHash"],
        "operationsHash": compute_canonical_hash(package["operations"]),
    }
    package["reviewDraft"].setdefault("extensions", {})[
        "verificationReceiptBinding"
    ] = copy.deepcopy(receipt_binding)
    package["updateBatchDraft"].setdefault("extensions", {})[
        "verificationReceiptBinding"
    ] = copy.deepcopy(receipt_binding)
    from apply_patch import compute_proposal_hash

    package["proposalHash"] = compute_proposal_hash(package)
    package["approval"]["proposalHash"] = package["proposalHash"]
    wrapper["sourceBinding"] = {
        "projectId": current.get("project", {}).get("id"),
        "schemaVersion": str(current.get("schemaVersion", "")),
        "templateVersion": str(current.get("meta", {}).get("templateVersion", "")),
        "baseRevision": package["baseRevision"],
        "baseDataHash": package["baseDataHash"],
        "gitHead": current.get("sourceBinding", {}).get("gitHead"),
        "sourceSnapshotHash": current.get("sourceBinding", {}).get("sourceSnapshotHash"),
    }
    return wrapper


def load_receipt(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise VerificationReceiptError(f"无法读取 Receipt：{exc}") from exc
    if size > MAX_RECEIPT_BYTES:
        raise VerificationReceiptError("Receipt 超过 1 MiB 上限。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationReceiptError(f"Receipt 不是有效 UTF-8 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise VerificationReceiptError("Receipt 必须是 JSON 对象。")
    return value


def _write_new(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise VerificationReceiptError(f"输出已存在，拒绝覆盖：{path}")
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="验证并预览脱敏 Verification Receipt。")
    parser.add_argument("panorama", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Mapping Preview 输出")
    parser.add_argument("--proposal-output", type=Path, default=None)
    parser.add_argument("--receipt-schema", type=Path, default=None)
    parser.add_argument("--panorama-schema", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        project_root = args.project_root.resolve(strict=True)
        panorama = args.panorama.resolve(strict=True)
        current = extract_data(panorama)
        receipt = load_receipt(args.receipt)
        preview = build_receipt_preview(
            current,
            receipt,
            project_root=project_root,
            panorama_path=panorama,
            receipt_schema=args.receipt_schema,
            panorama_schema=args.panorama_schema,
        )
        _write_new(args.output, preview)
        print(f"Receipt Preview 已写入：{args.output}")
        print(f"Receipt SHA-256：{preview['receiptHash']}")
        print(f"状态：{preview['status']} · 操作 {preview['mapping']['operationCount']} 个")
        if args.proposal_output is not None:
            proposal = build_receipt_proposal(
                current,
                preview,
                project_root=project_root,
                panorama_path=panorama,
                panorama_schema=args.panorama_schema,
            )
            _write_new(args.proposal_output, proposal)
            print(f"Receipt Proposal 已写入：{args.proposal_output}")
            print(f"Proposal SHA-256：{proposal['proposal']['proposalHash']}")
        return 0
    except (OSError, ValueError, VerificationReceiptError) as exc:
        print(f"Verification Receipt 错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
