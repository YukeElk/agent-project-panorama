from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from panorama_io import compute_presentation_hash, extract_data, replace_data
from studio_bridge import BRIDGE_VERSION, StudioBridge


def _evidence_data(reference_data: dict) -> dict:
    data = deepcopy(reference_data)
    data["schemaVersion"] = "0.2"
    data["sourceBinding"] = {
        "mode": "git",
        "gitHead": "a" * 40,
        "gitBranch": "main",
        "sourceSnapshotHash": "b" * 64,
        "observedAt": "2026-08-13T08:00:00Z",
        "lastObservationBatchId": "OBS-DEMO",
        "extensions": {},
    }
    data["factProvenance"] = [
        {
            "id": "PROV-DEMO",
            "path": "/architecture/modules/0/status/implementationMaturity",
            "authority": "observed",
            "confidence": "high",
            "evidence": ["REF-CODE-RETRIEVAL", "git status --short"],
            "sourceCommit": "a" * 40,
            "observedAt": "2026-08-13T08:00:00Z",
            "observationBatchId": "OBS-DEMO",
            "extensions": {"factClass": "CURRENT_IMPLEMENTATION"},
        }
    ]
    data["currentArchitectureSnapshots"] = [
        {
            "id": "ARCH-SNAP-DEMO",
            "sourceCommit": "a" * 40,
            "sourceSnapshotHash": "b" * 64,
            "observedAt": "2026-08-13T08:00:00Z",
            "moduleIds": [data["architecture"]["modules"][0]["id"]],
            "connectionIds": [],
            "unmappedPaths": [],
            "provenanceIds": ["PROV-DEMO"],
            "extensions": {},
        }
    ]
    data["standardAssessments"] = []
    data["observationBatches"] = [
        {
            "id": "OBS-DEMO",
            "revisionFrom": data["meta"]["revision"],
            "revisionTo": data["meta"]["revision"] + 1,
            "sourceCommitFrom": None,
            "sourceCommitTo": "a" * 40,
            "policyHash": "c" * 64,
            "evidenceSnapshotHash": "b" * 64,
            "standardAssessmentIds": [],
            "operations": [],
            "provenanceIds": ["PROV-DEMO"],
            "conflicts": ["Machine state differs from narrative documentation"],
            "findings": [],
            "validationResult": {"valid": True, "errors": 0, "warnings": 1},
            "observedAt": "2026-08-13T08:00:00Z",
            "status": "applied",
            "extensions": {},
        }
    ]
    return data


