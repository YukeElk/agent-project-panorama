from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import materialize_init as materialize_module
from discover_project_evidence import (
    _knowledge_base_hint,
    _knowledge_operational_parts,
    _knowledge_root_name,
    discover_project_evidence,
)
from inspect_operational_evidence import inspect_operational_evidence
from materialize_init import (
    InitMaterializationError,
    classify_finding,
    compute_preview_hash,
    materialize_prepared_init,
    reconcile_findings,
    semantic_projection,
)
from materialize_init import main as materialize_main
from new_project_data import build_minimal_project
from observe_project_runtime import (
    ObservationError,
    observe_project_runtime,
    source_snapshot,
)
from prepare_init_review import (
    PREPARED_VERSION,
    prepare_init_review,
    render_prepared_preview,
)
from record_init_approval import main as record_approval_main
from record_init_approval import record_init_approval
from validate_panorama import ValidationIssue, validate_data


T1 = "2026-08-09T08:00:00Z"
TP = "2026-08-09T08:30:00Z"
T2 = "2026-08-09T09:00:00Z"
SOURCE_SNAPSHOT = {
    "mode": "git",
    "gitHead": "a" * 40,
    "gitBranch": "main",
    "gitDirty": False,
    "gitStatusEntryCount": 0,
    "gitStatusHash": "b" * 64,
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _candidate(report: dict, path: str) -> dict:
    return next(item for item in report["candidates"] if item["path"] == path)


@pytest.mark.parametrize(
    "name",
    (
        "vault",
        "sandbox-vault",
        "personal_vault",
        "team.wiki",
        "private-knowledge",
        "private-knowledge-base",
        "personal-kb",
    ),
)
def test_knowledge_root_name_accepts_terminal_compounds(name: str):
    assert _knowledge_root_name(name) is True


@pytest.mark.parametrize(
    "name",
    (
        "vault-tools",
        "wiki-renderer",
        "knowledge-graph",
        "kb-client",
        "myvault",
        "knowledgebase-utils",
    ),
)
def test_knowledge_root_name_rejects_prefixes_and_substrings(name: str):
    assert _knowledge_root_name(name) is False


@pytest.mark.parametrize(
    "root_name",
    (
        "sandbox-vault",
        "personal-vault",
        "team_wiki",
        "private-knowledge",
        "private-knowledge-base",
        "personal-kb",
    ),
)
def test_compound_knowledge_roots_protect_ordinary_content(
    tmp_path: Path, root_name: str
):
    relative = f"{root_name}/notes/private.md"
    _write(tmp_path / relative, "private knowledge body")

    candidate = _candidate(discover_project_evidence(tmp_path), relative)

    assert candidate["accessClass"] == "PROJECT_CONTENT"
    assert candidate["knowledgeBaseHint"] is True
    assert candidate["operationalMetadataHint"] is False


@pytest.mark.parametrize(
    "directory_name",
    ("vault-tools", "wiki-renderer", "knowledge-graph", "kb-client"),
)
def test_engineering_directories_are_not_false_positive_knowledge_roots(
    tmp_path: Path, directory_name: str
):
    relative = f"{directory_name}/notes/overview.md"
    _write(tmp_path / relative, "public engineering overview")

    candidate = _candidate(discover_project_evidence(tmp_path), relative)

    assert candidate["accessClass"] == "PUBLIC_PROJECT_EVIDENCE"
    assert candidate["knowledgeBaseHint"] is False


def test_compound_knowledge_root_preserves_operational_escape_boundary(
    tmp_path: Path,
):
    payloads = {
        "sandbox-vault/10-Captures/note.md": "private capture",
        "sandbox-vault/.obsidian/app.json": '{"theme":"private"}',
        "sandbox-vault/tasks/personal-tasks.json": '{"tasks":[]}',
        "sandbox-vault/90-System/tasks/registry.json": '{"tasks":[]}',
        "sandbox-vault/90-System/state/current.json": '{"status":"active"}',
    }
    for relative, payload in payloads.items():
        _write(tmp_path / relative, payload)

    discovery = discover_project_evidence(tmp_path)

    for relative in (
        "sandbox-vault/10-Captures/note.md",
        "sandbox-vault/.obsidian/app.json",
        "sandbox-vault/tasks/personal-tasks.json",
    ):
        candidate = _candidate(discovery, relative)
        assert candidate["accessClass"] == "PROJECT_CONTENT"
        assert candidate["operationalMetadataHint"] is False
    for relative in (
        "sandbox-vault/90-System/tasks/registry.json",
        "sandbox-vault/90-System/state/current.json",
    ):
        candidate = _candidate(discovery, relative)
        assert candidate["accessClass"] == "PROJECT_OPERATIONAL_METADATA"
        assert candidate["operationalMetadataHint"] is True
    obsidian = inspect_operational_evidence(
        tmp_path, "sandbox-vault/.obsidian/app.json", observed_at=T2
    )
    assert obsidian["accessClass"] == "PROJECT_CONTENT"
    assert obsidian["safeToRead"] is False
    assert obsidian["facts"] == {}


def test_compound_root_hint_and_operational_parts_share_canonical_matcher():
    for root_name in (
        "sandbox-vault",
        "personal_vault",
        "team.wiki",
        "private-knowledge",
        "private-knowledge-base",
        "personal-kb",
    ):
        relative = Path(root_name) / "90-System" / "tasks" / "registry.json"
        assert _knowledge_base_hint(relative) is True
        assert _knowledge_operational_parts(relative) == ("90-system", "tasks")


def test_discovery_and_inspector_separate_operational_metadata_from_content(
    tmp_path: Path,
):
    _write(tmp_path / "vault" / "notes" / "private.md", "private knowledge body")
    _write(
        tmp_path / "vault" / "90-System" / "tasks" / "registry.json",
        '{"tasks":[{"id":"TASK-1","status":"active"}]}',
    )

    discovery = discover_project_evidence(tmp_path)
    private_candidate = _candidate(discovery, "vault/notes/private.md")
    registry_candidate = _candidate(
        discovery, "vault/90-System/tasks/registry.json"
    )
    private = inspect_operational_evidence(tmp_path, "vault/notes/private.md")
    registry = inspect_operational_evidence(
        tmp_path,
        "vault/90-System/tasks/registry.json",
        observed_at=T2,
    )

    assert private_candidate["accessClass"] == "PROJECT_CONTENT"
    assert private_candidate["operationalMetadataHint"] is False
    assert registry_candidate["accessClass"] == "PROJECT_OPERATIONAL_METADATA"
    assert registry_candidate["operationalMetadataHint"] is True
    assert private["safeToRead"] is False
    assert private["facts"] == {}
    assert registry["safeToRead"] is True
    assert registry["facts"]["tasks"][0] == {
        "id": "TASK-1",
        "status": "active",
    }


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("knowledge/reports/customer-report.json", "PROJECT_CONTENT"),
        ("vault/tasks/personal-tasks.json", "PROJECT_CONTENT"),
        (
            "vault/90-System/tasks/registry.json",
            "PROJECT_OPERATIONAL_METADATA",
        ),
    ],
)
def test_knowledge_root_requires_explicit_operational_system_boundary(
    tmp_path: Path, relative: str, expected: str
):
    _write(tmp_path / relative, '{"status":"active","tasks":[]}')

    candidate = _candidate(discover_project_evidence(tmp_path), relative)
    inspected = inspect_operational_evidence(tmp_path, relative, observed_at=T2)

    assert candidate["accessClass"] == expected
    assert candidate["operationalMetadataHint"] is (
        expected == "PROJECT_OPERATIONAL_METADATA"
    )
    assert inspected["safeToRead"] is (
        expected == "PROJECT_OPERATIONAL_METADATA"
    )


