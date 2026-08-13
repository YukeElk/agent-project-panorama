"""Pure-Python core for isolated Architecture Studio sessions.

The session is a draft boundary.  Nothing in this module writes a Panorama
HTML or asserts review, approval, evidence, verification, or deployment facts.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from panorama_io import compute_canonical_hash, compute_data_hash, extract_data
from schema_support import schema_for_data
from validate_panorama import validate_data


SESSION_FORMAT = "panorama-architecture-session.v0.1"
SESSION_SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "architecture-session.schema.v0.1.json"
SESSION_RELATIVE_DIR = Path(".panorama-work") / "studio" / "sessions"
MAX_SESSION_BYTES = 1024 * 1024
MAX_CANDIDATES = 12
MAX_NODES_PER_CANDIDATE = 200
MAX_EDGES_PER_CANDIDATE = 400
MAX_OPERATIONS = 5000
_ID_RE = re.compile(r"^[A-Z][A-Z0-9._:-]{1,127}$")
_SAFE_FILE_ID_RE = re.compile(r"^[A-Z][A-Z0-9._-]{1,127}$")


class StudioSessionError(ValueError):
    """A session, binding, path, candidate, or materialization is invalid."""


class SessionConflictError(StudioSessionError):
    """Optimistic revision or cooperating-writer conflict."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _candidate(session: dict[str, Any], candidate_id: str | None) -> dict[str, Any]:
    selected = candidate_id or session.get("activeCandidateId")
    for item in session.get("candidates", []):
        if isinstance(item, dict) and item.get("candidateId") == selected:
            return item
    raise StudioSessionError(f"candidate does not exist: {selected!r}")


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise StudioSessionError(f"{field} must be a legal Panorama ID")
    return value


def _file_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_FILE_ID_RE.fullmatch(value):
        raise StudioSessionError(f"{field} is unsafe for session storage")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise StudioSessionError(f"{field} cannot traverse directories")
    return value


def _slug(value: str, prefix: str) -> str:
    ascii_value = re.sub(r"[^A-Z0-9]+", "-", value.upper()).strip("-")
    if not ascii_value:
        ascii_value = compute_canonical_hash(value)[:12].upper()
    result = f"{prefix}-{ascii_value}"[:128].rstrip("-.")
    if len(result) < 2:
        result += "-X"
    return result


def _unique_id(prefix: str, seed: Any, occupied: set[str]) -> str:
    digest = compute_canonical_hash(seed).upper()
    for length in range(12, 65, 4):
        candidate = f"{prefix}-{digest[:length]}"
        if candidate not in occupied:
            occupied.add(candidate)
            return candidate
    raise StudioSessionError(f"unable to allocate deterministic {prefix} ID")


def _source_binding(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("sourceBinding")
    return value if isinstance(value, dict) else {}


def _git_head(project_root: str | os.PathLike[str] | None) -> str | None:
    if project_root is None:
        return None
    root = Path(project_root).resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40,64}", value) else None


def _target_modules(data: dict[str, Any]) -> list[dict[str, Any]]:
    modules = data.get("architecture", {}).get("modules", [])
    target = [item for item in modules if item.get("architectureScope") in {"target", "both"}]
    return target or list(modules)


