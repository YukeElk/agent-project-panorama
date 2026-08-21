from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from event_projection import (
    EventProjectionError,
    load_event_checkpoint,
    validate_event_checkpoint,
)
from event_store import record_request
from panorama_view_ir import (
    PanoramaViewIRError,
    compile_model_ir,
    compile_evolution_risk_view_ir,
    compile_lifecycle_view_ir,
    compile_sequence_view_ir,
    validate_model_ir,
    validate_view_ir,
)


def _reference(project_root: Path) -> dict:
    return json.loads(
        (project_root / "examples" / "reference-project.v0.2.json").read_text(
            encoding="utf-8"
        )
    )


def _request(project_root: Path, *, key: str, event_type: str, subject: str) -> dict:
    request = json.loads(
        (project_root / "examples" / "engineering-event-request.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    request["idempotencyKey"] = key
    request["eventType"] = event_type
    request["projectBinding"]["projectId"] = "PRJ-MARW"
    request["correlation"]["correlationId"] = "CORR-V060-SEQUENCE"
    request["subjectRefs"] = [
        {"type": "module", "id": subject, "relationship": "acts_on"}
    ]
    if event_type == "validation.started":
        from_state, to_state = "pending", "validating"
    else:
        from_state, to_state = "validating", "failed"
        request["outcome"] = {
            "status": "failed",
            "labelStrength": "observed",
            "summary": "Validation failed with bounded event metadata.",
        }
    request["extensions"] = {
        "panoramaProjection": {
            "lifecycleTransitions": [
                {
                    "subjectType": "module",
                    "subjectId": subject,
                    "fromState": from_state,
                    "toState": to_state,
                    "trigger": event_type,
                }
            ]
        }
    }
    return request


def _event_store(project_root: Path, tmp_path: Path) -> Path:
    store = tmp_path / "event-store" / "v0.1"
    record_request(
        store,
        _request(
            project_root,
            key="a" * 64,
            event_type="validation.started",
            subject="MOD-CONSOLE",
        ),
        recorded_at="2026-08-21T13:00:01Z",
    )
    record_request(
        store,
        _request(
            project_root,
            key="b" * 64,
            event_type="validation.failed",
            subject="MOD-API",
        ),
        recorded_at="2026-08-21T13:00:02Z",
    )
    return store


def test_v060_event_checkpoint_binds_current_valid_chain(project_root, tmp_path):
    checkpoint = load_event_checkpoint(
        _event_store(project_root, tmp_path), project_id="PRJ-MARW"
    )

    assert validate_event_checkpoint(checkpoint) == []
    assert checkpoint["asOfSequence"] == 2
    assert checkpoint["tailEventId"] == checkpoint["events"][-1]["eventId"]
    assert checkpoint["checkpointId"].startswith("EVCP-")


def test_v060_event_checkpoint_compiles_sequence_without_runtime_invention(
    project_root, tmp_path
):
    checkpoint = load_event_checkpoint(
        _event_store(project_root, tmp_path), project_id="PRJ-MARW"
    )
    model = compile_model_ir(_reference(project_root), event_checkpoint=checkpoint)
    view = compile_sequence_view_ir(model)

    assert validate_model_ir(model) == []
    assert validate_view_ir(view, model) == []
    assert model["eventBinding"] == {
        "status": "complete",
        "checkpointId": checkpoint["checkpointId"],
        "checkpointHash": checkpoint["checkpointHash"],
        "asOfSequence": 2,
    }
    assert model["asOf"] == {
        "mode": "event_checkpoint",
        "value": "2026-08-21T13:00:02Z",
    }
    assert "event_checkpoint_not_provided" not in model["informationGaps"]
    event_relations = [
        item for item in model["relations"] if item["kind"] == "sequence_message"
    ]
    assert [item["semantics"]["order"] for item in event_relations] == [1, 2]
    assert all(
        item["semantics"]["mode"] == "engineering_event_action"
        and item["attributes"]["runtimeCallObserved"] is False
        for item in event_relations
    )
    assert {edge["order"] for edge in view["edges"]} == {1, 2}
    assert view["profile"] == "sequence"
    assert "sequence_async_semantics_unknown" in view["informationGaps"]


def test_v060_sequence_correlation_filter_is_exact(project_root, tmp_path):
    checkpoint = load_event_checkpoint(
        _event_store(project_root, tmp_path), project_id="PRJ-MARW"
    )
    model = compile_model_ir(_reference(project_root), event_checkpoint=checkpoint)

    selected = compile_sequence_view_ir(
        model, correlation_ids=["CORR-V060-SEQUENCE"]
    )
    empty = compile_sequence_view_ir(model, correlation_ids=["CORR-OTHER"])
    assert len(selected["edges"]) == 2
    assert empty["edges"] == []
    assert "sequence_messages_not_available" in empty["informationGaps"]


def test_v060_lifecycle_and_evolution_use_distinct_event_evidence(
    project_root, tmp_path
):
    checkpoint = load_event_checkpoint(
        _event_store(project_root, tmp_path), project_id="PRJ-MARW"
    )
    model = compile_model_ir(_reference(project_root), event_checkpoint=checkpoint)
    lifecycle = compile_lifecycle_view_ir(model)
    evolution = compile_evolution_risk_view_ir(model)

    assert validate_view_ir(lifecycle, model) == []
    assert validate_view_ir(evolution, model) == []
    assert len(lifecycle["edges"]) == 2
    assert {edge["kind"] for edge in lifecycle["edges"]} == {
        "state_transition"
    }
    assert all(
        next(
            relation
            for relation in model["relations"]
            if relation["id"] == edge["relationRef"]["id"]
        )["semantics"]["mode"]
        == "explicit_event_state_transition"
        for edge in lifecycle["edges"]
    )
    assert len(evolution["nodes"]) == 2
    assert len(evolution["edges"]) == 1
    assert evolution["edges"][0]["kind"] == "trace"
    assert any(node["emphasis"] == "risk" for node in evolution["nodes"])


def test_v060_event_checkpoint_tamper_and_head_drift_fail_closed(
    project_root, tmp_path
):
    store = _event_store(project_root, tmp_path)
    checkpoint = load_event_checkpoint(store, project_id="PRJ-MARW")
    tampered = deepcopy(checkpoint)
    tampered["events"][0]["eventType"] = "tampered.event"
    assert validate_event_checkpoint(tampered)
    with pytest.raises(PanoramaViewIRError, match="Event Checkpoint"):
        compile_model_ir(_reference(project_root), event_checkpoint=tampered)

    head_path = store / "stream-head.json"
    head = json.loads(head_path.read_text(encoding="utf-8"))
    first = checkpoint["events"][0]
    head.update(
        {
            "sequence": 1,
            "eventId": first["eventId"],
            "eventHash": first["integrity"]["eventHash"],
        }
    )
    head_path.write_text(
        json.dumps(head, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(EventProjectionError, match="current checkpoint"):
        load_event_checkpoint(store, project_id="PRJ-MARW")


def test_v060_event_store_to_sequence_cli(project_root, tmp_path):
    store = _event_store(project_root, tmp_path)
    model_path = tmp_path / "event.model-ir.json"
    view_path = tmp_path / "event.sequence.view-ir.json"
    model_run = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "compile_panorama_model_ir.py"),
            str(project_root / "examples" / "reference-project.v0.2.json"),
            "--event-store",
            str(store),
            "--output",
            str(model_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert model_run.returncode == 0, model_run.stderr
    view_run = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "compile_panorama_view_ir.py"),
            str(model_path),
            "--profile",
            "sequence",
            "--correlation-id",
            "CORR-V060-SEQUENCE",
            "--output",
            str(view_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert view_run.returncode == 0, view_run.stderr
    model = json.loads(model_path.read_text(encoding="utf-8"))
    view = json.loads(view_path.read_text(encoding="utf-8"))
    assert validate_model_ir(model) == []
    assert validate_view_ir(view, model) == []