@pytest.mark.parametrize(
    ("relative", "payload"),
    [
        (
            "system/registry.json",
            '{"status":"active","api_token":"VERY_SECRET",'
            '"nested":{"private_key":"PRIVATE_SECRET"}}',
        ),
        (
            "system/registry.yaml",
            "status: active\napi_token: VERY_SECRET\nnested:\n  private_key: PRIVATE_SECRET\n",
        ),
        (
            "system/registry.toml",
            'status = "active"\napi_token = "VERY_SECRET"\n[nested]\nprivate_key = "PRIVATE_SECRET"\n',
        ),
        (
            "system/dashboard.md",
            "---\nstatus: active\napi_token: VERY_SECRET\n---\n"
            "# Status\nprivate_key: PRIVATE_SECRET\nhealth: good\n",
        ),
    ],
)
def test_structured_and_frontmatter_secrets_are_recursively_redacted(
    tmp_path: Path, relative: str, payload: str
):
    _write(tmp_path / relative, payload)

    result = inspect_operational_evidence(tmp_path, relative, observed_at=T2)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["safeToRead"] is True
    assert result["redactions"]
    assert "active" in rendered
    assert "***REDACTED***" in rendered
    assert "VERY_SECRET" not in rendered
    assert "PRIVATE_SECRET" not in rendered