def _baseline_candidate(data: dict[str, Any], now: str) -> dict[str, Any]:
    architecture = data.get("architecture", {})
    layers = sorted(architecture.get("layers", []), key=lambda item: item.get("order", 0))
    modules = _target_modules(data)
    layer_index = {item.get("id"): index for index, item in enumerate(layers)}
    lane_counts: dict[str, int] = {}
    nodes: list[dict[str, Any]] = []
    node_for_module: dict[str, str] = {}
    for module in modules:
        module_id = _id(module.get("id"), "module.id")
        layer_id = module.get("targetLayerId") or module.get("layerId")
        _id(layer_id, "module.layerId")
        slot = lane_counts.get(layer_id, 0)
        lane_counts[layer_id] = slot + 1
        target_design = module.get("targetDesign")
        current_design = module.get("currentDesign")
        design = (
            target_design
            if isinstance(target_design, dict)
            else current_design if isinstance(current_design, dict) else {}
        )
        effective_layer_id = module.get("targetLayerId") or module.get("layerId")
        node_id = _slug(module_id, "NODE")
        node_for_module[module_id] = node_id
        nodes.append({
            "nodeId": node_id,
            "entityRef": {"type": "module", "id": module_id},
            "name": module.get("name", module_id),
            "purpose": module.get("purpose", ""),
            "responsibilities": _clone(design.get("responsibilities", [])),
            "nonResponsibilities": _clone(design.get("nonResponsibilities", [])),
            "technologies": _clone(design.get("technologies", [])),
            "stateOwnership": design.get("stateOwnership", ""),
            "dataHandled": _clone(design.get("dataHandled", [])),
            "interfaceSummary": _clone(design.get("interfaceSummary", [])),
            "deploymentRole": design.get("deploymentRole", ""),
            "referenceIds": _clone(design.get("referenceIds", [])),
            "notes": design.get("notes", ""),
            "designExtensions": _clone(design.get("extensions", {})),
            "rationale": module.get("rationale", ""),
            "requirementIds": _clone(module.get("requirementIds", [])),
            "layerId": effective_layer_id,
            # This is part of the semantic draft baseline, not canvas layout.
            # It lets materialization distinguish a user layer edit from an
            # unchanged formal targetLayerId (including an explicit same-layer
            # target) without guessing.
            "baselineLayerId": effective_layer_id,
            "category": module.get("category", "supporting"),
            "isDraft": False,
            "x": 145 + (slot % 3) * 205,
            "y": 20 + layer_index.get(layer_id, 0) * 122 + (slot // 3) * 86,
        })
    edges: list[dict[str, Any]] = []
    for connection in architecture.get("connections", []):
        from_node = node_for_module.get(connection.get("fromModuleId"))
        to_node = node_for_module.get(connection.get("toModuleId"))
        if not from_node or not to_node or connection.get("architectureScope") not in {"target", "both"}:
            continue
        connection_id = _id(connection.get("id"), "connection.id")
        edges.append({
            "edgeId": _slug(connection_id, "EDGE"),
            "entityRef": {"type": "connection", "id": connection_id},
            "fromNodeId": from_node,
            "toNodeId": to_node,
            "name": connection.get("name", connection_id),
            "label": connection.get("protocol") or connection.get("communicationMode") or "data",
            "protocol": connection.get("protocol", ""),
            "communicationMode": connection.get("communicationMode", "direct"),
            "flowDirection": connection.get("flowDirection", "one_way"),
            "dataSummary": connection.get("dataSummary", ""),
            "contractReferenceIds": _clone(connection.get("contractReferenceIds", [])),
            "authSummary": connection.get("authSummary", ""),
            "reliabilitySummary": connection.get("reliabilitySummary", ""),
            "rationale": connection.get("rationale", ""),
            "transitionId": connection.get("transitionId"),
            "extensions": _clone(connection.get("extensions", {})),
            "isDraft": False,
        })
    return {
        "candidateId": "CANDIDATE-BASE", "label": "Target architecture copy",
        "kind": "baseline_copy", "status": "active", "assumptions": [], "unknowns": [],
        "nodes": nodes, "edges": edges, "createdAt": now, "updatedAt": now,
    }


def _semantic_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    nodes = []
    for node in candidate.get("nodes", []):
        nodes.append({
            key: _clone(value) for key, value in node.items()
            if key not in {"x", "y", "selected", "collapsed", "width", "height"}
        })
    edges = [_clone(item) for item in candidate.get("edges", [])]
    nodes.sort(key=lambda item: str(item.get("nodeId", "")))
    edges.sort(key=lambda item: str(item.get("edgeId", "")))
    return {
        "assumptions": _clone(candidate.get("assumptions", [])),
        "unknowns": _clone(candidate.get("unknowns", [])),
        "nodes": nodes, "edges": edges,
    }


def _layout_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": sorted(
            [
                {"nodeId": item.get("nodeId"), "x": item.get("x", 0), "y": item.get("y", 0)}
                for item in candidate.get("nodes", [])
            ], key=lambda item: str(item.get("nodeId", "")),
        )
    }


def semantic_hash(candidate: dict[str, Any]) -> str:
    """Hash architecture semantics while excluding canvas layout."""
    if not isinstance(candidate, dict):
        raise StudioSessionError("candidate must be an object")
    return compute_canonical_hash(_semantic_projection(candidate))


def layout_hash(candidate: dict[str, Any]) -> str:
    """Hash only canvas layout coordinates."""
    if not isinstance(candidate, dict):
        raise StudioSessionError("candidate must be an object")
    return compute_canonical_hash(_layout_projection(candidate))


def _semantic_identity(item: dict[str, Any], local_key: str, kind: str) -> str:
    entity_ref = item.get("entityRef")
    if isinstance(entity_ref, dict) and entity_ref.get("type") and entity_ref.get("id"):
        return f"entity:{entity_ref['type']}:{entity_ref['id']}"
    return f"{kind}:{item.get(local_key, '')}"


