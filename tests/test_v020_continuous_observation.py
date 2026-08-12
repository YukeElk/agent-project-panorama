from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

from continuous_observation import ObservationError, reconcile_html, validate_fact_operations
from manage_git_hook import HOOKS, MARKER, install, uninstall
from panorama_hook_dispatch import build_worker_argv, dispatch_event
from migrate_v01_to_v02 import migrate_data
from observation_policy import policy_hash
from panorama_io import compute_data_hash, compute_presentation_hash, extract_data, replace_data
from standard_pack import StandardPackError, assess_standard_pack, load_document, validate_standard_pack
from validate_panorama import validate_data


NOW = "2026-08-12T00:00:00Z"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace"
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "panorama@example.test")
    _git(root, "config", "user.name", "Panorama Test")
    (root / "README.md").write_text("# Test\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


@pytest.fixture
def v02_data(reference_data: dict) -> dict:
    return migrate_data(reference_data, migrated_at=NOW)


def test_v02_schema_is_valid(project_root: Path):
    schema = json.loads((project_root / "schema" / "panorama.schema.v0.2.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_migration_preserves_governed_semantics(reference_data: dict, v02_data: dict):
    for key in ("intent", "requirements", "decisions", "reviews", "guidance"):
        assert v02_data[key] == reference_data[key]
    assert v02_data["architecture"]["targetVersionId"] == reference_data["architecture"]["targetVersionId"]
    assert v02_data["schemaVersion"] == "0.2"
    assert v02_data["observationPolicy"]["policyHash"] == policy_hash(v02_data["observationPolicy"])


def test_v02_reference_validates(project_root: Path, v02_data: dict, tmp_path: Path):
    report = validate_data(v02_data, base_dir=tmp_path)
    assert report.errors == []


def test_reference_v02_html_matches_json_and_template(project_root: Path):
    data = json.loads((project_root / "examples" / "reference-project.v0.2.json").read_text(encoding="utf-8"))
    html = project_root / "examples" / "reference-project.v0.2.html"
    template = project_root / "templates" / "panorama.html"
    assert extract_data(html) == data
    assert compute_presentation_hash(html.read_text(encoding="utf-8")) == compute_presentation_hash(
        template.read_text(encoding="utf-8")
    )


def test_standard_pack_validates_and_assesses(project_root: Path, v02_data: dict):
    pack = load_document(project_root / "examples" / "standard-pack.example.yaml")
    assert validate_standard_pack(pack) == []
    result = assess_standard_pack(pack, project_root, v02_data, source_commit="abc", assessed_at=NOW)
    assert {item["status"] for item in result["results"]} == {"pass"}
    assert len(result["standardPackHash"]) == 64


def test_standard_pack_rejects_executable_evaluator(project_root: Path):
    pack = load_document(project_root / "examples" / "standard-pack.example.yaml")
    pack["rules"][0]["evaluator"] = {"type": "shell", "command": "rm -rf ."}
    assert validate_standard_pack(pack)


def test_standard_pack_rejects_path_escape(project_root: Path, v02_data: dict):
    pack = load_document(project_root / "examples" / "standard-pack.example.yaml")
    pack["rules"][0]["evaluator"] = {"type": "path_exists", "path": "../secret"}
    with pytest.raises(StandardPackError):
        assess_standard_pack(pack, project_root, v02_data, source_commit="abc", assessed_at=NOW)


@pytest.mark.parametrize(
    "path",
    [
        "/intent/objective",
        "/architecture/targetVersionId",
        "/decisions/0/status",
        "/reviews/0/status",
        "/guidance/recommendedOptionId",
        "/resources/0/access/credentials/mode",
    ],
)
def test_policy_rejects_governed_or_sensitive_paths(v02_data: dict, path: str):
    with pytest.raises(ObservationError):
        validate_fact_operations(
            [{"op": "replace", "path": path, "value": "forbidden"}],
            v02_data["observationPolicy"],
        )


def test_continuous_observation_updates_current_facts_without_governance(
    project_root: Path, template_path: Path, v02_data: dict, tmp_path: Path
):
    root = _repo(tmp_path)
    module = v02_data["architecture"]["modules"][0]
    module["codePath"] = "src"
    panorama = root / "project-panorama.local.html"
    replace_data(template_path, v02_data, panorama)
    before_presentation = compute_presentation_hash(panorama.read_text(encoding="utf-8"))
    before_governed = copy.deepcopy(
        {key: v02_data[key] for key in ("intent", "requirements", "decisions", "reviews", "guidance")}
    )
    changed, backup, updated = reconcile_html(
        panorama, root, standard_paths=[], observed_at="2026-08-12T01:00:00Z"
    )
    assert changed is True
    assert backup and backup.exists()
    assert updated["sourceBinding"]["gitHead"] == _git(root, "rev-parse", "HEAD")
    assert updated["architecture"]["modules"][0]["extensions"]["continuousObservation"]["pathStatus"] == "present"
    assert {key: updated[key] for key in before_governed} == before_governed
    assert compute_presentation_hash(panorama.read_text(encoding="utf-8")) == before_presentation
    assert validate_data(updated, base_dir=root).errors == []


def test_continuous_observation_is_idempotent(
    template_path: Path, v02_data: dict, tmp_path: Path
):
    root = _repo(tmp_path)
    panorama = root / "project-panorama.local.html"
    replace_data(template_path, v02_data, panorama)
    reconcile_html(panorama, root, standard_paths=[], observed_at="2026-08-12T01:00:00Z")
    first = extract_data(panorama)
    changed, backup, second = reconcile_html(
        panorama, root, standard_paths=[], observed_at="2026-08-12T02:00:00Z"
    )
    assert changed is False
    assert backup is None
    assert compute_data_hash(second) == compute_data_hash(first)


def test_removing_standard_pack_triggers_reconciliation(
    project_root: Path, template_path: Path, v02_data: dict, tmp_path: Path
):
    root = _repo(tmp_path)
    panorama = root / "project-panorama.local.html"
    replace_data(template_path, v02_data, panorama)
    standard = project_root / "examples" / "standard-pack.example.yaml"
    reconcile_html(
        panorama, root, standard_paths=[standard], observed_at="2026-08-12T01:00:00Z"
    )
    with_standard = extract_data(panorama)
    assert with_standard["standardAssessments"]
    assert any(
        risk.get("extensions", {}).get("standardAssessmentId")
        for risk in with_standard["risks"]
    )
    changed, _, updated = reconcile_html(
        panorama, root, standard_paths=[], observed_at="2026-08-12T02:00:00Z"
    )
    assert changed is True
    assert updated["standardAssessments"] == with_standard["standardAssessments"]
    assert updated["sourceBinding"]["extensions"]["standardPackHashes"] == []
    assert not any(
        risk.get("extensions", {}).get("standardAssessmentId")
        for risk in updated["risks"]
    )


def test_compensation_sync_catches_later_commit(
    template_path: Path, v02_data: dict, tmp_path: Path
):
    root = _repo(tmp_path)
    panorama = root / "project-panorama.local.html"
    replace_data(template_path, v02_data, panorama)
    reconcile_html(panorama, root, standard_paths=[], observed_at="2026-08-12T01:00:00Z")
    first_head = extract_data(panorama)["sourceBinding"]["gitHead"]
    (root / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "change")
    changed, _, updated = reconcile_html(
        panorama, root, standard_paths=[], observed_at="2026-08-12T02:00:00Z"
    )
    assert changed is True
    assert updated["sourceBinding"]["gitHead"] != first_head


def test_git_hook_install_refuses_external_hook_and_uninstalls_managed(tmp_path: Path):
    root = _repo(tmp_path)
    panorama = root / "project-panorama.local.html"
    panorama.write_text("placeholder", encoding="utf-8")
    paths = install(root, panorama)
    assert len(paths) == len(HOOKS)
    assert all(MARKER in path.read_text(encoding="utf-8") for path in paths)
    config = json.loads((root / ".panorama-work" / "continuous-observation.json").read_text(encoding="utf-8"))
    assert config["panorama"] == "project-panorama.local.html"
    argv, log_path = build_worker_argv(root)
    assert "continuous_observation.py" in " ".join(argv)
    assert str(panorama) in argv
    event = dispatch_event(root, "post-commit", observed_at=NOW)
    assert json.loads(event.read_text(encoding="utf-8"))["status"] == "pending"
    assert log_path.name == "continuous-observation.log"
    removed = uninstall(root)
    assert set(removed) == set(paths)
    hooks = Path(_git(root, "rev-parse", "--git-common-dir"))
    if not hooks.is_absolute():
        hooks = (root / hooks).resolve()
    hooks = hooks / "hooks"
    external = hooks / "post-commit"
    external.write_text("#!/bin/sh\necho external\n", encoding="utf-8")
    with pytest.raises(Exception):
        install(root, panorama)
