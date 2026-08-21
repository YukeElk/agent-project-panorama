"""Fail-closed candidate preparation for V0.6 multi-view delivery."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator, FormatChecker

import event_store
from panorama_io import atomic_write, compute_canonical_hash
from panorama_view_ir import PanoramaViewIRError
from render_panorama_views import TEMPLATE, build_bundle, render_html
from validate_panorama_renderer_geometry import validate_candidate as validate_geometry


ROOT = Path(__file__).resolve().parents[1]
BROWSER_SCHEMA = ROOT / "schema" / "panorama-renderer-browser-evidence.schema.v0.1.json"
RECEIPT_SCHEMA = ROOT / "schema" / "panorama-verified-delivery-receipt.schema.v0.1.json"
DELIVERY_REF = ".panorama-work/verified-delivery/v0.1"
DELIVERY_FORMAT = "panorama-verified-delivery-receipt.v0.1"
BROWSER_FORMAT = "panorama-renderer-browser-evidence.v0.1"
REQUIRED_DESKTOP_VIEWPORTS = {(1440, 900), (1600, 1000), (1920, 1080), (2048, 1320)}
MAX_JSON_BYTES = 4 * 1024 * 1024


class VerifiedDeliveryError(RuntimeError):
    """A candidate, evidence file, or immutable delivery artifact is invalid."""


class VerifiedDeliveryConflictError(VerifiedDeliveryError):
    """An immutable delivery path already contains different bytes."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _normalize_time(value: str | None, field: str) -> str:
    candidate = value or _now()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise VerifiedDeliveryError(f"{field} 必须是带时区的 RFC 3339 时间。") from exc
    if parsed.tzinfo is None:
        raise VerifiedDeliveryError(f"{field} 必须包含时区。")
    timespec = "milliseconds" if parsed.microsecond else "seconds"
    return parsed.astimezone(timezone.utc).isoformat(timespec=timespec).replace(
        "+00:00", "Z"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise VerifiedDeliveryError(f"JSON 制品不允许是符号链接：{path}")
        if path.stat().st_size > MAX_JSON_BYTES:
            raise VerifiedDeliveryError(f"JSON 制品超过 {MAX_JSON_BYTES} bytes：{path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except VerifiedDeliveryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerifiedDeliveryError(f"JSON 制品无效 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise VerifiedDeliveryError(f"JSON 制品必须是 object：{path}")
    return value


def _schema_errors(value: dict[str, Any], path: Path) -> list[str]:
    schema = _read_json(path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = []
    for issue in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path)):
        pointer = "/" + "/".join(str(part) for part in issue.absolute_path)
        errors.append(f"{pointer}: {issue.message}")
    return errors


def _hash_without_integrity(value: dict[str, Any], field: str) -> str:
    semantics = copy.deepcopy(value)
    semantics.get("integrity", {}).pop(field, None)
    return compute_canonical_hash(semantics)


def validate_browser_evidence(
    evidence: dict[str, Any], *, artifact_sha256: str, artifact_bytes: int
) -> None:
    errors = _schema_errors(evidence, BROWSER_SCHEMA)
    if errors:
        raise VerifiedDeliveryError("Browser Evidence Schema 无效：" + "; ".join(errors[:20]))
    if evidence["integrity"]["evidenceHash"] != _hash_without_integrity(
        evidence, "evidenceHash"
    ):
        raise VerifiedDeliveryError("Browser Evidence Hash 不匹配。")
    if evidence["artifact"] != {"sha256": artifact_sha256, "bytes": artifact_bytes}:
        raise VerifiedDeliveryError("Browser Evidence 与候选 Artifact 字节不匹配。")
    if evidence["status"] != "passed":
        raise VerifiedDeliveryError("只有 status=passed 的 Browser Evidence 可进入机器绿色候选。")
    observed = {(item["width"], item["height"]) for item in evidence["viewports"]}
    if not REQUIRED_DESKTOP_VIEWPORTS.issubset(observed):
        raise VerifiedDeliveryError("Browser Evidence 缺少四个 Required Desktop Viewport。")
    for item in evidence["viewports"]:
        desktop = (item["width"], item["height"]) in REQUIRED_DESKTOP_VIEWPORTS
        if (
            item["documentOverflowX"]
            or (desktop and item["documentOverflowY"])
            or item["nodeOverlapCount"]
            or item["edgeThroughNodeCount"]
            or item["sideOverflowCount"]
            or not item["captured"]
        ):
            raise VerifiedDeliveryError("Browser Evidence 含未通过的 Viewport Measurement。")
    if evidence["console"] != {"errorCount": 0, "warningCount": 0}:
        raise VerifiedDeliveryError("Browser Console 存在 error/warning。")


def build_browser_evidence(
    artifact: bytes,
    measurements: list[dict[str, Any]],
    *,
    console: dict[str, int],
    checked_at: str | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    artifact_binding = {"sha256": _sha256_bytes(artifact), "bytes": len(artifact)}
    semantics = {
        "formatVersion": BROWSER_FORMAT,
        "checkedAt": _normalize_time(checked_at, "checkedAt"),
        "artifact": artifact_binding,
        "status": "passed",
        "viewports": measurements,
        "console": console,
        "visualReview": "pending",
        "limitations": limitations or [],
    }
    identity_hash = compute_canonical_hash(semantics)
    evidence = {
        **semantics,
        "evidenceId": f"PBE-{identity_hash[:24].upper()}",
        "integrity": {"evidenceHash": "0" * 64},
    }
    evidence["integrity"]["evidenceHash"] = _hash_without_integrity(
        evidence, "evidenceHash"
    )
    validate_browser_evidence(
        evidence,
        artifact_sha256=artifact_binding["sha256"],
        artifact_bytes=artifact_binding["bytes"],
    )
    return evidence


def validate_delivery_receipt(receipt: dict[str, Any]) -> None:
    errors = _schema_errors(receipt, RECEIPT_SCHEMA)
    if errors:
        raise VerifiedDeliveryError("Delivery Receipt Schema 无效：" + "; ".join(errors[:20]))
    if receipt["integrity"]["receiptHash"] != _hash_without_integrity(
        receipt, "receiptHash"
    ):
        raise VerifiedDeliveryError("Delivery Receipt Hash 不匹配。")


def _assert_safe_project_root(project_root: Path) -> tuple[Path, Path]:
    raw = Path(project_root)
    if raw.is_symlink():
        raise VerifiedDeliveryError("Project Root 不允许是符号链接。")
    try:
        root = raw.resolve(strict=True)
    except OSError as exc:
        raise VerifiedDeliveryError("Project Root 不存在或不可访问。") from exc
    if not root.is_dir():
        raise VerifiedDeliveryError("Project Root 必须是目录。")
    delivery = root / Path(*DELIVERY_REF.split("/"))
    current = root
    for part in delivery.relative_to(root).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise VerifiedDeliveryError("Verified Delivery 路径不允许经过符号链接。")
    return root, delivery


@contextmanager
def _delivery_lock(delivery: Path) -> Iterator[None]:
    delivery.mkdir(parents=True, exist_ok=True)
    lock = delivery / ".delivery.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise VerifiedDeliveryConflictError("Verified Delivery 存在活动 Writer Lock。") from exc
    try:
        os.write(fd, b"panorama-verified-delivery\n")
        os.close(fd)
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        lock.unlink(missing_ok=True)


def _write_once(path: Path, value: bytes) -> bool:
    if path.exists():
        if path.is_symlink():
            raise VerifiedDeliveryConflictError(f"Immutable Artifact 不允许是符号链接：{path}")
        if path.read_bytes() != value:
            raise VerifiedDeliveryConflictError(f"Immutable Artifact 已存在异字节冲突：{path.name}")
        return False
    if path.parent.exists() and path.parent.is_symlink():
        raise VerifiedDeliveryConflictError("Immutable Artifact Parent 不允许是符号链接。")
    atomic_write(path, value.decode("utf-8"))
    return True


def _template_security_check() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    required = ["connect-src 'none'", "default-src 'none'", "font-src 'none'"]
    if any(item not in template for item in required):
        raise VerifiedDeliveryError("Renderer Template 缺少 fail-closed CSP。")
    lowered = template.lower()
    if "http://" in lowered or "https://" in lowered or "<script src=" in lowered:
        raise VerifiedDeliveryError("Renderer Template 含远程 URL 或外部 Script。")


def prepare_delivery(
    project_root: Path,
    model: dict[str, Any],
    views: list[dict[str, Any]],
    *,
    browser_evidence: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create an immutable candidate and receipt; never touch last-good."""

    _, delivery = _assert_safe_project_root(project_root)
    try:
        bundle = build_bundle(model, views)
        geometry = validate_geometry(model, bundle["views"])
    except PanoramaViewIRError as exc:
        raise VerifiedDeliveryError(str(exc)) from exc
    if geometry["status"] != "passed":
        raise VerifiedDeliveryError("Geometry Gate 未通过。")
    _template_security_check()
    try:
        event_store._validate_redaction(bundle)
    except event_store.EventStoreError as exc:
        raise VerifiedDeliveryError(f"Privacy/Redaction Gate 未通过：{exc}") from exc
    html = render_html(bundle)
    artifact = html.encode("utf-8")
    artifact_hash = _sha256_bytes(artifact)
    evidence_hash = None
    if browser_evidence is not None:
        validate_browser_evidence(
            browser_evidence,
            artifact_sha256=artifact_hash,
            artifact_bytes=len(artifact),
        )
        evidence_hash = browser_evidence["integrity"]["evidenceHash"]
    identity = compute_canonical_hash(
        {
            "bundleHash": bundle["bundleHash"],
            "artifactSha256": artifact_hash,
            "browserEvidenceHash": evidence_hash,
        }
    )
    delivery_id = f"PVD-{identity[:24].upper()}"
    candidate_ref = f"candidates/{delivery_id}.html"
    receipt_ref = f"receipts/{delivery_id}.json"
    evidence_ref = (
        f"evidence/{browser_evidence['evidenceId']}.json"
        if browser_evidence is not None
        else None
    )
    gaps = [] if browser_evidence is not None else ["browser_evidence_not_provided"]
    receipt = {
        "formatVersion": DELIVERY_FORMAT,
        "deliveryId": delivery_id,
        "generatedAt": _normalize_time(generated_at, "generatedAt"),
        "projectBinding": {
            "projectId": model["projectBinding"]["projectId"],
            "panoramaSchemaVersion": model["projectBinding"]["panoramaSchemaVersion"],
            "revision": model["projectBinding"]["revision"],
            "dataHash": model["projectBinding"]["dataHash"],
        },
        "modelBinding": {
            "modelId": model["modelId"],
            "semanticHash": model["integrity"]["semanticHash"],
        },
        "bundleBinding": {
            "bundleId": bundle["bundleId"],
            "bundleHash": bundle["bundleHash"],
            "renderer": copy.deepcopy(bundle["renderer"]),
        },
        "viewBindings": [
            {
                "viewId": view["viewId"],
                "profile": view["profile"],
                "semanticHash": view["integrity"]["semanticHash"],
                "layoutHash": view["integrity"]["layoutHash"],
            }
            for view in bundle["views"]
        ],
        "artifact": {
            "ref": candidate_ref,
            "sha256": artifact_hash,
            "bytes": len(artifact),
            "mediaType": "text/html; charset=utf-8",
        },
        "gates": {
            "modelView": "passed",
            "geometry": "passed",
            "privacy": "passed",
            "offlineSecurity": "passed",
            "browser": {
                "status": "passed" if browser_evidence is not None else "not_provided",
                "evidenceRef": evidence_ref,
                "evidenceHash": evidence_hash,
            },
            "visualReview": "pending",
        },
        "informationGaps": gaps,
        "integrity": {"receiptHash": "0" * 64},
    }
    receipt["integrity"]["receiptHash"] = _hash_without_integrity(receipt, "receiptHash")
    validate_delivery_receipt(receipt)
    receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with _delivery_lock(delivery):
        candidate_path = delivery / Path(*candidate_ref.split("/"))
        receipt_path = delivery / Path(*receipt_ref.split("/"))
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        created = _write_once(candidate_path, artifact)
        if browser_evidence is not None and evidence_ref is not None:
            evidence_path = delivery / Path(*evidence_ref.split("/"))
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_bytes = (
                json.dumps(browser_evidence, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            _write_once(evidence_path, evidence_bytes)
        if receipt_path.exists():
            existing = _read_json(receipt_path)
            validate_delivery_receipt(existing)
            stable_existing = copy.deepcopy(existing)
            stable_candidate = copy.deepcopy(receipt)
            stable_existing.pop("generatedAt", None)
            stable_candidate.pop("generatedAt", None)
            stable_existing["integrity"]["receiptHash"] = "0" * 64
            stable_candidate["integrity"]["receiptHash"] = "0" * 64
            if stable_existing != stable_candidate:
                raise VerifiedDeliveryConflictError("Delivery Receipt 已存在语义冲突。")
            receipt = existing
        else:
            _write_once(receipt_path, receipt_bytes)
            created = True
    return receipt, created


def load_delivery(project_root: Path, delivery_id: str) -> tuple[dict[str, Any], Path]:
    _, delivery = _assert_safe_project_root(project_root)
    if not delivery_id.startswith("PVD-") or len(delivery_id) != 28:
        raise VerifiedDeliveryError("deliveryId 格式无效。")
    receipt_path = delivery / "receipts" / f"{delivery_id}.json"
    receipt = _read_json(receipt_path)
    validate_delivery_receipt(receipt)
    candidate = delivery / Path(*receipt["artifact"]["ref"].split("/"))
    if candidate.is_symlink() or not candidate.is_file():
        raise VerifiedDeliveryError("候选 Artifact 缺失或是不安全链接。")
    artifact = candidate.read_bytes()
    if _sha256_bytes(artifact) != receipt["artifact"]["sha256"] or len(artifact) != receipt["artifact"]["bytes"]:
        raise VerifiedDeliveryError("候选 Artifact 与 Delivery Receipt 不匹配。")
    return receipt, candidate


__all__ = [
    "BROWSER_FORMAT",
    "DELIVERY_FORMAT",
    "DELIVERY_REF",
    "VerifiedDeliveryConflictError",
    "VerifiedDeliveryError",
    "build_browser_evidence",
    "load_delivery",
    "prepare_delivery",
    "validate_browser_evidence",
    "validate_delivery_receipt",
]
