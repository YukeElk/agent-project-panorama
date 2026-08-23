from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from panorama_io import compute_data_hash
import studio_session
from studio_session import (
    SESSION_FORMAT,
    SessionConflictError,
    StudioSessionError,
    archive_candidate,
    candidate_semantic_diff,
    candidate_to_panorama,
    clone_candidate,
    create_candidate,
    create_session,
    formal_validate_candidate,
    layout_hash,
    load_session,
    record_layout_operation,
    record_semantic_operation,
    rename_candidate,
    save_session,
    select_candidate,
    semantic_hash,
    session_path,
    validate_session,
)


def _candidate(session: dict) -> dict:
    return next(
        item
        for item in session["candidates"]
        if item["candidateId"] == session["activeCandidateId"]
    )


def test_create_session_binds_project_and_prefers_target(reference_data: dict):
    session = create_session(reference_data)

    assert session["format"] == SESSION_FORMAT
    assert session["sessionRevision"] == 0
    assert session["projectBinding"] == {
        "projectId": reference_data["project"]["id"],
        "schemaVersion": reference_data["schemaVersion"],
        "templateVersion": reference_data["meta"]["templateVersion"],
        "baseRevision": reference_data["meta"]["revision"],
        "baseDataHash": compute_data_hash(reference_data),
        "gitHead": None,
        "sourceSnapshotHash": None,
    }
    assert session["sourceObservation"] == {
        "observedAt": None,
        "actualGitHead": None,
        "boundGitHead": None,
        "formalSourceBindingPresent": False,
        "driftStatus": "not_observed",
    }
    formal_target_ids = {
        item["id"]
        for item in reference_data["architecture"]["modules"]
        if item["architectureScope"] in {"target", "both"}
    }
    assert {
        item["entityRef"]["id"] for item in _candidate(session)["nodes"]
    } == formal_target_ids
    assert validate_session(session) == []


def test_semantic_and_layout_projections_are_independent(reference_data: dict):
    candidate = _candidate(create_session(reference_data))
    semantic_before = semantic_hash(candidate)
    layout_before = layout_hash(candidate)

    candidate["nodes"][0]["x"] += 37
    candidate["nodes"][0]["y"] += 11

    assert semantic_hash(candidate) == semantic_before
    assert layout_hash(candidate) != layout_before
    candidate["nodes"][0]["purpose"] += " changed"
    assert semantic_hash(candidate) != semantic_before


def test_candidate_semantic_diff_aligns_by_reference_and_excludes_layout(
    reference_data: dict,
):
    session = create_session(reference_data)
    base = deepcopy(_candidate(session))
    compare = deepcopy(base)
    compare["candidateId"] = "CANDIDATE-COMPARE"
    compare["nodes"][0]["x"] += 99
    compare["nodes"][0]["y"] += 17

    assert candidate_semantic_diff(base, compare)["hasChanges"] is False

    compare["nodes"][0]["purpose"] += " changed"
    compare["nodes"][0]["responsibilities"].append("New responsibility")
    compare["assumptions"] = ["External event delivery is available"]
    result = candidate_semantic_diff(base, compare)

    assert result["hasChanges"] is True
    assert result["summary"] == {
        "candidateFieldsModified": 1,
        "nodesAdded": 0,
        "nodesRemoved": 0,
        "nodesModified": 1,
        "edgesAdded": 0,
        "edgesRemoved": 0,
        "edgesModified": 0,
        "moduleLogicCanvasesAdded": 0,
        "moduleLogicCanvasesRemoved": 0,
        "moduleLogicCanvasesModified": 0,
    }
    changed_fields = {
        item["field"] for item in result["nodes"]["modified"][0]["fields"]
    }
    assert changed_fields == {"purpose", "responsibilities"}
    assert all(item["field"] not in {"x", "y"} for item in result["nodes"]["modified"][0]["fields"])


