from __future__ import annotations

from pathlib import Path


def test_candidate_tabs_implement_complete_keyboard_model(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "role='tablist' aria-label='架构候选方案'",
        "role='tab' aria-selected='",
        "role='tabpanel'",
        "aria-controls='studio-candidate-panel'",
        'event.key === "ArrowLeft"',
        'event.key === "ArrowRight"',
        'event.key === "Home"',
        'event.key === "End"',
        "[data-studio-candidate-tab][aria-selected='true']",
    ):
        assert token in source

    assert "role='tree'" not in source
    assert "role='treeitem'" not in source


def test_canvas_nodes_have_keyboard_layout_controls_without_semantic_invalidation(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function moveStudioNodeWithKeyboard(",
        "Alt 加方向键调整画布位置",
        "event.altKey",
        "event.shiftKey ? 40 : 10",
        'appendStudioOperation("layout.move"',
        "affectsSemanticHash: Boolean(semantic)",
    ):
        assert token in source

    keyboard_move = source[
        source.index("function moveStudioNodeWithKeyboard(") : source.index(
            "function cssEscape("
        )
    ]
    assert "invalidateStudioArtifacts" not in keyboard_move
    assert 'appendStudioOperation("layout.move"' in keyboard_move
    assert ", false);" in keyboard_move


def test_every_svg_edge_has_focusable_text_equivalent(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function studioFlowListHtml(",
        "数据流文本列表",
        "class='studio-flow-item'",
        "data-studio-select-edge=",
        "aria-current='",
        "aria-hidden='true' focusable='false'",
    ):
        assert token in source


def test_drawer_and_dialog_focus_lifecycle_is_explicit(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        'role="dialog" aria-modal="true"',
        "function rememberDrawerReturnFocus(",
        "function trapDrawerFocus(",
        "returnTarget.focus()",
        "function showStudioDialog(",
        "function cancelStudioDialog(",
        "focusStudioReturn(returnAction)",
        'event.target.matches("dialog.studio-dialog")',
    ):
        assert token in source


def test_mobile_panels_and_touch_targets_meet_p0c_contract(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function studioMobileTabsHtml(",
        "Studio 移动端面板",
        "data-studio-mobile-panel=",
        "data-studio-panel='library'",
        "data-studio-panel='canvas'",
        "data-studio-panel='inspector'",
        ".studio-mobile-tabs { display: flex; }",
        ".studio-workspace [data-studio-panel].is-mobile-hidden { display: none; }",
        "touch-action: pan-x pan-y",
        "touch-action: none",
    ):
        assert token in source

    assert ".studio-action {\n      min-height: 44px" in source
    assert ".studio-library-btn { width: 100%; min-height: 44px" in source
    assert ".icon-btn { border: 1px solid var(--rule); background: #fff; border-radius: 4px; width: 44px; height: 44px" in source
    assert ".studio-shell .cell-sub { font-size: 12px; }" in source


def test_important_studio_errors_remain_persistent_alerts(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    assert "class='studio-conflict' role='alert'" in source
    assert "class='studio-artifact-line is-error' role='alert'" in source
    assert "状态同时使用文字" not in source  # Contract is implemented, not claimed in UI copy.


def test_async_bridge_updates_coalesce_studio_renders(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    assert "studioRenderQueued: false" in source
    assert "function scheduleStudioRender()" in source
    assert "if (state.studioRenderQueued) return" in source
    assert "state.studioRenderQueued = true" in source
    assert 'if (state.studioMode === "studio") scheduleStudioRender();' in source
