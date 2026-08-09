from __future__ import annotations

import json
import os
from pathlib import Path

import discover_project_evidence as evidence_discovery
from discover_project_evidence import (
    discover_project_evidence,
    is_within_root,
    main as discover_main,
)


def _write(path: Path, text: str = "evidence") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _by_path(report: dict) -> dict[str, dict]:
    return {item["path"]: item for item in report["candidates"]}


def test_evidence_discovery_classifies_structured_decision_test_narrative_runtime(
    tmp_path: Path,
):
    _write(tmp_path / "state" / "current-state.json", '{"stage":"build"}')
    _write(tmp_path / "config" / "resource-status.yaml", "status: unknown")
    _write(tmp_path / "docs" / "adr" / "adr-001.md", "# Accepted Decision")
    _write(tmp_path / "tests" / "test_service.py", "def test_service(): pass")
    _write(tmp_path / "README.md", "# Project guide")
    _write(tmp_path / "deploy" / "docker-compose.yml", "services: {}")
    _write(tmp_path / "src" / "service.py", "print('implementation')")

    report = discover_project_evidence(tmp_path)
    candidates = _by_path(report)

    assert candidates["state/current-state.json"]["kind"] == "state"
    assert candidates["config/resource-status.yaml"]["kind"] == "state"
    assert candidates["docs/adr/adr-001.md"]["kind"] == "decision"
    assert candidates["tests/test_service.py"]["kind"] == "test"
    assert candidates["README.md"]["kind"] == "narrative"
    assert candidates["deploy/docker-compose.yml"]["kind"] == "runtime"
    assert report["repository"]["sourceRoots"] == ["src"]


def test_secret_risk_files_are_metadata_only_and_never_probed(tmp_path: Path):
    secret = "UNIQUE_SECRET_VALUE_SHOULD_NEVER_APPEAR"
    _write(
        tmp_path / ".env",
        f"TOKEN={secret}\nPANORAMA_DATA_START\n"
        '<script id="project-panorama-data">secret</script>',
    )

    report = discover_project_evidence(tmp_path)
    candidate = _by_path(report)[".env"]
    rendered = json.dumps(report, ensure_ascii=False)

    assert candidate["secretRisk"] is True
    assert candidate["managedPanoramaHint"] is False
    assert candidate["panoramaKind"] == "none"
    assert secret not in rendered
    assert "Secret-risk" in report["scan"]["contentPolicy"]
    assert "knowledge-base" in report["scan"]["contentPolicy"]


def test_only_html_is_content_probed_for_marker_detection(
    tmp_path: Path, monkeypatch
):
    _write(tmp_path / "state.json", '{"private_project_fact":"metadata-only"}')
    _write(tmp_path / "README.md", "# Formal knowledge content")
    _write(tmp_path / "overview.html", "<h1>Project Overview</h1>")
    _write(
        tmp_path / "vault" / "private-overview.html",
        "<!-- PANORAMA_DATA_START -->"
        '<script id="project-panorama-data">private knowledge</script>',
    )
    calls: list[str] = []
    original = evidence_discovery._read_probe

    def tracked(path: Path, *, limit: int = 131_072) -> str:
        calls.append(path.name)
        return original(path, limit=limit)

    monkeypatch.setattr(evidence_discovery, "_read_probe", tracked)
    discover_project_evidence(tmp_path)

    assert calls == ["overview.html"]
    private = _by_path(discover_project_evidence(tmp_path))[
        "vault/private-overview.html"
    ]
    assert private["knowledgeBaseHint"] is True
    assert private["managedPanoramaHint"] is False