def test_renderer_exposes_evidence_inspector_without_a_fourth_primary_view(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert 'var RENDERER_VERSION = "0.4.0"' in source
    assert source.count('data-view="control"') == 1
    assert source.count('data-view="system"') == 1
    assert source.count('data-view="evolution"') == 1
    assert "function evidenceInspectorHtml()" in source
    assert "Evidence Inspector" in source
    assert "证据链与冲突" in source
    assert "来源详情" in source
    assert "data-provenance-id" in source
    assert "重新检测来源" in source
    assert "function refreshEvidenceContext()" in source


def test_offline_freshness_never_claims_current_or_converts_confidence_to_score(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert 'status: "currentness_unknown"' in source
    assert "CURRENTNESS UNKNOWN · 仅显示记录时状态" in source
    assert "未连接 Bridge；不得据此声明当前 Git 或 Source 仍匹配" in source
    assert 'observationBadge.classList.add(observedHead ? "is-recorded" : "is-stale")' in source
    assert 'observationBadge.textContent = observedHead ? "已记录 · "' in source
    assert 'observedHead ? "is-current"' not in source
    assert "Confidence: " in source
    assert "confidence * 100" not in source
    assert "confidenceScore" not in source


def test_conflicts_are_batch_summaries_and_never_invent_structured_sides(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert "Conflict Summary" in source
    assert "Needs Human Review — structured sides unavailable" in source
    assert "Renderer 不裁决冲突" in source


def test_v01_has_an_explicit_evidence_empty_state(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    assert "尚无事实级证据记录" in source
    assert "Schema 0.1 不包含持续观察字段" in source
    assert "架构基线信息仍显示在下方" in source


def test_bridge_context_exposes_hash_only_live_freshness(
    tmp_path: Path, template_path: Path, reference_data: dict
):
    panorama = tmp_path / "project-panorama.local.html"
    data = _evidence_data(reference_data)
    replace_data(template_path, data, panorama)
    app = StudioBridge(panorama, tmp_path)
    try:
        assert BRIDGE_VERSION == "0.4.0"
        context = app.context()
        freshness = context["sourceFreshness"]
        assert freshness["status"] in {"match", "drift", "uninitialized", "incomplete"}
        assert freshness["checkedAt"]
        assert freshness["contentDigest"]["algorithm"] == "sha256-path-size-content-v1"
        assert set(freshness["contentDigest"]) == {
            "algorithm",
            "baselineHash",
            "actualHash",
            "fileCount",
            "totalBytes",
            "coverageComplete",
            "incompleteReason",
        }
        rendered = str(freshness)
        assert "source bytes" not in rendered
        assert data["factProvenance"][0]["evidence"][1] not in rendered
    finally:
        app.close()


def test_bridge_freshness_reports_match_drift_and_incomplete(
    tmp_path: Path, template_path: Path, reference_data: dict, monkeypatch
):
    panorama = tmp_path / "project-panorama.local.html"
    replace_data(template_path, reference_data, panorama)
    app = StudioBridge(panorama, tmp_path)
    formal = {
        "sourceBinding": {
            "mode": "git",
            "gitHead": "a" * 40,
            "gitBranch": "main",
            "sourceSnapshotHash": "b" * 64,
            "observedAt": "2026-08-13T08:00:00Z",
            "lastObservationBatchId": "OBS-DEMO",
            "extensions": {},
        }
    }
    digest = {
        "algorithm": "sha256-path-size-content-v1",
        "contentHash": "c" * 64,
        "fileCount": 3,
        "totalBytes": 120,
        "coverageComplete": True,
        "incompleteReason": None,
    }
    app.evidence_live_baseline = {
        "capturedAt": "2026-08-13T08:00:00Z",
        "gitHead": "a" * 40,
        "studioSourceDigest": digest,
    }
    monkeypatch.setattr(
        app,
        "_source_observation",
        lambda: {
            "gitHead": "a" * 40,
            "gitBranch": "main",
            "gitDirty": False,
            "sourceSnapshotHash": "b" * 64,
        },
    )
    try:
        monkeypatch.setattr(
            app,
            "_live_baseline",
            lambda: {
                "capturedAt": "2026-08-13T08:01:00Z",
                "gitHead": "a" * 40,
                "studioSourceDigest": deepcopy(digest),
            },
        )
        assert app._evidence_source_freshness(formal)["status"] == "match"

        changed = deepcopy(digest)
        changed["contentHash"] = "d" * 64
        monkeypatch.setattr(
            app,
            "_live_baseline",
            lambda: {
                "capturedAt": "2026-08-13T08:02:00Z",
                "gitHead": "a" * 40,
                "studioSourceDigest": changed,
            },
        )
        drift = app._evidence_source_freshness(formal)
        assert drift["status"] == "drift"
        assert "source_content_changed" in drift["reasons"]

        incomplete = deepcopy(changed)
        incomplete["coverageComplete"] = False
        incomplete["incompleteReason"] = "byte_limit_exceeded"
        monkeypatch.setattr(
            app,
            "_live_baseline",
            lambda: {
                "capturedAt": "2026-08-13T08:03:00Z",
                "gitHead": "a" * 40,
                "studioSourceDigest": incomplete,
            },
        )
        limited = app._evidence_source_freshness(formal)
        assert limited["status"] == "incomplete"
        assert "coverage_incomplete" in limited["reasons"]
    finally:
        app.close()


def test_v04_renderer_upgrade_keeps_reference_payloads_unchanged(
    project_root: Path, template_path: Path
):
    expected = compute_presentation_hash(template_path.read_text(encoding="utf-8"))
    for json_name, html_name in (
        ("reference-project.v0.1.json", "reference-project.html"),
        ("reference-project.v0.2.json", "reference-project.v0.2.html"),
    ):
        source_json = project_root / "examples" / json_name
        html = project_root / "examples" / html_name
        assert extract_data(html) == __import__("json").loads(
            source_json.read_text(encoding="utf-8")
        )
        assert compute_presentation_hash(html.read_text(encoding="utf-8")) == expected
