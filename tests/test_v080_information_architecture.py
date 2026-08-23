from __future__ import annotations

import json


def test_control_is_decision_cockpit_not_requirement_inventory(project_root):
    source = (project_root / "templates" / "panorama.html").read_text(encoding="utf-8")

    control = source[source.index("function renderControl()") : source.index("function renderSystem()")]
    assert "阶段决策视图" in control
    assert "controlStageDecisionSummary(currentStage)" in control
    assert "进入完整演进视图" in source
    assert "需求快照" not in control
    assert "function requirementSnapshot()" not in source


def test_evolution_owns_complete_requirement_board(project_root):
    source = (project_root / "templates" / "panorama.html").read_text(encoding="utf-8")

    evolution = source[source.index("function renderEvolution()") : source.index("function showToast(")]
    assert "requirementEvolutionBoard()" in evolution
    assert "需求演进板" in evolution
    assert "阶段与执行工作" in evolution
    assert "架构层级与功能模块" in evolution
    assert "架构版本与迁移" in evolution
    assert "发布与交付证据" in evolution
    assert "由工作项 / 发布推导" in evolution
    assert "未使用名称匹配或画布位置" in evolution


def test_requirement_projection_only_uses_exact_id_routes(project_root):
    source = (project_root / "templates" / "panorama.html").read_text(encoding="utf-8")
    helpers = source[source.index("function sortedStages()") : source.index("function renderControl()")]

    assert "arr(item.requirementIds).indexOf(requirementId)" in helpers
    assert "item.stageId" in helpers
    assert "release.stageId" in helpers
    assert "arr(transition.workItemIds)" in helpers
    assert "release.architectureVersionId" in helpers
    assert "transition.fromVersionId" in helpers
    assert "transition.toVersionId" in helpers
    assert "module.targetLayerId || module.layerId" in helpers
    assert "name.includes" not in helpers


def test_requirement_detail_prioritizes_human_impact_chain_and_collapses_raw_trace(project_root):
    source = (project_root / "templates" / "panorama.html").read_text(encoding="utf-8")

    inspector = source[source.index("function requirementImpactHtml") : source.index("function kvRow(")]
    assert "需求演进与影响" in inspector
    assert "1 · 需求定义" in inspector
    assert "2 · 演进阶段" in inspector
    assert "3 · 层级与模块" in inspector
    assert "4 · 架构版本 / 迁移" in inspector
    assert "5 · 发布与验收" in inspector
    assert "交付证据链" in inspector
    assert "<details class='drawer-deep-evidence'>" in inspector
    assert "技术诊断：原始 Trace、字段路径与校验发现" in inspector
    assert "drawerSection(\"Trace Route\"" not in inspector


def test_delivery_projection_uses_human_chinese_labels(project_root):
    source = (project_root / "templates" / "panorama.html").read_text(encoding="utf-8")
    projection = source[source.index("function governanceProjection(") : source.index("function pendingReview(")]

    assert 'governanceCardHtml("验证"' in projection
    assert 'governanceCardHtml("验收"' in projection
    assert 'governanceCardHtml("部署"' in projection
    assert "暂无完整证据" in projection
    assert "no complete evidence" not in projection


def test_requirement_evolution_contract_records_schema_boundary(project_root):
    contract = (project_root / "docs" / "requirement-evolution-contract.md").read_text(encoding="utf-8")
    schema = json.loads((project_root / "schema" / "panorama.schema.v0.2.json").read_text(encoding="utf-8"))

    assert "P1 Requirement Realization 数据合同" in contract
    assert "P1 可重建架构版本快照" in contract
    assert "P1 Core Schema 待实现" in contract
    assert "requirementRealizations" not in schema["properties"]
    assert "SF-67" in (project_root / "docs" / "schema-findings.md").read_text(encoding="utf-8")
