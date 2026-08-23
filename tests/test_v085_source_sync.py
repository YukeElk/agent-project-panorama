from __future__ import annotations

import json
from pathlib import Path

import pytest

from sync_source_observation import SourceSyncError, sync_source_observation


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_v085_source_sync_initializes_noops_and_reconciles_change(tmp_path):
    root = tmp_path / "project"
    _write(root / "go.mod", "module example.com/app\ngo 1.23\n")
    _write(root / "main.go", "package main\n")
    first = sync_source_observation(
        root, project_id="PRJ-SYNC", observed_at="2026-08-24T08:00:00Z"
    )
    assert first["status"] == "initialized"
    assert first["changed"] is True
    second = sync_source_observation(
        root, project_id="PRJ-SYNC", observed_at="2026-08-24T09:00:00Z"
    )
    assert second["status"] == "match"
    assert second["observationHash"] == first["observationHash"]

    _write(root / "main.go", "package main\nimport \"fmt\"\n")
    third = sync_source_observation(
        root, project_id="PRJ-SYNC", observed_at="2026-08-24T10:00:00Z"
    )
    assert third["status"] == "changed"
    assert third["observationHash"] != first["observationHash"]
    latest = json.loads(
        (root / ".panorama-work/source/latest.observation.json").read_text(encoding="utf-8")
    )
    assert latest["observationId"] == third["observationId"]


def test_v085_source_sync_rejects_output_escape_and_tampered_latest(tmp_path):
    root = tmp_path / "project"
    _write(root / "main.py", "VALUE = 1\n")
    with pytest.raises(SourceSyncError, match="project root"):
        sync_source_observation(
            root,
            project_id="PRJ-SYNC",
            observed_at="2026-08-24T08:00:00Z",
            output_dir=tmp_path / "outside",
        )
    sync_source_observation(
        root, project_id="PRJ-SYNC", observed_at="2026-08-24T08:00:00Z"
    )
    latest = root / ".panorama-work/source/latest.observation.json"
    value = json.loads(latest.read_text(encoding="utf-8"))
    value["extensions"]["adapterRegistry"]["version"] = "tampered"
    latest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SourceSyncError, match="latest Source Observation 无效"):
        sync_source_observation(
            root, project_id="PRJ-SYNC", observed_at="2026-08-24T09:00:00Z"
        )
