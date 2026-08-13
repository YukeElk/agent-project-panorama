from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

import studio_agent

from studio_agent import (
    StudioAgentError,
    build_review_bundle,
    codex_capability,
    locate_codex_cli,
    review_with_codex,
)


def _session(reference_data: dict) -> dict:
    module = reference_data["architecture"]["modules"][0]
    return {
        "projectBinding": {
            "projectId": reference_data["project"]["id"],
            "baseRevision": reference_data["meta"]["revision"],
        },
        "scope": {"level": "project"},
        "candidate": module,
    }


def test_review_bundle_excludes_resources_and_credentials(reference_data: dict):
    session = _session(reference_data)
    candidate = {
        "candidateId": "CANDIDATE-1",
        "nodes": [{"nodeId": "NODE-1", "name": "Agent"}],
        "edges": [],
    }
    bundle = build_review_bundle(
        reference_data, session, candidate, {"valid": True, "issues": []}
    )
    rendered = json.dumps(bundle, ensure_ascii=False)

    assert "resources" not in bundle["formalPanorama"]
    assert "credentials" not in bundle["formalPanorama"]
    assert bundle["privacyBoundary"]["projectContentIncluded"] is False
    assert bundle["privacyBoundary"]["sourceFileContentsIncluded"] is False
    assert bundle["privacyBoundary"]["hostIsolation"] == {
        "codexSandbox": "read_only",
        "filesystemIsolation": "not_enforced",
        "networkIsolation": "not_enforced",
        "processTreeIsolation": "not_enforced",
    }
    assert "sourceSnapshot" not in (bundle.get("projectObservation") or {})
    assert "sourceSnapshotSummary" in (bundle.get("projectObservation") or {}) or bundle.get("projectObservation") is None
    for resource in reference_data.get("resources", []):
        access = resource.get("access", {})
        value = access.get("value")
        if isinstance(value, str) and value:
            assert value not in rendered
    assert "codePath" not in rendered or all(
        not Path(path).is_absolute()
        for path in [item.get("codePath", "") for item in bundle["formalPanorama"]["architecture"]["modules"]]
    )


def test_missing_codex_cli_is_an_explicit_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing = tmp_path / "missing-codex"
    discovered = tmp_path / "codex-on-path.exe"
    discovered.write_bytes(b"stub")
    monkeypatch.setenv("PANORAMA_CODEX_CLI", str(discovered))
    monkeypatch.setattr(studio_agent.shutil, "which", lambda _name: str(discovered))
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(studio_agent.subprocess, "run", fake_run)

    # A bad explicit setting is never concealed by env/PATH discovery.
    assert locate_codex_cli(missing) is None
    capability = codex_capability(missing)
    assert capability["available"] is False
    assert set(capability) == {"available", "authenticated", "version"}
    strict_capability = codex_capability(strict=True)
    assert strict_capability["available"] is False
    assert calls == []


def test_standalone_codex_cli_can_discover_path_when_not_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    discovered = (tmp_path / "codex-on-path.exe").resolve()
    discovered.write_bytes(b"stub")
    monkeypatch.delenv("PANORAMA_CODEX_CLI", raising=False)
    monkeypatch.setattr(studio_agent.shutil, "which", lambda _name: str(discovered))

    def fake_run(command, **_kwargs):
        assert command[0] == str(discovered)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(studio_agent.subprocess, "run", fake_run)

    assert locate_codex_cli() == discovered


def test_explicit_codex_cli_probe_and_review_command_use_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference_data: dict
):
    executable = (tmp_path / "configured-codex.exe").resolve()
    executable.write_bytes(b"stub")
    observed_commands: list[list[str]] = []
    candidate = {
        "candidateId": "CANDIDATE-1",
        "nodes": [{"nodeId": "NODE-1", "name": "Agent"}],
        "edges": [],
    }
    bundle = build_review_bundle(
        reference_data, _session(reference_data), candidate, {"valid": True, "issues": []}
    )
    model_review = {
        "reviewVersion": "panorama-architecture-agent-review.v0.1",
        "verdict": "ready",
        "summary": "Strict executable binding.",
        "strengths": [],
        "findings": [],
        "recommendations": [],
        "assumptionsToVerify": [],
        "unknowns": [],
    }

    def fake_run(command, **_kwargs):
        observed_commands.append(command)
        if "--output-last-message" in command:
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(json.dumps(model_review), encoding="utf-8")

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(studio_agent.subprocess, "run", fake_run)

    located = locate_codex_cli(executable, allow_discovery=False)
    assert located == executable
    review_with_codex(
        bundle,
        codex_cli=executable,
        strict=True,
    )

    assert observed_commands
    assert all(command[0] == str(executable) for command in observed_commands)


