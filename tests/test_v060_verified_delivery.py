from __future__ import annotations

from copy import deepcopy

import pytest

from render_panorama_views import build_bundle, render_html
from test_v060_multi_view_renderer import _model_and_views
from verified_delivery import (
    VerifiedDeliveryConflictError,
    VerifiedDeliveryError,
    build_browser_evidence,
    load_delivery,
    prepare_delivery,
    validate_browser_evidence,
)


def _measurements():
    return [
        {
            "width": width,
            "height": height,
            "documentOverflowX": False,
            "documentOverflowY": False,
            "nodeOverlapCount": 0,
            "edgeThroughNodeCount": 0,
            "sideOverflowCount": 0,
            "captured": True,
            "screenshotSha256": f"{width:064x}"[-64:],
            "screenshotBytes": width + height,
        }
        for width, height in (
            (1440, 900),
            (1600, 1000),
            (1920, 1080),
            (2048, 1320),
        )
    ]


def _artifact(model, views) -> bytes:
    return render_html(build_bundle(model, views)).encode("utf-8")


def _fixture(project_root, tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    return _model_and_views(project_root, inputs)


def _evidence(model, views):
    return build_browser_evidence(
        _artifact(model, views),
        _measurements(),
        console={"errorCount": 0, "warningCount": 0},
        checked_at="2026-08-21T15:30:00Z",
        limitations=[],
    )


def test_v060_prepare_verified_candidate_is_hash_bound_and_does_not_promote(
    project_root, tmp_path
):
    model, views = _fixture(project_root, tmp_path)
    delivery_project = tmp_path / "project"
    delivery_project.mkdir()
    receipt, created = prepare_delivery(
        delivery_project,
        model,
        views,
        browser_evidence=_evidence(model, views),
        generated_at="2026-08-21T15:30:01Z",
    )

    root = delivery_project / ".panorama-work" / "verified-delivery" / "v0.1"
    candidate = root / receipt["artifact"]["ref"]
    receipt_path = root / "receipts" / f"{receipt['deliveryId']}.json"
    evidence_path = root / receipt["gates"]["browser"]["evidenceRef"]
    assert created is True
    assert candidate.exists() and receipt_path.exists() and evidence_path.exists()
    assert receipt["gates"] == {
        "modelView": "passed",
        "geometry": "passed",
        "privacy": "passed",
        "offlineSecurity": "passed",
        "browser": {
            "status": "passed",
            "evidenceRef": f"evidence/{_evidence(model, views)['evidenceId']}.json",
            "evidenceHash": _evidence(model, views)["integrity"]["evidenceHash"],
        },
        "visualReview": "pending",
    }
    assert not (root / "last-good").exists()
    loaded, loaded_path = load_delivery(delivery_project, receipt["deliveryId"])
    assert loaded == receipt
    assert loaded_path == candidate


def test_v060_prepare_is_idempotent_but_candidate_tamper_fails_closed(
    project_root, tmp_path
):
    model, views = _fixture(project_root, tmp_path)
    delivery_project = tmp_path / "project"
    delivery_project.mkdir()
    evidence = _evidence(model, views)
    first, first_created = prepare_delivery(
        delivery_project,
        model,
        views,
        browser_evidence=evidence,
        generated_at="2026-08-21T15:31:00Z",
    )
    second, second_created = prepare_delivery(
        delivery_project,
        model,
        views,
        browser_evidence=evidence,
        generated_at="2026-08-21T15:32:00Z",
    )
    assert first_created is True
    assert second_created is False
    assert second == first

    root = delivery_project / ".panorama-work" / "verified-delivery" / "v0.1"
    candidate = root / first["artifact"]["ref"]
    candidate.write_text("tampered", encoding="utf-8")
    with pytest.raises(VerifiedDeliveryError, match="不匹配"):
        load_delivery(delivery_project, first["deliveryId"])
    with pytest.raises(VerifiedDeliveryConflictError, match="异字节冲突"):
        prepare_delivery(
            delivery_project,
            model,
            views,
            browser_evidence=evidence,
            generated_at="2026-08-21T15:33:00Z",
        )


def test_v060_browser_evidence_rejects_missing_viewport_overflow_and_wrong_artifact(
    project_root, tmp_path
):
    model, views = _fixture(project_root, tmp_path)
    artifact = _artifact(model, views)
    missing = build_browser_evidence(
        artifact,
        _measurements(),
        console={"errorCount": 0, "warningCount": 0},
        checked_at="2026-08-21T15:34:00Z",
    )
    missing["viewports"].pop()
    missing["integrity"]["evidenceHash"] = "0" * 64
    from verified_delivery import _hash_without_integrity

    missing["integrity"]["evidenceHash"] = _hash_without_integrity(
        missing, "evidenceHash"
    )
    with pytest.raises(VerifiedDeliveryError, match="缺少四个"):
        validate_browser_evidence(
            missing, artifact_sha256=missing["artifact"]["sha256"], artifact_bytes=len(artifact)
        )

    overflow_measurements = _measurements()
    overflow_measurements[0]["documentOverflowX"] = True
    with pytest.raises(VerifiedDeliveryError, match="Viewport Measurement"):
        build_browser_evidence(
            artifact,
            overflow_measurements,
            console={"errorCount": 0, "warningCount": 0},
            checked_at="2026-08-21T15:35:00Z",
        )

    evidence = _evidence(model, views)
    with pytest.raises(VerifiedDeliveryError, match="字节不匹配"):
        validate_browser_evidence(
            evidence, artifact_sha256="f" * 64, artifact_bytes=len(artifact)
        )


def test_v060_browser_evidence_hash_tamper_fails(project_root, tmp_path):
    model, views = _fixture(project_root, tmp_path)
    evidence = deepcopy(_evidence(model, views))
    evidence["console"]["warningCount"] = 1
    with pytest.raises(VerifiedDeliveryError, match="Hash 不匹配"):
        validate_browser_evidence(
            evidence,
            artifact_sha256=evidence["artifact"]["sha256"],
            artifact_bytes=evidence["artifact"]["bytes"],
        )