def test_approval_registry_returns_ids_states_timestamps_not_body(tmp_path: Path):
    _write(
        tmp_path / "system" / "approvals" / "registry.json",
        json.dumps(
            {
                "approvals": [
                    {
                        "approvalId": "APR-001",
                        "state": "approved",
                        "approvedAt": T2,
                        "body": "private approval discussion",
                    },
                    {
                        "approvalId": "APR-002",
                        "state": "pending",
                        "updatedAt": T2,
                    },
                ]
            }
        ),
    )

    result = inspect_operational_evidence(
        tmp_path, "system/approvals/registry.json", observed_at=T2
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["safeToRead"] is True
    assert result["summary"]["idCount"] == 2
    assert result["summary"]["statusCounts"] == {
        "approved": 1,
        "pending": 1,
    }
    assert "/approvals/0/body" in result["omittedFields"]
    assert "private approval discussion" not in rendered


def test_generated_dashboard_uses_bounded_status_extraction(tmp_path: Path):
    payload = "status: degraded\nhealth: warning\n" + "private narrative\n" * 500
    _write(tmp_path / "90-System" / "dashboard.md", payload)

    result = inspect_operational_evidence(
        tmp_path,
        "90-System/dashboard.md",
        max_markdown_bytes=128,
        observed_at=T2,
    )

    assert result["safeToRead"] is True
    assert result["truncated"] is True
    assert result["contentHashScope"] == "bounded_prefix"
    assert result["facts"]["statusLines"][:2] == [
        {"key": "status", "value": "degraded"},
        {"key": "health", "value": "warning"},
    ]
    assert "private narrative" not in json.dumps(result, ensure_ascii=False)


def test_user_knowledge_yaml_and_external_paths_are_not_read(tmp_path: Path):
    _write(
        tmp_path / "knowledge" / "article-001.yaml",
        "title: private article\ncontent: user knowledge\n",
    )
    outside = tmp_path.parent / "external-private.yaml"
    _write(outside, "content: EXTERNAL_SECRET")

    article = inspect_operational_evidence(
        tmp_path, "knowledge/article-001.yaml"
    )
    external = inspect_operational_evidence(tmp_path, outside)

    assert article["accessClass"] == "PROJECT_CONTENT"
    assert article["safeToRead"] is False
    assert external["accessClass"] == "EXTERNAL_PRIVATE_DATA"
    assert external["safeToRead"] is False
    assert "EXTERNAL_SECRET" not in json.dumps(external)


def test_runtime_observation_reports_git_dependency_process_scheduler_and_file(
    tmp_path: Path,
):
    _write(tmp_path / "tracked.txt", "tracked")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Panorama Test",
            "-c",
            "user.email=panorama@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )

    result = observe_project_runtime(
        tmp_path,
        dependencies=["json", "definitely_unavailable_panorama_module"],
        process_names=["worker"],
        scheduler_names=["nightly-check"],
        check_paths=["tracked.txt", "missing.txt"],
        observed_at=T2,
        process_provider=lambda: {"worker"},
        scheduler_provider=lambda name: "registered",
    )

    assert result["git"]["isRepository"] is True
    assert len(result["git"]["head"]) == 40
    assert result["git"]["dirty"] is False
    assert result["dependencies"][0]["status"] == "available"
    assert result["dependencies"][1]["status"] == "unavailable"
    assert result["processes"] == [
        {"name": "worker", "status": "running", "provenance": "observed_now"}
    ]
    assert result["schedulers"][0]["status"] == "registered"
    assert [item["status"] for item in result["files"]] == [
        "present",
        "not_detected",
    ]
    assert result["safety"]["processArgumentsRead"] is False


def test_safe_active_test_is_current_observation_and_output_is_not_returned(
    tmp_path: Path,
):
    _write(tmp_path / "safe_check.py", "raise SystemExit(0)\n")
    _write(
        tmp_path / "observation.json",
        json.dumps(
            {
                "commands": [
                    {
                        "id": "unit",
                        "argv": [sys.executable, "safe_check.py"],
                        "workingDirectory": ".",
                        "timeoutSeconds": 30,
                        "safe": True,
                        "networkAccess": "none",
                        "writesProject": False,
                        "installsDependencies": False,
                    }
                ]
            }
        ),
    )
    _write(
        tmp_path / "reports" / "test-report.json",
        '{"status":"passed","result":"historical"}',
    )

    historical = inspect_operational_evidence(
        tmp_path, "reports/test-report.json", observed_at=T1
    )
    current = observe_project_runtime(
        tmp_path,
        test_manifest="observation.json",
        test_id="unit",
        authorize_test_execution=True,
        observed_at=T2,
    )["verification"][0]

    assert historical["provenance"] == "historical_test"
    assert current["provenance"] == "current_test"
    assert current["status"] == "passed"
    assert current["projectModified"] is False
    assert current["networkIsolation"] == "not_enforced"
    assert current["filesystemIsolation"] == "not_enforced"
    assert current["projectWriteCheck"] == "post_execution_metadata_check"
    assert "stdout" not in current and "stderr" not in current


def test_declared_test_requires_authorization_and_is_not_executed(
    tmp_path: Path,
):
    _write(
        tmp_path / "untrusted.py",
        "from pathlib import Path\nPath('executed').write_text('bad')\n",
    )
    _write(
        tmp_path / "observation.json",
        json.dumps(
            {
                "commands": [
                    {
                        "id": "declared-only",
                        "argv": [sys.executable, "untrusted.py"],
                        "workingDirectory": ".",
                        "timeoutSeconds": 30,
                        "safe": True,
                        "networkAccess": "none",
                        "writesProject": False,
                        "installsDependencies": False,
                    }
                ]
            }
        ),
    )

    result = observe_project_runtime(
        tmp_path,
        test_manifest="observation.json",
        test_id="declared-only",
        observed_at=T2,
    )
    observation = result["verification"][0]

    assert observation["status"] == "requires_explicit_authorization"
    assert observation["provenance"] == "not_observed"
    assert observation["authorizationGranted"] is False
    assert not (tmp_path / "executed").exists()
    assert "networkUsed" not in result["safety"]
    assert "secretValuesRead" not in result["safety"]
    assert result["safety"]["networkIsolation"] == "not_enforced"
    assert result["safety"]["filesystemIsolation"] == "not_enforced"


def test_source_snapshot_ignores_panorama_owned_artifacts_but_detects_project_drift(
    tmp_path: Path,
):
    _write(tmp_path / ".gitignore", "ignored.local.html\n")
    _write(tmp_path / "tracked.txt", "tracked")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Panorama Test",
            "-c",
            "user.email=panorama@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    artifacts = (
        tmp_path / ".panorama-work" / "prepared-init-review.json",
        tmp_path / ".preview.panorama.lock",
        tmp_path / "project.backup-20260810.html",
        tmp_path / "ignored.local.html",
    )
    for index, artifact in enumerate(artifacts):
        _write(artifact, f"artifact-{index}")

    before = source_snapshot(tmp_path)
    for index, artifact in enumerate(artifacts):
        _write(artifact, f"changed-artifact-{index}")
    after_artifacts = source_snapshot(tmp_path)
    _write(tmp_path / "new-project-source.txt", "project change")
    after_project_change = source_snapshot(tmp_path)

    assert before == after_artifacts
    assert after_project_change != before


