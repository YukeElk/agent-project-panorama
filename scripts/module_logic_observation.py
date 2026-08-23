"""Validation and deterministic identity for derived module-logic observations.

The observation is a read-only sidecar.  It may explain source-bound current
implementation logic, but it never mutates Panorama Core or promotes an AI
summary to approved architecture.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from panorama_io import compute_canonical_hash, compute_data_hash


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "module-logic-observation.schema.v0.1.json"
MAX_BYTES = 1024 * 1024
_DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|//|\\\\)")
_SECRET_SEGMENTS = {
    ".env",
    ".ssh",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "private_keys",
    "private-key",
}
_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "prompt",
    "model_output",
    "reasoning",
    "chain_of_thought",
    "source_body",
    "source_content",
}


class ModuleLogicObservationError(ValueError):
    """Raised when a module-logic observation cannot be safely materialized."""


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_errors(value: dict[str, Any]) -> list[str]:
    errors = sorted(
        _schema_validator().iter_errors(value),
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


def _without_integrity(value: dict[str, Any]) -> dict[str, Any]:
    projected = deepcopy(value)
    projected.pop("integrity", None)
    return projected


def compute_observation_semantic_hash(observation: dict[str, Any]) -> str:
    return compute_canonical_hash(_without_integrity(observation))


def compute_observation_id(observation: dict[str, Any]) -> str:
    projected = _without_integrity(observation)
    projected.pop("observationId", None)
    return "MLO-" + compute_canonical_hash(projected)[:24].upper()


def materialize_observation(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return a hash-bound observation without modifying the input candidate."""

    observation = deepcopy(candidate)
    observation.setdefault("formatVersion", "module-logic-observation.v0.1")
    observation.pop("integrity", None)
    observation["observationId"] = compute_observation_id(observation)
    observation["integrity"] = {
        "semanticHash": compute_observation_semantic_hash(observation),
        "semanticHashScope": "module_logic_observation_without_integrity",
    }
    errors = validate_module_logic_observation(observation)
    if errors:
        raise ModuleLogicObservationError("; ".join(errors))
    return observation