def _semantic_field_changes(
    before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [
        {"field": field, "before": _clone(before.get(field)), "after": _clone(after.get(field))}
        for field in fields
        if before.get(field) != after.get(field)
    ]


def candidate_semantic_diff(
    base_candidate: dict[str, Any], compare_candidate: dict[str, Any]
) -> dict[str, Any]:
    """Return a deterministic field-level semantic diff between two candidates.

    Formal entities align only by ``entityRef.type + entityRef.id``. Draft
    entities align only by their session-local ID; display names are never an
    identity. Canvas coordinates and operation timestamps are intentionally
    absent from this projection.
    """

    if not isinstance(base_candidate, dict) or not isinstance(compare_candidate, dict):
        raise StudioSessionError("candidates must be objects")

    node_fields = (
        "name", "purpose", "responsibilities", "nonResponsibilities",
        "stateOwnership", "layerId", "category", "technologies", "dataHandled",
        "interfaceSummary", "deploymentRole", "referenceIds", "notes",
        "designExtensions", "rationale", "requirementIds", "isDraft",
    )
    edge_fields = (
        "fromNodeRef", "toNodeRef", "name", "label", "protocol",
        "communicationMode", "flowDirection", "dataSummary",
        "contractReferenceIds", "authSummary", "reliabilitySummary", "rationale",
        "transitionId", "extensions", "isDraft",
    )

    def nodes(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            _semantic_identity(item, "nodeId", "node"): item
            for item in candidate.get("nodes", [])
        }

    def edges(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
        node_refs = {
            item.get("nodeId"): _semantic_identity(item, "nodeId", "node")
            for item in candidate.get("nodes", [])
        }
        result: dict[str, dict[str, Any]] = {}
        for item in candidate.get("edges", []):
            projected = _clone(item)
            projected["fromNodeRef"] = node_refs.get(
                item.get("fromNodeId"), f"node:{item.get('fromNodeId', '')}"
            )
            projected["toNodeRef"] = node_refs.get(
                item.get("toNodeId"), f"node:{item.get('toNodeId', '')}"
            )
            result[_semantic_identity(item, "edgeId", "edge")] = projected
        return result

    def entity_diff(
        base_items: dict[str, dict[str, Any]],
        compare_items: dict[str, dict[str, Any]],
        fields: tuple[str, ...],
    ) -> dict[str, list[Any]]:
        base_keys = set(base_items)
        compare_keys = set(compare_items)
        modified = []
        for identity in sorted(base_keys & compare_keys):
            changes = _semantic_field_changes(
                base_items[identity], compare_items[identity], fields
            )
            if changes:
                modified.append({"identity": identity, "fields": changes})
        return {
            "added": sorted(compare_keys - base_keys),
            "removed": sorted(base_keys - compare_keys),
            "modified": modified,
        }

    node_diff = entity_diff(nodes(base_candidate), nodes(compare_candidate), node_fields)
    edge_diff = entity_diff(edges(base_candidate), edges(compare_candidate), edge_fields)
    candidate_changes = _semantic_field_changes(
        base_candidate, compare_candidate, ("assumptions", "unknowns")
    )
    summary = {
        "candidateFieldsModified": len(candidate_changes),
        "nodesAdded": len(node_diff["added"]),
        "nodesRemoved": len(node_diff["removed"]),
        "nodesModified": len(node_diff["modified"]),
        "edgesAdded": len(edge_diff["added"]),
        "edgesRemoved": len(edge_diff["removed"]),
        "edgesModified": len(edge_diff["modified"]),
    }
    return {
        "baseCandidateId": base_candidate.get("candidateId"),
        "compareCandidateId": compare_candidate.get("candidateId"),
        "candidateFields": candidate_changes,
        "nodes": node_diff,
        "edges": edge_diff,
        "summary": summary,
        "hasChanges": any(summary.values()),
    }


def _refresh_hashes(session: dict[str, Any]) -> None:
    candidate = _candidate(session, None)
    session["semanticHash"] = semantic_hash(candidate)
    session["layoutHash"] = layout_hash(candidate)


def create_session(
    panorama_data: dict[str, Any], project_root: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    """Create an isolated session from Target, or Current when no Target exists."""
    if not isinstance(panorama_data, dict):
        raise StudioSessionError("panorama_data must be an object")
    project_id = _id(panorama_data.get("project", {}).get("id"), "project.id")
    meta = panorama_data.get("meta", {})
    source = _source_binding(panorama_data)
    now = _now()
    binding_head = source.get("gitHead")
    observed_head = _git_head(project_root)
    candidate = _baseline_candidate(panorama_data, now)
    source_binding_present = bool(source)
    git_drift = bool(
        project_root is not None
        and observed_head is not None
        and source_binding_present
        and binding_head != observed_head
    )
    if project_root is None:
        drift_status = "not_observed"
    elif not source_binding_present:
        drift_status = "unbound"
    elif observed_head is None:
        drift_status = "not_observed"
    elif git_drift:
        drift_status = "drift"
    else:
        drift_status = "match"
    session = {
        "format": SESSION_FORMAT,
        "sessionId": f"SESSION-{uuid.uuid4().hex[:20].upper()}",
        "sessionRevision": 0,
        "projectBinding": {
            "projectId": project_id,
            "schemaVersion": str(panorama_data.get("schemaVersion", "")),
            "templateVersion": str(meta.get("templateVersion", "")),
            "baseRevision": meta.get("revision"),
            "baseDataHash": compute_data_hash(panorama_data),
            # The materialization baseline is the formal Panorama binding.
            # A live repository observation is disclosed separately below and
            # must never silently replace the approved/formal base.
            "gitHead": binding_head,
            "sourceSnapshotHash": source.get("sourceSnapshotHash"),
        },
        "sourceObservation": {
            "observedAt": now if project_root is not None else None,
            "actualGitHead": observed_head,
            "boundGitHead": binding_head,
            "formalSourceBindingPresent": source_binding_present,
            "driftStatus": drift_status,
        },
        "mode": "offline",
        "status": "stale" if git_drift else "draft",
        "scope": {"level": "project", "subjectRefs": [{"type": "project", "id": project_id}]},
        "layers": _clone(panorama_data.get("architecture", {}).get("layers", [])),
        "candidates": [candidate], "activeCandidateId": candidate["candidateId"],
        "semanticOperations": [], "layoutOperations": [], "clientValidation": [],
        "formalValidation": None, "reviewArtifact": None, "proposalArtifact": None,
        "createdAt": now, "updatedAt": now,
    }
    _refresh_hashes(session)
    session["clientValidation"] = validate_session(session)
    return session


def _finding(level: str, code: str, message: str, path: str = "", candidate_id: str | None = None) -> dict[str, Any]:
    result = {"level": level, "code": code, "message": message, "path": path}
    if candidate_id:
        result["candidateId"] = candidate_id
    return result


def validate_session(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-governance client findings; these are never Formal Findings."""
    findings: list[dict[str, Any]] = []
    if not isinstance(session, dict):
        return [_finding("error", "CLIENT_SESSION_TYPE", "session must be an object")]
    try:
        import jsonschema
        schema = json.loads(SESSION_SCHEMA.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        for error in sorted(validator.iter_errors(session), key=lambda item: list(item.path)):
            path = "/" + "/".join(str(item) for item in error.absolute_path)
            findings.append(_finding("error", "CLIENT_SESSION_SCHEMA", error.message, path))
    except (ImportError, OSError, json.JSONDecodeError) as exc:
        findings.append(_finding("error", "CLIENT_SCHEMA_RUNTIME", str(exc)))
        return findings
    candidates = session.get("candidates", [])
    candidate_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        candidate_id = candidate.get("candidateId")
        if candidate_id in candidate_ids:
            findings.append(_finding("error", "CLIENT_DUPLICATE_CANDIDATE", f"duplicate candidate ID: {candidate_id}", f"/candidates/{index}/candidateId", candidate_id))
        candidate_ids.add(candidate_id)
        node_ids: set[str] = set()
        for node_index, node in enumerate(candidate.get("nodes", [])):
            node_id = node.get("nodeId")
            if node_id in node_ids:
                findings.append(_finding("error", "CLIENT_DUPLICATE_NODE", f"duplicate node ID: {node_id}", f"/candidates/{index}/nodes/{node_index}/nodeId", candidate_id))
            node_ids.add(node_id)
            if not str(node.get("name", "")).strip():
                findings.append(_finding("error", "CLIENT_NODE_NAME", f"{node_id} has no name", candidate_id=candidate_id))
            if not str(node.get("purpose", "")).strip():
                findings.append(_finding("warning", "CLIENT_NODE_PURPOSE", f"{node_id} has no purpose", candidate_id=candidate_id))
            if not str(node.get("stateOwnership", "")).strip():
                findings.append(_finding("warning", "CLIENT_STATE_OWNERSHIP", f"{node_id} has no state ownership", candidate_id=candidate_id))
        edge_ids: set[str] = set()
        for edge_index, edge in enumerate(candidate.get("edges", [])):
            edge_id = edge.get("edgeId")
            if edge_id in edge_ids:
                findings.append(_finding("error", "CLIENT_DUPLICATE_EDGE", f"duplicate edge ID: {edge_id}", f"/candidates/{index}/edges/{edge_index}/edgeId", candidate_id))
            edge_ids.add(edge_id)
            if edge.get("fromNodeId") not in node_ids or edge.get("toNodeId") not in node_ids:
                findings.append(_finding("error", "CLIENT_EDGE_ENDPOINT", f"{edge_id} has a missing endpoint", candidate_id=candidate_id))
            if edge.get("fromNodeId") == edge.get("toNodeId"):
                findings.append(_finding("warning", "CLIENT_EDGE_SELF_LOOP", f"{edge_id} is a self-loop", candidate_id=candidate_id))
    if session.get("activeCandidateId") not in candidate_ids:
        findings.append(_finding("error", "CLIENT_ACTIVE_CANDIDATE", "activeCandidateId does not exist", "/activeCandidateId"))
    if any(
        len(candidate.get("nodes", [])) > MAX_NODES_PER_CANDIDATE
        for candidate in candidates
    ):
        findings.append(_finding("error", "CLIENT_SESSION_LIMIT", "a candidate exceeds the node limit"))
    if any(
        len(candidate.get("edges", [])) > MAX_EDGES_PER_CANDIDATE
        for candidate in candidates
    ):
        findings.append(_finding("error", "CLIENT_SESSION_LIMIT", "a candidate exceeds the edge limit"))
    if len(session.get("semanticOperations", [])) + len(session.get("layoutOperations", [])) > MAX_OPERATIONS:
        findings.append(_finding("error", "CLIENT_OPERATION_LIMIT", "session exceeds the operation limit"))
    for operation in session.get("semanticOperations", []):
        if operation.get("affectsSemanticHash") is not True:
            findings.append(_finding("error", "CLIENT_SEMANTIC_OPERATION", "semantic operation must affect semantic hash"))
    for operation in session.get("layoutOperations", []):
        if operation.get("affectsSemanticHash") is not False:
            findings.append(_finding("error", "CLIENT_LAYOUT_OPERATION", "layout operation must not affect semantic hash"))
    return findings


def _invalidate_formal_artifacts(session: dict[str, Any]) -> None:
    for key in (
        "formalValidation", "formalValidationArtifact", "reviewArtifact",
        "reviewArtifacts", "proposalArtifact", "proposalArtifacts", "review", "proposal",
    ):
        if key in session:
            session[key] = None
    artifacts = session.get("artifacts")
    if isinstance(artifacts, dict):
        for key in ("formalValidation", "review", "proposal"):
            if key in artifacts:
                artifacts[key] = None


def _touch(session: dict[str, Any], *, semantic: bool) -> None:
    if semantic:
        _invalidate_formal_artifacts(session)
    if session.get("status") != "stale":
        session["status"] = "dirty"
    session["updatedAt"] = _now()
    _refresh_hashes(session)
    session["clientValidation"] = validate_session(session)


def create_candidate(session: dict[str, Any], label: str, *, clone_from: str | None = None) -> dict[str, Any]:
    if len(session.get("candidates", [])) >= MAX_CANDIDATES:
        raise StudioSessionError("candidate limit reached")
    if not str(label).strip():
        raise StudioSessionError("candidate label is required")
    now = _now()
    existing = {item.get("candidateId") for item in session.get("candidates", [])}
    candidate_id = _unique_id("CANDIDATE", {"session": session.get("sessionId"), "label": label, "count": len(existing)}, existing)
    if clone_from:
        item = _clone(_candidate(session, clone_from))
        item.update({"candidateId": candidate_id, "label": label.strip(), "kind": "alternative", "status": "active", "createdAt": now, "updatedAt": now})
    else:
        item = {"candidateId": candidate_id, "label": label.strip(), "kind": "alternative", "status": "active", "assumptions": [], "unknowns": [], "nodes": [], "edges": [], "createdAt": now, "updatedAt": now}
    session["candidates"].append(item)
    session["activeCandidateId"] = candidate_id
    _append_operation(session, "candidate.create", {"candidateId": candidate_id}, None, {"label": item["label"]}, semantic=True)
    return item


def clone_candidate(session: dict[str, Any], candidate_id: str, label: str | None = None) -> dict[str, Any]:
    source = _candidate(session, candidate_id)
    return create_candidate(session, label or f"{source.get('label', candidate_id)} copy", clone_from=candidate_id)


def rename_candidate(session: dict[str, Any], candidate_id: str, label: str) -> dict[str, Any]:
    if not str(label).strip():
        raise StudioSessionError("candidate label is required")
    item = _candidate(session, candidate_id)
    before = item.get("label")
    item["label"] = label.strip()
    item["updatedAt"] = _now()
    _append_operation(session, "candidate.rename", {"candidateId": candidate_id}, before, item["label"], semantic=True)
    return item


def archive_candidate(session: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    item = _candidate(session, candidate_id)
    active = [candidate for candidate in session.get("candidates", []) if candidate.get("status") != "archived" and candidate.get("candidateId") != candidate_id]
    if not active:
        raise StudioSessionError("cannot archive the last active candidate")
    item["status"] = "archived"
    item["updatedAt"] = _now()
    if session.get("activeCandidateId") == candidate_id:
        session["activeCandidateId"] = active[0]["candidateId"]
    _append_operation(session, "candidate.archive", {"candidateId": candidate_id}, "active", "archived", semantic=True)
    return item


def select_candidate(session: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    item = _candidate(session, candidate_id)
    if item.get("status") == "archived":
        raise StudioSessionError("cannot select an archived candidate")
    session["activeCandidateId"] = candidate_id
    session["updatedAt"] = _now()
    _refresh_hashes(session)
    session["clientValidation"] = validate_session(session)
    return item


def _append_operation(session: dict[str, Any], kind: str, target: dict[str, Any], before: Any, after: Any, *, semantic: bool) -> dict[str, Any]:
    operations = session["semanticOperations"] if semantic else session["layoutOperations"]
    total = len(session.get("semanticOperations", [])) + len(session.get("layoutOperations", []))
    if total >= MAX_OPERATIONS:
        raise StudioSessionError("operation limit reached")
    operation_target = _clone(target)
    operation_target.setdefault("candidateId", session.get("activeCandidateId"))
    operation = {
        "opId": _unique_id("OP", {"session": session.get("sessionId"), "seq": total + 1, "kind": kind, "target": target}, set()),
        "seq": total + 1, "at": _now(), "actor": "human", "kind": kind,
        "target": operation_target, "before": _clone(before), "after": _clone(after),
        "affectsSemanticHash": semantic,
    }
    operations.append(operation)
    _touch(session, semantic=semantic)
    return operation


def record_semantic_operation(session: dict[str, Any], kind: str, target: dict[str, Any], before: Any = None, after: Any = None) -> dict[str, Any]:
    return _append_operation(session, kind, target, before, after, semantic=True)


def record_layout_operation(session: dict[str, Any], kind: str, target: dict[str, Any], before: Any = None, after: Any = None) -> dict[str, Any]:
    return _append_operation(session, kind, target, before, after, semantic=False)


def _safe_store(store: str | os.PathLike[str]) -> Path:
    root = Path(store).resolve()
    if root.name == "sessions" and root.parent.name == "studio" and root.parent.parent.name == ".panorama-work":
        return root
    return root / SESSION_RELATIVE_DIR


def session_path(store: str | os.PathLike[str], session_id: str) -> Path:
    safe_id = _file_id(session_id, "sessionId")
    root = _safe_store(store)
    result = (root / f"{safe_id}.json").resolve()
    if result.parent != root:
        raise StudioSessionError("session path escapes the session store")
    return result


@contextmanager
def _session_lock(path: Path):
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SessionConflictError(f"session is locked: {path.name}") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _read_session(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_SESSION_BYTES:
            raise StudioSessionError("session file exceeds size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioSessionError(f"cannot read session {path}: {exc}") from exc
    errors = [item for item in validate_session(value) if item.get("level") == "error"]
    if errors:
        raise StudioSessionError(f"invalid session: {errors[0]['message']}")
    return value


def load_session(path_or_store: str | os.PathLike[str], session_id: str | None = None) -> dict[str, Any]:
    """Load either an explicit JSON path or an ID from a safe project store."""
    path = Path(path_or_store).resolve() if session_id is None else session_path(path_or_store, session_id)
    if session_id is None and path.suffix.lower() != ".json":
        raise StudioSessionError("explicit session path must end in .json")
    return _read_session(path)


def save_session(
    store: str | os.PathLike[str], session: dict[str, Any], expected_revision: int | None = None
) -> dict[str, Any]:
    """Atomically persist a session with optimistic ``sessionRevision`` CAS."""
    if not isinstance(session, dict):
        raise StudioSessionError("session must be an object")
    session_id = _file_id(session.get("sessionId"), "sessionId")
    path = session_path(store, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _session_lock(path):
        disk: dict[str, Any] | None = _read_session(path) if path.exists() else None
        disk_revision = disk.get("sessionRevision") if disk else None
        supplied_revision = session.get("sessionRevision")
        compare_revision = supplied_revision if expected_revision is None else expected_revision
        if disk is None:
            if compare_revision not in {None, 0}:
                raise SessionConflictError(f"expected session revision {compare_revision}, but session does not exist")
            next_revision = 1
        else:
            if compare_revision != disk_revision:
                raise SessionConflictError(f"expected session revision {compare_revision}, current revision is {disk_revision}")
            next_revision = int(disk_revision) + 1
        persisted = _clone(session)
        persisted["sessionRevision"] = next_revision
        persisted["updatedAt"] = _now()
        _refresh_hashes(persisted)
        persisted["clientValidation"] = validate_session(persisted)
        errors = [item for item in persisted["clientValidation"] if item.get("level") == "error"]
        if errors:
            raise StudioSessionError(f"invalid session: {errors[0]['message']}")
        rendered = json.dumps(persisted, ensure_ascii=False, indent=2) + "\n"
        if len(rendered.encode("utf-8")) > MAX_SESSION_BYTES:
            raise StudioSessionError("session file exceeds size limit")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
                handle.write(rendered); handle.flush(); os.fsync(handle.fileno()); temp_path = Path(handle.name)
            os.replace(temp_path, path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    session.clear(); session.update(_clone(persisted))
    return persisted


def _binding_matches(current: dict[str, Any], session: dict[str, Any]) -> None:
    binding = session.get("projectBinding", {})
    actual = {
        "projectId": current.get("project", {}).get("id"),
        "schemaVersion": str(current.get("schemaVersion", "")),
        "templateVersion": str(current.get("meta", {}).get("templateVersion", "")),
        "baseRevision": current.get("meta", {}).get("revision"),
        "baseDataHash": compute_data_hash(current),
        "gitHead": _source_binding(current).get("gitHead"),
        "sourceSnapshotHash": _source_binding(current).get("sourceSnapshotHash"),
    }
    for key, value in actual.items():
        if binding.get(key) != value:
            raise SessionConflictError(f"project binding mismatch for {key}: session={binding.get(key)!r}, current={value!r}")


def _module_design(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "responsibilities": _clone(node.get("responsibilities", [])),
        "nonResponsibilities": _clone(node.get("nonResponsibilities", [])),
        "technologies": _clone(node.get("technologies", [])),
        "stateOwnership": str(node.get("stateOwnership", "")),
        "dataHandled": _clone(node.get("dataHandled", [])),
        "interfaceSummary": _clone(node.get("interfaceSummary", [])),
        "deploymentRole": str(node.get("deploymentRole", "")),
        "referenceIds": _clone(node.get("referenceIds", [])),
        "notes": str(node.get("notes", "")), "extensions": _clone(node.get("designExtensions", {})),
    }


def _updated_module_design(module: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Overlay editable draft fields while retaining all formal/unknown fields."""
    target_design = module.get("targetDesign")
    current_design = module.get("currentDesign")
    original = (
        target_design
        if isinstance(target_design, dict)
        else current_design if isinstance(current_design, dict) else None
    )
    design = _clone(original) if isinstance(original, dict) else _module_design(node)
    mapped = {
        "responsibilities": "responsibilities",
        "nonResponsibilities": "nonResponsibilities",
        "technologies": "technologies",
        "stateOwnership": "stateOwnership",
        "dataHandled": "dataHandled",
        "interfaceSummary": "interfaceSummary",
        "deploymentRole": "deploymentRole",
        "referenceIds": "referenceIds",
        "notes": "notes",
    }
    for node_key, design_key in mapped.items():
        if node_key in node:
            design[design_key] = _clone(node[node_key])
    if "designExtensions" in node:
        design["extensions"] = _clone(node["designExtensions"])
    return design


def _new_module(node: dict[str, Any], module_id: str, now: str) -> dict[str, Any]:
    category = node.get("category") if node.get("category") in {"core", "supporting", "integration", "data", "infrastructure", "external"} else "supporting"
    return {
        "id": module_id, "name": str(node.get("name", "")).strip(), "layerId": node.get("layerId"),
        "architectureScope": "target", "targetLayerId": None, "category": category,
        "purpose": str(node.get("purpose", "")), "rationale": str(node.get("rationale", "Architecture Studio draft candidate.")),
        "requirementIds": _clone(node.get("requirementIds", [])),
        "source": {"origin": "custom", "baselineId": None, "changeType": "added", "rationale": "Architecture Studio draft candidate.", "changedAreas": [], "upgradeImpact": "unknown"},
        "currentDesign": None, "targetDesign": _module_design(node),
        "status": {"designMaturity": "draft", "implementationMaturity": "not_started", "verificationStatus": "pending", "runtimeStatus": "not_deployed"},
        "codePath": "", "decisionIds": [], "riskIds": [], "acceptanceCriteriaIds": [], "gateIds": [], "workItemIds": [], "reviewIds": [],
        "createdAt": now, "updatedAt": now, "extensions": _clone(node.get("extensions", {})),
    }


def _new_connection(edge: dict[str, Any], connection_id: str, from_id: str, to_id: str) -> dict[str, Any]:
    communication = edge.get("communicationMode") if edge.get("communicationMode") in {"sync", "async", "event", "stream", "batch", "direct"} else "direct"
    return {
        "id": connection_id, "name": str(edge.get("name") or edge.get("label") or f"{from_id} to {to_id}"),
        "fromModuleId": from_id, "toModuleId": to_id, "architectureScope": "target", "lifecycle": "planned",
        "flowDirection": edge.get("flowDirection") if edge.get("flowDirection") in {"one_way", "bidirectional"} else "one_way",
        "protocol": str(edge.get("protocol") or edge.get("label") or ""), "communicationMode": communication,
        "dataSummary": str(edge.get("dataSummary", "")), "contractReferenceIds": _clone(edge.get("contractReferenceIds", [])),
        "authSummary": str(edge.get("authSummary", "")), "reliabilitySummary": str(edge.get("reliabilitySummary", "")),
        "rationale": str(edge.get("rationale", "Architecture Studio draft candidate.")), "transitionId": None,
        "extensions": _clone(edge.get("extensions", {})),
    }


def candidate_to_panorama(current: dict[str, Any], session: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    """Project one candidate into Panorama data without deleting formal entities."""
    _binding_matches(current, session)
    candidate = _candidate(session, candidate_id)
    if candidate.get("status") == "archived":
        raise StudioSessionError("cannot materialize an archived candidate")
    errors = [item for item in validate_session(session) if item.get("level") == "error" and item.get("candidateId") in {None, candidate_id}]
    if errors:
        raise StudioSessionError(f"session client validation failed: {errors[0]['message']}")
    result = _clone(current)
    architecture = result["architecture"]
    modules = architecture["modules"]
    connections = architecture["connections"]
    module_by_id = {item.get("id"): item for item in modules}
    connection_by_id = {item.get("id"): item for item in connections}
    occupied = set(module_by_id) | set(connection_by_id)
    # Materialization is a pure projection of a frozen session. Repeating it
    # must yield byte-identical candidate data and proposal operations, so use
    # the recorded candidate/session time instead of a new wall-clock value.
    now = candidate.get("updatedAt") or session.get("updatedAt")
    if not isinstance(now, str) or not now:
        raise StudioSessionError("candidate materialization timestamp is missing")
    node_module: dict[str, str] = {}
    for node in candidate.get("nodes", []):
        ref = node.get("entityRef")
        if isinstance(ref, dict):
            if ref.get("type") != "module" or ref.get("id") not in module_by_id:
                raise StudioSessionError(f"node {node.get('nodeId')} has an invalid formal module reference")
            module_id = ref["id"]
            module = module_by_id[module_id]
            before = _clone(module)
            target_design = module.get("targetDesign")
            current_design = module.get("currentDesign")
            effective_design = (
                target_design
                if isinstance(target_design, dict)
                else current_design if isinstance(current_design, dict) else None
            )
            desired_design = _updated_module_design(module, node)
            module["name"] = str(node.get("name", module.get("name", ""))).strip()
            module["purpose"] = str(node.get("purpose", module.get("purpose", "")))
            module["category"] = node.get("category", module.get("category"))
            baseline_layer_id = node.get(
                "baselineLayerId", module.get("targetLayerId") or module.get("layerId")
            )
            if node.get("layerId") != baseline_layer_id:
                module["targetLayerId"] = (
                    node.get("layerId")
                    if node.get("layerId") != module.get("layerId")
                    else None
                )
            for key in ("rationale", "requirementIds"):
                if key in node:
                    module[key] = _clone(node[key])
            if desired_design != effective_design:
                module["targetDesign"] = desired_design
            if module != before:
                if module.get("architectureScope") == "current":
                    module["architectureScope"] = "both"
                module["updatedAt"] = now
        else:
            module_id = _unique_id("MOD-STUDIO", {"session": session.get("sessionId"), "candidate": candidate_id, "node": _semantic_projection({"nodes": [node], "edges": []})}, occupied)
            module = _new_module(node, module_id, now)
            modules.append(module); module_by_id[module_id] = module
        node_module[node["nodeId"]] = module_id
    for edge in candidate.get("edges", []):
        from_id = node_module.get(edge.get("fromNodeId")); to_id = node_module.get(edge.get("toNodeId"))
        if not from_id or not to_id:
            raise StudioSessionError(f"edge {edge.get('edgeId')} has an invalid endpoint")
        ref = edge.get("entityRef")
        if isinstance(ref, dict):
            if ref.get("type") != "connection" or ref.get("id") not in connection_by_id:
                raise StudioSessionError(f"edge {edge.get('edgeId')} has an invalid formal connection reference")
            connection = connection_by_id[ref["id"]]
            connection["fromModuleId"] = from_id; connection["toModuleId"] = to_id
            if "name" in edge:
                connection["name"] = str(edge["name"])
            # Existing entities are updated only from explicit semantic fields.
            # `label` is display data and may have been derived from
            # communicationMode, so using it as a protocol fallback would
            # rewrite an untouched formal connection.
            if "protocol" in edge:
                connection["protocol"] = str(edge["protocol"])
            if "dataSummary" in edge:
                connection["dataSummary"] = str(edge["dataSummary"])
            for key in (
                "flowDirection", "communicationMode", "contractReferenceIds",
                "authSummary", "reliabilitySummary", "rationale", "transitionId",
            ):
                if key in edge:
                    connection[key] = _clone(edge[key])
            if "extensions" in edge:
                connection["extensions"] = _clone(edge["extensions"])
            if connection.get("architectureScope") == "current":
                connection["architectureScope"] = "both"
        else:
            connection_id = _unique_id("CONN-STUDIO", {"session": session.get("sessionId"), "candidate": candidate_id, "edge": _clone(edge), "from": from_id, "to": to_id}, occupied)
            connection = _new_connection(edge, connection_id, from_id, to_id)
            connections.append(connection); connection_by_id[connection_id] = connection
    return result


def _pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result: list[dict[str, Any]] = []
        for key in sorted(before.keys() - after.keys(), reverse=True):
            result.append({"op": "remove", "path": f"{path}/{_pointer(key)}"})
        for key in sorted(after.keys() - before.keys()):
            result.append({"op": "add", "path": f"{path}/{_pointer(key)}", "value": _clone(after[key])})
        for key in sorted(before.keys() & after.keys()):
            result.extend(_diff(before[key], after[key], f"{path}/{_pointer(key)}"))
        return result
    if isinstance(before, list) and isinstance(after, list):
        # Entity arrays are replaced as one value: deterministic and does not
        # expose delete operations for missing candidate entities.
        return [{"op": "replace", "path": path, "value": _clone(after)}]
    return [{"op": "replace", "path": path, "value": _clone(after)}]


def formal_validate_candidate(
    current: dict[str, Any], session: dict[str, Any], candidate_id: str,
    schema_path: str | os.PathLike[str] | None, base_dir: str | os.PathLike[str],
    source_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Materialize, reuse the formal Validator, and return a proposal-ready diff."""
    candidate_data = candidate_to_panorama(current, session, candidate_id)
    report = validate_data(candidate_data, schema_path, base_dir=base_dir, source_path=source_path)
    result = {
        "candidateData": candidate_data,
        "operations": _diff(current, candidate_data),
        "validation": report.to_dict(),
        "semanticHash": semantic_hash(_candidate(session, candidate_id)),
    }
    session["formalValidation"] = {
        "candidateId": candidate_id, "semanticHash": result["semanticHash"],
        "validatedAt": _now(), "validation": _clone(result["validation"]),
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage isolated Panorama Architecture Studio sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create"); create.add_argument("panorama", type=Path); create.add_argument("--project-root", type=Path); create.add_argument("--store", type=Path, default=Path("."))
    validate = sub.add_parser("validate"); validate.add_argument("session", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            source = args.panorama
            data = extract_data(source) if source.suffix.lower() in {".html", ".htm"} else json.loads(source.read_text(encoding="utf-8"))
            session = create_session(data, args.project_root)
            saved = save_session(args.store, session, expected_revision=0)
            print(json.dumps({"sessionId": saved["sessionId"], "sessionRevision": saved["sessionRevision"], "path": str(session_path(args.store, saved["sessionId"]))}, ensure_ascii=False))
            return 0
        findings = validate_session(load_session(args.session))
        print(json.dumps({"valid": not any(item["level"] == "error" for item in findings), "findings": findings}, ensure_ascii=False, indent=2))
        return 1 if any(item["level"] == "error" for item in findings) else 0
    except (StudioSessionError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SESSION_FORMAT", "StudioSessionError", "SessionConflictError",
    "create_session", "validate_session", "semantic_hash", "layout_hash",
    "load_session", "save_session", "session_path", "candidate_to_panorama",
    "formal_validate_candidate", "create_candidate", "clone_candidate",
    "rename_candidate", "archive_candidate", "select_candidate",
    "record_semantic_operation", "record_layout_operation",
]