def test_candidate_semantic_diff_never_aligns_drafts_by_display_name(
    reference_data: dict,
):
    base = deepcopy(_candidate(create_session(reference_data)))
    draft = {
        "nodeId": "DRAFT-NODE-A",
        "entityRef": None,
        "name": "Same display name",
        "purpose": "Base draft",
        "responsibilities": [],
        "stateOwnership": "none",
        "layerId": base["nodes"][0]["layerId"],
        "category": "service",
        "isDraft": True,
        "x": 1,
        "y": 1,
    }
    base["nodes"].append(draft)
    compare = deepcopy(base)
    compare["candidateId"] = "CANDIDATE-COMPARE"
    compare["nodes"][-1]["nodeId"] = "DRAFT-NODE-B"
    compare["nodes"][-1]["purpose"] = "Different draft"

    result = candidate_semantic_diff(base, compare)

    assert result["nodes"]["added"] == ["node:DRAFT-NODE-B"]
    assert result["nodes"]["removed"] == ["node:DRAFT-NODE-A"]
    assert result["nodes"]["modified"] == []


def test_operations_bind_active_candidate_and_preserve_dual_track(reference_data: dict):
    session = create_session(reference_data)
    candidate_id = session["activeCandidateId"]

    semantic = record_semantic_operation(
        session, "node.update", {"nodeId": "NODE-X"}, "before", "after"
    )
    layout = record_layout_operation(
        session, "layout.move", {"nodeId": "NODE-X"}, {"x": 1}, {"x": 2}
    )

    assert semantic["target"]["candidateId"] == candidate_id
    assert semantic["affectsSemanticHash"] is True
    assert layout["target"]["candidateId"] == candidate_id
    assert layout["affectsSemanticHash"] is False


def test_candidate_lifecycle_is_isolated(reference_data: dict):
    session = create_session(reference_data)
    base_id = session["activeCandidateId"]
    alternative = clone_candidate(session, base_id, "Alternative A")
    assert alternative["candidateId"] != base_id
    assert session["activeCandidateId"] == alternative["candidateId"]

    rename_candidate(session, alternative["candidateId"], "Alternative B")
    select_candidate(session, base_id)
    archive_candidate(session, alternative["candidateId"])
    assert alternative["label"] == "Alternative B"
    assert alternative["status"] == "archived"
    with pytest.raises(StudioSessionError, match="archived"):
        select_candidate(session, alternative["candidateId"])

    empty = create_candidate(session, "Blank")
    assert empty["nodes"] == []
    assert len(session["candidates"]) == 3


def test_only_semantic_change_invalidates_formal_artifacts(reference_data: dict):
    session = create_session(reference_data)
    session["formalValidation"] = {"valid": True}
    session["reviewArtifact"] = {"status": "pending"}
    session["proposalArtifact"] = {"proposalHash": "not-a-real-proposal"}

    record_layout_operation(
        session, "layout.move", {"nodeId": _candidate(session)["nodes"][0]["nodeId"]}
    )
    assert session["formalValidation"] == {"valid": True}
    assert session["reviewArtifact"] == {"status": "pending"}
    assert session["proposalArtifact"] == {"proposalHash": "not-a-real-proposal"}

    record_semantic_operation(session, "node.update", {"nodeId": "NODE-X"})
    assert session["formalValidation"] is None
    assert session["reviewArtifact"] is None
    assert session["proposalArtifact"] is None


def test_safe_atomic_persistence_and_optimistic_revision(
    reference_data: dict, tmp_path: Path
):
    session = create_session(reference_data)
    persisted = save_session(tmp_path, session, expected_revision=0)
    path = session_path(tmp_path, persisted["sessionId"])

    assert path.parent == (
        tmp_path / ".panorama-work" / "studio" / "sessions"
    ).resolve()
    assert persisted["sessionRevision"] == 1
    assert load_session(tmp_path, persisted["sessionId"]) == persisted
    assert not list(path.parent.glob("*.tmp"))

    stale = deepcopy(persisted)
    saved_again = save_session(tmp_path, persisted, expected_revision=1)
    assert saved_again["sessionRevision"] == 2
    with pytest.raises(SessionConflictError, match="current revision is 2"):
        save_session(tmp_path, stale, expected_revision=1)
    with pytest.raises(StudioSessionError, match="unsafe"):
        load_session(tmp_path, "../escape")


