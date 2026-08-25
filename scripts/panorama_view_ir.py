"""Deterministic, read-only Panorama Model IR and View IR compilation.

V0.6 keeps Core/Event truth separate from derived reading projections.  This
module compiles the formal Panorama Core plus bounded source observations into
shared Model IR and deterministic architecture, dependency/data-flow, and
deployment/runtime reading projections.  Event sequence views, interactive
rendering, and last-good delivery remain separate work packages.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from event_projection import validate_event_checkpoint
from module_logic_observation import validate_module_logic_observation
from panorama_io import compute_canonical_hash, compute_data_hash
from source_topology import validate_source_observation
from validate_panorama import load_panorama, validate_data


ROOT = Path(__file__).resolve().parents[1]
MODEL_SCHEMA = ROOT / "schema" / "panorama-model-ir.schema.v0.1.json"
VIEW_SCHEMA = ROOT / "schema" / "panorama-view-ir.schema.v0.1.json"
MODEL_COMPILER = {"id": "panorama-core-model-compiler", "version": "0.4.0"}
VIEW_COMPILER = {"id": "panorama-module-view-compiler", "version": "0.1.0"}
MODULE_LOGIC_VIEW_COMPILER = {
    "id": "panorama-module-logic-view-compiler",
    "version": "0.1.0",
}
DEPENDENCY_VIEW_COMPILER = {
    "id": "panorama-dependency-dataflow-view-compiler",
    "version": "0.1.0",
}
DEPLOYMENT_VIEW_COMPILER = {
    "id": "panorama-deployment-runtime-view-compiler",
    "version": "0.1.0",
}
SEQUENCE_VIEW_COMPILER = {
    "id": "panorama-event-sequence-view-compiler",
    "version": "0.1.0",
}
LIFECYCLE_VIEW_COMPILER = {
    "id": "panorama-event-lifecycle-view-compiler",
    "version": "0.1.0",
}
EVOLUTION_VIEW_COMPILER = {
    "id": "panorama-event-evolution-risk-view-compiler",
    "version": "0.1.0",
}


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


def _release_scopes(status: str) -> list[str]:
    mapping = {
        "planned": ["target"],
        "development": ["transition"],
        "candidate": ["transition"],
        "deployed": ["current"],
        "retired": ["historical"],
    }
    if status not in mapping:
        raise PanoramaViewIRError(f"未知 Release status：{status!r}")
    return mapping[status]


def _deployment_scopes(status: str) -> list[str]:
    mapping = {
        "planned": ["target"],
        "deploying": ["transition"],
        "active": ["current"],
        "degraded": ["current"],
        "stopped": ["historical"],
        "retired": ["historical"],
    }
    if status not in mapping:
        raise PanoramaViewIRError(f"未知 Deployment status：{status!r}")
    return mapping[status]


def _resource_scopes(status: str) -> list[str]:
    mapping = {
        "planned": ["target"],
        "reserved": ["target"],
        "active": ["current"],
        "degraded": ["current"],
        "inactive": ["historical"],
        "retired": ["historical"],
        "unknown": ["current"],
    }
    if status not in mapping:
        raise PanoramaViewIRError(f"未知 Resource status：{status!r}")
    return mapping[status]


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


def _observation_source_binding(observation: dict[str, Any]) -> dict[str, Any]:
    source = observation["sourceBinding"]
    return {
        "mode": source["mode"],
        "gitHead": source["gitHead"],
        "sourceSnapshotHash": observation["integrity"]["semanticHash"],
        "sourceContentDigest": source["contentDigest"],
        "coverage": source["coverage"],
        "currentness": source["currentness"],
    }


def _model_source_pin(pin: dict[str, Any]) -> dict[str, Any]:
    line = pin.get("line")
    column = pin.get("column")
    suffix = ""
    if line is not None:
        suffix = f"#L{line}"
        if column is not None:
            suffix += f":{column}"
    return {
        "evidenceId": pin["evidenceId"],
        "kind": "source_location",
        "ref": pin["ref"] + suffix,
        "revision": pin["revision"],
        "digest": pin["digest"],
        "accessClass": pin["accessClass"],
        "freshness": pin["freshness"],
    }


def _model_module_logic_pin(pin: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Module Logic Observation pin without retaining source text."""
    suffix = ""
    line_start = pin.get("lineStart")
    line_end = pin.get("lineEnd")
    if line_start is not None:
        suffix = f"#L{line_start}"
        if line_end is not None and line_end != line_start:
            suffix += f"-L{line_end}"
    return {
        "evidenceId": pin["evidenceId"],
        "kind": pin["kind"],
        "ref": pin["ref"] + suffix,
        "revision": None,
        "digest": pin["digest"],
        "accessClass": pin["accessClass"],
        "freshness": pin["freshness"],
    }


def _display_fields(value: dict[str, Any]) -> dict[str, str]:
    technical = str(value["name"])
    display = value.get("displayName")
    if not isinstance(display, str) or not display.strip():
        display = technical
    return {"displayLabel": display, "technicalLabel": technical}


def _empty_layer_bindings() -> dict[str, None]:
    return {"current": None, "target": None, "historical": None}


def _relation_semantics(
    *,
    mode: str,
    protocol: str | None = None,
    data_summary: str | None = None,
    order: int | None = None,
) -> dict[str, Any]:
    return {
        "protocol": protocol or None,
        "mode": mode,
        "dataSummary": data_summary or None,
        "order": order,
        "asynchronous": None,
    }


def _event_fact(authority: str) -> str:
    return "derived" if authority == "inferred" else authority


