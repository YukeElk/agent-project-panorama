"""Deterministic, read-only Panorama Model IR and View IR compilation.

V0.6 keeps Core/Event truth separate from derived reading projections.  This
module initially supports only the formal Panorama Core architecture model and
the module architecture profile.  Source extraction, event sequence views,
layout rendering, and last-good delivery are separate later work packages.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from panorama_io import compute_canonical_hash, compute_data_hash
from validate_panorama import load_panorama, validate_data


ROOT = Path(__file__).resolve().parents[1]
MODEL_SCHEMA = ROOT / "schema" / "panorama-model-ir.schema.v0.1.json"
VIEW_SCHEMA = ROOT / "schema" / "panorama-view-ir.schema.v0.1.json"
MODEL_COMPILER = {"id": "panorama-core-model-compiler", "version": "0.1.0"}
VIEW_COMPILER = {"id": "panorama-module-view-compiler", "version": "0.1.0"}


class PanoramaViewIRError(ValueError):
    """Raised when a projection cannot be proven from its bound inputs."""


def _schema_validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_errors(value: dict[str, Any], path: Path) -> list[str]:
    errors = sorted(
        _schema_validator(path).iter_errors(value),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    rendered: list[str] = []
    for error in errors:
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        rendered.append(f"{pointer or '/'}: {error.message}")
    return rendered


def _stable_derived_id(prefix: str, value: Any, length: int = 24) -> str:
    return f"{prefix}-{compute_canonical_hash(value)[:length].upper()}"


def _architecture_scopes(value: str) -> list[str]:
    if value == "both":
        return ["current", "target"]
    if value in {"current", "target", "historical"}:
        return [value]
    raise PanoramaViewIRError(f"未知 architectureScope：{value!r}")


def _layer_bindings(module: dict[str, Any], scopes: list[str]) -> dict[str, Any]:
    current_layer = module.get("layerId") if "current" in scopes else None
    target_layer = (
        (module.get("targetLayerId") or module.get("layerId"))
        if "target" in scopes
        else None
    )
    historical_layer = module.get("layerId") if "historical" in scopes else None
    return {
        "current": current_layer,
        "target": target_layer,
        "historical": historical_layer,
    }


def _exact_provenance(data: dict[str, Any], pointer: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in data.get("factProvenance", [])
        if isinstance(item, dict) and item.get("path") == pointer
    ]
    if len(matches) > 1:
        raise PanoramaViewIRError(f"同一 JSON Pointer 存在重复 Fact Provenance：{pointer}")
    return matches[0] if matches else None


def _fact_projection(
    data: dict[str, Any], pointer: str
) -> tuple[str, str, str]:
    provenance = _exact_provenance(data, pointer)
    if provenance is None:
        return "declared", "declared", "unknown"
    authority = str(provenance.get("authority", "unknown"))
    confidence = str(provenance.get("confidence", "unknown"))
    fact_status = "derived" if authority == "inferred" else authority
    if fact_status not in {"observed", "declared", "derived", "unknown", "conflict"}:
        fact_status = "unknown"
    return fact_status, authority, confidence


def _evidence_pin(
    *, pointer: str, value: Any, data_hash: str, freshness: str = "recorded_as_of"
) -> dict[str, Any]:
    digest = compute_canonical_hash(value)
    return {
        "evidenceId": _stable_derived_id(
            "EVID", {"pointer": pointer, "digest": digest}, 20
        ),
        "kind": "json_pointer",
        "ref": pointer,
        "revision": data_hash,
        "digest": digest,
        "accessClass": "PROJECT_OPERATIONAL_METADATA",
        "freshness": freshness,
    }


def _source_binding(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("sourceBinding")
    if not isinstance(raw, dict):
        return {
            "mode": "unknown",
            "gitHead": None,
            "sourceSnapshotHash": None,
            "sourceContentDigest": None,
            "coverage": "unknown",
            "currentness": "unknown",
        }
    raw_mode = raw.get("mode")
    if raw_mode == "git":
        mode = "git"
    elif raw_mode == "filesystem_metadata":
        mode = "recorded_metadata"
    else:
        mode = "unknown"
    git_head = raw.get("gitHead") if isinstance(raw.get("gitHead"), str) else None
    snapshot = raw.get("sourceSnapshotHash")
    if not isinstance(snapshot, str) or len(snapshot) != 64:
        snapshot = None
    content_digest = raw.get("sourceContentDigest")
    if not isinstance(content_digest, str) or len(content_digest) != 64:
        content_digest = None
    return {
        "mode": mode,
        "gitHead": git_head,
        "sourceSnapshotHash": snapshot,
        "sourceContentDigest": content_digest,
        "coverage": "unknown",
        "currentness": "recorded_as_of",
    }


def compute_model_semantic_hash(model: dict[str, Any]) -> str:
    value = deepcopy(model)
    value.pop("integrity", None)
    return compute_canonical_hash(value)


def compute_view_semantic_hash(view: dict[str, Any]) -> str:
    value = deepcopy(view)
    value.pop("integrity", None)
    value.pop("layout", None)
    return compute_canonical_hash(value)


def compute_view_layout_hash(view: dict[str, Any]) -> str:
    return compute_canonical_hash(view.get("layout"))


def compile_model_ir(data: dict[str, Any]) -> dict[str, Any]:
    report = validate_data(data)
    if report.errors:
        details = "; ".join(issue.render() for issue in report.errors[:10])
        raise PanoramaViewIRError(f"Panorama Core 无效，不能编译 Model IR：{details}")

    data_hash = compute_data_hash(data)
    project = data["project"]
    meta = data["meta"]
    architecture = data["architecture"]
    compiled_at = meta.get("updatedAt") or meta.get("createdAt")
    if not isinstance(compiled_at, str):
        raise PanoramaViewIRError("Panorama meta 缺少可用的 updatedAt/createdAt。")

    layers: list[dict[str, Any]] = []
    for index, layer in enumerate(architecture.get("layers", [])):
        pointer = f"/architecture/layers/{index}"
        layers.append(
            {
                "id": layer["id"],
                "name": layer["name"],
                "order": layer["order"],
                "summary": layer.get("summary", ""),
                "evidencePins": [
                    _evidence_pin(pointer=pointer, value=layer, data_hash=data_hash)
                ],
            }
        )
    layers.sort(key=lambda item: (item["order"], item["id"]))

    entities: list[dict[str, Any]] = []
    for index, module in enumerate(architecture.get("modules", [])):
        pointer = f"/architecture/modules/{index}"
        fact_status, authority, confidence = _fact_projection(data, pointer)
        status = module.get("status", {})
        scopes = _architecture_scopes(module.get("architectureScope", "current"))
        entities.append(
            {
                "id": module["id"],
                "kind": "module",
                "name": module["name"],
                "layerBindings": _layer_bindings(module, scopes),
                "architectureScopes": scopes,
                "purpose": module.get("purpose", ""),
                "factStatus": fact_status,
                "authority": authority,
                "confidence": confidence,
                "evidencePins": [
                    _evidence_pin(pointer=pointer, value=module, data_hash=data_hash)
                ],
                "attributes": {
                    "category": module.get("category"),
                    "codePath": module.get("codePath"),
                    "targetLayerId": module.get("targetLayerId"),
                    "designMaturity": status.get("designMaturity"),
                    "implementationMaturity": status.get("implementationMaturity"),
                    "verificationStatus": status.get("verificationStatus"),
                    "runtimeStatus": status.get("runtimeStatus"),
                },
            }
        )
    entities.sort(key=lambda item: item["id"])

    relations: list[dict[str, Any]] = []
    for index, connection in enumerate(architecture.get("connections", [])):
        pointer = f"/architecture/connections/{index}"
        fact_status, authority, confidence = _fact_projection(data, pointer)
        mode = connection.get("communicationMode")
        asynchronous: bool | None
        if mode in {"async", "event", "stream", "batch"}:
            asynchronous = True
        elif mode == "sync":
            asynchronous = False
        else:
            asynchronous = None
        relations.append(
            {
                "id": connection["id"],
                "kind": "communication",
                "name": connection["name"],
                "fromEntityId": connection["fromModuleId"],
                "toEntityId": connection["toModuleId"],
                "direction": (
                    "two_way"
                    if connection.get("flowDirection") == "bidirectional"
                    else "one_way"
                ),
                "architectureScopes": _architecture_scopes(
                    connection.get("architectureScope", "current")
                ),
                "factStatus": fact_status,
                "authority": authority,
                "confidence": confidence,
                "evidencePins": [
                    _evidence_pin(
                        pointer=pointer, value=connection, data_hash=data_hash
                    )
                ],
                "semantics": {
                    "protocol": connection.get("protocol") or None,
                    "mode": mode,
                    "dataSummary": connection.get("dataSummary") or None,
                    "order": None,
                    "asynchronous": asynchronous,
                },
                "attributes": {
                    "lifecycle": connection.get("lifecycle"),
                    "contractReferenceIds": connection.get(
                        "contractReferenceIds", []
                    ),
                    "transitionId": connection.get("transitionId"),
                },
            }
        )
    relations.sort(key=lambda item: item["id"])

    source_binding = _source_binding(data)
    project_binding = {
        "projectId": project["id"],
        "projectName": project["name"],
        "panoramaSchemaVersion": data["schemaVersion"],
        "revision": meta["revision"],
        "dataHash": data_hash,
    }
    event_binding = {
        "status": "not_provided",
        "checkpointId": None,
        "checkpointHash": None,
        "asOfSequence": None,
    }
    as_of = {"mode": "recorded_as_of", "value": compiled_at}
    model_id = _stable_derived_id(
        "MODEL",
        {
            "compiler": MODEL_COMPILER,
            "projectBinding": project_binding,
            "sourceBinding": source_binding,
            "eventBinding": event_binding,
            "asOf": as_of,
        },
    )
    information_gaps = ["event_checkpoint_not_provided"]
    if source_binding["currentness"] != "current":
        information_gaps.append("source_currentness_not_verified")

    model: dict[str, Any] = {
        "formatVersion": "panorama-model-ir.v0.1",
        "modelId": model_id,
        "compiledAt": compiled_at,
        "compiler": MODEL_COMPILER,
        "projectBinding": project_binding,
        "sourceBinding": source_binding,
        "eventBinding": event_binding,
        "asOf": as_of,
        "layers": layers,
        "entities": entities,
        "relations": relations,
        "informationGaps": sorted(information_gaps),
        "extensions": {},
    }
    model["integrity"] = {
        "hashAlgorithm": "sha256",
        "semanticHash": compute_model_semantic_hash(model),
        "hashScope": "model_without_integrity",
    }
    errors = validate_model_ir(model)
    if errors:
        raise PanoramaViewIRError("Model IR 编译结果无效：" + "; ".join(errors[:10]))
    return model


def validate_model_ir(model: dict[str, Any]) -> list[str]:
    errors = _schema_errors(model, MODEL_SCHEMA)
    if errors:
        return errors
    expected = compute_model_semantic_hash(model)
    if model["integrity"]["semanticHash"] != expected:
        errors.append("/integrity/semanticHash: 与 Model IR 内容不匹配")

    layer_ids = [item["id"] for item in model["layers"]]
    entity_ids = [item["id"] for item in model["entities"]]
    relation_ids = [item["id"] for item in model["relations"]]
    for label, values in (
        ("layer", layer_ids),
        ("entity", entity_ids),
        ("relation", relation_ids),
    ):
        if len(values) != len(set(values)):
            errors.append(f"/{label}s: 存在重复稳定 ID")
    layer_set = set(layer_ids)
    entity_set = set(entity_ids)
    for entity in model["entities"]:
        for scope, layer_id in entity.get("layerBindings", {}).items():
            if layer_id is not None and layer_id not in layer_set:
                errors.append(
                    f"/entities/{entity['id']}: {scope} 使用未知 layerId {layer_id}"
                )
    for relation in model["relations"]:
        if relation["fromEntityId"] not in entity_set:
            errors.append(
                f"/relations/{relation['id']}: 未知 fromEntityId {relation['fromEntityId']}"
            )
        if relation["toEntityId"] not in entity_set:
            errors.append(
                f"/relations/{relation['id']}: 未知 toEntityId {relation['toEntityId']}"
            )
    return errors


def compile_module_view_ir(
    model: dict[str, Any], *, architecture_scopes: list[str] | None = None
) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:10]))
    scopes = architecture_scopes or ["current"]
    allowed_scopes = {"current", "target", "transition", "historical"}
    if not scopes or len(scopes) != len(set(scopes)) or not set(scopes) <= allowed_scopes:
        raise PanoramaViewIRError("architecture_scopes 必须是非空、唯一的合法范围。")
    if len(scopes) != 1:
        raise PanoramaViewIRError("module profile v0.1 每次只允许一个 architecture scope。")
    ordered_scopes = [
        scope
        for scope in ("current", "target", "transition", "historical")
        if scope in scopes
    ]
    selected_entities = [
        entity
        for entity in model["entities"]
        if entity["kind"] == "module"
        and set(entity["architectureScopes"]) & set(ordered_scopes)
    ]
    selected_entity_ids = {entity["id"] for entity in selected_entities}
    selected_relations = [
        relation
        for relation in model["relations"]
        if relation["fromEntityId"] in selected_entity_ids
        and relation["toEntityId"] in selected_entity_ids
        and set(relation["architectureScopes"]) & set(ordered_scopes)
    ]
    selected_scope = ordered_scopes[0]
    layer_ids = {
        entity["layerBindings"][selected_scope]
        for entity in selected_entities
        if entity["layerBindings"].get(selected_scope) is not None
    }
    selected_layers = [layer for layer in model["layers"] if layer["id"] in layer_ids]

    group_by_layer: dict[str, str] = {}
    groups: list[dict[str, Any]] = []
    for layer in selected_layers:
        group_id = _stable_derived_id("GROUP", {"layerId": layer["id"]}, 20)
        group_by_layer[layer["id"]] = group_id
        groups.append(
            {
                "id": group_id,
                "layerId": layer["id"],
                "label": layer["name"],
                "order": layer["order"],
            }
        )

    node_by_entity: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for entity in selected_entities:
        node_id = _stable_derived_id("NODE", {"entityId": entity["id"]}, 20)
        node_by_entity[entity["id"]] = node_id
        nodes.append(
            {
                "id": node_id,
                "entityRef": {"type": "entity", "id": entity["id"]},
                "label": entity["name"],
                "kind": entity["kind"],
                "groupId": group_by_layer.get(
                    entity["layerBindings"].get(selected_scope)
                ),
                "factStatus": entity["factStatus"],
                "authority": entity["authority"],
                "confidence": entity["confidence"],
                "evidencePinIds": sorted(
                    pin["evidenceId"] for pin in entity["evidencePins"]
                ),
                "emphasis": "default",
            }
        )
    nodes.sort(key=lambda item: item["entityRef"]["id"])

    edges: list[dict[str, Any]] = []
    for relation in selected_relations:
        edges.append(
            {
                "id": _stable_derived_id(
                    "EDGE", {"relationId": relation["id"]}, 20
                ),
                "relationRef": {"type": "relation", "id": relation["id"]},
                "fromNodeId": node_by_entity[relation["fromEntityId"]],
                "toNodeId": node_by_entity[relation["toEntityId"]],
                "label": relation["name"],
                "kind": relation["kind"],
                "direction": relation["direction"],
                "order": relation["semantics"]["order"],
                "evidencePinIds": sorted(
                    pin["evidenceId"] for pin in relation["evidencePins"]
                ),
            }
        )
    edges.sort(key=lambda item: item["relationRef"]["id"])

    model_binding = {
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "projectId": model["projectBinding"]["projectId"],
        "panoramaDataHash": model["projectBinding"]["dataHash"],
        "asOf": model["asOf"]["value"],
    }
    view_id = _stable_derived_id(
        "VIEW",
        {
            "compiler": VIEW_COMPILER,
            "modelSemanticHash": model_binding["modelSemanticHash"],
            "profile": "module",
            "architectureScopes": ordered_scopes,
        },
    )
    layout = {"strategy": "auto_layered", "positions": []}
    view: dict[str, Any] = {
        "formatVersion": "panorama-view-ir.v0.1",
        "viewId": view_id,
        "generatedAt": model["compiledAt"],
        "compiler": VIEW_COMPILER,
        "modelBinding": model_binding,
        "viewType": "architecture",
        "profile": "module",
        "title": f"{model['projectBinding']['projectName']} 模块架构",
        "description": "从正式 Panorama Core 架构模型确定性编译的只读模块视图。",
        "filters": {
            "architectureScopes": ordered_scopes,
            "factStatuses": [
                "observed",
                "declared",
                "derived",
                "unknown",
                "conflict",
            ],
        },
        "groups": groups,
        "nodes": nodes,
        "edges": edges,
        "informationGaps": list(model["informationGaps"]),
        "layout": layout,
        "extensions": {},
    }
    view["integrity"] = {
        "hashAlgorithm": "sha256",
        "semanticHash": compute_view_semantic_hash(view),
        "semanticHashScope": "view_without_layout_or_integrity",
        "layoutHash": compute_view_layout_hash(view),
        "layoutHashScope": "layout_only",
    }
    errors = validate_view_ir(view, model)
    if errors:
        raise PanoramaViewIRError("View IR 编译结果无效：" + "; ".join(errors[:10]))
    return view


def validate_view_ir(view: dict[str, Any], model: dict[str, Any]) -> list[str]:
    errors = _schema_errors(view, VIEW_SCHEMA)
    if errors:
        return errors
    if view["integrity"]["semanticHash"] != compute_view_semantic_hash(view):
        errors.append("/integrity/semanticHash: 与 View IR 语义内容不匹配")
    if view["integrity"]["layoutHash"] != compute_view_layout_hash(view):
        errors.append("/integrity/layoutHash: 与 View IR layout 不匹配")

    binding = view["modelBinding"]
    expected_binding = {
        "modelId": model.get("modelId"),
        "modelSemanticHash": model.get("integrity", {}).get("semanticHash"),
        "projectId": model.get("projectBinding", {}).get("projectId"),
        "panoramaDataHash": model.get("projectBinding", {}).get("dataHash"),
        "asOf": model.get("asOf", {}).get("value"),
    }
    if binding != expected_binding:
        errors.append("/modelBinding: 与输入 Model IR 不匹配")

    group_ids = [item["id"] for item in view["groups"]]
    node_ids = [item["id"] for item in view["nodes"]]
    edge_ids = [item["id"] for item in view["edges"]]
    for label, values in (
        ("groups", group_ids),
        ("nodes", node_ids),
        ("edges", edge_ids),
    ):
        if len(values) != len(set(values)):
            errors.append(f"/{label}: 存在重复稳定 ID")

    model_layers = {item["id"]: item for item in model.get("layers", [])}
    model_entities = {item["id"]: item for item in model.get("entities", [])}
    model_relations = {item["id"]: item for item in model.get("relations", [])}
    group_set = set(group_ids)
    node_set = set(node_ids)
    node_by_entity: dict[str, str] = {}
    for group in view["groups"]:
        if group["layerId"] not in model_layers:
            errors.append(f"/groups/{group['id']}: 未知 layerId {group['layerId']}")
    for node in view["nodes"]:
        entity_id = node["entityRef"]["id"]
        entity = model_entities.get(entity_id)
        if entity is None:
            errors.append(f"/nodes/{node['id']}: 未知 entityRef {entity_id}")
            continue
        if entity_id in node_by_entity:
            errors.append(f"/nodes/{node['id']}: entityRef {entity_id} 重复投影")
        node_by_entity[entity_id] = node["id"]
        if node["groupId"] is not None and node["groupId"] not in group_set:
            errors.append(f"/nodes/{node['id']}: 未知 groupId {node['groupId']}")
        evidence_ids = {pin["evidenceId"] for pin in entity["evidencePins"]}
        if not set(node["evidencePinIds"]) <= evidence_ids:
            errors.append(f"/nodes/{node['id']}: Evidence Pin 不属于绑定 Entity")
    for edge in view["edges"]:
        relation_id = edge["relationRef"]["id"]
        relation = model_relations.get(relation_id)
        if relation is None:
            errors.append(f"/edges/{edge['id']}: 未知 relationRef {relation_id}")
            continue
        if edge["fromNodeId"] not in node_set or edge["toNodeId"] not in node_set:
            errors.append(f"/edges/{edge['id']}: 端点 Node 不存在")
        expected_from = node_by_entity.get(relation["fromEntityId"])
        expected_to = node_by_entity.get(relation["toEntityId"])
        if edge["fromNodeId"] != expected_from or edge["toNodeId"] != expected_to:
            errors.append(f"/edges/{edge['id']}: 端点与 Relation 不匹配")
        evidence_ids = {pin["evidenceId"] for pin in relation["evidencePins"]}
        if not set(edge["evidencePinIds"]) <= evidence_ids:
            errors.append(f"/edges/{edge['id']}: Evidence Pin 不属于绑定 Relation")
    for position in view["layout"]["positions"]:
        if position["nodeId"] not in node_set:
            errors.append(
                f"/layout/positions/{position['nodeId']}: 未知布局 Node"
            )
    return errors


def load_and_compile_model_ir(path: Path) -> dict[str, Any]:
    data, _ = load_panorama(path)
    return compile_model_ir(data)
