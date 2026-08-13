from __future__ import annotations

import json
from pathlib import Path

from panorama_io import compute_presentation_hash, extract_data


def test_trace_route_uses_only_declared_id_fields_and_shows_json_pointers(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "var TRACE_RELATION_SPECS = [",
        '["moduleIds", "module", "Module"]',
        '["acceptanceCriteriaIds", "acceptance", "Acceptance"]',
        '["sourceReferenceIds", "reference", "Reference"]',
        '["workItemIds", "work_item", "WorkItem"]',
        '["gateIds", "gate", "Gate"]',
        '["evidenceReferenceIds", "reference", "Evidence Reference"]',
        '["deploymentIds", "deployment", "Deployment"]',
        '"/moduleDeployments/" + deploymentIndex + "/moduleId"',
        "function traceRegisterEdge(",
        "sourcePath: sourcePath",
        "<code class='trace-pointer'>",
    ):
        assert token in source

    assert "Trace 只读取正式 ID 字段" in source
    assert "不执行 Mission、调度或治理决策" in source
    assert "data-entity-type='requirement'><span class='mono'>" in source


def test_multi_hop_routes_keep_intermediate_entities_and_each_hop_source(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function traceRoutes(",
        "nodes: route.nodes.concat([next])",
        "edges: route.edges.concat([edge])",
        "多跳 Route · 保留中间实体",
        '"<div>Hop " + (index + 1)',
        'e(edge.sourcePath)',
    ):
        assert token in source


def test_trace_gap_candidates_are_not_formal_findings(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function traceGapCandidates(",
        "uncovered_requirement",
        "orphan_work_item",
        "evidence_gap",
        "△ Trace Gap Candidate",
        "function traceFormalFindings(",
        "Formal Finding ·",
        "Candidate 与 Formal Finding 保持语义分离",
        "not_detected 不等于 absent",
    ):
        assert token in source


def test_gate_acceptance_and_deployment_projections_are_evidence_gated(
    template_path: Path,
):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function governanceProjection(",
        'releases = releases.concat(entitiesByIds("release", item.targetReleaseIds))',
        'gate.status !== "passed"',
        "evidenceReferencesComplete(gate.evidenceReferenceIds)",
        'acceptance.status !== "accepted"',
        'acceptance.verificationStatus !== "passed"',
        "evidenceReferencesComplete(acceptance.evidenceReferenceIds)",
        'deployment.status === "active"',
        "deployment.releaseId === release.id",
        "deployment.architectureVersionId === release.architectureVersionId",
        "modulesObserved",
        'verified: { state: !gates.length ? "unknown"',
        'accepted: { state: !acceptances.length ? "unknown"',
        "deployed_observed",
        "V0.4 不使用 integrated",
    ):
        assert token in source


def test_proposal_projection_renders_exact_bridge_outputs(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        "function studioProposalProjectionHtml(",
        "arr(proposal.operations)",
        "arr(proposal.affectedEntities)",
        "proposal.validation || {}",
        "operation.op",
        "operation.path",
        "JSON.stringify(operation.value, null, 2)",
        "<details class='proposal-operation'>",
        "JSON Patch · propose_update.py 原样输出",
        "Affected Entities · 原样输出",
        "Validation · 原样输出",
        "function studioChangeReadyProjection(",
        'reason !== "formal_source_unbound"',
        "Proposal 与活动 Candidate Semantic Hash 一致",
        "Formal Validation 当前且 errors=0",
        "没有 Session CAS Conflict",
        'governanceCardHtml("change_ready", changeReady)',
    ):
        assert token in source


def test_trace_projection_keeps_three_primary_views_and_reference_parity(
    project_root: Path, template_path: Path
):
    source = template_path.read_text(encoding="utf-8")
    assert source.count('data-view="control"') == 1
    assert source.count('data-view="system"') == 1
    assert source.count('data-view="evolution"') == 1
    assert "Trace Route" in source

    expected_presentation = compute_presentation_hash(source)
    for json_name, html_name in (
        ("reference-project.v0.1.json", "reference-project.html"),
        ("reference-project.v0.2.json", "reference-project.v0.2.html"),
    ):
        expected_data = json.loads(
            (project_root / "examples" / json_name).read_text(encoding="utf-8")
        )
        html = project_root / "examples" / html_name
        assert extract_data(html) == expected_data
        assert compute_presentation_hash(html.read_text(encoding="utf-8")) == expected_presentation
