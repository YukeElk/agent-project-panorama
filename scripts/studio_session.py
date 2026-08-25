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
LAYER_ASSIGNMENTS_FORMAT = "panorama-layer-assignments.v0.1"
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


def _candidate_layer_assignment(
    module: dict[str, Any], effective_layer_id: str
) -> dict[str, Any]:
    extensions = module.get("extensions", {})
    assignment_set = (
        extensions.get("layerAssignments", {})
        if isinstance(extensions, dict)
        else {}
    )
    raw = None
    if isinstance(assignment_set, dict):
        raw = assignment_set.get("target") or assignment_set.get("current")
    if not isinstance(raw, dict):
        raw = {}
    return {
        "layerId": effective_layer_id,
        "basis": raw.get("basis", "unknown"),
        "rationale": str(raw.get("rationale", "")),
        "evidenceReferenceIds": _clone(raw.get("evidenceReferenceIds", [])),
        "confidence": raw.get("confidence", "unknown"),
        "alternativeLayerIds": _clone(raw.get("alternativeLayerIds", [])),
    }


def _module_extensions_with_target_assignment(
    module: dict[str, Any], assignment: dict[str, Any]
) -> dict[str, Any]:
    extensions = _clone(module.get("extensions", {}))
    if not isinstance(extensions, dict):
        extensions = {}
    assignment_set = extensions.get("layerAssignments")
    if not isinstance(assignment_set, dict):
        assignment_set = {}
    else:
        assignment_set = _clone(assignment_set)
    assignment_set["formatVersion"] = LAYER_ASSIGNMENTS_FORMAT
    assignment_set["target"] = {
        "layerId": assignment.get("layerId"),
        "basis": assignment.get("basis", "unknown"),
        "rationale": str(assignment.get("rationale", "")),
        "evidenceReferenceIds": _clone(assignment.get("evidenceReferenceIds", [])),
        "confidence": assignment.get("confidence", "unknown"),
        "alternativeLayerIds": _clone(assignment.get("alternativeLayerIds", [])),
    }
    extensions["layerAssignments"] = assignment_set
    return extensions