def test_review_bundle_has_size_guard(reference_data: dict):
    session = _session(reference_data)
    candidate = {
        "candidateId": "CANDIDATE-1",
        "nodes": [{"nodeId": "NODE-1", "name": "x" * 2_100_000}],
        "edges": [],
    }
    with pytest.raises(StudioAgentError, match="2 MB"):
        build_review_bundle(
            reference_data, session, candidate, {"valid": True, "issues": []}
        )


def test_review_result_is_schema_valid_inside_advisory_envelope(
    reference_data: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"stub")
    candidate = {
        "candidateId": "CANDIDATE-1",
        "nodes": [{"nodeId": "NODE-1", "name": "Agent"}],
        "edges": [],
    }
    bundle = build_review_bundle(
        reference_data, _session(reference_data), candidate, {"valid": True, "issues": []}
    )
    model_review = {
        "reviewVersion": "panorama-architecture-agent-review.v0.1",
        "verdict": "changes_requested",
        "summary": "Advisory review only.",
        "strengths": [],
        "findings": [],
        "recommendations": [],
        "assumptionsToVerify": [],
        "unknowns": [],
    }
    observed: dict = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        output_flag = command.index("--output-last-message")
        Path(command[output_flag + 1]).write_text(
            json.dumps(model_review), encoding="utf-8"
        )

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(
        studio_agent,
        "locate_codex_cli",
        lambda _explicit=None, **_kwargs: executable,
    )
    monkeypatch.setattr(studio_agent.subprocess, "run", fake_run)

    envelope = review_with_codex(bundle, codex_cli=executable, work_root=tmp_path)

    assert envelope["format"] == studio_agent.REVIEW_ENVELOPE_FORMAT
    assert envelope["inputHash"] == studio_agent.compute_canonical_hash(bundle)
    assert envelope["advisory"] is True
    assert envelope["review"] == model_review
    assert envelope["adapter"]["canApprove"] is False
    assert envelope["adapter"]["canApply"] is False
    assert envelope["adapter"]["mode"] == "bounded_bundle_read_only"
    assert envelope["adapter"]["runDirectory"] == "system_temporary_directory"
    assert envelope["adapter"]["sandbox"] == "read_only"
    assert envelope["adapter"]["filesystemIsolation"] == "not_enforced"
    assert envelope["adapter"]["networkIsolation"] == "not_enforced"
    assert envelope["adapter"]["processTreeIsolation"] == "not_enforced"
    assert observed["stdout"] is studio_agent.subprocess.DEVNULL
    assert observed["stderr"] is studio_agent.subprocess.DEVNULL
    assert "capture_output" not in observed


def test_review_ignores_project_work_root_and_uses_clean_system_temp_directory(
    reference_data: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"stub")
    project_root = tmp_path / "business-project"
    requested_work_root = project_root / ".panorama-work" / "agent-runs"
    project_root.mkdir()
    (project_root / "business-source.py").write_text(
        "BUSINESS_PROJECT_SENTINEL = True\n", encoding="utf-8"
    )
    candidate = {
        "candidateId": "CANDIDATE-1",
        "nodes": [{"nodeId": "NODE-1", "name": "Agent"}],
        "edges": [],
    }
    bundle = build_review_bundle(
        reference_data, _session(reference_data), candidate, {"valid": True, "issues": []}
    )
    model_review = {
        "reviewVersion": "panorama-architecture-agent-review.v0.1",
        "verdict": "ready",
        "summary": "Bounded advisory review.",
        "strengths": [],
        "findings": [],
        "recommendations": [],
        "assumptionsToVerify": [],
        "unknowns": [],
    }
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        run_dir = Path(command[command.index("--cd") + 1]).resolve()
        observed["runDir"] = run_dir
        observed["initialFiles"] = sorted(path.name for path in run_dir.iterdir())
        observed["containsProjectSentinel"] = any(
            path.name == "business-source.py" for path in run_dir.rglob("*")
        )
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(model_review), encoding="utf-8")

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(
        studio_agent,
        "locate_codex_cli",
        lambda _explicit=None, **_kwargs: executable,
    )
    monkeypatch.setattr(studio_agent.subprocess, "run", fake_run)

    review_with_codex(
        bundle,
        codex_cli=executable,
        work_root=requested_work_root,
    )

    run_dir = observed["runDir"]
    assert isinstance(run_dir, Path)
    assert run_dir.parent == Path(tempfile.gettempdir()).resolve()
    assert not run_dir.is_relative_to(project_root.resolve())
    assert observed["initialFiles"] == [
        "review-input.json",
        "review-output.schema.json",
    ]
    assert observed["containsProjectSentinel"] is False
    assert not requested_work_root.exists()


def test_codex_response_schema_consts_are_explicitly_typed():
    schema = json.loads(studio_agent.REVIEW_SCHEMA.read_text(encoding="utf-8"))

    def visit(value):
        if isinstance(value, dict):
            if "const" in value:
                assert "type" in value
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