def test_costly_or_mutating_test_command_is_refused(tmp_path: Path):
    _write(
        tmp_path / "observation.json",
        json.dumps(
            {
                "commands": [
                    {
                        "id": "unsafe",
                        "argv": [sys.executable, "-m", "pip", "install", "package"],
                        "workingDirectory": ".",
                        "timeoutSeconds": 30,
                        "safe": True,
                        "networkAccess": "none",
                        "writesProject": False,
                        "installsDependencies": False,
                    }
                ]
            }
        ),
    )

    with pytest.raises(ObservationError, match="install/build/network"):
        observe_project_runtime(
            tmp_path, test_manifest="observation.json", test_id="unsafe"
        )


def _module(reference_data: dict, module_id: str) -> dict:
    module = deepcopy(reference_data["architecture"]["modules"][0])
    module.update(
        {
            "id": module_id,
            "name": module_id,
            "layerId": "LAYER-UNASSIGNED",
            "architectureScope": "both",
            "targetLayerId": None,
            "requirementIds": [],
            "codePath": f"src/{module_id.lower()}/",
            "decisionIds": [],
            "riskIds": [],
            "acceptanceCriteriaIds": [],
            "gateIds": [],
            "workItemIds": [],
            "reviewIds": [],
            "createdAt": T1,
            "updatedAt": T1,
        }
    )
    module["source"].update({"baselineId": None, "changedAreas": []})
    module["currentDesign"]["referenceIds"] = []
    module["status"] = {
        "designMaturity": "confirmed",
        "implementationMaturity": "stable",
        "verificationStatus": "pending",
        "runtimeStatus": "not_deployed",
    }
    return module


