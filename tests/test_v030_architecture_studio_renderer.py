from __future__ import annotations

from pathlib import Path
import json

from panorama_io import compute_presentation_hash, extract_data


def test_renderer_exposes_studio_as_system_mode_not_primary_view(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert 'var RENDERER_VERSION = "0.4.0"' in source
    assert source.count('data-view="control"') == 1
    assert source.count('data-view="system"') == 1
    assert source.count('data-view="evolution"') == 1
    assert 'data-studio-mode="view"' in source
    assert 'data-studio-mode="studio"' in source
    assert "架构设计 Studio" in source
    assert "function renderStudio()" in source


def test_studio_keeps_offline_csp_and_discloses_bridge_governance(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert "connect-src 'none'" in source
    assert "离线" in source
    assert "正式全景不会受到影响" in source
    assert "Codex 只读评审" in source
    assert "生成正式提案" in source
    assert "/api/v1/health" in source
    assert "X-Panorama-Capability" in source
    assert "X-Panorama-CSRF" in source
    assert "Proposal Hash" in source


def test_studio_session_separates_semantic_and_layout_operations(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert 'format: "panorama-architecture-session.v0.1"' in source
    assert "semanticOperations: []" in source
    assert "layoutOperations: []" in source
    assert 'appendStudioOperation("layout.move"' in source
    assert "affectsSemanticHash: Boolean(semantic)" in source
    assert "布局操作不影响语义" in source


def test_studio_supports_isolated_draft_interactions(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function addStudioNode(",
        "function deleteStudioNode(",
        "function addStudioEdge(",
        "function deleteStudioEdge(",
        "function updateStudioField(",
        "function exportStudioSession(",
        "function importStudioSession(",
        "function studioSessionShapeValid(",
        "function validateStudioSession(",
        "studioUndoStack",
        "studioRedoStack",
        "window.localStorage",
    ):
        assert token in source


def test_reference_htmls_match_current_renderer_and_source_json(project_root: Path):
    template = project_root / "templates" / "panorama.html"
    expected_presentation = compute_presentation_hash(
        template.read_text(encoding="utf-8")
    )
    for json_name, html_name in (
        ("reference-project.v0.1.json", "reference-project.html"),
        ("reference-project.v0.2.json", "reference-project.v0.2.html"),
    ):
        json_path = project_root / "examples" / json_name
        html_path = project_root / "examples" / html_name
        assert extract_data(html_path) == json.loads(
            json_path.read_text(encoding="utf-8")
        )
        assert (
            compute_presentation_hash(html_path.read_text(encoding="utf-8"))
            == expected_presentation
        )
