from __future__ import annotations

from pathlib import Path


def test_studio_consumes_fragment_capability_and_uses_bridge_security_headers(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert 'var RENDERER_VERSION = "0.3.0"' in source
    assert 'match(/(?:^#|&)cap=([^&]+)/)' in source
    assert "window.history.replaceState" in source
    assert '"X-Panorama-Capability"' in source
    assert '"X-Panorama-CSRF"' in source
    assert '"/api/v1/health"' in source
    assert '"/api/v1/context"' in source
    # The canonical/file renderer remains network inert; the Bridge serves a
    # transient CSP variant instead of weakening this file.
    assert "connect-src 'none'" in source


def test_studio_exposes_complete_bridge_workflow_without_editing_data_element(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        '"/api/v1/sessions"',
        '"/api/v1/formal-validations"',
        '"/api/v1/review-jobs"',
        '"/api/v1/jobs/"',
        '"/api/v1/proposals"',
        '"/api/v1/approvals"',
        '"/api/v1/apply"',
        '"/api/v1/facts/refresh"',
        '"批准 Studio Proposal " + proposal.proposalHash',
        "id='studio-proposal-dialog'",
        "data-studio-action='submit-proposal'",
        "<dialog class='studio-dialog'",
        "data-studio-action='formal-validate'",
        "data-studio-action='review'",
        "data-studio-action='proposal'",
        "data-studio-action='approve'",
        "data-studio-action='apply'",
    ):
        assert token in source

    assert "rootDataElement.textContent =" not in source
    assert "proposalHash: proposal.proposalHash, approvalId: approval.approvalId" in source
    assert 'window.prompt("Proposal 摘要"' not in source
    assert "正式 project-panorama-data 仅由 Bridge Apply 更新" in source
    # Formal validation advances the server-side session revision even when a
    # stale UI result is discarded; the client must retain that CAS revision.
    assert "artifact.sessionRevision" in source
    assert "result.sessionRevision" in source
    assert "baseDataHash: null" in source
    assert "semanticHash: null" in source
    assert "layoutHash: null" in source
    assert "reviewError" in source
    assert "不阻断人工 Proposal" in source
    assert "validation.summary || validation.counts" in source


def test_studio_has_candidate_management_comparison_and_cas_conflict_ui(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function createStudioCandidate(",
        "function renameStudioCandidate(",
        "function archiveStudioCandidate(",
        "studioCompareIds",
        "最多同时比较 3 个候选方案",
        "expectedRevision: expected",
        "state.studioBridge.conflict",
        "加载服务端版本",
        "scheduleStudioBridgeSave()",
    ):
        assert token in source


def test_semantic_operations_invalidate_artifacts_but_layout_does_not(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert "if (semantic) invalidateStudioArtifacts();" in source
    assert 'appendStudioOperation("layout.move"' in source
    assert "affectsSemanticHash: Boolean(semantic)" in source
    assert "布局操作不影响语义" in source


def test_offline_copy_explains_how_to_enable_complete_bridge_loop(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert "file:// 离线草稿" in source
    assert "用 studio_bridge.py 打开即可启用完整闭环" in source
    assert "需连接受控的本地 Panorama Bridge" not in source


def test_async_results_are_generation_bound_and_never_silently_overwrite_drafts(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "studioGeneration",
        "requestGeneration",
        "state.studioGeneration === requestGeneration",
        "旧响应未覆盖本地草稿",
        "studioRequestBinding()",
        "studioRequestIsCurrent(requestBinding)",
        "旧结果已丢弃",
        "expectedRevision: Number(current.sessionRevision || 0)",
        "未从服务端静默覆盖",
        'restored.mode = "offline"',
        "sessionMutationBusy",
    ):
        assert token in source


def test_studio_discloses_errors_findings_agent_state_and_post_apply_freeze(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function studioFindingsHtml(",
        "validation.findings",
        "advisoryReview.findings",
        "Bridge 错误",
        "not_configured",
        "Codex CLI 未配置或不可用",
        "Studio 已冻结",
        "data-studio-action='reload-panorama'",
        "function reloadCurrentPanoramaFromBridge(",
        '"#cap=" + encodeURIComponent(state.studioBridge.capability)',
        "window.location.reload()",
    ):
        assert token in source