def _draft_model(reference_data: dict) -> dict:
    data = build_minimal_project(
        "PRJ-GENERIC",
        "Generic Project",
        "Model a generic project",
        "Reconcile verification and transition evidence",
        [],
    )
    data["project"]["lifecycle"] = "active"
    modules = [_module(reference_data, "MOD-ALPHA"), _module(reference_data, "MOD-BETA")]
    data["architecture"]["modules"] = modules
    target = deepcopy(data["architecture"]["versions"][0])
    target.update(
        {
            "id": "ARCH-V01",
            "label": "Approved target",
            "status": "accepted",
            "acceptedAt": T1,
            "summary": "Approved target for the generic project.",
            "reviewId": None,
        }
    )
    target["delta"]["modifiedModuleIds"] = ["MOD-ALPHA", "MOD-BETA"]
    data["architecture"]["versions"].append(target)
    data["architecture"]["targetVersionId"] = "ARCH-V01"
    data["architecture"]["transitions"] = [
        {
            "id": "TRANS-ALPHA",
            "subjectType": "module",
            "subjectId": "MOD-ALPHA",
            "fromVersionId": "ARCH-V00",
            "toVersionId": "ARCH-V01",
            "state": "blocked",
            "summary": "Blocked generic transition.",
            "reason": "Awaiting a verified transition prerequisite.",
            "workItemIds": [],
            "decisionIds": [],
            "riskIds": [],
            "startedAt": T1,
            "targetDate": None,
            "completedAt": None,
            "extensions": {},
        }
    ]
    release = deepcopy(reference_data["releases"][0])
    release.update(
        {
            "id": "REL-001",
            "version": "0.1-candidate",
            "name": "Candidate",
            "status": "candidate",
            "architectureVersionId": "ARCH-V01",
            "stageId": "STG-INIT",
            "moduleIds": ["MOD-ALPHA", "MOD-BETA"],
            "createdAt": T1,
            "releasedAt": None,
            "deploymentIds": [],
            "referenceIds": [],
        }
    )
    data["releases"] = [release]
    data["project"]["currentReleaseId"] = None
    guidance = deepcopy(data["guidance"])
    guidance["generatedAt"] = T1
    guidance["status"] = "proposed"
    preview = {
        "generatedAt": T1,
        "sourceSnapshot": deepcopy(SOURCE_SNAPSHOT),
        "panoramaData": data,
        "reviewSubjectRefs": [
            {"type": "project", "id": "PRJ-GENERIC"},
            {"type": "intent", "id": "PRJ-GENERIC"},
            {"type": "architecture_version", "id": "ARCH-V01"},
            {"type": "module", "id": "MOD-ALPHA"},
            {"type": "module", "id": "MOD-BETA"},
            {"type": "release", "id": "REL-001"},
        ],
        "changeIntents": [
            {
                "category": "architecture",
                "impactLevel": "project",
                "summary": "Initial architecture model established",
                "reason": "Materialize the approved project model.",
                "source": "mixed",
                "entityRefs": [
                    {"type": "architecture_version", "id": "ARCH-V01"}
                ],
                "beforeSummary": "No Managed Panorama architecture.",
                "afterSummary": "Current and target architecture captured.",
                "significant": True,
                "architectureVersionId": "ARCH-V01",
                "referenceIds": [],
            },
            {
                "category": "module",
                "impactLevel": "architecture",
                "summary": "Module responsibility map established",
                "reason": "Materialize approved module boundaries.",
                "source": "mixed",
                "entityRefs": [
                    {"type": "module", "id": "MOD-ALPHA"},
                    {"type": "module", "id": "MOD-BETA"},
                ],
                "beforeSummary": "No Managed Panorama module map.",
                "afterSummary": "Two approved module responsibilities captured.",
                "significant": True,
                "architectureVersionId": "ARCH-V01",
                "referenceIds": [],
            },
            {
                "category": "verification",
                "impactLevel": "project",
                "summary": "Initial verification and transition gaps registered",
                "reason": "Expose approved unresolved operational concerns.",
                "source": "mixed",
                "entityRefs": [
                    {"type": "module", "id": "MOD-ALPHA"},
                    {"type": "module", "id": "MOD-BETA"},
                    {"type": "transition", "id": "TRANS-ALPHA"},
                ],
                "beforeSummary": "No CONTROL attention surface.",
                "afterSummary": "Verification mapping and transition blocker visible.",
                "significant": True,
                "architectureVersionId": "ARCH-V01",
                "referenceIds": [],
            },
        ],
        "evidenceInventory": [
            {
                "path": "tests/alpha-result.json",
                "accessClass": "PUBLIC_PROJECT_EVIDENCE",
                "factClass": "VERIFICATION",
                "provenance": "historical_test",
                "relatedEntities": [{"type": "module", "id": "MOD-ALPHA"}],
                "contentHash": "sha256:" + "1" * 64,
                "observedAt": T1,
            },
            {
                "path": "reports/beta-result.json",
                "accessClass": "PROJECT_OPERATIONAL_METADATA",
                "factClass": "VERIFICATION",
                "provenance": "generated_current_state",
                "relatedEntities": [{"type": "module", "id": "MOD-BETA"}],
                "contentHash": "sha256:" + "2" * 64,
                "observedAt": T1,
            },
        ],
        "guidance": guidance,
        "updateSummaryItems": [
            "Approved Managed Panorama Initialization",
            "Current and target architecture captured",
            "Initial operational gaps reconciled",
        ],
        "changeLevel": "project",
        "requestedBy": "ai",
        "reviewSummary": "Approved Managed Panorama Initialization",
        "reviewDecisionIds": [],
    }
    return {
        "draftVersion": "draft-init-model.v0.1",
        "preview": preview,
    }