def _walk_sensitive(value: Any, path: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}/{key}"
            if str(key).lower() in _SENSITIVE_KEYS:
                errors.append(f"{child_path}: 不允许持久化敏感字段或源码/模型正文")
            errors.extend(_walk_sensitive(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_walk_sensitive(item, f"{path}/{index}"))
    return errors


def _safe_source_ref(value: str) -> bool:
    if not value or "\\" in value or _DRIVE_OR_UNC.match(value):
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    lowered = {part.lower() for part in path.parts}
    if lowered & _SECRET_SEGMENTS:
        return False
    name = path.name.lower()
    if name.startswith(".env") or name.endswith((".pem", ".key", ".p12", ".pfx", ".jks")):
        return False
    return True


def _unique_index(
    items: Any, field: str, pointer: str, errors: list[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(items, list):
        return result
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_id = item.get(field)
        if not isinstance(item_id, str):
            continue
        if item_id in result:
            errors.append(f"{pointer}/{index}/{field}: ID {item_id} 重复")
        result[item_id] = item
    return result


def _validate_evidence_bindings(
    observation: dict[str, Any], errors: list[str]
) -> dict[str, dict[str, Any]]:
    evidence = _unique_index(
        observation.get("evidencePins"), "evidenceId", "/evidencePins", errors
    )
    pins = observation.get("evidencePins", [])
    if isinstance(pins, list):
        for index, pin in enumerate(pins):
            if not isinstance(pin, dict):
                continue
            if pin.get("kind") == "source_location" and not _safe_source_ref(
                str(pin.get("ref", ""))
            ):
                errors.append(
                    f"/evidencePins/{index}/ref: Source Location 必须是安全的项目相对 POSIX 路径"
                )
            if pin.get("kind") == "json_pointer" and not str(
                pin.get("ref", "")
            ).startswith("/"):
                errors.append(
                    f"/evidencePins/{index}/ref: JSON Pointer 必须以 / 开始"
                )
            start = pin.get("lineStart")
            end = pin.get("lineEnd")
            if isinstance(start, int) and isinstance(end, int) and end < start:
                errors.append(
                    f"/evidencePins/{index}/lineEnd: 不能早于 lineStart"
                )

    collections = (
        ("nodes", "id"),
        ("edges", "id"),
        ("boundaryPorts", "id"),
        ("externalReferences", "id"),
        ("unresolved", "id"),
        ("transformationLoss", "code"),
    )
    for collection, _ in collections:
        for index, item in enumerate(observation.get(collection, [])):
            if not isinstance(item, dict):
                continue
            for pin_index, pin_id in enumerate(item.get("evidencePinIds", [])):
                if pin_id not in evidence:
                    errors.append(
                        f"/{collection}/{index}/evidencePinIds/{pin_index}: 未知 Evidence Pin {pin_id!r}"
                    )
    return evidence


def _validate_graph(observation: dict[str, Any], errors: list[str]) -> None:
    nodes = _unique_index(observation.get("nodes"), "id", "/nodes", errors)
    edges = _unique_index(observation.get("edges"), "id", "/edges", errors)
    ports = _unique_index(
        observation.get("boundaryPorts"), "id", "/boundaryPorts", errors
    )
    external_refs = _unique_index(
        observation.get("externalReferences"),
        "id",
        "/externalReferences",
        errors,
    )
    global_ids: dict[str, str] = {}
    for kind, values in (
        ("node", nodes),
        ("edge", edges),
        ("boundary_port", ports),
        ("external_reference", external_refs),
    ):
        for item_id in values:
            if item_id in global_ids:
                errors.append(
                    f"/{kind}: ID {item_id} 已被 {global_ids[item_id]} 使用"
                )
            global_ids[item_id] = kind

    for index, edge in enumerate(observation.get("edges", [])):
        if not isinstance(edge, dict):
            continue
        from_id = edge.get("fromNodeId")
        to_id = edge.get("toNodeId")
        if from_id not in nodes:
            errors.append(f"/edges/{index}/fromNodeId: 未知内部逻辑节点 {from_id!r}")
        if to_id not in nodes:
            errors.append(f"/edges/{index}/toNodeId: 未知内部逻辑节点 {to_id!r}")
        if from_id == to_id:
            errors.append(f"/edges/{index}: 逻辑边不能自连接")
        if edge.get("kind") == "loop_back":
            endpoint_types = {
                nodes.get(from_id, {}).get("type"),
                nodes.get(to_id, {}).get("type"),
            }
            if "loop" not in endpoint_types:
                errors.append(
                    f"/edges/{index}: loop_back 必须精确连接一个 loop 节点"
                )

    for index, node in enumerate(observation.get("nodes", [])):
        if not isinstance(node, dict) or node.get("type") != "loop":
            continue
        node_id = node.get("id")
        if not str(node.get("loopExitCondition") or "").strip():
            errors.append(f"/nodes/{index}/loopExitCondition: loop 必须声明退出条件")
        if not any(
            edge.get("fromNodeId") == node_id or edge.get("toNodeId") == node_id
            for edge in observation.get("edges", [])
            if isinstance(edge, dict) and edge.get("kind") == "loop_back"
        ):
            errors.append(f"/nodes/{index}: loop 必须至少绑定一条 loop_back 边")

    for index, port in enumerate(observation.get("boundaryPorts", [])):
        if not isinstance(port, dict):
            continue
        if port.get("internalNodeId") not in nodes:
            errors.append(
                f"/boundaryPorts/{index}/internalNodeId: 未知内部逻辑节点 {port.get('internalNodeId')!r}"
            )
        if port.get("externalReferenceId") not in external_refs:
            errors.append(
                f"/boundaryPorts/{index}/externalReferenceId: 未知外部引用 {port.get('externalReferenceId')!r}"
            )


def _validate_panorama_binding(
    observation: dict[str, Any], panorama: dict[str, Any], errors: list[str]
) -> None:
    project = panorama.get("project", {})
    meta = panorama.get("meta", {})
    project_binding = observation.get("projectBinding", {})
    module_binding = observation.get("moduleBinding", {})
    if project_binding.get("projectId") != project.get("id"):
        errors.append("/projectBinding/projectId: 与 Panorama Project 不匹配")
    if project_binding.get("panoramaSchemaVersion") != panorama.get("schemaVersion"):
        errors.append("/projectBinding/panoramaSchemaVersion: 与 Panorama Schema 不匹配")
    if project_binding.get("revision") != meta.get("revision"):
        errors.append("/projectBinding/revision: 与 Panorama Revision 不匹配")
    if project_binding.get("dataHash") != compute_data_hash(panorama):
        errors.append("/projectBinding/dataHash: 与 Panorama Data Hash 不匹配")

    architecture = panorama.get("architecture", {})
    modules = {
        item.get("id"): item
        for item in architecture.get("modules", [])
        if isinstance(item, dict)
    }
    connections = {
        item.get("id"): item
        for item in architecture.get("connections", [])
        if isinstance(item, dict)
    }
    resources = {
        item.get("id"): item
        for item in panorama.get("resources", [])
        if isinstance(item, dict)
    }
    root_module_id = module_binding.get("moduleId")
    root_module = modules.get(root_module_id)
    if root_module is None:
        errors.append("/moduleBinding/moduleId: 未绑定正式 Module")
        return
    if module_binding.get("moduleDigest") != compute_canonical_hash(root_module):
        errors.append("/moduleBinding/moduleDigest: 与正式 Module 对象不匹配")

    external_refs = {
        item.get("id"): item
        for item in observation.get("externalReferences", [])
        if isinstance(item, dict)
    }
    for index, external in enumerate(observation.get("externalReferences", [])):
        if not isinstance(external, dict):
            continue
        entity_ref = external.get("entityRef", {})
        entity_type = entity_ref.get("type")
        entity_id = entity_ref.get("id")
        if entity_type == "module":
            if entity_id not in modules:
                errors.append(
                    f"/externalReferences/{index}/entityRef/id: 未知 Module {entity_id!r}"
                )
            if entity_id == root_module_id:
                errors.append(
                    f"/externalReferences/{index}/entityRef/id: 外部引用不能指向当前 Module"
                )
        elif entity_type == "resource" and entity_id not in resources:
            errors.append(
                f"/externalReferences/{index}/entityRef/id: 未知 Resource {entity_id!r}"
            )

    for index, port in enumerate(observation.get("boundaryPorts", [])):
        if not isinstance(port, dict):
            continue
        external = external_refs.get(port.get("externalReferenceId"), {})
        entity_ref = external.get("entityRef", {}) if isinstance(external, dict) else {}
        binding_id = port.get("bindingId")
        if port.get("bindingKind") == "connection":
            connection = connections.get(binding_id)
            if connection is None:
                errors.append(
                    f"/boundaryPorts/{index}/bindingId: 未知 Connection {binding_id!r}"
                )
                continue
            if entity_ref.get("type") != "module":
                errors.append(
                    f"/boundaryPorts/{index}: Connection 端口必须绑定外部 Module 引用"
                )
                continue
            external_id = entity_ref.get("id")
            from_id = connection.get("fromModuleId")
            to_id = connection.get("toModuleId")
            bidirectional = connection.get("flowDirection") == "bidirectional"
            direction = port.get("direction")
            valid = False
            if direction == "input":
                valid = (from_id, to_id) == (external_id, root_module_id) or (
                    bidirectional and (from_id, to_id) == (root_module_id, external_id)
                )
            elif direction == "output":
                valid = (from_id, to_id) == (root_module_id, external_id) or (
                    bidirectional and (from_id, to_id) == (external_id, root_module_id)
                )
            elif direction == "bidirectional":
                valid = bidirectional and {from_id, to_id} == {
                    root_module_id,
                    external_id,
                }
            if not valid:
                errors.append(
                    f"/boundaryPorts/{index}: 端口方向与正式 Connection 端点不匹配"
                )
        elif port.get("bindingKind") == "resource_usage":
            resource = resources.get(binding_id)
            if resource is None:
                errors.append(
                    f"/boundaryPorts/{index}/bindingId: 未知 Resource {binding_id!r}"
                )
                continue
            if entity_ref != {"type": "resource", "id": binding_id}:
                errors.append(
                    f"/boundaryPorts/{index}: Resource Usage 必须绑定同一 Resource 引用"
                )
            if root_module_id not in resource.get("usedByModuleIds", []):
                errors.append(
                    f"/boundaryPorts/{index}: Resource 未正式声明供当前 Module 使用"
                )


def validate_module_logic_observation(
    observation: dict[str, Any], panorama: dict[str, Any] | None = None
) -> list[str]:
    """Return deterministic validation errors without mutating either input."""

    if not isinstance(observation, dict):
        return ["/: Module Logic Observation 必须是 JSON 对象"]
    errors = _schema_errors(observation)
    encoded = json.dumps(
        observation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        errors.append("/: Module Logic Observation 超过 1 MiB")
    errors.extend(_walk_sensitive(observation))
    if errors:
        return sorted(set(errors))

    if observation.get("observationId") != compute_observation_id(observation):
        errors.append("/observationId: 与 Observation 绑定语义不匹配")
    integrity = observation.get("integrity", {})
    if integrity.get("semanticHash") != compute_observation_semantic_hash(
        observation
    ):
        errors.append("/integrity/semanticHash: 与 Observation 语义内容不匹配")

    _validate_evidence_bindings(observation, errors)
    _validate_graph(observation, errors)

    coverage = observation.get("coverage", {})
    source_binding = observation.get("sourceBinding", {})
    considered = coverage.get("filesConsidered", 0)
    read = coverage.get("filesReadTransiently", 0)
    if isinstance(considered, int) and isinstance(read, int) and read > considered:
        errors.append("/coverage/filesReadTransiently: 不能超过 filesConsidered")
    if coverage.get("status") == "complete":
        if coverage.get("unsupportedLanguages"):
            errors.append("/coverage/status: 存在未支持语言时不能声明 complete")
        if source_binding.get("coverage") != "complete":
            errors.append("/sourceBinding/coverage: 完整 Observation 必须绑定完整 Source")
    if source_binding.get("coverage") != "complete" and source_binding.get(
        "currentness"
    ) == "current":
        errors.append("/sourceBinding/currentness: partial/unknown Source 不能声明 current")
    if observation.get("informationGaps") and coverage.get("status") == "complete":
        errors.append("/coverage/status: 存在 Information Gap 时不能声明 complete")
    if observation.get("unresolved") and coverage.get("status") == "complete":
        errors.append("/coverage/status: 存在未解析项时不能声明 complete")
    if observation.get("transformationLoss") and coverage.get("status") == "complete":
        errors.append("/coverage/status: 存在 Transformation Loss 时不能声明 complete")

    if panorama is not None:
        _validate_panorama_binding(observation, panorama, errors)
    return sorted(set(errors))


__all__ = [
    "MAX_BYTES",
    "ModuleLogicObservationError",
    "compute_observation_id",
    "compute_observation_semantic_hash",
    "materialize_observation",
    "validate_module_logic_observation",
]