def test_materialization_edits_existing_and_adds_complete_target_entities(
    reference_data: dict, schema_path: Path, tmp_path: Path
):
    current = deepcopy(reference_data)
    current["architecture"]["modules"][0]["extensions"]["unknownFutureField"] = {
        "preserve": True
    }
    # The source hash must bind the exact object being materialized.
    session = create_session(current)
    candidate = _candidate(session)
    existing = candidate["nodes"][0]
    existing["purpose"] = "Edited in Studio"
    existing_id = existing["entityRef"]["id"]
    new_node = {
        "nodeId": "DRAFT-NODE-NEW",
        "entityRef": None,
        "name": "New Draft Service",
        "purpose": "A conservative target-only service.",
        "responsibilities": ["Handle draft responsibility"],
        "stateOwnership": "Draft state boundary",
        "layerId": candidate["nodes"][0]["layerId"],
        "category": "supporting",
        "isDraft": True,
        "x": 400,
        "y": 240,
        "extensions": {"unknownDraftField": "preserved"},
    }
    candidate["nodes"].append(new_node)
    candidate["edges"].append(
        {
            "edgeId": "DRAFT-EDGE-NEW",
            "entityRef": None,
            "fromNodeId": existing["nodeId"],
            "toNodeId": new_node["nodeId"],
            "label": "HTTPS",
            "dataSummary": "Draft request data",
            "isDraft": True,
            "extensions": {"unknownEdgeField": "preserved"},
        }
    )

    result = formal_validate_candidate(
        current, session, candidate["candidateId"], schema_path, tmp_path
    )

    assert result["validation"]["valid"] is True
    assert result["semanticHash"] == semantic_hash(candidate)
    materialized = result["candidateData"]
    edited = next(
        item for item in materialized["architecture"]["modules"] if item["id"] == existing_id
    )
    assert edited["purpose"] == "Edited in Studio"
    assert edited["extensions"]["unknownFutureField"] == {"preserve": True}

    added_modules = [
        item
        for item in materialized["architecture"]["modules"]
        if item["id"].startswith("MOD-STUDIO-")
    ]
    assert len(added_modules) == 1
    added = added_modules[0]
    assert added["architectureScope"] == "target"
    assert added["status"] == {
        "designMaturity": "draft",
        "implementationMaturity": "not_started",
        "verificationStatus": "pending",
        "runtimeStatus": "not_deployed",
    }
    assert added["reviewIds"] == []
    assert added["extensions"]["unknownDraftField"] == "preserved"

    added_connections = [
        item
        for item in materialized["architecture"]["connections"]
        if item["id"].startswith("CONN-STUDIO-")
    ]
    assert len(added_connections) == 1
    assert added_connections[0]["architectureScope"] == "target"
    assert added_connections[0]["lifecycle"] == "planned"
    assert added_connections[0]["extensions"]["unknownEdgeField"] == "preserved"
    repeated = candidate_to_panorama(current, session, candidate["candidateId"])
    assert repeated == materialized
    assert added["createdAt"] == candidate["updatedAt"]
    assert added["updatedAt"] == candidate["updatedAt"]