def _prepared_approval(
    reference_data: dict, schema_path: Path, base_dir: Path
) -> tuple[dict, dict]:
    prepared = prepare_init_review(
        _draft_model(reference_data),
        schema_path,
        base_dir=base_dir,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        prepared_at=TP,
    )
    approval = record_init_approval(
        prepared,
        approved_hash=prepared["previewHash"],
        approved_by="project-owner",
        recorded_at=T2,
    )
    return prepared, approval


def test_case_a_prepare_prevalidates_then_freezes_exact_preview(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    draft = _draft_model(reference_data)

    prepared = prepare_init_review(
        draft,
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        prepared_at=TP,
    )
    markdown = render_prepared_preview(
        prepared, artifact_name="prepared-init-review.json"
    )

    assert prepared["preparedVersion"] == PREPARED_VERSION
    assert prepared["approvalState"] == "awaiting_user_approval"
    assert prepared["preview"] == draft["preview"]
    assert prepared["previewHash"] == compute_preview_hash(draft["preview"])
    assert prepared["validation"]["valid"] is True
    assert prepared["validation"]["counts"]["errors"] == 0
    assert "## Current Architecture" in markdown
    assert "## Verification / Runtime" in markdown
    assert f"Preview SHA-256: {prepared['previewHash']}" in markdown
    assert "Status: Awaiting explicit hash approval" in markdown


def test_case_b_prepare_rejects_schema_invalid_draft_without_output(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    draft = _draft_model(reference_data)
    draft["preview"]["panoramaData"]["project"]["id"] = "invalid id"
    output = tmp_path / "prepared-init-review.json"

    with pytest.raises(InitMaterializationError, match="prevalidation failed"):
        prepare_init_review(
            draft,
            schema_path,
            base_dir=tmp_path,
            current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
            prepared_at=TP,
        )

    assert not output.exists()


def test_case_c_candidate_release_as_current_fails_before_approval(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    draft = _draft_model(reference_data)
    draft["preview"]["panoramaData"]["project"]["currentReleaseId"] = "REL-001"

    with pytest.raises(
        InitMaterializationError,
        match="candidate/planned release cannot be currentReleaseId",
    ):
        prepare_init_review(
            draft,
            schema_path,
            base_dir=tmp_path,
            current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
            prepared_at=TP,
        )

    prepared = prepare_init_review(
        _draft_model(reference_data),
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        prepared_at=TP,
    )
    prepared["preview"]["panoramaData"]["project"]["currentReleaseId"] = "REL-001"
    prepared["previewHash"] = compute_preview_hash(prepared["preview"])
    approval = record_init_approval(
        prepared,
        approved_hash=prepared["previewHash"],
        approved_by="project-owner",
        recorded_at=T2,
    )
    with pytest.raises(
        InitMaterializationError,
        match="candidate/planned release cannot be currentReleaseId",
    ):
        materialize_prepared_init(
            prepared,
            approval,
            schema_path,
            base_dir=tmp_path,
            current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        )


def test_case_d_and_e_explicit_hash_records_only_exact_prepared_hash(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    prepared = prepare_init_review(
        _draft_model(reference_data),
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        prepared_at=TP,
    )

    approval = record_init_approval(
        prepared,
        approved_hash=prepared["previewHash"],
        approved_by="project-owner",
        recorded_at=T2,
    )

    assert approval == {
        "approvalVersion": "init-approval.v0.1",
        "status": "approved",
        "previewHash": prepared["previewHash"],
        "approvedBy": "project-owner",
        "approvalRecordedAt": T2,
        "approvalMethod": "explicit_hash_confirmation",
        "approvalTimeSource": "approval_recorder_clock",
    }
    with pytest.raises(InitMaterializationError, match="does not match"):
        record_init_approval(
            prepared,
            approved_hash="0" * 64,
            approved_by="project-owner",
            recorded_at=T2,
        )


def test_case_f_materializer_cli_requires_separate_approval_without_output(
    tmp_path: Path,
):
    output = tmp_path / "panorama.json"
    with pytest.raises(SystemExit) as exit_info:
        materialize_main(
            [
                str(tmp_path / "prepared.json"),
                "--project-root",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
    assert exit_info.value.code == 2
    assert not output.exists()


def test_case_g_post_approval_preview_mutation_is_rejected_even_if_rehashed(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    prepared, approval = _prepared_approval(reference_data, schema_path, tmp_path)
    mutated = deepcopy(prepared)
    mutated["preview"]["reviewSummary"] = "post-approval mutation"
    mutated["previewHash"] = compute_preview_hash(mutated["preview"])

    with pytest.raises(InitMaterializationError, match="hash binding"):
        materialize_prepared_init(
            mutated,
            approval,
            schema_path,
            base_dir=tmp_path,
            current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        )


def test_case_h_approval_artifact_is_write_once(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    prepared = prepare_init_review(
        _draft_model(reference_data),
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        prepared_at=TP,
    )
    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    _write(prepared_path, json.dumps(prepared, ensure_ascii=False))
    argv = [
        str(prepared_path),
        "--approved-hash",
        prepared["previewHash"],
        "--approved-by",
        "project-owner",
        "--output",
        str(approval_path),
    ]

    assert record_approval_main(argv) == 0
    first = approval_path.read_bytes()
    assert record_approval_main(argv) == 2
    assert approval_path.read_bytes() == first


def test_case_k_materialization_preserves_approved_semantic_projection(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    prepared, approval = _prepared_approval(reference_data, schema_path, tmp_path)
    approved_data = deepcopy(prepared["preview"]["panoramaData"])
    approved_data["guidance"] = deepcopy(prepared["preview"]["guidance"])

    result = materialize_prepared_init(
        prepared,
        approval,
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
    )

    assert semantic_projection(result["data"]) == semantic_projection(approved_data)


def test_case_i_materialization_uses_recorder_time_for_review_and_bindings(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    prepared, approval = _prepared_approval(reference_data, schema_path, tmp_path)

    first = materialize_prepared_init(
        prepared,
        approval,
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
    )
    second = materialize_prepared_init(
        prepared,
        approval,
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
    )
    data = first["data"]
    batch = data["updateBatches"][0]

    assert first["data"] == second["data"]
    assert len(data["reviews"]) == 1
    assert data["reviews"][0]["status"] == "approved"
    assert data["reviews"][0]["requestedAt"] == T1
    assert data["reviews"][0]["reviewedAt"] == T2
    assert data["reviews"][0]["reviewedBy"] == "project-owner"
    assert len(data["changes"]) == 3
    assert batch["id"].startswith("UPD-INIT-")
    assert batch["status"] == "applied"
    assert data["meta"]["latestUpdateBatchId"] == batch["id"]
    assert data["guidance"] == prepared["preview"]["guidance"]
    assert data["project"]["currentReleaseId"] is None
    assert "candidateReleaseNormalizedFrom" not in batch["extensions"]
    binding = data["reviews"][0]["extensions"]["approvalBinding"]
    assert binding["approvedHash"] == prepared["previewHash"]
    assert binding["approvalRecordedAt"] == T2
    assert binding["approvalMethod"] == "explicit_hash_confirmation"
    assert binding["approvalTimeSource"] == "approval_recorder_clock"
    assert first["materialization"]["formalHighCriticalCount"] == 3
    assert len(batch["attentionItems"]) == 2
    assert any(
        item["title"] == "验证证据映射不完整"
        and {entity["id"] for entity in item["relatedEntities"]}
        >= {"MOD-ALPHA", "MOD-BETA"}
        for item in batch["attentionItems"]
    )
    assert any(
        item["title"] == "架构迁移阻塞"
        for item in batch["attentionItems"]
    )
    assert validate_data(data, schema_path, base_dir=tmp_path).errors == []
    final_report = validate_data(data, schema_path, base_dir=tmp_path)
    expected = reconcile_findings(
        final_report.issues,
        data["extensions"]["initMaterialization"]["evidenceInventory"],
        data,
    )
    assert batch["extensions"]["findingReconciliation"] == expected[
        "classifiedFindings"
    ]
    assert batch["attentionItems"] == expected["attentionItems"]


def test_case_l_materialization_stabilizes_findings_created_after_reconciliation(
    tmp_path: Path,
    schema_path: Path,
    reference_data: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    original_validate = materialize_module.validate_data

    def staged_validate(data, schema, *, base_dir):
        report = original_validate(data, schema, base_dir=base_dir)
        batches = data.get("updateBatches", [])
        if batches and batches[0].get("extensions", {}).get("findingReconciliation"):
            report.issues.append(
                ValidationIssue(
                    "WARNING",
                    "RESOURCE_RISK",
                    "Reconciliation materialization exposed a resource risk.",
                    severity="high",
                    related_entities=(
                        {"type": "project", "id": data["project"]["id"]},
                    ),
                )
            )
        return report

    monkeypatch.setattr(materialize_module, "validate_data", staged_validate)
    prepared, approval = _prepared_approval(reference_data, schema_path, tmp_path)
    result = materialize_prepared_init(
        prepared,
        approval,
        schema_path,
        base_dir=tmp_path,
        current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
    )
    batch = result["data"]["updateBatches"][0]

    assert any(
        item["code"] == "RESOURCE_RISK"
        for item in batch["extensions"]["findingReconciliation"]
    )
    assert any(
        entity["id"] == result["data"]["project"]["id"]
        for attention in batch["attentionItems"]
        for entity in attention["relatedEntities"]
    )


def test_finding_classification_distinguishes_project_evidence_and_blocker(
    reference_data: dict,
):
    model = _draft_model(reference_data)
    data = model["preview"]["panoramaData"]
    verification = ValidationIssue(
        "WARNING",
        "VERIFICATION_GAP",
        "Module verification is incomplete.",
        severity="high",
        related_entities=({"type": "module", "id": "MOD-ALPHA"},),
    )
    transition = ValidationIssue(
        "WARNING",
        "TRANSITION_RISK",
        "Transition is blocked.",
        severity="high",
        related_entities=({"type": "transition", "id": "TRANS-ALPHA"},),
    )

    without_evidence = classify_finding(verification, [], data)
    with_evidence = classify_finding(
        verification, model["preview"]["evidenceInventory"], data
    )
    blocker = classify_finding(transition, [], data)

    assert without_evidence["classification"] == "PROJECT_GAP"
    assert with_evidence["classification"] == "PANORAMA_EVIDENCE_GAP"
    assert blocker["classification"] == "CONTROL_BLOCKER"


def test_case_j_materialization_rejects_hash_approval_and_source_snapshot_drift(
    tmp_path: Path, schema_path: Path, reference_data: dict
):
    prepared, approval = _prepared_approval(reference_data, schema_path, tmp_path)
    mutated = deepcopy(prepared)
    mutated["preview"]["reviewSummary"] = "changed after approval"
    with pytest.raises(InitMaterializationError, match="hash binding"):
        materialize_prepared_init(
            mutated,
            approval,
            schema_path,
            base_dir=tmp_path,
            current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        )

    wrong_approval = deepcopy(approval)
    wrong_approval["previewHash"] = "0" * 64
    with pytest.raises(InitMaterializationError, match="hash binding"):
        materialize_prepared_init(
            prepared,
            wrong_approval,
            schema_path,
            base_dir=tmp_path,
            current_source_snapshot=deepcopy(SOURCE_SNAPSHOT),
        )

    changed_snapshot = deepcopy(SOURCE_SNAPSHOT)
    changed_snapshot["gitHead"] = "c" * 40
    with pytest.raises(InitMaterializationError, match="source snapshot changed"):
        materialize_prepared_init(
            prepared,
            approval,
            schema_path,
            base_dir=tmp_path,
            current_source_snapshot=changed_snapshot,
        )


def test_skill_exposes_v014_evidence_and_materialization_contracts(
    project_root: Path,
):
    skill = (project_root / "SKILL.md").read_text(encoding="utf-8")
    for phrase in (
        "V0.1.4",
        "PROJECT_OPERATIONAL_METADATA",
        "PROJECT_CONTENT",
        "EXTERNAL_PRIVATE_DATA",
        "inspect_operational_evidence.py",
        "observe_project_runtime.py",
        "init-materialization-contract.md",
        "prepare_init_review.py",
        "record_init_approval.py",
        "prepared-init-review.v0.1",
        "init-approval.v0.1",
        "explicit_hash_confirmation",
        "approval_recorder_clock",
        "materialize_init.py",
        "PANORAMA_EVIDENCE_GAP",
        "CONTROL_BLOCKER",
    ):
        assert phrase in skill


def test_v014_production_logic_has_no_case_specific_shortcuts(project_root: Path):
    production_files = (
        project_root / "SKILL.md",
        project_root / "docs" / "semantic-modeling-protocol.md",
        project_root / "scripts" / "discover_project_evidence.py",
        project_root / "scripts" / "inspect_operational_evidence.py",
        project_root / "scripts" / "observe_project_runtime.py",
        project_root / "scripts" / "prepare_init_review.py",
        project_root / "scripts" / "record_init_approval.py",
        project_root / "scripts" / "materialize_init.py",
    )
    forbidden = (
        "ai-wiki",
        "q5",
        "q7",
        "task-020",
        "lancedb",
        "ticktick",
        "obsidian",
        "zotero",
        "portal stage b",
    )
    for path in production_files:
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"case-specific token {token!r} found in {path}"