def test_discovery_does_not_escape_project_root(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside-state.json"
    _write(outside, '{"outside":true}')
    assert is_within_root(outside, root) is False

    link = root / "outside-link.json"
    try:
        os.symlink(outside, link)
    except OSError:
        pass

    report = discover_project_evidence(root)
    assert "outside-link.json" not in _by_path(report)
    assert all(not item["path"].startswith("..") for item in report["candidates"])


def test_discovery_excludes_eval_oracle_paths(tmp_path: Path):
    _write(tmp_path / "README.md", "# Project")
    _write(
        tmp_path / "evals" / "semantic" / "cases" / "case-a" / "invariants.yaml",
        "must_detect: [hidden_oracle_fact]",
    )

    paths = _by_path(discover_project_evidence(tmp_path))
    assert "README.md" in paths
    assert not any(path.startswith("evals/") for path in paths)


def test_managed_and_legacy_panorama_markers_are_distinguished(tmp_path: Path):
    _write(
        tmp_path / "managed.html",
        "<!-- PANORAMA_DATA_START -->"
        '<script id="project-panorama-data" type="application/json">{}</script>',
    )
    _write(
        tmp_path / "project-overview.html",
        "<html><title>Project Overview</title><h1>Architecture Status Dashboard</h1></html>",
    )
    _write(tmp_path / "docs" / "project-guide.md", "# Human-maintained project guide")

    candidates = _by_path(discover_project_evidence(tmp_path))
    managed = candidates["managed.html"]
    legacy = candidates["project-overview.html"]
    narrative_legacy = candidates["docs/project-guide.md"]

    assert managed["managedPanoramaHint"] is True
    assert managed["legacyPanoramaHint"] is False
    assert managed["panoramaKind"] == "managed"
    assert legacy["managedPanoramaHint"] is False
    assert legacy["legacyPanoramaHint"] is True
    assert legacy["panoramaKind"] == "legacy"
    assert narrative_legacy["legacyPanoramaHint"] is True
    assert narrative_legacy["panoramaKind"] == "legacy"


def test_discovery_cli_emits_json_without_writing_project(
    tmp_path: Path, capsys
):
    _write(tmp_path / "status.json", '{"status":"unknown"}')
    before = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    assert discover_main([str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    after = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    assert payload["scan"]["candidateCount"] == 1
    assert before == after


def test_skill_contains_mandatory_semantic_protocol_contract(project_root: Path):
    skill = (project_root / "SKILL.md").read_text(encoding="utf-8")
    required = (
        "evidence discovery",
        "Fact Class",
        "Source Freshness Audit",
        "Preserve Conflict",
        "Historical Verification",
        "Current Runtime Availability",
        "Legacy",
        "Current / Target / Transition",
        "Risk Candidate",
        "Formal Finding",
        "INIT Preview Gate",
        "HUMAN REVIEW",
        "docs/semantic-modeling-protocol.md",
    )
    for phrase in required:
        assert phrase in skill


def test_semantic_protocol_preserves_truthfulness_and_reasoning_boundary(
    project_root: Path,
):
    protocol = (project_root / "docs" / "semantic-modeling-protocol.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "Historical Verification ≠ Current Runtime Availability",
        "Planned ≠ Implemented ≠ Deployed ≠ Active",
        "Installed ≠ Running",
        "Not Detected ≠ Absent",
        "Narrative Documentation cannot silently override fresher structured evidence",
        "Requirement Extraction",
        "Module Decomposition",
        "Architecture Version Reconstruction",
        "Script 负责",
        "Agent 负责",
        "输出 Preview 后停止",
    ):
        assert phrase in protocol


def test_eval_harness_contains_fixed_bare_prompt_and_100_point_rubric(
    project_root: Path,
):
    eval_root = project_root / "evals" / "semantic"
    expected_files = (
        "README.md",
        "rubric.yaml",
        "prompts.md",
        "result-template.md",
        "cases/ai-wiki/invariants.yaml",
        "cases/ai-wiki/notes.md",
    )
    for relative in expected_files:
        assert (eval_root / relative).is_file()

    prompts = (eval_root / "prompts.md").read_text(encoding="utf-8")
    bare = (
        "为这个项目初始化 Managed Project Panorama。\n"
        "先只输出 INIT Preview，不要写入正式 HTML。"
    )
    assert bare in prompts

    rubric = (eval_root / "rubric.yaml").read_text(encoding="utf-8")
    assert 'total_points: 100' in rubric
    assert 'bare_skill_minimum: 80' in rubric
    assert 'external_instruction_count' in rubric
    assert 'unsupported_claim_count' in rubric
    assert 'eval_oracle_used_to_generate_answer' in rubric


def test_production_semantic_logic_has_no_case_specific_shortcuts(project_root: Path):
    production_files = (
        project_root / "SKILL.md",
        project_root / "docs" / "semantic-modeling-protocol.md",
        project_root / "scripts" / "discover_project_evidence.py",
    )
    forbidden = (
        "ai-wiki",
        "q7",
        "lancedb",
        "deepseek",
        "docs/decisions.yaml",
    )
    for path in production_files:
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"case-specific token {token!r} found in {path}"