def _baseline_module_logic_canvases(
    data: dict[str, Any], module_ids: set[str], now: str
) -> list[dict[str, Any]]:
    """Copy formal Target Module Logic into an isolated Studio candidate.

    Current observations deliberately never enter the Studio session.  Formal
    identities are retained through ``entityRef`` while layout is initialized
    locally, so an untouched candidate materializes byte-for-byte.
    """

    designs = data.get("architecture", {}).get("moduleLogicDesigns", [])
    canvases: list[dict[str, Any]] = []
    for design in designs if isinstance(designs, list) else []:
        if (
            not isinstance(design, dict)
            or design.get("architectureScope") != "target"
            or design.get("moduleId") not in module_ids
        ):
            continue
        design_id = _id(design.get("id"), "moduleLogicDesign.id")
        nodes = []
        node_ids: set[str] = set()
        for index, node in enumerate(design.get("nodes", [])):
            logic_id = _id(node.get("id"), "moduleLogicNode.id")
            node_ids.add(logic_id)
            nodes.append(
                {
                    "nodeId": _slug(logic_id, "NODE"),
                    "entityRef": {"type": "logic_node", "id": logic_id},
                    "name": node.get("name", logic_id),
                    "displayName": node.get("displayName"),
                    "type": node.get("type", "processing"),
                    "purpose": node.get("purpose", ""),
                    "rationale": node.get("rationale", ""),
                    "implementationSummary": node.get("implementationSummary", ""),
                    "loopExitCondition": node.get("loopExitCondition"),
                    "requirementIds": _clone(node.get("requirementIds", [])),
                    "decisionIds": _clone(node.get("decisionIds", [])),
                    "riskIds": _clone(node.get("riskIds", [])),
                    "referenceIds": _clone(node.get("referenceIds", [])),
                    "isDraft": False,
                    "x": 160 + (index % 3) * 230,
                    "y": 100 + (index // 3) * 150,
                    "extensions": _clone(node.get("extensions", {})),
                }
            )
        local_node = {
            item["entityRef"]["id"]: item["nodeId"] for item in nodes
        }
        edges = []
        for edge in design.get("edges", []):
            edge_id = _id(edge.get("id"), "moduleLogicEdge.id")
            edges.append(
                {
                    "edgeId": _slug(edge_id, "EDGE"),
                    "entityRef": {"type": "logic_edge", "id": edge_id},
                    "fromNodeId": local_node.get(edge.get("fromNodeId"), ""),
                    "toNodeId": local_node.get(edge.get("toNodeId"), ""),
                    "kind": edge.get("kind", "flow"),
                    "condition": edge.get("condition", ""),
                    "dataSummary": edge.get("dataSummary", ""),
                    "order": edge.get("order"),
                    "referenceIds": _clone(edge.get("referenceIds", [])),
                    "isDraft": False,
                    "extensions": _clone(edge.get("extensions", {})),
                }
            )
        ports = []
        external_refs: list[dict[str, str]] = []
        seen_external: set[tuple[str, str]] = set()
        for index, port in enumerate(design.get("boundaryPorts", [])):
            port_id = _id(port.get("id"), "moduleLogicBoundaryPort.id")
            external = _clone(port.get("externalEntityRef", {}))
            key = (str(external.get("type", "")), str(external.get("id", "")))
            if key not in seen_external:
                seen_external.add(key)
                external_refs.append(external)
            ports.append(
                {
                    "portId": _slug(port_id, "PORT"),
                    "entityRef": {"type": "boundary_port", "id": port_id},
                    "name": port.get("name", port_id),
                    "direction": port.get("direction", "input"),
                    "internalNodeId": local_node.get(port.get("internalNodeId"), ""),
                    "externalEntityRef": external,
                    "bindingKind": port.get("bindingKind", "connection"),
                    "bindingId": port.get("bindingId"),
                    "dataSummary": port.get("dataSummary", ""),
                    "protocol": port.get("protocol", ""),
                    "referenceIds": _clone(port.get("referenceIds", [])),
                    "isDraft": False,
                    "x": 60 if port.get("direction") == "input" else 900,
                    "y": 100 + index * 90,
                    "extensions": _clone(port.get("extensions", {})),
                }
            )
        canvases.append(
            {
                "canvasId": _slug(design_id, "CANVAS"),
                "rootModuleId": design["moduleId"],
                "architectureScope": "target",
                "currentObservationBinding": None,
                "baseTargetDesignId": design_id,
                "name": design.get("name", design_id),
                "displayName": design.get("displayName"),
                "summary": design.get("summary", ""),
                "requirementIds": _clone(design.get("requirementIds", [])),
                "decisionIds": _clone(design.get("decisionIds", [])),
                "riskIds": _clone(design.get("riskIds", [])),
                "acceptanceCriteriaIds": _clone(
                    design.get("acceptanceCriteriaIds", [])
                ),
                "gateIds": _clone(design.get("gateIds", [])),
                "referenceIds": _clone(design.get("referenceIds", [])),
                "nodes": nodes,
                "edges": edges,
                "boundaryPorts": ports,
                "externalEntityRefs": external_refs,
                "createdAt": now,
                "updatedAt": now,
                "extensions": _clone(design.get("extensions", {})),
            }
        )
    return canvases


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
        layer_assignment = _candidate_layer_assignment(module, effective_layer_id)
        nodes.append({
            "nodeId": node_id,
            "entityRef": {"type": "module", "id": module_id},
            "name": module.get("name", module_id),
            "displayName": module.get("displayName"),
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
            "layerAssignment": layer_assignment,
            "baselineLayerAssignment": _clone(layer_assignment),
            "category": module.get("category", "supporting"),
            "isDraft": False,
            "x": 145 + (slot % 3) * 205,
            "y": 24 + layer_index.get(layer_id, 0) * 142 + (slot // 3) * 96,
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
    candidate = {
        "candidateId": "CANDIDATE-BASE", "label": "Target architecture copy",
        "kind": "baseline_copy", "status": "active", "assumptions": [], "unknowns": [],
        "architectureLayers": _clone(layers),
        "nodes": nodes, "edges": edges, "createdAt": now, "updatedAt": now,
    }
    module_ids = set(node_for_module)
    canvases = _baseline_module_logic_canvases(data, module_ids, now)
    if canvases:
        candidate["moduleLogicCanvases"] = canvases
    return candidate


def _semantic_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    nodes = []
    for node in candidate.get("nodes", []):
        nodes.append({
            key: _clone(value) for key, value in node.items()
            if key not in {"x", "y", "selected", "collapsed", "width", "height", "baselineLayerAssignment"}
        })
    edges = [_clone(item) for item in candidate.get("edges", [])]
    layers = []
    for layer in candidate.get("architectureLayers", []):
        layers.append({
            key: _clone(value) for key, value in layer.items()
            if key not in {"layoutHeight", "selected", "collapsed"}
        })
    nodes.sort(key=lambda item: str(item.get("nodeId", "")))
    edges.sort(key=lambda item: str(item.get("edgeId", "")))
    layers.sort(key=lambda item: (int(item.get("order", 0)), str(item.get("id", ""))))
    projection = {
        "assumptions": _clone(candidate.get("assumptions", [])),
        "unknowns": _clone(candidate.get("unknowns", [])),
        "architectureLayers": layers,
        "nodes": nodes, "edges": edges,
    }
    if "moduleLogicCanvases" in candidate:
        canvases = []
        for canvas in candidate.get("moduleLogicCanvases", []):
            projected_canvas = {
                key: _clone(value)
                for key, value in canvas.items()
                if key not in {"viewerState", "camera", "createdAt", "updatedAt"}
            }
            projected_canvas["nodes"] = sorted(
                [
                    {
                        key: _clone(value)
                        for key, value in node.items()
                        if key
                        not in {
                            "x", "y", "selected", "collapsed", "width", "height"
                        }
                    }
                    for node in canvas.get("nodes", [])
                ],
                key=lambda item: str(item.get("nodeId", "")),
            )
            projected_canvas["boundaryPorts"] = sorted(
                [
                    {
                        key: _clone(value)
                        for key, value in port.items()
                        if key not in {"x", "y", "selected"}
                    }
                    for port in canvas.get("boundaryPorts", [])
                ],
                key=lambda item: str(item.get("portId", "")),
            )
            projected_canvas["edges"] = sorted(
                [_clone(edge) for edge in canvas.get("edges", [])],
                key=lambda item: str(item.get("edgeId", "")),
            )
            canvases.append(projected_canvas)
        projection["moduleLogicCanvases"] = sorted(
            canvases, key=lambda item: str(item.get("canvasId", ""))
        )
    return projection


def _layout_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    projection = {
        "nodes": sorted(
            [
                {"nodeId": item.get("nodeId"), "x": item.get("x", 0), "y": item.get("y", 0)}
                for item in candidate.get("nodes", [])
            ], key=lambda item: str(item.get("nodeId", "")),
        )
    }
    if "moduleLogicCanvases" in candidate:
        projection["moduleLogicCanvases"] = sorted(
            [
                {
                    "canvasId": canvas.get("canvasId"),
                    "nodes": sorted(
                        [
                            {
                                "nodeId": item.get("nodeId"),
                                "x": item.get("x", 0),
                                "y": item.get("y", 0),
                            }
                            for item in canvas.get("nodes", [])
                        ],
                        key=lambda item: str(item.get("nodeId", "")),
                    ),
                    "boundaryPorts": sorted(
                        [
                            {
                                "portId": item.get("portId"),
                                "x": item.get("x", 0),
                                "y": item.get("y", 0),
                            }
                            for item in canvas.get("boundaryPorts", [])
                        ],
                        key=lambda item: str(item.get("portId", "")),
                    ),
                }
                for canvas in candidate.get("moduleLogicCanvases", [])
            ],
            key=lambda item: str(item.get("canvasId", "")),
        )
    return projection


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
        "name", "displayName", "purpose", "responsibilities", "nonResponsibilities",
        "stateOwnership", "layerId", "category", "technologies", "dataHandled",
        "interfaceSummary", "deploymentRole", "referenceIds", "notes",
        "designExtensions", "rationale", "requirementIds", "layerAssignment", "isDraft",
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
    def logic_canvases(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
        projected = _semantic_projection(candidate).get("moduleLogicCanvases", [])
        return {
            (
                f"design:{item.get('baseTargetDesignId')}"
                if item.get("baseTargetDesignId")
                else f"module:{item.get('rootModuleId')}"
            ): item
            for item in projected
        }

    logic_fields = (
        "rootModuleId", "architectureScope", "currentObservationBinding",
        "baseTargetDesignId", "name", "displayName", "summary",
        "requirementIds", "decisionIds", "riskIds", "acceptanceCriteriaIds",
        "gateIds", "referenceIds", "nodes", "edges", "boundaryPorts",
        "externalEntityRefs", "extensions",
    )
    logic_diff = entity_diff(
        logic_canvases(base_candidate), logic_canvases(compare_candidate), logic_fields
    )
    candidate_changes = _semantic_field_changes(
        base_candidate, compare_candidate, ("assumptions", "unknowns", "architectureLayers")
    )
    summary = {
        "candidateFieldsModified": len(candidate_changes),
        "nodesAdded": len(node_diff["added"]),
        "nodesRemoved": len(node_diff["removed"]),
        "nodesModified": len(node_diff["modified"]),
        "edgesAdded": len(edge_diff["added"]),
        "edgesRemoved": len(edge_diff["removed"]),
        "edgesModified": len(edge_diff["modified"]),
        "moduleLogicCanvasesAdded": len(logic_diff["added"]),
        "moduleLogicCanvasesRemoved": len(logic_diff["removed"]),
        "moduleLogicCanvasesModified": len(logic_diff["modified"]),
    }
    return {
        "baseCandidateId": base_candidate.get("candidateId"),
        "compareCandidateId": compare_candidate.get("candidateId"),
        "candidateFields": candidate_changes,
        "nodes": node_diff,
        "edges": edge_diff,
        "moduleLogicCanvases": logic_diff,
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
        layers = candidate.get("architectureLayers", session.get("layers", []))
        layer_ids: set[str] = set()
        layer_parent: dict[str, str] = {}
        for layer_index, layer in enumerate(layers):
            layer_id = layer.get("id")
            if layer_id in layer_ids:
                findings.append(_finding("error", "CLIENT_DUPLICATE_LAYER", f"duplicate layer ID: {layer_id}", f"/candidates/{index}/architectureLayers/{layer_index}/id", candidate_id))
            layer_ids.add(layer_id)
        for layer_index, layer in enumerate(layers):
            layer_id = layer.get("id")
            parent_id = layer.get("parentLayerId")
            if parent_id is not None and parent_id not in layer_ids:
                findings.append(_finding("error", "CLIENT_LAYER_PARENT", f"{layer_id} has an unknown parent layer: {parent_id}", f"/candidates/{index}/architectureLayers/{layer_index}/parentLayerId", candidate_id))
            if isinstance(layer_id, str) and isinstance(parent_id, str):
                layer_parent[layer_id] = parent_id
        for layer_id in sorted(layer_parent):
            visited: set[str] = set()
            current = layer_id
            while current in layer_parent:
                if current in visited:
                    findings.append(_finding("error", "CLIENT_LAYER_CYCLE", f"layer hierarchy contains a cycle from {layer_id}", f"/candidates/{index}/architectureLayers", candidate_id))
                    break
                visited.add(current)
                current = layer_parent[current]
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
            if node.get("layerId") not in layer_ids:
                findings.append(_finding("error", "CLIENT_NODE_LAYER", f"{node_id} references an unknown layer: {node.get('layerId')}", f"/candidates/{index}/nodes/{node_index}/layerId", candidate_id))
            assignment = node.get("layerAssignment")
            if assignment is not None:
                assignment_path = f"/candidates/{index}/nodes/{node_index}/layerAssignment"
                if not isinstance(assignment, dict):
                    findings.append(_finding("error", "CLIENT_LAYER_ASSIGNMENT", f"{node_id} layerAssignment must be an object", assignment_path, candidate_id))
                else:
                    if assignment.get("layerId") != node.get("layerId"):
                        findings.append(_finding("error", "CLIENT_LAYER_ASSIGNMENT", f"{node_id} layerAssignment does not match node.layerId", f"{assignment_path}/layerId", candidate_id))
                    if assignment.get("basis") not in {"responsibility", "capability", "interface", "state_ownership", "deployment", "security", "data_ownership", "independent_evolution", "explicit_design_decision", "unknown"}:
                        findings.append(_finding("error", "CLIENT_LAYER_ASSIGNMENT", f"{node_id} has an invalid layer assignment basis", f"{assignment_path}/basis", candidate_id))
                    if assignment.get("confidence") not in {"high", "medium", "low", "unknown"}:
                        findings.append(_finding("error", "CLIENT_LAYER_ASSIGNMENT", f"{node_id} has an invalid layer assignment confidence", f"{assignment_path}/confidence", candidate_id))
                    if not str(assignment.get("rationale", "")).strip() and (
                        node.get("isDraft") is True
                        or assignment != node.get("baselineLayerAssignment")
                    ):
                        findings.append(_finding("warning", "CLIENT_LAYER_ASSIGNMENT_RATIONALE", f"{node_id} has no layer assignment rationale", f"{assignment_path}/rationale", candidate_id))
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
        formal_module_ids = {
            node.get("entityRef", {}).get("id")
            for node in candidate.get("nodes", [])
            if isinstance(node.get("entityRef"), dict)
            and node.get("entityRef", {}).get("type") == "module"
        }
        valid_root_ids = formal_module_ids | node_ids
        canvas_ids: set[str] = set()
        canvas_roots: set[str] = set()
        for canvas_index, canvas in enumerate(candidate.get("moduleLogicCanvases", [])):
            canvas_id = canvas.get("canvasId")
            canvas_path = f"/candidates/{index}/moduleLogicCanvases/{canvas_index}"
            if canvas_id in canvas_ids:
                findings.append(_finding("error", "CLIENT_DUPLICATE_LOGIC_CANVAS", f"duplicate Module Logic canvas ID: {canvas_id}", f"{canvas_path}/canvasId", candidate_id))
            canvas_ids.add(canvas_id)
            root_id = canvas.get("rootModuleId")
            if root_id in canvas_roots:
                findings.append(_finding("error", "CLIENT_DUPLICATE_LOGIC_ROOT", f"module {root_id} has more than one Target Module Logic canvas", f"{canvas_path}/rootModuleId", candidate_id))
            canvas_roots.add(root_id)
            if root_id not in valid_root_ids:
                findings.append(_finding("error", "CLIENT_LOGIC_ROOT", f"Module Logic root does not exist in the candidate: {root_id}", f"{canvas_path}/rootModuleId", candidate_id))
            observation = canvas.get("currentObservationBinding")
            if isinstance(observation, dict) and observation.get("currentness") != "match":
                findings.append(_finding("warning", "CLIENT_LOGIC_OBSERVATION_STALE", f"Module Logic source observation is {observation.get('currentness')}", f"{canvas_path}/currentObservationBinding/currentness", candidate_id))

            logic_node_ids: set[str] = set()
            logic_formal_ids: set[str] = set()
            loop_ids: set[str] = set()
            loop_back_ids: set[str] = set()
            for logic_index, logic_node in enumerate(canvas.get("nodes", [])):
                logic_id = logic_node.get("nodeId")
                if logic_id in logic_node_ids:
                    findings.append(_finding("error", "CLIENT_DUPLICATE_LOGIC_NODE", f"duplicate Module Logic node ID: {logic_id}", f"{canvas_path}/nodes/{logic_index}/nodeId", candidate_id))
                logic_node_ids.add(logic_id)
                entity_ref = logic_node.get("entityRef")
                if isinstance(entity_ref, dict):
                    formal_id = entity_ref.get("id")
                    if formal_id in logic_formal_ids:
                        findings.append(_finding("error", "CLIENT_DUPLICATE_LOGIC_ENTITY", f"duplicate formal Module Logic node reference: {formal_id}", f"{canvas_path}/nodes/{logic_index}/entityRef/id", candidate_id))
                    logic_formal_ids.add(formal_id)
                if logic_node.get("type") == "loop":
                    loop_ids.add(logic_id)
                    if not str(logic_node.get("loopExitCondition", "")).strip():
                        findings.append(_finding("error", "CLIENT_LOGIC_LOOP_EXIT", f"Loop node {logic_id} requires an exit condition", f"{canvas_path}/nodes/{logic_index}/loopExitCondition", candidate_id))
                elif logic_node.get("loopExitCondition") is not None:
                    findings.append(_finding("error", "CLIENT_LOGIC_NON_LOOP_EXIT", f"Non-loop node {logic_id} cannot declare a loop exit condition", f"{canvas_path}/nodes/{logic_index}/loopExitCondition", candidate_id))

            logic_edge_ids: set[str] = set()
            logic_edge_formal_ids: set[str] = set()
            for logic_edge_index, logic_edge in enumerate(canvas.get("edges", [])):
                logic_edge_id = logic_edge.get("edgeId")
                edge_path = f"{canvas_path}/edges/{logic_edge_index}"
                if logic_edge_id in logic_edge_ids:
                    findings.append(_finding("error", "CLIENT_DUPLICATE_LOGIC_EDGE", f"duplicate Module Logic edge ID: {logic_edge_id}", f"{edge_path}/edgeId", candidate_id))
                logic_edge_ids.add(logic_edge_id)
                entity_ref = logic_edge.get("entityRef")
                if isinstance(entity_ref, dict):
                    formal_id = entity_ref.get("id")
                    if formal_id in logic_edge_formal_ids:
                        findings.append(_finding("error", "CLIENT_DUPLICATE_LOGIC_EDGE_ENTITY", f"duplicate formal Module Logic edge reference: {formal_id}", f"{edge_path}/entityRef/id", candidate_id))
                    logic_edge_formal_ids.add(formal_id)
                from_id = logic_edge.get("fromNodeId")
                to_id = logic_edge.get("toNodeId")
                if from_id not in logic_node_ids or to_id not in logic_node_ids:
                    findings.append(_finding("error", "CLIENT_LOGIC_EDGE_ENDPOINT", f"Module Logic edge {logic_edge_id} has a missing endpoint", edge_path, candidate_id))
                if from_id == to_id:
                    findings.append(_finding("error", "CLIENT_LOGIC_SELF_EDGE", f"Module Logic edge {logic_edge_id} is a self-loop", edge_path, candidate_id))
                if logic_edge.get("kind") == "loop_back":
                    touching = {from_id, to_id} & loop_ids
                    if not touching:
                        findings.append(_finding("error", "CLIENT_LOGIC_LOOP_BACK", f"loop_back edge {logic_edge_id} does not touch a Loop node", f"{edge_path}/kind", candidate_id))
                    loop_back_ids.update(touching)
            for loop_id in sorted(loop_ids - loop_back_ids):
                findings.append(_finding("error", "CLIENT_LOGIC_LOOP_BACK_MISSING", f"Loop node {loop_id} requires a loop_back edge", f"{canvas_path}/edges", candidate_id))

            external_keys = {
                (item.get("type"), item.get("id"))
                for item in canvas.get("externalEntityRefs", [])
                if isinstance(item, dict)
            }
            port_ids: set[str] = set()
            for port_index, port in enumerate(canvas.get("boundaryPorts", [])):
                port_id = port.get("portId")
                port_path = f"{canvas_path}/boundaryPorts/{port_index}"
                if port_id in port_ids:
                    findings.append(_finding("error", "CLIENT_DUPLICATE_LOGIC_PORT", f"duplicate boundary port ID: {port_id}", f"{port_path}/portId", candidate_id))
                port_ids.add(port_id)
                if port.get("internalNodeId") not in logic_node_ids:
                    findings.append(_finding("error", "CLIENT_LOGIC_PORT_NODE", f"boundary port {port_id} references a missing internal node", f"{port_path}/internalNodeId", candidate_id))
                external_ref = port.get("externalEntityRef", {})
                external_key = (external_ref.get("type"), external_ref.get("id")) if isinstance(external_ref, dict) else (None, None)
                if external_key not in external_keys:
                    findings.append(_finding("error", "CLIENT_LOGIC_EXTERNAL_REF", f"boundary port {port_id} is missing its compact external reference", f"{port_path}/externalEntityRef", candidate_id))
                if external_key == ("module", root_id):
                    findings.append(_finding("error", "CLIENT_LOGIC_SELF_EXTERNAL_REF", f"boundary port {port_id} references its own root module", f"{port_path}/externalEntityRef/id", candidate_id))
                binding_kind = port.get("bindingKind")
                binding_id = port.get("bindingId")
                if binding_kind == "unbound_candidate" and binding_id is not None:
                    findings.append(_finding("error", "CLIENT_LOGIC_UNBOUND_ID", f"unbound candidate port {port_id} cannot carry a binding ID", f"{port_path}/bindingId", candidate_id))
                if binding_kind in {"connection", "resource_usage"} and not binding_id:
                    findings.append(_finding("error", "CLIENT_LOGIC_BINDING_MISSING", f"boundary port {port_id} requires a binding ID", f"{port_path}/bindingId", candidate_id))
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
        item = {"candidateId": candidate_id, "label": label.strip(), "kind": "alternative", "status": "active", "assumptions": [], "unknowns": [], "architectureLayers": _clone(session.get("layers", [])), "nodes": [], "edges": [], "createdAt": now, "updatedAt": now}
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
    result = {
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
    if isinstance(node.get("layerAssignment"), dict):
        result["extensions"] = _module_extensions_with_target_assignment(
            result, node["layerAssignment"]
        )
    if node.get("displayName"):
        result["displayName"] = str(node["displayName"]).strip()
    return result


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


def _occupied_formal_ids(data: dict[str, Any]) -> set[str]:
    occupied: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            item_id = value.get("id")
            if isinstance(item_id, str) and _ID_RE.fullmatch(item_id):
                occupied.add(item_id)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return occupied


def _logic_node_payload(
    node: dict[str, Any], formal_id: str
) -> dict[str, Any]:
    result = {
        "id": formal_id,
        "name": str(node.get("name", "")).strip(),
        "type": node.get("type", "processing"),
        "purpose": str(node.get("purpose", "")),
        "rationale": str(node.get("rationale", "")),
        "implementationSummary": str(node.get("implementationSummary", "")),
        "loopExitCondition": node.get("loopExitCondition"),
        "requirementIds": _clone(node.get("requirementIds", [])),
        "decisionIds": _clone(node.get("decisionIds", [])),
        "riskIds": _clone(node.get("riskIds", [])),
        "referenceIds": _clone(node.get("referenceIds", [])),
        "extensions": _clone(node.get("extensions", {})),
    }
    if node.get("displayName") is not None:
        result["displayName"] = str(node["displayName"])
    return result


def _materialize_module_logic_designs(
    architecture: dict[str, Any],
    resources: list[dict[str, Any]],
    candidate: dict[str, Any],
    session_id: str,
    candidate_id: str,
    node_module: dict[str, str],
    edge_connection: dict[str, str],
    occupied: set[str],
) -> None:
    canvases = candidate.get("moduleLogicCanvases", [])
    if not canvases and "moduleLogicDesigns" not in architecture:
        return
    designs = architecture.setdefault("moduleLogicDesigns", [])
    design_by_id = {
        item.get("id"): item for item in designs if isinstance(item, dict)
    }
    module_by_id = {
        item.get("id"): item
        for item in architecture.get("modules", [])
        if isinstance(item, dict)
    }
    connection_ids = {
        item.get("id")
        for item in architecture.get("connections", [])
        if isinstance(item, dict)
    }
    resource_ids = {
        item.get("id")
        for item in resources
        if isinstance(item, dict)
    }

    for canvas in canvases:
        canvas_id = canvas.get("canvasId")
        observation = canvas.get("currentObservationBinding")
        if isinstance(observation, dict) and observation.get("currentness") != "match":
            raise StudioSessionError(
                f"Module Logic canvas {canvas_id} is based on a "
                f"{observation.get('currentness')} Current observation"
            )
        root_token = canvas.get("rootModuleId")
        root_module_id = node_module.get(root_token, root_token)
        root_module = module_by_id.get(root_module_id)
        if root_module is None:
            raise StudioSessionError(
                f"Module Logic canvas {canvas_id} has an invalid root module"
            )

        base_id = canvas.get("baseTargetDesignId")
        base = design_by_id.get(base_id) if base_id else None
        if base_id and (
            base is None
            or base.get("architectureScope") != "target"
            or base.get("moduleId") != root_module_id
        ):
            raise StudioSessionError(
                f"Module Logic canvas {canvas_id} has an invalid base Target design"
            )
        if not canvas.get("nodes"):
            raise StudioSessionError(
                f"Module Logic canvas {canvas_id} must contain at least one logic node"
            )

        base_nodes = {
            item.get("id"): item for item in (base or {}).get("nodes", [])
        }
        base_edges = {
            item.get("id"): item for item in (base or {}).get("edges", [])
        }
        base_ports = {
            item.get("id"): item for item in (base or {}).get("boundaryPorts", [])
        }
        logic_node_ids: dict[str, str] = {}
        materialized_nodes: list[dict[str, Any]] = []
        for node in canvas.get("nodes", []):
            entity_ref = node.get("entityRef")
            if isinstance(entity_ref, dict):
                formal_id = entity_ref.get("id")
                if entity_ref.get("type") != "logic_node" or formal_id not in base_nodes:
                    raise StudioSessionError(
                        f"Module Logic node {node.get('nodeId')} has an invalid formal reference"
                    )
            else:
                formal_id = _unique_id(
                    "MLN-STUDIO",
                    {
                        "session": session_id,
                        "candidate": candidate_id,
                        "canvas": canvas_id,
                        "node": {
                            key: _clone(value)
                            for key, value in node.items()
                            if key not in {"x", "y", "selected", "collapsed", "width", "height"}
                        },
                    },
                    occupied,
                )
            logic_node_ids[node["nodeId"]] = formal_id
            materialized_nodes.append(_logic_node_payload(node, formal_id))

        materialized_edges: list[dict[str, Any]] = []
        for edge in canvas.get("edges", []):
            from_id = logic_node_ids.get(edge.get("fromNodeId"))
            to_id = logic_node_ids.get(edge.get("toNodeId"))
            if not from_id or not to_id:
                raise StudioSessionError(
                    f"Module Logic edge {edge.get('edgeId')} has an invalid endpoint"
                )
            entity_ref = edge.get("entityRef")
            if isinstance(entity_ref, dict):
                formal_id = entity_ref.get("id")
                if entity_ref.get("type") != "logic_edge" or formal_id not in base_edges:
                    raise StudioSessionError(
                        f"Module Logic edge {edge.get('edgeId')} has an invalid formal reference"
                    )
            else:
                formal_id = _unique_id(
                    "MLE-STUDIO",
                    {
                        "session": session_id,
                        "candidate": candidate_id,
                        "canvas": canvas_id,
                        "edge": {
                            key: _clone(value)
                            for key, value in edge.items()
                            if key not in {"selected"}
                        },
                        "from": from_id,
                        "to": to_id,
                    },
                    occupied,
                )
            materialized_edges.append(
                {
                    "id": formal_id,
                    "fromNodeId": from_id,
                    "toNodeId": to_id,
                    "kind": edge.get("kind", "flow"),
                    "condition": str(edge.get("condition", "")),
                    "dataSummary": str(edge.get("dataSummary", "")),
                    "order": edge.get("order"),
                    "referenceIds": _clone(edge.get("referenceIds", [])),
                    "extensions": _clone(edge.get("extensions", {})),
                }
            )

        materialized_ports: list[dict[str, Any]] = []
        for port in canvas.get("boundaryPorts", []):
            internal_id = logic_node_ids.get(port.get("internalNodeId"))
            if not internal_id:
                raise StudioSessionError(
                    f"Module Logic boundary port {port.get('portId')} has an invalid internal node"
                )
            entity_ref = port.get("entityRef")
            if isinstance(entity_ref, dict):
                formal_id = entity_ref.get("id")
                if entity_ref.get("type") != "boundary_port" or formal_id not in base_ports:
                    raise StudioSessionError(
                        f"Module Logic boundary port {port.get('portId')} has an invalid formal reference"
                    )
            else:
                formal_id = _unique_id(
                    "MLP-STUDIO",
                    {
                        "session": session_id,
                        "candidate": candidate_id,
                        "canvas": canvas_id,
                        "port": {
                            key: _clone(value)
                            for key, value in port.items()
                            if key not in {"x", "y", "selected"}
                        },
                    },
                    occupied,
                )
            binding_kind = port.get("bindingKind")
            binding_token = port.get("bindingId")
            if binding_kind == "unbound_candidate":
                raise StudioSessionError(
                    f"Module Logic boundary port {port.get('portId')} is not bound to a formal Candidate connection"
                )
            if binding_kind == "connection":
                binding_id = edge_connection.get(binding_token, binding_token)
                if binding_id not in connection_ids:
                    raise StudioSessionError(
                        f"Module Logic boundary port {port.get('portId')} references an unknown Candidate connection"
                    )
            elif binding_kind == "resource_usage":
                binding_id = binding_token
                if binding_id not in resource_ids:
                    raise StudioSessionError(
                        f"Module Logic boundary port {port.get('portId')} references an unknown resource"
                    )
            else:
                raise StudioSessionError(
                    f"Module Logic boundary port {port.get('portId')} has an invalid binding kind"
                )
            external = _clone(port.get("externalEntityRef", {}))
            if external.get("type") == "module":
                external["id"] = node_module.get(external.get("id"), external.get("id"))
                if external.get("id") not in module_by_id:
                    raise StudioSessionError(
                        f"Module Logic boundary port {port.get('portId')} references an unknown external module"
                    )
            elif external.get("type") == "resource":
                if external.get("id") not in resource_ids:
                    raise StudioSessionError(
                        f"Module Logic boundary port {port.get('portId')} references an unknown external resource"
                    )
            materialized_ports.append(
                {
                    "id": formal_id,
                    "name": str(port.get("name", "")).strip(),
                    "direction": port.get("direction", "input"),
                    "internalNodeId": internal_id,
                    "externalEntityRef": external,
                    "bindingKind": binding_kind,
                    "bindingId": binding_id,
                    "dataSummary": str(port.get("dataSummary", "")),
                    "protocol": str(port.get("protocol", "")),
                    "referenceIds": _clone(port.get("referenceIds", [])),
                    "extensions": _clone(port.get("extensions", {})),
                }
            )

        if base is None:
            design_id = _unique_id(
                "MLD-STUDIO",
                {
                    "session": session_id,
                    "candidate": candidate_id,
                    "canvas": _semantic_projection(
                        {"moduleLogicCanvases": [canvas]}
                    ).get("moduleLogicCanvases", []),
                    "module": root_module_id,
                },
                occupied,
            )
            design: dict[str, Any] = {"id": design_id}
            designs.append(design)
            design_by_id[design_id] = design
        else:
            design = base

        design.update(
            {
                "moduleId": root_module_id,
                "architectureScope": "target",
                "name": str(
                    canvas.get("name")
                    or f"{root_module.get('name', root_module_id)} target logic"
                ),
                "summary": str(canvas.get("summary", root_module.get("purpose", ""))),
                "nodes": materialized_nodes,
                "edges": materialized_edges,
                "boundaryPorts": materialized_ports,
                "requirementIds": _clone(canvas.get("requirementIds", [])),
                "decisionIds": _clone(canvas.get("decisionIds", [])),
                "riskIds": _clone(canvas.get("riskIds", [])),
                "acceptanceCriteriaIds": _clone(
                    canvas.get("acceptanceCriteriaIds", [])
                ),
                "gateIds": _clone(canvas.get("gateIds", [])),
                "referenceIds": _clone(canvas.get("referenceIds", [])),
                "extensions": _clone(canvas.get("extensions", {})),
            }
        )
        if canvas.get("displayName") is not None:
            design["displayName"] = str(canvas["displayName"])
        else:
            design.pop("displayName", None)


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
    candidate_layers = candidate.get("architectureLayers", session.get("layers", []))
    if isinstance(candidate_layers, list) and candidate_layers:
        architecture["layers"] = [
            {
                key: _clone(value)
                for key, value in layer.items()
                if key not in {"layoutHeight", "selected", "collapsed"}
                and not (key == "displayName" and not value)
            }
            for layer in sorted(
                candidate_layers,
                key=lambda item: (int(item.get("order", 0)), str(item.get("id", ""))),
            )
        ]
    modules = architecture["modules"]
    connections = architecture["connections"]
    module_by_id = {item.get("id"): item for item in modules}
    connection_by_id = {item.get("id"): item for item in connections}
    occupied = _occupied_formal_ids(result)
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
            if node.get("displayName"):
                module["displayName"] = str(node["displayName"]).strip()
            else:
                module.pop("displayName", None)
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
            baseline_assignment = node.get("baselineLayerAssignment")
            assignment = node.get("layerAssignment")
            if isinstance(assignment, dict) and (
                assignment != baseline_assignment
                or node.get("layerId") != baseline_layer_id
            ):
                module["extensions"] = _module_extensions_with_target_assignment(
                    module, assignment
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
    edge_connection: dict[str, str] = {}
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
            connection_id = connection["id"]
        else:
            connection_id = _unique_id("CONN-STUDIO", {"session": session.get("sessionId"), "candidate": candidate_id, "edge": _clone(edge), "from": from_id, "to": to_id}, occupied)
            connection = _new_connection(edge, connection_id, from_id, to_id)
            connections.append(connection); connection_by_id[connection_id] = connection
        edge_connection[edge["edgeId"]] = connection_id
    _materialize_module_logic_designs(
        architecture,
        result.get("resources", []),
        candidate,
        str(session.get("sessionId")),
        candidate_id,
        node_module,
        edge_connection,
        occupied,
    )
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