def _event_pin(event: dict[str, Any]) -> dict[str, Any]:
    event_hash = event["integrity"]["eventHash"]
    return {
        "evidenceId": _stable_derived_id(
            "EVID", {"eventId": event["eventId"], "eventHash": event_hash}, 20
        ),
        "kind": "event_id",
        "ref": event["eventId"],
        "revision": event_hash,
        "digest": event_hash,
        "accessClass": "PROJECT_OPERATIONAL_METADATA",
        "freshness": "current",
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


def compile_model_ir(
    data: dict[str, Any],
    *,
    source_observation: dict[str, Any] | None = None,
    event_checkpoint: dict[str, Any] | None = None,
    module_logic_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        layer_semantics = layer.get("extensions", {}).get("layerSemantics")
        layers.append(
            {
                "id": layer["id"],
                "name": layer["name"],
                **_display_fields(layer),
                "order": layer["order"],
                "summary": layer.get("summary", ""),
                "parentLayerId": layer.get("parentLayerId"),
                **({"kind": layer["kind"]} if layer.get("kind") else {}),
                **(
                    {"semantics": deepcopy(layer_semantics)}
                    if isinstance(layer_semantics, dict)
                    else {}
                ),
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
                **_display_fields(module),
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

    for index, release in enumerate(data.get("releases", [])):
        pointer = f"/releases/{index}"
        fact_status, authority, confidence = _fact_projection(data, pointer)
        entities.append(
            {
                "id": release["id"],
                "kind": "release",
                "name": release["name"],
                "layerBindings": {
                    "current": None,
                    "target": None,
                    "historical": None,
                },
                "architectureScopes": _release_scopes(release["status"]),
                "purpose": "正式 Panorama Release 记录。",
                "factStatus": fact_status,
                "authority": authority,
                "confidence": confidence,
                "evidencePins": [
                    _evidence_pin(pointer=pointer, value=release, data_hash=data_hash)
                ],
                "attributes": {
                    "version": release["version"],
                    "status": release["status"],
                    "architectureVersionId": release["architectureVersionId"],
                    "stageId": release["stageId"],
                    "createdAt": release["createdAt"],
                    "releasedAt": release.get("releasedAt"),
                },
            }
        )

    for index, deployment in enumerate(data.get("deployments", [])):
        pointer = f"/deployments/{index}"
        fact_status, authority, confidence = _fact_projection(data, pointer)
        entities.append(
            {
                "id": deployment["id"],
                "kind": "deployment",
                "name": deployment["name"],
                "layerBindings": {
                    "current": None,
                    "target": None,
                    "historical": None,
                },
                "architectureScopes": _deployment_scopes(deployment["status"]),
                "purpose": "正式 Panorama Deployment 记录；状态不自动提升为运行观察。",
                "factStatus": fact_status,
                "authority": authority,
                "confidence": confidence,
                "evidencePins": [
                    _evidence_pin(
                        pointer=pointer, value=deployment, data_hash=data_hash
                    )
                ],
                "attributes": {
                    "environment": deployment["environment"],
                    "status": deployment["status"],
                    "releaseId": deployment["releaseId"],
                    "architectureVersionId": deployment["architectureVersionId"],
                    "deployedAt": deployment.get("deployedAt"),
                },
            }
        )

    for index, resource in enumerate(data.get("resources", [])):
        pointer = f"/resources/{index}"
        fact_status, authority, confidence = _fact_projection(data, pointer)
        entities.append(
            {
                "id": resource["id"],
                "kind": (
                    "data_store"
                    if resource["type"]
                    in {"database", "cache", "object_store", "vector_store"}
                    else "resource"
                ),
                "name": resource["name"],
                "layerBindings": {
                    "current": None,
                    "target": None,
                    "historical": None,
                },
                "architectureScopes": _resource_scopes(resource["status"]),
                "purpose": "正式 Panorama Resource 记录；不包含 Access/Credential 正文。",
                "factStatus": fact_status,
                "authority": authority,
                "confidence": confidence,
                "evidencePins": [
                    _evidence_pin(pointer=pointer, value=resource, data_hash=data_hash)
                ],
                "attributes": {
                    "resourceType": resource["type"],
                    "environment": resource["environment"],
                    "status": resource["status"],
                    "provider": resource.get("provider"),
                    "location": resource.get("location"),
                },
            }
        )

    module_logic_bindings: list[dict[str, Any]] = []
    formal_logic_relations: list[dict[str, Any]] = []
    for design_index, design in enumerate(architecture.get("moduleLogicDesigns", [])):
        scope = design["architectureScope"]
        design_pointer = f"/architecture/moduleLogicDesigns/{design_index}"
        design_digest = compute_canonical_hash(design)
        binding = {
            "source": "formal_design",
            "moduleId": design["moduleId"],
            "architectureScope": scope,
            "designId": design["id"],
            "designDigest": design_digest,
        }
        module_logic_bindings.append(binding)
        binding_key = f"formal_design:{design['id']}:{design_digest}"

        for node_index, node in enumerate(design["nodes"]):
            pointer = f"{design_pointer}/nodes/{node_index}"
            pin = _evidence_pin(pointer=pointer, value=node, data_hash=data_hash)
            entities.append(
                {
                    "id": node["id"],
                    "kind": "module_logic_node",
                    "name": node["name"],
                    **_display_fields(node),
                    "layerBindings": _empty_layer_bindings(),
                    "architectureScopes": [scope],
                    "purpose": node.get("purpose", ""),
                    "factStatus": "declared",
                    "authority": "declared",
                    "confidence": "unknown",
                    "evidencePins": [pin],
                    "attributes": {
                        "rootModuleId": design["moduleId"],
                        "moduleLogicSource": "formal_design",
                        "moduleLogicBindingKey": binding_key,
                        "designId": design["id"],
                        "logicNodeId": node["id"],
                        "logicType": node["type"],
                        "rationale": node.get("rationale", ""),
                        "implementationSummary": node.get(
                            "implementationSummary", ""
                        ),
                        "loopExitCondition": node.get("loopExitCondition"),
                        "requirementIds": node.get("requirementIds", []),
                        "decisionIds": node.get("decisionIds", []),
                        "riskIds": node.get("riskIds", []),
                        "referenceIds": node.get("referenceIds", []),
                    },
                }
            )

        for edge_index, edge in enumerate(design["edges"]):
            pointer = f"{design_pointer}/edges/{edge_index}"
            pin = _evidence_pin(pointer=pointer, value=edge, data_hash=data_hash)
            formal_logic_relations.append(
                {
                    "id": edge["id"],
                    "kind": "logic_flow",
                    "name": edge.get("condition") or edge["kind"],
                    "fromEntityId": edge["fromNodeId"],
                    "toEntityId": edge["toNodeId"],
                    "direction": "one_way",
                    "architectureScopes": [scope],
                    "factStatus": "declared",
                    "authority": "declared",
                    "confidence": "unknown",
                    "evidencePins": [pin],
                    "semantics": _relation_semantics(
                        mode=edge["kind"],
                        data_summary=edge.get("dataSummary"),
                        order=edge.get("order"),
                    ),
                    "attributes": {
                        "rootModuleId": design["moduleId"],
                        "moduleLogicSource": "formal_design",
                        "moduleLogicBindingKey": binding_key,
                        "designId": design["id"],
                        "logicEdgeId": edge["id"],
                        "condition": edge.get("condition", ""),
                        "referenceIds": edge.get("referenceIds", []),
                    },
                }
            )

        for port_index, port in enumerate(design["boundaryPorts"]):
            pointer = f"{design_pointer}/boundaryPorts/{port_index}"
            pin = _evidence_pin(pointer=pointer, value=port, data_hash=data_hash)
            entities.append(
                {
                    "id": port["id"],
                    "kind": "boundary_port",
                    "name": port["name"],
                    "displayLabel": port["name"],
                    "technicalLabel": port["name"],
                    "layerBindings": _empty_layer_bindings(),
                    "architectureScopes": [scope],
                    "purpose": port.get("dataSummary", ""),
                    "factStatus": "declared",
                    "authority": "declared",
                    "confidence": "unknown",
                    "evidencePins": [pin],
                    "attributes": {
                        "rootModuleId": design["moduleId"],
                        "moduleLogicSource": "formal_design",
                        "moduleLogicBindingKey": binding_key,
                        "designId": design["id"],
                        "boundaryPortId": port["id"],
                        "portDirection": port["direction"],
                        "internalNodeId": port["internalNodeId"],
                        "externalEntityType": port["externalEntityRef"]["type"],
                        "externalEntityId": port["externalEntityRef"]["id"],
                        "bindingKind": port["bindingKind"],
                        "bindingId": port["bindingId"],
                        "dataSummary": port.get("dataSummary", ""),
                        "protocol": port.get("protocol", ""),
                        "referenceIds": port.get("referenceIds", []),
                    },
                }
            )
            internal_id = port["internalNodeId"]
            external_id = port["externalEntityRef"]["id"]
            if port["direction"] == "input":
                internal_from, internal_to = port["id"], internal_id
                external_from, external_to = external_id, port["id"]
                direction = "one_way"
            elif port["direction"] == "output":
                internal_from, internal_to = internal_id, port["id"]
                external_from, external_to = port["id"], external_id
                direction = "one_way"
            else:
                internal_from, internal_to = internal_id, port["id"]
                external_from, external_to = port["id"], external_id
                direction = "two_way"
            common_attributes = {
                "rootModuleId": design["moduleId"],
                "moduleLogicSource": "formal_design",
                "moduleLogicBindingKey": binding_key,
                "designId": design["id"],
                "boundaryPortId": port["id"],
                "bindingKind": port["bindingKind"],
                "bindingId": port["bindingId"],
                "portDirection": port["direction"],
            }
            formal_logic_relations.extend(
                [
                    {
                        "id": _stable_derived_id(
                            "MLREL",
                            {"binding": binding_key, "port": port["id"], "side": "internal"},
                        ),
                        "kind": "port_binding",
                        "name": port["name"],
                        "fromEntityId": internal_from,
                        "toEntityId": internal_to,
                        "direction": direction,
                        "architectureScopes": [scope],
                        "factStatus": "declared",
                        "authority": "declared",
                        "confidence": "unknown",
                        "evidencePins": [pin],
                        "semantics": _relation_semantics(
                            mode="internal_port_binding",
                            protocol=port.get("protocol"),
                            data_summary=port.get("dataSummary"),
                        ),
                        "attributes": {**common_attributes, "boundarySide": "internal"},
                    },
                    {
                        "id": _stable_derived_id(
                            "MLREL",
                            {"binding": binding_key, "port": port["id"], "side": "external"},
                        ),
                        "kind": (
                            "resource_usage"
                            if port["bindingKind"] == "resource_usage"
                            else "port_binding"
                        ),
                        "name": port["name"],
                        "fromEntityId": external_from,
                        "toEntityId": external_to,
                        "direction": direction,
                        "architectureScopes": [scope],
                        "factStatus": "declared",
                        "authority": "declared",
                        "confidence": "unknown",
                        "evidencePins": [pin],
                        "semantics": _relation_semantics(
                            mode=port["bindingKind"],
                            protocol=port.get("protocol"),
                            data_summary=port.get("dataSummary"),
                        ),
                        "attributes": {**common_attributes, "boundarySide": "external"},
                    },
                ]
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

    relations.extend(formal_logic_relations)

    for deployment_index, deployment in enumerate(data.get("deployments", [])):
        scopes = _deployment_scopes(deployment["status"])
        release_pointer = f"/deployments/{deployment_index}/releaseId"
        fact_status, authority, confidence = _fact_projection(
            data, release_pointer
        )
        relations.append(
            {
                "id": _stable_derived_id(
                    "REL",
                    {
                        "kind": "deployment_release",
                        "deploymentId": deployment["id"],
                        "releaseId": deployment["releaseId"],
                    },
                ),
                "kind": "deployment",
                "name": "部署使用发布",
                "fromEntityId": deployment["id"],
                "toEntityId": deployment["releaseId"],
                "direction": "one_way",
                "architectureScopes": scopes,
                "factStatus": fact_status,
                "authority": authority,
                "confidence": confidence,
                "evidencePins": [
                    _evidence_pin(
                        pointer=release_pointer,
                        value=deployment["releaseId"],
                        data_hash=data_hash,
                    )
                ],
                "semantics": {
                    "protocol": None,
                    "mode": "deployment_release",
                    "dataSummary": None,
                    "order": None,
                    "asynchronous": None,
                },
                "attributes": {
                    "environment": deployment["environment"],
                    "deploymentStatus": deployment["status"],
                    "runtimeObserved": authority == "observed",
                },
            }
        )
        for module_index, module_deployment in enumerate(
            deployment.get("moduleDeployments", [])
        ):
            pointer = (
                f"/deployments/{deployment_index}/moduleDeployments/{module_index}"
            )
            fact_status, authority, confidence = _fact_projection(data, pointer)
            relations.append(
                {
                    "id": _stable_derived_id(
                        "REL",
                        {
                            "kind": "module_deployment",
                            "moduleId": module_deployment["moduleId"],
                            "deploymentId": deployment["id"],
                        },
                    ),
                    "kind": "deployment",
                    "name": "模块部署到环境",
                    "fromEntityId": module_deployment["moduleId"],
                    "toEntityId": deployment["id"],
                    "direction": "one_way",
                    "architectureScopes": scopes,
                    "factStatus": fact_status,
                    "authority": authority,
                    "confidence": confidence,
                    "evidencePins": [
                        _evidence_pin(
                            pointer=pointer,
                            value=module_deployment,
                            data_hash=data_hash,
                        )
                    ],
                    "semantics": {
                        "protocol": None,
                        "mode": "module_deployment",
                        "dataSummary": None,
                        "order": None,
                        "asynchronous": None,
                    },
                    "attributes": {
                        "environment": deployment["environment"],
                        "deploymentStatus": deployment["status"],
                        "moduleDeploymentStatus": module_deployment["status"],
                        "artifactVersion": module_deployment["artifactVersion"],
                        "runtimeObserved": authority == "observed",
                    },
                }
            )
        for resource_index, resource_id in enumerate(deployment.get("resourceIds", [])):
            pointer = f"/deployments/{deployment_index}/resourceIds/{resource_index}"
            fact_status, authority, confidence = _fact_projection(data, pointer)
            relations.append(
                {
                    "id": _stable_derived_id(
                        "REL",
                        {
                            "kind": "deployment_resource",
                            "deploymentId": deployment["id"],
                            "resourceId": resource_id,
                        },
                    ),
                    "kind": "deployment",
                    "name": "部署使用资源",
                    "fromEntityId": deployment["id"],
                    "toEntityId": resource_id,
                    "direction": "one_way",
                    "architectureScopes": scopes,
                    "factStatus": fact_status,
                    "authority": authority,
                    "confidence": confidence,
                    "evidencePins": [
                        _evidence_pin(
                            pointer=pointer, value=resource_id, data_hash=data_hash
                        )
                    ],
                    "semantics": {
                        "protocol": None,
                        "mode": "deployment_resource",
                        "dataSummary": None,
                        "order": None,
                        "asynchronous": None,
                    },
                    "attributes": {
                        "environment": deployment["environment"],
                        "deploymentStatus": deployment["status"],
                        "runtimeObserved": authority == "observed",
                    },
                }
            )
    relations.sort(key=lambda item: item["id"])

    project_binding = {
        "projectId": project["id"],
        "projectName": project["name"],
        "panoramaSchemaVersion": data["schemaVersion"],
        "revision": meta["revision"],
        "dataHash": data_hash,
    }
    source_observation_binding: dict[str, Any] | None = None
    if source_observation is not None:
        observation_errors = validate_source_observation(source_observation)
        if observation_errors:
            raise PanoramaViewIRError(
                "Source Observation 无效：" + "; ".join(observation_errors[:10])
            )
        if source_observation["projectBinding"]["projectId"] != project["id"]:
            raise PanoramaViewIRError(
                "Source Observation projectId 与 Panorama Core 不匹配。"
            )
        source_binding = _observation_source_binding(source_observation)
        compiled_at = source_observation["observedAt"]
        source_observation_binding = {
            "observationId": source_observation["observationId"],
            "semanticHash": source_observation["integrity"]["semanticHash"],
            "producer": source_observation["producer"],
        }
        for source_element in source_observation["elements"]:
            entities.append(
                {
                    "id": source_element["id"],
                    "kind": "source_element",
                    "name": source_element["name"],
                    "layerBindings": {
                        "current": None,
                        "target": None,
                        "historical": None,
                    },
                    "architectureScopes": ["current"],
                    "purpose": "源码抽取候选；不是正式 Panorama Module。",
                    "factStatus": source_element["factStatus"],
                    "authority": source_element["authority"],
                    "confidence": source_element["confidence"],
                    "evidencePins": [
                        _model_source_pin(pin)
                        for pin in source_element["evidencePins"]
                    ],
                    "attributes": {
                        "sourceObservationId": source_observation["observationId"],
                        "sourceKind": source_element["kind"],
                        "path": source_element["path"],
                        "language": source_element["language"],
                        "parseStatus": source_element["parseStatus"],
                        "sourceAttributes": source_element["attributes"],
                    },
                }
            )
        for source_relation in source_observation["relations"]:
            relations.append(
                {
                    "id": source_relation["id"],
                    "kind": "dependency",
                    "name": source_relation["name"],
                    "fromEntityId": source_relation["fromElementId"],
                    "toEntityId": source_relation["toElementId"],
                    "direction": "one_way",
                    "architectureScopes": ["current"],
                    "factStatus": source_relation["factStatus"],
                    "authority": source_relation["authority"],
                    "confidence": source_relation["confidence"],
                    "evidencePins": [
                        _model_source_pin(pin)
                        for pin in source_relation["evidencePins"]
                    ],
                    "semantics": {
                        "protocol": None,
                        "mode": source_relation["kind"],
                        "dataSummary": None,
                        "order": None,
                        "asynchronous": None,
                    },
                    "attributes": {
                        "sourceObservationId": source_observation["observationId"],
                        "sourceRelationKind": source_relation["kind"],
                        "resolution": source_relation["resolution"],
                        "runtimeObserved": False,
                        "sourceAttributes": source_relation["attributes"],
                    },
                }
            )
        entities.sort(key=lambda item: item["id"])
        relations.sort(key=lambda item: item["id"])
    else:
        source_binding = _source_binding(data)

    module_logic_observation_gaps: set[str] = set()
    seen_current_logic_modules: set[str] = set()
    for observation in module_logic_observations or []:
        observation_errors = validate_module_logic_observation(observation, data)
        if observation_errors:
            raise PanoramaViewIRError(
                "Module Logic Observation 无效："
                + "; ".join(observation_errors[:10])
            )
        module_id = observation["moduleBinding"]["moduleId"]
        if module_id in seen_current_logic_modules:
            raise PanoramaViewIRError(
                f"同一 Module 只能绑定一个 Current Module Logic Observation：{module_id}"
            )
        seen_current_logic_modules.add(module_id)
        currentness = observation["sourceBinding"]["currentness"]
        source_coverage = observation["sourceBinding"]["coverage"]
        logic_coverage = observation["coverage"]["status"]
        if (
            currentness not in {"current", "recorded_as_of"}
            or source_coverage not in {"complete", "partial"}
            or logic_coverage not in {"complete", "partial"}
        ):
            raise PanoramaViewIRError(
                "Current Module Logic 只接受 current/recorded_as_of 且"
                f" complete/partial 的有效 Observation：{module_id}"
            )
        observation_id = observation["observationId"]
        observation_hash = observation["integrity"]["semanticHash"]
        binding_key = f"current_observation:{observation_id}:{observation_hash}"
        module_logic_bindings.append(
            {
                "source": "current_observation",
                "moduleId": module_id,
                "architectureScope": "current",
                "observationId": observation_id,
                "semanticHash": observation_hash,
                "coverage": logic_coverage,
                "sourceCoverage": source_coverage,
                "currentness": currentness,
            }
        )
        pin_by_id = {
            pin["evidenceId"]: _model_module_logic_pin(pin)
            for pin in observation["evidencePins"]
        }
        node_entity_by_local_id: dict[str, str] = {}
        for node in observation["nodes"]:
            entity_id = _stable_derived_id(
                "MLEN",
                {
                    "observationId": observation_id,
                    "kind": "logic_node",
                    "localId": node["id"],
                },
            )
            node_entity_by_local_id[node["id"]] = entity_id
            entities.append(
                {
                    "id": entity_id,
                    "kind": "module_logic_node",
                    "name": node["name"],
                    "displayLabel": node.get("displayName") or node["name"],
                    "technicalLabel": node["name"],
                    "layerBindings": _empty_layer_bindings(),
                    "architectureScopes": ["current"],
                    "purpose": node.get("purpose", ""),
                    "factStatus": node["factStatus"],
                    "authority": node["authority"],
                    "confidence": node["confidence"],
                    "evidencePins": [
                        pin_by_id[pin_id] for pin_id in node["evidencePinIds"]
                    ],
                    "attributes": {
                        "rootModuleId": module_id,
                        "moduleLogicSource": "current_observation",
                        "moduleLogicBindingKey": binding_key,
                        "observationId": observation_id,
                        "logicNodeId": node["id"],
                        "logicType": node["type"],
                        "rationale": node.get("rationale", ""),
                        "implementationSummary": node.get(
                            "implementationSummary", ""
                        ),
                        "loopExitCondition": node.get("loopExitCondition"),
                    },
                }
            )

        for edge in observation["edges"]:
            relations.append(
                {
                    "id": _stable_derived_id(
                        "MLREL",
                        {
                            "observationId": observation_id,
                            "kind": "logic_edge",
                            "localId": edge["id"],
                        },
                    ),
                    "kind": "logic_flow",
                    "name": edge.get("condition") or edge["kind"],
                    "fromEntityId": node_entity_by_local_id[edge["fromNodeId"]],
                    "toEntityId": node_entity_by_local_id[edge["toNodeId"]],
                    "direction": "one_way",
                    "architectureScopes": ["current"],
                    "factStatus": edge["factStatus"],
                    "authority": edge["authority"],
                    "confidence": edge["confidence"],
                    "evidencePins": [
                        pin_by_id[pin_id] for pin_id in edge["evidencePinIds"]
                    ],
                    "semantics": _relation_semantics(
                        mode=edge["kind"],
                        data_summary=edge.get("dataSummary"),
                        order=edge.get("order"),
                    ),
                    "attributes": {
                        "rootModuleId": module_id,
                        "moduleLogicSource": "current_observation",
                        "moduleLogicBindingKey": binding_key,
                        "observationId": observation_id,
                        "logicEdgeId": edge["id"],
                        "condition": edge.get("condition", ""),
                    },
                }
            )

        external_by_id = {
            external["id"]: external for external in observation["externalReferences"]
        }
        for port in observation["boundaryPorts"]:
            port_entity_id = _stable_derived_id(
                "MLPORT",
                {
                    "observationId": observation_id,
                    "kind": "boundary_port",
                    "localId": port["id"],
                },
            )
            external = external_by_id[port["externalReferenceId"]]
            external_entity_id = external["entityRef"]["id"]
            port_pins = [
                pin_by_id[pin_id] for pin_id in port["evidencePinIds"]
            ]
            entities.append(
                {
                    "id": port_entity_id,
                    "kind": "boundary_port",
                    "name": port["name"],
                    "displayLabel": port["name"],
                    "technicalLabel": port["name"],
                    "layerBindings": _empty_layer_bindings(),
                    "architectureScopes": ["current"],
                    "purpose": port.get("dataSummary", ""),
                    "factStatus": port["factStatus"],
                    "authority": port["authority"],
                    "confidence": port["confidence"],
                    "evidencePins": port_pins,
                    "attributes": {
                        "rootModuleId": module_id,
                        "moduleLogicSource": "current_observation",
                        "moduleLogicBindingKey": binding_key,
                        "observationId": observation_id,
                        "boundaryPortId": port["id"],
                        "portDirection": port["direction"],
                        "internalNodeId": port["internalNodeId"],
                        "externalEntityType": external["entityRef"]["type"],
                        "externalEntityId": external_entity_id,
                        "externalReferenceId": external["id"],
                        "bindingKind": port["bindingKind"],
                        "bindingId": port["bindingId"],
                        "dataSummary": port.get("dataSummary", ""),
                        "protocol": port.get("protocol", ""),
                    },
                }
            )
            internal_entity_id = node_entity_by_local_id[port["internalNodeId"]]
            if port["direction"] == "input":
                internal_from, internal_to = port_entity_id, internal_entity_id
                external_from, external_to = external_entity_id, port_entity_id
                direction = "one_way"
            elif port["direction"] == "output":
                internal_from, internal_to = internal_entity_id, port_entity_id
                external_from, external_to = port_entity_id, external_entity_id
                direction = "one_way"
            else:
                internal_from, internal_to = internal_entity_id, port_entity_id
                external_from, external_to = port_entity_id, external_entity_id
                direction = "two_way"
            common_attributes = {
                "rootModuleId": module_id,
                "moduleLogicSource": "current_observation",
                "moduleLogicBindingKey": binding_key,
                "observationId": observation_id,
                "boundaryPortId": port["id"],
                "bindingKind": port["bindingKind"],
                "bindingId": port["bindingId"],
                "portDirection": port["direction"],
            }
            relations.extend(
                [
                    {
                        "id": _stable_derived_id(
                            "MLREL",
                            {"binding": binding_key, "port": port["id"], "side": "internal"},
                        ),
                        "kind": "port_binding",
                        "name": port["name"],
                        "fromEntityId": internal_from,
                        "toEntityId": internal_to,
                        "direction": direction,
                        "architectureScopes": ["current"],
                        "factStatus": port["factStatus"],
                        "authority": port["authority"],
                        "confidence": port["confidence"],
                        "evidencePins": port_pins,
                        "semantics": _relation_semantics(
                            mode="internal_port_binding",
                            protocol=port.get("protocol"),
                            data_summary=port.get("dataSummary"),
                        ),
                        "attributes": {**common_attributes, "boundarySide": "internal"},
                    },
                    {
                        "id": _stable_derived_id(
                            "MLREL",
                            {"binding": binding_key, "port": port["id"], "side": "external"},
                        ),
                        "kind": (
                            "resource_usage"
                            if port["bindingKind"] == "resource_usage"
                            else "port_binding"
                        ),
                        "name": port["name"],
                        "fromEntityId": external_from,
                        "toEntityId": external_to,
                        "direction": direction,
                        "architectureScopes": ["current"],
                        "factStatus": port["factStatus"],
                        "authority": port["authority"],
                        "confidence": port["confidence"],
                        "evidencePins": port_pins,
                        "semantics": _relation_semantics(
                            mode=port["bindingKind"],
                            protocol=port.get("protocol"),
                            data_summary=port.get("dataSummary"),
                        ),
                        "attributes": {**common_attributes, "boundarySide": "external"},
                    },
                ]
            )
        module_logic_observation_gaps.update(observation["informationGaps"])
        if logic_coverage != "complete":
            module_logic_observation_gaps.add(
                f"module_logic_coverage_partial:{module_id}"
            )
        if source_coverage != "complete":
            module_logic_observation_gaps.add(
                f"module_logic_source_coverage_partial:{module_id}"
            )
        if currentness != "current":
            module_logic_observation_gaps.add(
                f"module_logic_currentness_recorded_as_of:{module_id}"
            )
        module_logic_observation_gaps.update(
            f"module_logic_unresolved:{module_id}:{item['kind']}"
            for item in observation["unresolved"]
        )
        module_logic_observation_gaps.update(
            f"module_logic_transformation_loss:{module_id}:{item['code']}"
            for item in observation["transformationLoss"]
        )
    entities.sort(key=lambda item: item["id"])
    relations.sort(key=lambda item: item["id"])
    module_logic_bindings.sort(
        key=lambda item: (item["moduleId"], item["architectureScope"], item["source"])
    )

    event_projection_gaps: set[str] = set()
    event_checkpoint_binding: dict[str, Any] | None = None
    if event_checkpoint is None:
        event_binding = {
            "status": "not_provided",
            "checkpointId": None,
            "checkpointHash": None,
            "asOfSequence": None,
        }
    else:
        checkpoint_errors = validate_event_checkpoint(event_checkpoint)
        if checkpoint_errors:
            raise PanoramaViewIRError(
                "Event Checkpoint 无效：" + "; ".join(checkpoint_errors[:10])
            )
        if event_checkpoint["projectId"] != project["id"]:
            raise PanoramaViewIRError(
                "Event Checkpoint projectId 与 Panorama Core 不匹配。"
            )
        event_binding = {
            "status": "complete",
            "checkpointId": event_checkpoint["checkpointId"],
            "checkpointHash": event_checkpoint["checkpointHash"],
            "asOfSequence": event_checkpoint["asOfSequence"],
        }
        event_checkpoint_binding = {
            "streamId": event_checkpoint["streamId"],
            "epoch": event_checkpoint["epoch"],
            "tailEventId": event_checkpoint["tailEventId"],
            "tailEventHash": event_checkpoint["tailEventHash"],
        }
        compiled_at = event_checkpoint["asOf"]
        entity_by_id = {item["id"]: item for item in entities}

        def add_event_entity(candidate: dict[str, Any]) -> None:
            existing = entity_by_id.get(candidate["id"])
            if existing is None:
                entities.append(candidate)
                entity_by_id[candidate["id"]] = candidate
                return
            if existing["attributes"].get("eventParticipantType") is None:
                return
            evidence_ids = {
                pin["evidenceId"] for pin in existing["evidencePins"]
            }
            for pin in candidate["evidencePins"]:
                if pin["evidenceId"] not in evidence_ids:
                    existing["evidencePins"].append(pin)
            existing["evidencePins"].sort(key=lambda item: item["evidenceId"])
            if existing["authority"] != candidate["authority"]:
                existing["authority"] = "conflict"
                existing["factStatus"] = "conflict"
                existing["confidence"] = "unknown"
            elif existing["confidence"] != candidate["confidence"]:
                existing["confidence"] = "unknown"

        previous_event_entity_id: str | None = None
        lifecycle_transition_count = 0
        for event in event_checkpoint["events"]:
            pin = _event_pin(event)
            actor = event["actor"]
            event_entity_id = _stable_derived_id(
                "EVEVENT", {"eventId": event["eventId"]}, 20
            )
            add_event_entity(
                {
                    "id": event_entity_id,
                    "kind": "unknown",
                    "name": event["eventType"],
                    "layerBindings": {
                        "current": None,
                        "target": None,
                        "historical": None,
                    },
                    "architectureScopes": ["historical"],
                    "purpose": "Engineering Event Timeline Item；不是架构 Module。",
                    "factStatus": _event_fact(event["authority"]),
                    "authority": event["authority"],
                    "confidence": event["confidence"],
                    "evidencePins": [pin],
                    "attributes": {
                        "eventParticipantType": "event",
                        "eventId": event["eventId"],
                        "eventType": event["eventType"],
                        "recordedAt": event["recordedAt"],
                        "occurredAt": event["occurredAt"],
                        "correlationId": event["correlation"]["correlationId"],
                        "outcomeStatus": event["outcome"]["status"],
                        "labelStrength": event["outcome"]["labelStrength"],
                        "streamSequence": event["stream"]["sequence"],
                    },
                }
            )
            if previous_event_entity_id is not None:
                relations.append(
                    {
                        "id": _stable_derived_id(
                            "REL",
                            {
                                "kind": "event_chain_trace",
                                "eventId": event["eventId"],
                            },
                        ),
                        "kind": "trace",
                        "name": "event_chain_next",
                        "fromEntityId": previous_event_entity_id,
                        "toEntityId": event_entity_id,
                        "direction": "one_way",
                        "architectureScopes": ["historical"],
                        "factStatus": "observed",
                        "authority": "observed",
                        "confidence": "high",
                        "evidencePins": [pin],
                        "semantics": {
                            "protocol": None,
                            "mode": "engineering_event_hash_chain",
                            "dataSummary": None,
                            "order": event["stream"]["sequence"],
                            "asynchronous": None,
                        },
                        "attributes": {
                            "eventId": event["eventId"],
                            "correlationId": event["correlation"]["correlationId"],
                            "previousEventHash": event["stream"][
                                "previousEventHash"
                            ],
                            "outcomeStatus": event["outcome"]["status"],
                        },
                    }
                )
            previous_event_entity_id = event_entity_id
            actor_id = _stable_derived_id(
                "EVACT",
                {
                    "kind": actor["kind"],
                    "id": actor["id"],
                    "version": actor["version"],
                },
                20,
            )
            add_event_entity(
                {
                    "id": actor_id,
                    "kind": "actor",
                    "name": actor["id"],
                    "layerBindings": {
                        "current": None,
                        "target": None,
                        "historical": None,
                    },
                    "architectureScopes": ["historical"],
                    "purpose": "Engineering Event Actor；不是 Runtime Service。",
                    "factStatus": _event_fact(event["authority"]),
                    "authority": event["authority"],
                    "confidence": event["confidence"],
                    "evidencePins": [pin],
                    "attributes": {
                        "eventParticipantType": "actor",
                        "actorKind": actor["kind"],
                        "actorId": actor["id"],
                        "actorVersion": actor["version"],
                    },
                }
            )
            if not event["subjectRefs"]:
                event_projection_gaps.add("event_subject_not_provided")
            for subject_index, subject in enumerate(event["subjectRefs"]):
                subject_id = subject["id"]
                if subject_id not in entity_by_id:
                    subject_id = _stable_derived_id(
                        "EVSUB",
                        {"type": subject["type"], "id": subject["id"]},
                        20,
                    )
                    add_event_entity(
                        {
                            "id": subject_id,
                            "kind": "unknown",
                            "name": subject["id"],
                            "layerBindings": {
                                "current": None,
                                "target": None,
                                "historical": None,
                            },
                            "architectureScopes": ["historical"],
                            "purpose": "Event Subject Candidate；未自动提升为正式实体。",
                            "factStatus": _event_fact(event["authority"]),
                            "authority": event["authority"],
                            "confidence": event["confidence"],
                            "evidencePins": [pin],
                            "attributes": {
                                "eventParticipantType": "subject",
                                "subjectType": subject["type"],
                                "subjectId": subject["id"],
                            },
                        }
                    )
                relations.append(
                    {
                        "id": _stable_derived_id(
                            "REL",
                            {
                                "eventId": event["eventId"],
                                "subjectIndex": subject_index,
                                "subjectType": subject["type"],
                                "subjectId": subject["id"],
                            },
                        ),
                        "kind": "sequence_message",
                        "name": event["eventType"],
                        "fromEntityId": actor_id,
                        "toEntityId": subject_id,
                        "direction": "one_way",
                        "architectureScopes": ["historical"],
                        "factStatus": _event_fact(event["authority"]),
                        "authority": event["authority"],
                        "confidence": event["confidence"],
                        "evidencePins": [pin],
                        "semantics": {
                            "protocol": None,
                            "mode": "engineering_event_action",
                            "dataSummary": None,
                            "order": event["stream"]["sequence"],
                            "asynchronous": None,
                        },
                        "attributes": {
                            "eventId": event["eventId"],
                            "eventType": event["eventType"],
                            "recordedAt": event["recordedAt"],
                            "occurredAt": event["occurredAt"],
                            "correlationId": event["correlation"]["correlationId"],
                            "subjectType": subject["type"],
                            "subjectRelationship": subject["relationship"],
                            "outcomeStatus": event["outcome"]["status"],
                            "labelStrength": event["outcome"]["labelStrength"],
                            "runtimeCallObserved": False,
                        },
                    }
                )
            projection = event["extensions"].get("panoramaProjection", {})
            if not isinstance(projection, dict):
                raise PanoramaViewIRError(
                    f"Event {event['eventId']} panoramaProjection 无效。"
                )
            transitions = projection.get("lifecycleTransitions", [])
            if not isinstance(transitions, list):
                raise PanoramaViewIRError(
                    f"Event {event['eventId']} lifecycleTransitions 无效。"
                )
            valid_subjects = {
                (item["type"], item["id"]) for item in event["subjectRefs"]
            }
            for transition_index, transition in enumerate(transitions):
                required_transition = {
                    "subjectType",
                    "subjectId",
                    "fromState",
                    "toState",
                    "trigger",
                }
                if (
                    not isinstance(transition, dict)
                    or set(transition) != required_transition
                    or not all(
                        isinstance(transition[field], str)
                        and 0 < len(transition[field]) <= 128
                        for field in required_transition
                    )
                    or (transition["subjectType"], transition["subjectId"])
                    not in valid_subjects
                ):
                    raise PanoramaViewIRError(
                        f"Event {event['eventId']} lifecycleTransitions/{transition_index} 无效。"
                    )
                state_entity_ids: list[str] = []
                for state_role in ("fromState", "toState"):
                    state_entity_id = _stable_derived_id(
                        "EVSTATE",
                        {
                            "subjectType": transition["subjectType"],
                            "subjectId": transition["subjectId"],
                            "state": transition[state_role],
                        },
                        20,
                    )
                    state_entity_ids.append(state_entity_id)
                    add_event_entity(
                        {
                            "id": state_entity_id,
                            "kind": "unknown",
                            "name": transition[state_role],
                            "layerBindings": {
                                "current": None,
                                "target": None,
                                "historical": None,
                            },
                            "architectureScopes": ["historical"],
                            "purpose": "显式 Event Lifecycle State；不是推断状态。",
                            "factStatus": _event_fact(event["authority"]),
                            "authority": event["authority"],
                            "confidence": event["confidence"],
                            "evidencePins": [pin],
                            "attributes": {
                                "eventParticipantType": "lifecycle_state",
                                "subjectType": transition["subjectType"],
                                "subjectId": transition["subjectId"],
                                "state": transition[state_role],
                            },
                        }
                    )
                relations.append(
                    {
                        "id": _stable_derived_id(
                            "REL",
                            {
                                "kind": "event_state_transition",
                                "eventId": event["eventId"],
                                "transitionIndex": transition_index,
                            },
                        ),
                        "kind": "state_transition",
                        "name": transition["trigger"],
                        "fromEntityId": state_entity_ids[0],
                        "toEntityId": state_entity_ids[1],
                        "direction": "one_way",
                        "architectureScopes": ["historical"],
                        "factStatus": _event_fact(event["authority"]),
                        "authority": event["authority"],
                        "confidence": event["confidence"],
                        "evidencePins": [pin],
                        "semantics": {
                            "protocol": None,
                            "mode": "explicit_event_state_transition",
                            "dataSummary": None,
                            "order": event["stream"]["sequence"],
                            "asynchronous": None,
                        },
                        "attributes": {
                            "eventId": event["eventId"],
                            "correlationId": event["correlation"]["correlationId"],
                            "subjectType": transition["subjectType"],
                            "subjectId": transition["subjectId"],
                            "fromState": transition["fromState"],
                            "toState": transition["toState"],
                            "trigger": transition["trigger"],
                        },
                    }
                )
                lifecycle_transition_count += 1
        if lifecycle_transition_count == 0:
            event_projection_gaps.add("event_lifecycle_transition_not_provided")
        entities.sort(key=lambda item: item["id"])
        relations.sort(key=lambda item: item["id"])
    as_of = {
        "mode": (
            "event_checkpoint"
            if event_checkpoint is not None
            else "explicit_time"
            if source_observation is not None
            else "recorded_as_of"
        ),
        "value": compiled_at,
    }
    model_id = _stable_derived_id(
        "MODEL",
        {
            "compiler": MODEL_COMPILER,
            "projectBinding": project_binding,
            "sourceBinding": source_binding,
            "eventBinding": event_binding,
            "moduleLogicBindings": module_logic_bindings,
            "asOf": as_of,
        },
    )
    information_gaps = (
        ["event_checkpoint_not_provided"]
        if event_checkpoint is None
        else sorted(event_projection_gaps)
    )
    if source_binding["currentness"] != "current":
        information_gaps.append("source_currentness_not_verified")
    if source_observation is not None:
        information_gaps.extend(source_observation["informationGaps"])
    information_gaps.extend(sorted(module_logic_observation_gaps))
    for layer in architecture.get("layers", []):
        if not isinstance(layer.get("displayName"), str) or not layer["displayName"].strip():
            information_gaps.append(f"display_name_missing:layer:{layer.get('id', 'unknown')}")
    for module in architecture.get("modules", []):
        if not isinstance(module.get("displayName"), str) or not module["displayName"].strip():
            information_gaps.append(f"display_name_missing:module:{module.get('id', 'unknown')}")

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
        "moduleLogicBindings": module_logic_bindings,
        "informationGaps": sorted(information_gaps),
        "extensions": {
            **(
                {"layeringProfile": deepcopy(architecture.get("extensions", {}).get("layeringProfile"))}
                if isinstance(architecture.get("extensions", {}).get("layeringProfile"), dict)
                else {}
            ),
            **(
                {"sourceObservationBinding": source_observation_binding}
                if source_observation_binding is not None
                else {}
            ),
            **(
                {"eventCheckpointBinding": event_checkpoint_binding}
                if event_checkpoint_binding is not None
                else {}
            ),
        },
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
    layer_parent: dict[str, str] = {}
    for layer in model["layers"]:
        parent_id = layer.get("parentLayerId")
        if parent_id is not None:
            if parent_id not in layer_set:
                errors.append(
                    f"/layers/{layer['id']}/parentLayerId: 未知父 Layer {parent_id}"
                )
            else:
                layer_parent[layer["id"]] = parent_id
    for layer_id in sorted(layer_parent):
        visited: set[str] = set()
        current = layer_id
        while current in layer_parent:
            if current in visited:
                errors.append(f"/layers/{layer_id}: 父子层级形成循环")
                break
            visited.add(current)
            current = layer_parent[current]
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
    module_ids = {
        item["id"] for item in model["entities"] if item["kind"] == "module"
    }
    binding_keys: set[tuple[str, str]] = set()
    for index, binding in enumerate(model.get("moduleLogicBindings", [])):
        module_id = binding["moduleId"]
        scope = binding["architectureScope"]
        key = (module_id, scope)
        if key in binding_keys:
            errors.append(
                f"/moduleLogicBindings/{index}: 同一 Module/Scope 存在重复 Binding"
            )
        binding_keys.add(key)
        if module_id not in module_ids:
            errors.append(
                f"/moduleLogicBindings/{index}/moduleId: 未知 Module {module_id}"
            )
        if binding["source"] == "current_observation":
            binding_key = (
                f"current_observation:{binding['observationId']}:{binding['semanticHash']}"
            )
        else:
            binding_key = (
                f"formal_design:{binding['designId']}:{binding['designDigest']}"
            )
        bound_entities = [
            item
            for item in model["entities"]
            if item.get("attributes", {}).get("moduleLogicBindingKey") == binding_key
        ]
        if not any(item["kind"] == "module_logic_node" for item in bound_entities):
            errors.append(
                f"/moduleLogicBindings/{index}: 未找到绑定的 Module Logic Node"
            )
        if any(
            item.get("attributes", {}).get("rootModuleId") != module_id
            or scope not in item["architectureScopes"]
            for item in bound_entities
        ):
            errors.append(
                f"/moduleLogicBindings/{index}: Entity Root Module 或 Scope 与 Binding 不匹配"
            )
    for entity in model["entities"]:
        if entity["kind"] in {"module_logic_node", "boundary_port"}:
            root_module_id = entity.get("attributes", {}).get("rootModuleId")
            if root_module_id not in module_ids:
                errors.append(
                    f"/entities/{entity['id']}: Module Logic Entity 未绑定正式 Module"
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
        if relation["kind"] == "communication"
        and relation["fromEntityId"] in selected_entity_ids
        and relation["toEntityId"] in selected_entity_ids
        and set(relation["architectureScopes"]) & set(ordered_scopes)
    ]
    selected_scope = ordered_scopes[0]
    direct_layer_ids = {
        entity["layerBindings"][selected_scope]
        for entity in selected_entities
        if entity["layerBindings"].get(selected_scope) is not None
    }
    layer_by_id = {layer["id"]: layer for layer in model["layers"]}
    layer_ids = set(direct_layer_ids)
    for direct_layer_id in sorted(direct_layer_ids):
        current_layer_id = direct_layer_id
        visited: set[str] = set()
        while current_layer_id in layer_by_id:
            parent_id = layer_by_id[current_layer_id].get("parentLayerId")
            if parent_id is None or parent_id in visited:
                break
            layer_ids.add(parent_id)
            visited.add(parent_id)
            current_layer_id = parent_id
    selected_layers = [layer for layer in model["layers"] if layer["id"] in layer_ids]

    group_by_layer: dict[str, str] = {
        layer["id"]: _stable_derived_id("GROUP", {"layerId": layer["id"]}, 20)
        for layer in selected_layers
    }
    groups: list[dict[str, Any]] = []
    for layer in selected_layers:
        group_id = group_by_layer[layer["id"]]
        groups.append(
            {
                "id": group_id,
                "groupType": "layer",
                "groupRef": layer["id"],
                "layerId": layer["id"],
                "label": layer.get("displayLabel", layer["name"]),
                "order": layer["order"],
                "parentGroupId": group_by_layer.get(layer.get("parentLayerId")),
                **({"kind": layer["kind"]} if layer.get("kind") else {}),
                "summary": layer.get("summary", ""),
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
                "label": entity.get("displayLabel", entity["name"]),
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


def _module_logic_binding_key(binding: dict[str, Any]) -> str:
    if binding["source"] == "current_observation":
        return (
            f"current_observation:{binding['observationId']}:{binding['semanticHash']}"
        )
    return f"formal_design:{binding['designId']}:{binding['designDigest']}"


def compile_module_logic_view_ir(
    model: dict[str, Any],
    *,
    parent_view: dict[str, Any],
    root_module_id: str,
    architecture_scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Compile one evidence-bound module child canvas from the shared Model IR."""
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:10]))
    parent_errors = validate_view_ir(parent_view, model)
    if parent_errors:
        raise PanoramaViewIRError(
            "父架构 View IR 无效：" + "; ".join(parent_errors[:10])
        )
    if parent_view["profile"] != "module" or parent_view["viewType"] != "architecture":
        raise PanoramaViewIRError("Module Logic 子画布必须绑定 module 架构父 View。")
    scopes = _checked_single_scope(architecture_scopes, profile="module_logic")
    scope = scopes[0]
    if parent_view["filters"]["architectureScopes"] != scopes:
        raise PanoramaViewIRError("Module Logic Scope 必须与父架构 View 精确一致。")
    parent_entity_ids = {
        node["entityRef"]["id"] for node in parent_view["nodes"]
    }
    if root_module_id not in parent_entity_ids:
        raise PanoramaViewIRError(
            f"父架构 View 未投影 Root Module：{root_module_id}"
        )
    entity_by_id = {item["id"]: item for item in model["entities"]}
    root_module = entity_by_id.get(root_module_id)
    if root_module is None or root_module["kind"] != "module":
        raise PanoramaViewIRError(f"未知正式 Root Module：{root_module_id}")

    matches = [
        item
        for item in model.get("moduleLogicBindings", [])
        if item["moduleId"] == root_module_id
        and item["architectureScope"] == scope
    ]
    if len(matches) != 1:
        raise PanoramaViewIRError(
            f"Root Module/Scope 必须精确绑定一个 Module Logic 数据源："
            f"{root_module_id}/{scope}，实际 {len(matches)} 个"
        )
    binding = matches[0]
    if scope == "current" and binding["source"] != "current_observation":
        raise PanoramaViewIRError("Current Module Logic 只能来自源码 Observation。")
    if scope in {"target", "historical"} and binding["source"] != "formal_design":
        raise PanoramaViewIRError("Target/Historical Module Logic 只能来自正式 Design。")
    binding_key = _module_logic_binding_key(binding)
    internal_entities = [
        item
        for item in model["entities"]
        if item.get("attributes", {}).get("moduleLogicBindingKey") == binding_key
    ]
    internal_ids = {item["id"] for item in internal_entities}
    selected_relations = [
        item
        for item in model["relations"]
        if item.get("attributes", {}).get("moduleLogicBindingKey") == binding_key
    ]
    selected_ids = set(internal_ids)
    for relation in selected_relations:
        selected_ids.add(relation["fromEntityId"])
        selected_ids.add(relation["toEntityId"])
    external_ids = selected_ids - internal_ids
    for external_id in external_ids:
        external = entity_by_id.get(external_id)
        if external is None or external["kind"] not in {
            "module",
            "resource",
            "data_store",
        }:
            raise PanoramaViewIRError(
                f"Module Logic 外部端点只能复用正式 Module/Resource：{external_id}"
            )
    selected_entities = [entity_by_id[item] for item in sorted(selected_ids)]

    group_specs = [
        (
            "internal",
            f"{root_module.get('displayLabel', root_module['name'])} · 内部逻辑",
            0,
        ),
        ("boundary", "模块边界端口", 1),
        ("external", "外部交互模块与资源", 2),
    ]
    group_ids: dict[str, str] = {}
    groups: list[dict[str, Any]] = []
    for role, label, order in group_specs:
        group_id = _stable_derived_id(
            "GROUP",
            {"rootModuleId": root_module_id, "scope": scope, "role": role},
            20,
        )
        group_ids[role] = group_id
        groups.append(
            {
                "id": group_id,
                "groupType": "source_kind",
                "groupRef": _stable_derived_id(
                    "MLGROUP",
                    {"rootModuleId": root_module_id, "scope": scope, "role": role},
                    20,
                ),
                "layerId": None,
                "label": label,
                "order": order,
            }
        )

    node_by_entity: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for entity in selected_entities:
        if entity["id"] in external_ids:
            role = "external"
        elif entity["kind"] == "boundary_port":
            role = "boundary"
        else:
            role = "internal"
        node = _view_node(entity, group_id=group_ids[role])
        if role == "boundary":
            node["emphasis"] = "primary"
        node_by_entity[entity["id"]] = node["id"]
        nodes.append(node)
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
    parent_binding = {
        "viewId": parent_view["viewId"],
        "semanticHash": parent_view["integrity"]["semanticHash"],
    }
    if binding["source"] == "current_observation":
        view_logic_binding = {
            "source": "current_observation",
            "moduleId": root_module_id,
            "observationId": binding["observationId"],
            "semanticHash": binding["semanticHash"],
            "coverage": binding["coverage"],
            "sourceCoverage": binding["sourceCoverage"],
            "currentness": binding["currentness"],
        }
    else:
        view_logic_binding = {
            "source": "formal_design",
            "moduleId": root_module_id,
            "designId": binding["designId"],
            "designDigest": binding["designDigest"],
        }
    view_id = _stable_derived_id(
        "VIEW",
        {
            "compiler": MODULE_LOGIC_VIEW_COMPILER,
            "modelSemanticHash": model_binding["modelSemanticHash"],
            "parentViewBinding": parent_binding,
            "moduleLogicBinding": view_logic_binding,
            "architectureScopes": scopes,
        },
    )
    layout = {"strategy": "auto_layered", "positions": []}
    view: dict[str, Any] = {
        "formatVersion": "panorama-view-ir.v0.1",
        "viewId": view_id,
        "generatedAt": model["compiledAt"],
        "compiler": MODULE_LOGIC_VIEW_COMPILER,
        "modelBinding": model_binding,
        "viewType": "module_logic",
        "profile": "module_logic",
        "title": f"{root_module.get('displayLabel', root_module['name'])} · 模块内部逻辑",
        "description": (
            "同一全景画布中的模块子画布；内部逻辑来自绑定数据源，"
            "外部模块与资源仅显示紧凑引用。"
            + (
                " 当前为局部、按时点记录的只读源码归纳；未证明部分以信息缺口披露。"
                if binding["source"] == "current_observation"
                and (
                    binding["coverage"] != "complete"
                    or binding["currentness"] != "current"
                )
                else ""
            )
        ),
        "rootModuleId": root_module_id,
        "parentViewBinding": parent_binding,
        "moduleLogicBinding": view_logic_binding,
        "filters": {
            "architectureScopes": scopes,
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
        "extensions": {
            "canvasLevel": "module_logic",
            "internalEntityIds": sorted(internal_ids),
            "boundaryPortEntityIds": sorted(
                item["id"]
                for item in internal_entities
                if item["kind"] == "boundary_port"
            ),
            "externalEntityIds": sorted(external_ids),
            "navigation": {
                "enterGesture": "double_click_module",
                "backTargetViewId": parent_view["viewId"],
            },
        },
    }
    view["integrity"] = {
        "hashAlgorithm": "sha256",
        "semanticHash": compute_view_semantic_hash(view),
        "semanticHashScope": "view_without_layout_or_integrity",
        "layoutHash": compute_view_layout_hash(view),
        "layoutHashScope": "layout_only",
    }
    errors = validate_view_ir(view, model, parent_view=parent_view)
    if errors:
        raise PanoramaViewIRError(
            "Module Logic View IR 编译结果无效：" + "; ".join(errors[:10])
        )
    return view


def _checked_single_scope(
    architecture_scopes: list[str] | None, *, profile: str
) -> list[str]:
    scopes = architecture_scopes or ["current"]
    allowed = {"current", "target", "transition", "historical"}
    if not scopes or len(scopes) != len(set(scopes)) or not set(scopes) <= allowed:
        raise PanoramaViewIRError(
            f"{profile} architecture_scopes 必须是非空、唯一的合法范围。"
        )
    if len(scopes) != 1:
        raise PanoramaViewIRError(f"{profile} 每次只允许一个 architecture scope。")
    return [
        scope
        for scope in ("current", "target", "transition", "historical")
        if scope in scopes
    ]


def _view_node(entity: dict[str, Any], *, group_id: str | None) -> dict[str, Any]:
    return {
        "id": _stable_derived_id("NODE", {"entityId": entity["id"]}, 20),
        "entityRef": {"type": "entity", "id": entity["id"]},
        "label": entity.get("displayLabel", entity["name"]),
        "kind": entity["kind"],
        "groupId": group_id,
        "factStatus": entity["factStatus"],
        "authority": entity["authority"],
        "confidence": entity["confidence"],
        "evidencePinIds": sorted(
            pin["evidenceId"] for pin in entity["evidencePins"]
        ),
        "emphasis": "unknown" if entity["factStatus"] == "unknown" else "default",
    }


def _view_edge(
    relation: dict[str, Any], *, node_by_entity: dict[str, str]
) -> dict[str, Any]:
    return {
        "id": _stable_derived_id("EDGE", {"relationId": relation["id"]}, 20),
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


def compile_dependency_dataflow_view_ir(
    model: dict[str, Any],
    *,
    architecture_scopes: list[str] | None = None,
    root_entity_ids: list[str] | None = None,
    max_depth: int = 2,
    max_nodes: int = 30,
) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:10]))
    scopes = _checked_single_scope(
        architecture_scopes, profile="dependency_dataflow"
    )
    if max_depth < 0 or max_depth > 16:
        raise PanoramaViewIRError("max_depth 必须位于 0..16。")
    if max_nodes < 1 or max_nodes > 100000:
        raise PanoramaViewIRError("max_nodes 必须位于 1..100000。")
    roots = root_entity_ids or []
    if len(roots) != len(set(roots)):
        raise PanoramaViewIRError("root_entity_ids 不能重复。")

    entities_by_id = {item["id"]: item for item in model["entities"]}
    eligible_relations = [
        relation
        for relation in model["relations"]
        if relation["kind"] in {"dependency", "data_flow"}
        and set(relation["architectureScopes"]) & set(scopes)
    ]
    endpoint_ids = {
        entity_id
        for relation in eligible_relations
        for entity_id in (relation["fromEntityId"], relation["toEntityId"])
    }
    selected_ids: set[str]
    if roots:
        unknown = sorted(set(roots) - set(entities_by_id))
        if unknown:
            raise PanoramaViewIRError(
                "dependency_dataflow 使用未知 root entity：" + ", ".join(unknown)
            )
        out_of_scope = sorted(
            entity_id
            for entity_id in roots
            if not set(entities_by_id[entity_id]["architectureScopes"])
            & set(scopes)
        )
        if out_of_scope:
            raise PanoramaViewIRError(
                "dependency_dataflow root entity 不属于所选 scope："
                + ", ".join(out_of_scope)
            )
        selected_ids = set(roots)
        adjacency: dict[str, set[str]] = {}
        for relation in eligible_relations:
            source = relation["fromEntityId"]
            target = relation["toEntityId"]
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
        frontier = set(roots)
        for _ in range(max_depth):
            next_frontier = {
                neighbor
                for entity_id in frontier
                for neighbor in adjacency.get(entity_id, set())
                if neighbor not in selected_ids
            }
            selected_ids.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
    else:
        selected_ids = endpoint_ids

    if len(selected_ids) > max_nodes:
        raise PanoramaViewIRError(
            "dependency_dataflow 节点数 "
            f"{len(selected_ids)} 超过 max_nodes={max_nodes}；请提供精确 root entity。"
        )
    selected_entities = sorted(
        (entities_by_id[entity_id] for entity_id in selected_ids),
        key=lambda item: item["id"],
    )
    selected_relations = sorted(
        (
            relation
            for relation in eligible_relations
            if relation["fromEntityId"] in selected_ids
            and relation["toEntityId"] in selected_ids
        ),
        key=lambda item: item["id"],
    )

    source_kinds = sorted(
        {
            str(entity["attributes"].get("sourceKind"))
            for entity in selected_entities
            if entity["kind"] == "source_element"
            and entity["attributes"].get("sourceKind")
        }
    )
    source_kind_labels = {
        "source_file": "源码文件",
        "manifest": "Manifest",
        "configuration": "配置",
        "external_package": "外部依赖",
        "unresolved_target": "未解析目标",
    }
    group_by_source_kind: dict[str, str] = {}
    groups: list[dict[str, Any]] = []
    for order, source_kind in enumerate(source_kinds):
        group_id = _stable_derived_id(
            "GROUP", {"groupType": "source_kind", "groupRef": source_kind}, 20
        )
        group_by_source_kind[source_kind] = group_id
        groups.append(
            {
                "id": group_id,
                "groupType": "source_kind",
                "groupRef": source_kind,
                "layerId": None,
                "label": source_kind_labels.get(source_kind, source_kind),
                "order": order,
            }
        )
    nodes = [
        _view_node(
            entity,
            group_id=group_by_source_kind.get(
                str(entity["attributes"].get("sourceKind"))
            ),
        )
        for entity in selected_entities
    ]
    node_by_entity = {
        node["entityRef"]["id"]: node["id"] for node in nodes
    }
    edges = [
        _view_edge(relation, node_by_entity=node_by_entity)
        for relation in selected_relations
    ]
    information_gaps = set(model["informationGaps"])
    if not any(item["kind"] == "dependency" for item in selected_relations):
        information_gaps.add("dependency_relations_not_available")
    if not any(item["kind"] == "data_flow" for item in selected_relations):
        information_gaps.add("data_flow_relations_not_available")
    model_binding = {
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "projectId": model["projectBinding"]["projectId"],
        "panoramaDataHash": model["projectBinding"]["dataHash"],
        "asOf": model["asOf"]["value"],
    }
    view: dict[str, Any] = {
        "formatVersion": "panorama-view-ir.v0.1",
        "viewId": _stable_derived_id(
            "VIEW",
            {
                "compiler": DEPENDENCY_VIEW_COMPILER,
                "modelSemanticHash": model_binding["modelSemanticHash"],
                "profile": "dependency_dataflow",
                "architectureScopes": scopes,
                "roots": sorted(roots),
                "maxDepth": max_depth,
                "maxNodes": max_nodes,
            },
        ),
        "generatedAt": model["compiledAt"],
        "compiler": DEPENDENCY_VIEW_COMPILER,
        "modelBinding": model_binding,
        "viewType": "dataflow",
        "profile": "dependency_dataflow",
        "title": f"{model['projectBinding']['projectName']} 依赖与数据流",
        "description": "只投影已有 dependency/data_flow 关系；静态依赖不是运行调用。",
        "filters": {
            "architectureScopes": scopes,
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
        "informationGaps": sorted(information_gaps),
        "layout": {"strategy": "auto_layered", "positions": []},
        "extensions": {
            "focus": {
                "rootEntityIds": sorted(roots),
                "maxDepth": max_depth,
                "maxNodes": max_nodes,
            }
        },
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
        raise PanoramaViewIRError(
            "Dependency/Data Flow View IR 编译结果无效：" + "; ".join(errors[:10])
        )
    return view


def compile_deployment_runtime_view_ir(
    model: dict[str, Any],
    *,
    architecture_scopes: list[str] | None = None,
    environments: list[str] | None = None,
) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:10]))
    scopes = _checked_single_scope(
        architecture_scopes, profile="deployment_runtime"
    )
    selected_environments = sorted(set(environments or []))
    if environments and len(environments) != len(selected_environments):
        raise PanoramaViewIRError("environments 不能重复。")
    eligible_relations = [
        relation
        for relation in model["relations"]
        if relation["kind"] == "deployment"
        and set(relation["architectureScopes"]) & set(scopes)
        and (
            not selected_environments
            or relation["attributes"].get("environment")
            in selected_environments
        )
    ]
    selected_ids = {
        entity_id
        for relation in eligible_relations
        for entity_id in (relation["fromEntityId"], relation["toEntityId"])
    }
    entities_by_id = {item["id"]: item for item in model["entities"]}
    selected_entities = sorted(
        (entities_by_id[entity_id] for entity_id in selected_ids),
        key=lambda item: item["id"],
    )
    environment_values = sorted(
        {
            str(entity["attributes"].get("environment"))
            for entity in selected_entities
            if entity["kind"] in {"deployment", "resource", "data_store"}
            and entity["attributes"].get("environment")
        }
    )
    group_by_environment: dict[str, str] = {}
    groups: list[dict[str, Any]] = []
    for order, environment in enumerate(environment_values):
        group_id = _stable_derived_id(
            "GROUP", {"groupType": "environment", "groupRef": environment}, 20
        )
        group_by_environment[environment] = group_id
        groups.append(
            {
                "id": group_id,
                "groupType": "environment",
                "groupRef": environment,
                "layerId": None,
                "label": environment,
                "order": order,
            }
        )
    nodes = [
        _view_node(
            entity,
            group_id=group_by_environment.get(
                str(entity["attributes"].get("environment"))
            ),
        )
        for entity in selected_entities
    ]
    node_by_entity = {
        node["entityRef"]["id"]: node["id"] for node in nodes
    }
    edges = [
        _view_edge(relation, node_by_entity=node_by_entity)
        for relation in sorted(eligible_relations, key=lambda item: item["id"])
    ]
    information_gaps = set(model["informationGaps"])
    if not edges:
        information_gaps.add("deployment_relations_not_available")
    if any(
        relation["attributes"].get("runtimeObserved") is not True
        for relation in eligible_relations
    ):
        information_gaps.add("deployment_runtime_observation_not_verified")
    model_binding = {
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "projectId": model["projectBinding"]["projectId"],
        "panoramaDataHash": model["projectBinding"]["dataHash"],
        "asOf": model["asOf"]["value"],
    }
    filters: dict[str, Any] = {
        "architectureScopes": scopes,
        "factStatuses": [
            "observed",
            "declared",
            "derived",
            "unknown",
            "conflict",
        ],
    }
    if selected_environments:
        filters["environments"] = selected_environments
    view: dict[str, Any] = {
        "formatVersion": "panorama-view-ir.v0.1",
        "viewId": _stable_derived_id(
            "VIEW",
            {
                "compiler": DEPLOYMENT_VIEW_COMPILER,
                "modelSemanticHash": model_binding["modelSemanticHash"],
                "profile": "deployment_runtime",
                "architectureScopes": scopes,
                "environments": selected_environments,
            },
        ),
        "generatedAt": model["compiledAt"],
        "compiler": DEPLOYMENT_VIEW_COMPILER,
        "modelBinding": model_binding,
        "viewType": "deployment",
        "profile": "deployment_runtime",
        "title": f"{model['projectBinding']['projectName']} 部署与运行",
        "description": "投影正式 Deployment/Release/Resource；Declared 与 Observed Runtime 分层。",
        "filters": filters,
        "groups": groups,
        "nodes": nodes,
        "edges": edges,
        "informationGaps": sorted(information_gaps),
        "layout": {"strategy": "auto_layered", "positions": []},
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
        raise PanoramaViewIRError(
            "Deployment/Runtime View IR 编译结果无效：" + "; ".join(errors[:10])
        )
    return view


def compile_sequence_view_ir(
    model: dict[str, Any],
    *,
    architecture_scopes: list[str] | None = None,
    correlation_ids: list[str] | None = None,
) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:10]))
    scopes = _checked_single_scope(
        architecture_scopes or ["historical"], profile="sequence"
    )
    correlations = sorted(set(correlation_ids or []))
    if correlation_ids and len(correlation_ids) != len(correlations):
        raise PanoramaViewIRError("correlation_ids 不能重复。")
    selected_relations = [
        relation
        for relation in model["relations"]
        if relation["kind"] == "sequence_message"
        and set(relation["architectureScopes"]) & set(scopes)
        and (
            not correlations
            or relation["attributes"].get("correlationId") in correlations
        )
    ]
    selected_relations.sort(
        key=lambda item: (
            item["semantics"]["order"],
            item["attributes"].get("eventId", ""),
            item["id"],
        )
    )
    selected_ids = {
        entity_id
        for relation in selected_relations
        for entity_id in (relation["fromEntityId"], relation["toEntityId"])
    }
    entities_by_id = {item["id"]: item for item in model["entities"]}
    nodes = [
        _view_node(entities_by_id[entity_id], group_id=None)
        for entity_id in sorted(selected_ids)
    ]
    node_by_entity = {
        node["entityRef"]["id"]: node["id"] for node in nodes
    }
    edges = [
        _view_edge(relation, node_by_entity=node_by_entity)
        for relation in selected_relations
    ]
    information_gaps = set(model["informationGaps"])
    if not edges:
        information_gaps.add("sequence_messages_not_available")
    if any(
        relation["semantics"]["asynchronous"] is None
        for relation in selected_relations
    ):
        information_gaps.add("sequence_async_semantics_unknown")
    if any(
        relation["attributes"].get("occurredAt") is None
        for relation in selected_relations
    ):
        information_gaps.add("sequence_occurrence_time_not_provided")
    model_binding = {
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "projectId": model["projectBinding"]["projectId"],
        "panoramaDataHash": model["projectBinding"]["dataHash"],
        "asOf": model["asOf"]["value"],
    }
    view: dict[str, Any] = {
        "formatVersion": "panorama-view-ir.v0.1",
        "viewId": _stable_derived_id(
            "VIEW",
            {
                "compiler": SEQUENCE_VIEW_COMPILER,
                "modelSemanticHash": model_binding["modelSemanticHash"],
                "profile": "sequence",
                "architectureScopes": scopes,
                "correlationIds": correlations,
            },
        ),
        "generatedAt": model["compiledAt"],
        "compiler": SEQUENCE_VIEW_COMPILER,
        "modelBinding": model_binding,
        "viewType": "sequence",
        "profile": "sequence",
        "title": f"{model['projectBinding']['projectName']} 工程事件时序",
        "description": "按 Event Stream Sequence 投影 Actor → Subject 工程动作；不是 Runtime Call。",
        "filters": {
            "architectureScopes": scopes,
            "factStatuses": [
                "observed",
                "declared",
                "derived",
                "unknown",
                "conflict",
            ],
        },
        "groups": [],
        "nodes": nodes,
        "edges": edges,
        "informationGaps": sorted(information_gaps),
        "layout": {"strategy": "none", "positions": []},
        "extensions": {"correlationIds": correlations},
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
        raise PanoramaViewIRError(
            "Sequence View IR 编译结果无效：" + "; ".join(errors[:10])
        )
    return view


def _compile_event_projection_view(
    model: dict[str, Any],
    *,
    profile: str,
    view_type: str,
    relation_kind: str,
    compiler: dict[str, str],
    title_suffix: str,
    description: str,
    architecture_scopes: list[str] | None,
    correlation_ids: list[str] | None,
) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:10]))
    scopes = _checked_single_scope(
        architecture_scopes or ["historical"], profile=profile
    )
    correlations = sorted(set(correlation_ids or []))
    if correlation_ids and len(correlation_ids) != len(correlations):
        raise PanoramaViewIRError("correlation_ids 不能重复。")
    selected_relations = [
        relation
        for relation in model["relations"]
        if relation["kind"] == relation_kind
        and set(relation["architectureScopes"]) & set(scopes)
        and (
            not correlations
            or relation["attributes"].get("correlationId") in correlations
        )
    ]
    selected_relations.sort(
        key=lambda item: (item["semantics"]["order"], item["id"])
    )
    selected_ids = {
        entity_id
        for relation in selected_relations
        for entity_id in (relation["fromEntityId"], relation["toEntityId"])
    }
    if profile == "evolution_risk":
        selected_ids.update(
            entity["id"]
            for entity in model["entities"]
            if entity["attributes"].get("eventParticipantType") == "event"
            and set(entity["architectureScopes"]) & set(scopes)
            and (
                not correlations
                or entity["attributes"].get("correlationId") in correlations
            )
        )
    entities_by_id = {item["id"]: item for item in model["entities"]}
    nodes = [
        _view_node(entities_by_id[entity_id], group_id=None)
        for entity_id in sorted(selected_ids)
    ]
    if profile == "evolution_risk":
        for node in nodes:
            entity = entities_by_id[node["entityRef"]["id"]]
            if entity["attributes"].get("outcomeStatus") in {"failed", "rejected"}:
                node["emphasis"] = "risk"
    node_by_entity = {
        node["entityRef"]["id"]: node["id"] for node in nodes
    }
    edges = [
        _view_edge(relation, node_by_entity=node_by_entity)
        for relation in selected_relations
    ]
    information_gaps = set(model["informationGaps"])
    if not edges and profile == "lifecycle":
        information_gaps.add("lifecycle_transitions_not_available")
    if not nodes and profile == "evolution_risk":
        information_gaps.add("evolution_events_not_available")
    model_binding = {
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "projectId": model["projectBinding"]["projectId"],
        "panoramaDataHash": model["projectBinding"]["dataHash"],
        "asOf": model["asOf"]["value"],
    }
    view: dict[str, Any] = {
        "formatVersion": "panorama-view-ir.v0.1",
        "viewId": _stable_derived_id(
            "VIEW",
            {
                "compiler": compiler,
                "modelSemanticHash": model_binding["modelSemanticHash"],
                "profile": profile,
                "architectureScopes": scopes,
                "correlationIds": correlations,
            },
        ),
        "generatedAt": model["compiledAt"],
        "compiler": compiler,
        "modelBinding": model_binding,
        "viewType": view_type,
        "profile": profile,
        "title": f"{model['projectBinding']['projectName']} {title_suffix}",
        "description": description,
        "filters": {
            "architectureScopes": scopes,
            "factStatuses": [
                "observed",
                "declared",
                "derived",
                "unknown",
                "conflict",
            ],
        },
        "groups": [],
        "nodes": nodes,
        "edges": edges,
        "informationGaps": sorted(information_gaps),
        "layout": {"strategy": "none", "positions": []},
        "extensions": {"correlationIds": correlations},
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
        raise PanoramaViewIRError(
            f"{profile} View IR 编译结果无效：" + "; ".join(errors[:10])
        )
    return view


def compile_lifecycle_view_ir(
    model: dict[str, Any],
    *,
    architecture_scopes: list[str] | None = None,
    correlation_ids: list[str] | None = None,
) -> dict[str, Any]:
    return _compile_event_projection_view(
        model,
        profile="lifecycle",
        view_type="lifecycle",
        relation_kind="state_transition",
        compiler=LIFECYCLE_VIEW_COMPILER,
        title_suffix="事件生命周期",
        description="只投影 Event Adapter 显式声明的 before/after state，不从 Outcome 猜状态。",
        architecture_scopes=architecture_scopes,
        correlation_ids=correlation_ids,
    )


def compile_evolution_risk_view_ir(
    model: dict[str, Any],
    *,
    architecture_scopes: list[str] | None = None,
    correlation_ids: list[str] | None = None,
) -> dict[str, Any]:
    return _compile_event_projection_view(
        model,
        profile="evolution_risk",
        view_type="evolution",
        relation_kind="trace",
        compiler=EVOLUTION_VIEW_COMPILER,
        title_suffix="工程演进与风险",
        description="按已验证 Event Hash Chain 投影演进；失败仅作风险强调，不推断 blast radius。",
        architecture_scopes=architecture_scopes,
        correlation_ids=correlation_ids,
    )


def validate_view_ir(
    view: dict[str, Any],
    model: dict[str, Any],
    *,
    parent_view: dict[str, Any] | None = None,
) -> list[str]:
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

    expected_view_types = {
        "system_context": "architecture",
        "module": "architecture",
        "module_logic": "module_logic",
        "dependency_dataflow": "dataflow",
        "deployment_runtime": "deployment",
        "sequence": "sequence",
        "lifecycle": "lifecycle",
        "evolution_risk": "evolution",
    }
    if expected_view_types.get(view["profile"]) != view["viewType"]:
        errors.append("/viewType: 与 profile 不匹配")
    expected_compilers = {
        "module": VIEW_COMPILER,
        "module_logic": MODULE_LOGIC_VIEW_COMPILER,
        "dependency_dataflow": DEPENDENCY_VIEW_COMPILER,
        "deployment_runtime": DEPLOYMENT_VIEW_COMPILER,
        "sequence": SEQUENCE_VIEW_COMPILER,
        "lifecycle": LIFECYCLE_VIEW_COMPILER,
        "evolution_risk": EVOLUTION_VIEW_COMPILER,
    }
    expected_compiler = expected_compilers.get(view["profile"])
    if expected_compiler is not None and view["compiler"] != expected_compiler:
        errors.append("/compiler: 与 profile 编译器不匹配")

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
    group_by_id = {item["id"]: item for item in view["groups"]}
    layer_group_by_ref = {
        item["layerId"]: item["id"]
        for item in view["groups"]
        if item["groupType"] == "layer"
    }
    node_set = set(node_ids)
    node_by_entity: dict[str, str] = {}
    for group in view["groups"]:
        if group["groupType"] == "layer":
            if group["layerId"] not in model_layers:
                errors.append(
                    f"/groups/{group['id']}: 未知 layerId {group['layerId']}"
                )
            if group["groupRef"] != group["layerId"]:
                errors.append(f"/groups/{group['id']}: layer groupRef 不匹配")
            model_layer = model_layers.get(group["layerId"])
            if model_layer is not None:
                expected_parent = layer_group_by_ref.get(model_layer.get("parentLayerId"))
                if group.get("parentGroupId") != expected_parent:
                    errors.append(
                        f"/groups/{group['id']}: parentGroupId 与 Model Layer 父级不匹配"
                    )
                if "kind" in group and group["kind"] != model_layer.get("kind"):
                    errors.append(f"/groups/{group['id']}: kind 与 Model Layer 不匹配")
                if "summary" in group and group["summary"] != model_layer.get("summary", ""):
                    errors.append(f"/groups/{group['id']}: summary 与 Model Layer 不匹配")
        elif group["layerId"] is not None:
            errors.append(f"/groups/{group['id']}: 非 layer group 不能绑定 layerId")
        parent_group_id = group.get("parentGroupId")
        if parent_group_id is not None and parent_group_id not in group_by_id:
            errors.append(f"/groups/{group['id']}: 未知 parentGroupId {parent_group_id}")
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
        expected_node_fields = {
            "label": entity.get("displayLabel", entity["name"]),
            "kind": entity["kind"],
            "factStatus": entity["factStatus"],
            "authority": entity["authority"],
            "confidence": entity["confidence"],
        }
        for node_field, expected_value in expected_node_fields.items():
            if node[node_field] != expected_value:
                errors.append(
                    f"/nodes/{node['id']}: {node_field} 与绑定 Entity 不匹配"
                )
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
        expected_edge_fields = {
            "label": relation["name"],
            "kind": relation["kind"],
            "direction": relation["direction"],
            "order": relation["semantics"]["order"],
        }
        for field, expected_value in expected_edge_fields.items():
            if edge[field] != expected_value:
                errors.append(
                    f"/edges/{edge['id']}: {field} 与绑定 Relation 不匹配"
                )
        evidence_ids = {pin["evidenceId"] for pin in relation["evidencePins"]}
        if not set(edge["evidencePinIds"]) <= evidence_ids:
            errors.append(f"/edges/{edge['id']}: Evidence Pin 不属于绑定 Relation")
    allowed_relation_kinds = {
        "module": {"communication"},
        "module_logic": {"logic_flow", "port_binding", "resource_usage"},
        "dependency_dataflow": {"dependency", "data_flow"},
        "deployment_runtime": {"deployment"},
        "sequence": {"sequence_message"},
        "lifecycle": {"state_transition"},
        "evolution_risk": {"trace"},
    }
    allowed_kinds = allowed_relation_kinds.get(view["profile"])
    if allowed_kinds is not None:
        for edge in view["edges"]:
            if edge["kind"] not in allowed_kinds:
                errors.append(
                    f"/edges/{edge['id']}: relation kind 不属于 {view['profile']}"
                )
    if view["profile"] in {"sequence", "lifecycle", "evolution_risk"}:
        for edge in view["edges"]:
            if edge["order"] is None:
                errors.append(f"/edges/{edge['id']}: Event projection order 不能为空")
    scopes = set(view["filters"]["architectureScopes"])
    for edge in view["edges"]:
        relation = model_relations.get(edge["relationRef"]["id"])
        if relation is not None and not scopes & set(
            relation["architectureScopes"]
        ):
            errors.append(f"/edges/{edge['id']}: Relation 不属于所选 scope")
    if view["profile"] in {"module", "dependency_dataflow"}:
        for node in view["nodes"]:
            entity = model_entities.get(node["entityRef"]["id"])
            if entity is not None and not scopes & set(
                entity["architectureScopes"]
            ):
                errors.append(f"/nodes/{node['id']}: Entity 不属于所选 scope")
    if view["profile"] == "module_logic":
        root_module_id = view["rootModuleId"]
        root_module = model_entities.get(root_module_id)
        if root_module is None or root_module["kind"] != "module":
            errors.append("/rootModuleId: 未绑定正式 Module Entity")
        scope_values = view["filters"]["architectureScopes"]
        model_bindings = [
            item
            for item in model.get("moduleLogicBindings", [])
            if item["moduleId"] == root_module_id
            and item["architectureScope"] in scope_values
        ]
        if len(model_bindings) != 1:
            errors.append(
                "/moduleLogicBinding: Model 中不存在唯一的 Root Module/Scope Binding"
            )
        else:
            model_logic_binding = model_bindings[0]
            if model_logic_binding["source"] == "current_observation":
                expected_logic_binding = {
                    "source": "current_observation",
                    "moduleId": root_module_id,
                    "observationId": model_logic_binding["observationId"],
                    "semanticHash": model_logic_binding["semanticHash"],
                    "coverage": model_logic_binding["coverage"],
                    "sourceCoverage": model_logic_binding["sourceCoverage"],
                    "currentness": model_logic_binding["currentness"],
                }
            else:
                expected_logic_binding = {
                    "source": "formal_design",
                    "moduleId": root_module_id,
                    "designId": model_logic_binding["designId"],
                    "designDigest": model_logic_binding["designDigest"],
                }
            if view["moduleLogicBinding"] != expected_logic_binding:
                errors.append("/moduleLogicBinding: 与 Model Module Logic Binding 不匹配")
            binding_key = _module_logic_binding_key(model_logic_binding)
            internal_ids = {
                item["id"]
                for item in model["entities"]
                if item.get("attributes", {}).get("moduleLogicBindingKey")
                == binding_key
            }
            projected_ids = set(node_by_entity)
            if not internal_ids <= projected_ids:
                errors.append("/nodes: 未完整投影绑定的内部 Logic/Boundary Entity")
            external_ids = projected_ids - internal_ids
            if any(
                model_entities[item]["kind"]
                not in {"module", "resource", "data_store"}
                for item in external_ids
                if item in model_entities
            ):
                errors.append("/nodes: 外部引用展开了其他模块内部逻辑")
            for edge in view["edges"]:
                relation = model_relations.get(edge["relationRef"]["id"])
                if (
                    relation is not None
                    and relation.get("attributes", {}).get(
                        "moduleLogicBindingKey"
                    )
                    != binding_key
                ):
                    errors.append(
                        f"/edges/{edge['id']}: Relation 不属于绑定的 Module Logic"
                    )
        if parent_view is not None:
            parent_errors = validate_view_ir(parent_view, model)
            if parent_errors:
                errors.append("/parentViewBinding: 父 View 本身无效")
            expected_parent_binding = {
                "viewId": parent_view.get("viewId"),
                "semanticHash": parent_view.get("integrity", {}).get(
                    "semanticHash"
                ),
            }
            if view["parentViewBinding"] != expected_parent_binding:
                errors.append("/parentViewBinding: 与提供的父 View 不匹配")
            if parent_view.get("profile") != "module":
                errors.append("/parentViewBinding: 父 View 必须是 module profile")
            parent_entity_ids = {
                item["entityRef"]["id"]
                for item in parent_view.get("nodes", [])
            }
            if root_module_id not in parent_entity_ids:
                errors.append("/rootModuleId: 父 View 未包含 Root Module")
            if parent_view.get("filters", {}).get("architectureScopes") != scope_values:
                errors.append("/filters/architectureScopes: 与父 View Scope 不匹配")
    for position in view["layout"]["positions"]:
        if position["nodeId"] not in node_set:
            errors.append(
                f"/layout/positions/{position['nodeId']}: 未知布局 Node"
            )
    return errors


def load_and_compile_model_ir(
    path: Path,
    *,
    source_observation_path: Path | None = None,
    event_store_path: Path | None = None,
    module_logic_observation_paths: list[Path] | None = None,
) -> dict[str, Any]:
    data, _ = load_panorama(path)
    source_observation = None
    if source_observation_path is not None:
        source_observation = json.loads(
            source_observation_path.read_text(encoding="utf-8")
        )
        if not isinstance(source_observation, dict):
            raise PanoramaViewIRError("Source Observation 根必须是 JSON object。")
    event_checkpoint = None
    if event_store_path is not None:
        from event_projection import EventProjectionError, load_event_checkpoint

        try:
            event_checkpoint = load_event_checkpoint(
                event_store_path, project_id=data["project"]["id"]
            )
        except EventProjectionError as exc:
            raise PanoramaViewIRError(str(exc)) from exc
    module_logic_observations: list[dict[str, Any]] = []
    for observation_path in module_logic_observation_paths or []:
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        if not isinstance(observation, dict):
            raise PanoramaViewIRError(
                f"Module Logic Observation 根必须是 JSON object：{observation_path}"
            )
        module_logic_observations.append(observation)
    return compile_model_ir(
        data,
        source_observation=source_observation,
        event_checkpoint=event_checkpoint,
        module_logic_observations=module_logic_observations,
    )