def test_existing_design_and_connection_round_trip_is_lossless(reference_data: dict):
    current = deepcopy(reference_data)
    module = next(
        item
        for item in current["architecture"]["modules"]
        if isinstance(item.get("targetDesign") or item.get("currentDesign"), dict)
    )
    design_key = "targetDesign" if isinstance(module.get("targetDesign"), dict) else "currentDesign"
    module[design_key]["extensions"]["futureNested"] = {"keep": [1, 2, 3]}
    module["extensions"]["futureModule"] = {"keep": True}
    connection = current["architecture"]["connections"][0]
    connection["extensions"]["futureConnection"] = {"keep": True}
    before_module = deepcopy(module)
    before_connection = deepcopy(connection)

    session = create_session(current)
    candidate = _candidate(session)
    node = next(item for item in candidate["nodes"] if item["entityRef"]["id"] == module["id"])

    # The full formal design is present in the semantic draft, including the
    # extension point, and an untouched candidate is a true no-op.
    assert node["nonResponsibilities"] == module[design_key]["nonResponsibilities"]
    assert node["technologies"] == module[design_key]["technologies"]
    assert node["dataHandled"] == module[design_key]["dataHandled"]
    assert node["interfaceSummary"] == module[design_key]["interfaceSummary"]
    assert node["deploymentRole"] == module[design_key]["deploymentRole"]
    assert node["referenceIds"] == module[design_key]["referenceIds"]
    assert node["designExtensions"] == module[design_key]["extensions"]

    materialized = candidate_to_panorama(current, session, candidate["candidateId"])
    assert next(item for item in materialized["architecture"]["modules"] if item["id"] == module["id"]) == before_module
    assert next(item for item in materialized["architecture"]["connections"] if item["id"] == connection["id"]) == before_connection
    assert compute_data_hash(materialized) == compute_data_hash(current)


def test_editing_one_field_preserves_every_other_design_field(reference_data: dict):
    current = deepcopy(reference_data)
    module = next(
        item
        for item in current["architecture"]["modules"]
        if isinstance(item.get("targetDesign") or item.get("currentDesign"), dict)
    )
    design_key = "targetDesign" if isinstance(module.get("targetDesign"), dict) else "currentDesign"
    before_design = deepcopy(module[design_key])
    session = create_session(current)
    candidate = _candidate(session)
    node = next(item for item in candidate["nodes"] if item["entityRef"]["id"] == module["id"])
    node["responsibilities"] = [*node["responsibilities"], "Studio edited responsibility"]

    materialized = candidate_to_panorama(current, session, candidate["candidateId"])
    edited = next(item for item in materialized["architecture"]["modules"] if item["id"] == module["id"])
    actual_design = edited.get("targetDesign")
    assert actual_design["responsibilities"][-1] == "Studio edited responsibility"
    for key, value in before_design.items():
        if key != "responsibilities":
            assert actual_design[key] == value


def test_v02_session_binds_formal_source_and_discloses_git_drift(
    reference_data: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    current = deepcopy(reference_data)
    current["schemaVersion"] = "0.2"
    current["sourceBinding"] = {
        "gitHead": "a" * 40,
        "sourceSnapshotHash": "b" * 64,
    }
    monkeypatch.setattr(studio_session, "_git_head", lambda _root: "c" * 40)

    session = create_session(current, project_root=tmp_path)

    assert session["projectBinding"]["gitHead"] == "a" * 40
    assert session["projectBinding"]["sourceSnapshotHash"] == "b" * 64
    assert session["sourceObservation"]["actualGitHead"] == "c" * 40
    assert session["sourceObservation"]["driftStatus"] == "drift"
    assert session["status"] == "stale"
    # Drift is disclosed, but the exact formal Panorama remains the valid
    # materialization baseline rather than failing against the observed HEAD.
    assert candidate_to_panorama(current, session, session["activeCandidateId"])


def test_materialization_never_deletes_formal_entities(reference_data: dict):
    session = create_session(reference_data)
    candidate = _candidate(session)
    removed_node = candidate["nodes"].pop()
    candidate["edges"] = [
        edge
        for edge in candidate["edges"]
        if removed_node["nodeId"] not in {edge["fromNodeId"], edge["toNodeId"]}
    ]

    materialized = candidate_to_panorama(
        reference_data, session, candidate["candidateId"]
    )
    assert len(materialized["architecture"]["modules"]) == len(
        reference_data["architecture"]["modules"]
    )
    assert len(materialized["architecture"]["connections"]) == len(
        reference_data["architecture"]["connections"]
    )


def test_materialization_rejects_bound_project_drift(reference_data: dict):
    session = create_session(reference_data)
    changed = deepcopy(reference_data)
    changed["meta"]["revision"] += 1
    with pytest.raises(SessionConflictError, match="baseRevision"):
        candidate_to_panorama(changed, session, session["activeCandidateId"])
