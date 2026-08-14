from __future__ import annotations

from pathlib import Path


def test_receipt_adapter_lives_in_source_view_without_a_fourth_primary_view(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")
    assert source.count('data-view="control"') == 1
    assert source.count('data-view="system"') == 1
    assert source.count('data-view="evolution"') == 1
    assert "Verification Receipt Adapter" in source
    assert "function verificationReceiptImportHtml()" in source
    assert "data-receipt-import" in source
    assert "Experimental Adapter" in source
    assert "不会执行测试、自动选优或直接改变 Gate" in source


def test_receipt_import_uses_bridge_preview_then_normal_proposal_approval_apply(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")
    for token in (
        '"/api/v1/verification-receipts/preview"',
        '"/api/v1/verification-receipts/proposals"',
        '"/api/v1/approvals"',
        '"/api/v1/apply"',
        "批准只绑定该 64 位 Proposal Hash",
        "write-once Approval",
        "Apply 仍执行 Revision/Data/Source/Presentation 保护",
    ):
        assert token in source


def test_gate_and_acceptance_drawers_show_deduplicated_receipt_timeline(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")
    for token in (
        "function verificationReceiptReferences(type, id)",
        "verificationReceiptHash",
        "function verificationReceiptTimelineHtml(type, id)",
        'drawerSection("Verification Receipt Timeline"',
        "External Finding ·",
        "不是 Panorama Formal Finding",
        "Isolation 原样披露",
        "未自动推进 Gate、Acceptance 或 Approval",
    ):
        assert token in source


def test_receipt_upload_is_bounded_and_errors_remain_visible(template_path: Path):
    source = template_path.read_text(encoding="utf-8")
    assert "file.size > 1024 * 1024" in source
    assert "Receipt 超过 1 MiB 上限" in source
    assert "Receipt 导入失败" in source
    assert "role='alert'" in source
    assert "preview.validation && preview.validation.counts" in source
    assert "Number(validationCounts.warnings || 0)" in source
    assert "not_enforced" not in source.split("function receiptIsolationHtml", 1)[1].split(
        "function receiptVerificationHtml", 1
    )[0]  # UI renders the exact receipt enum through status(), not a guessed label.


def test_mobile_receipt_and_system_actions_keep_a_44px_touch_target(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")
    mobile = source.split("@media (max-width: 840px)", 1)[1].split(
        "@media (prefers-reduced-motion", 1
    )[0]
    assert ".mode-switch-btn, .small-btn { min-height: 44px; }" in mobile
