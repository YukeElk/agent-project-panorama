from __future__ import annotations

from pathlib import Path


def test_studio_consumes_fragment_capability_and_uses_bridge_security_headers(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert 'var RENDERER_VERSION = "0.4.0"' in source
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
        "function studioCandidateDialogHtml(",
        "function submitStudioCandidateDialog(",
        "studioCompareIds",
        "最多同时比较 3 个候选方案",
        "expectedRevision: expected",
        "state.studioBridge.conflict",
        "加载服务端版本",
        "scheduleStudioBridgeSave()",
    ):
        assert token in source
    assert "window.prompt(" not in source


def test_semantic_operations_invalidate_artifacts_but_layout_does_not(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    assert 'if (semantic) invalidateStudioArtifacts("candidate_semantic_changed");' in source
    assert 'appendStudioOperation("layout.move"' in source
    assert "affectsSemanticHash: Boolean(semantic)" in source
    assert "布局操作不影响语义" in source


def test_studio_exposes_bridge_authoritative_dual_hash_and_dual_track_journal(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function studioHashStripHtml(",
        "Bridge 权威双 Hash",
        "not_computed_by_bridge",
        "Bridge 权威值 · 语义编辑会改变",
        "Bridge 权威值 · 仅布局坐标会改变",
        "function studioJournalHtml(",
        "Session Operations",
        "data-studio-journal='semantic'",
        "data-studio-journal='layout'",
        "affectsSemanticHash",
        "before\\n",
        "after\\n",
    ):
        assert token in source


def test_studio_semantic_diff_is_field_level_and_excludes_layout_coordinates(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function studioSemanticIdentity(",
        "function studioSemanticDiff(",
        'return "entity:" + ref.type + ":" + ref.id',
        "候选方案字段级 Semantic Diff",
        "模块字段级 Diff",
        "数据流字段级 Diff",
        "按 entityRef / session-local ID 对齐",
        "坐标已排除",
        'fieldChanges(base, compare, ["assumptions", "unknowns"])',
        '"responsibilities", "nonResponsibilities"',
        '"protocol", "communicationMode", "flowDirection", "dataSummary"',
    ):
        assert token in source

    semantic_diff = source[source.index("function studioSemanticDiff("):source.index("function studioDiffValue(")]
    assert '"x"' not in semantic_diff
    assert '"y"' not in semantic_diff


def test_studio_discloses_multiple_concrete_stale_reasons_and_field_impact(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function studioStaleReasons(",
        "function studioStaleStripHtml(",
        "candidate_semantic_changed",
        "candidate_switched",
        "panorama_revision_changed",
        "data_hash_changed",
        "git_head_changed",
        "source_snapshot_changed",
        "source_content_changed",
        "session_cas_conflict",
        "coverage_incomplete",
        "formal_source_uninitialized",
        "STALE 原因",
        "影响 Semantic Hash",
        "仅影响 Layout Hash",
        'invalidateStudioArtifacts("candidate_switched")',
        "function updateStudioEdgeField(",
        'data-studio-edge-field=\'protocol\'',
        'data-studio-edge-field=\'communicationMode\'',
        'data-studio-edge-field=\'flowDirection\'',
        'data-studio-edge-field=\'reliabilitySummary\'',
    ):
        assert token in source


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
