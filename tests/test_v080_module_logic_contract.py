from __future__ import annotations

from copy import deepcopy
import json

import jsonschema
import pytest

from module_logic_observation import (
    ModuleLogicObservationError,
    compute_observation_semantic_hash,
    materialize_observation,
    validate_module_logic_observation,
)
from panorama_io import compute_canonical_hash, compute_data_hash
from studio_session import create_session, layout_hash, semantic_hash
from validate_panorama import validate_data


def _observation_candidate(panorama: dict) -> dict:
    root_module = next(
        module
        for module in panorama["architecture"]["modules"]
        if module["id"] == "MOD-ORCHESTRATOR"
    )
    evidence_id = "EVIDENCE-ORCHESTRATOR-01"
    return {
        "formatVersion": "module-logic-observation.v0.1",
        "generatedAt": "2026-08-23T08:00:00Z",
        "producer": {
            "id": "panorama-source-observer",
            "version": "0.1.0",
            "contractVersion": "module-logic-observation.v0.1",
        },
        "projectBinding": {
            "projectId": panorama["project"]["id"],
            "panoramaSchemaVersion": panorama["schemaVersion"],
            "revision": panorama["meta"]["revision"],
            "dataHash": compute_data_hash(panorama),
        },
        "moduleBinding": {
            "moduleId": root_module["id"],
            "moduleDigest": compute_canonical_hash(root_module),
        },
        "sourceBinding": {
            "mode": "content_digest",
            "gitHead": None,
            "sourceContentDigest": "a" * 64,
            "coverage": "complete",
            "currentness": "current",
        },
        "asOf": "2026-08-23T07:59:00Z",
        "evidencePins": [
            {
                "evidenceId": evidence_id,
                "kind": "source_location",
                "ref": "src/orchestration/request_router.py",
                "lineStart": 12,
                "lineEnd": 88,
                "digest": "b" * 64,
                "accessClass": "PROJECT_OPERATIONAL_METADATA",
                "freshness": "current",
            }
        ],
        "nodes": [
            {
                "id": "LOGIC-OBS-INPUT",
                "name": "receive_request",
                "displayName": "接收请求",
                "type": "input",
                "purpose": "接收控制台提交的项目任务。",
                "rationale": "在编排边界先固化输入契约。",
                "implementationSummary": "从入站连接解析项目上下文和操作指令。",
                "loopExitCondition": None,
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "high",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            },
            {
                "id": "LOGIC-OBS-OUTPUT",
                "name": "dispatch_task",
                "displayName": "分发任务",
                "type": "output",
                "purpose": "把规范化任务交给业务 Agent。",
                "rationale": "隔离请求入口与执行者。",
                "implementationSummary": "根据任务类型选择下游执行路径。",
                "loopExitCondition": None,
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "medium",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            },
        ],
        "edges": [
            {
                "id": "LOGIC-OBS-EDGE-01",
                "fromNodeId": "LOGIC-OBS-INPUT",
                "toNodeId": "LOGIC-OBS-OUTPUT",
                "kind": "flow",
                "condition": "",
                "dataSummary": "规范化任务",
                "order": 0,
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "high",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            }
        ],
        "boundaryPorts": [
            {
                "id": "LOGIC-OBS-PORT-IN",
                "name": "console_request",
                "direction": "input",
                "internalNodeId": "LOGIC-OBS-INPUT",
                "externalReferenceId": "LOGIC-OBS-EXT-CONSOLE",
                "bindingKind": "connection",
                "bindingId": "CONN-CONSOLE-ORCH",
                "dataSummary": "项目任务与上下文",
                "protocol": "HTTPS",
                "factStatus": "derived",
                "authority": "inferred",
                "confidence": "high",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            }
        ],
        "externalReferences": [
            {
                "id": "LOGIC-OBS-EXT-CONSOLE",
                "entityRef": {"type": "module", "id": "MOD-CONSOLE"},
                "label": "项目控制台",
                "purpose": "作为当前模块的外部请求入口。",
                "evidencePinIds": [evidence_id],
                "extensions": {},
            }
        ],
        "coverage": {
            "status": "complete",
            "filesConsidered": 2,
            "filesReadTransiently": 2,
            "supportedLanguages": ["Python"],
            "unsupportedLanguages": [],
        },
        "unresolved": [],
        "transformationLoss": [],
        "informationGaps": [],
        "extensions": {},
    }


def _target_design() -> dict:
    return {
        "id": "MLD-ORCHESTRATOR-TARGET",
        "moduleId": "MOD-ORCHESTRATOR",
        "architectureScope": "target",
        "name": "orchestrator_target_logic",
        "displayName": "编排器目标内部逻辑",
        "summary": "先规范化入站请求，再进入任务分发。",
        "nodes": [
            {
                "id": "LOGIC-TARGET-INPUT",
                "name": "normalize_request",
                "displayName": "规范化请求",
                "type": "preprocess",
                "purpose": "统一输入格式。",
                "rationale": "减少下游分支对输入差异的处理。",
                "implementationSummary": "执行输入验证、默认值补齐与上下文封装。",
                "loopExitCondition": None,
                "requirementIds": ["REQ-001"],
                "decisionIds": [],
                "riskIds": [],
                "referenceIds": ["REF-INTERFACE"],
                "extensions": {},
            },
            {
                "id": "LOGIC-TARGET-OUTPUT",
                "name": "dispatch_task",
                "displayName": "分发任务",
                "type": "output",
                "purpose": "将任务交给下游 Agent。",
                "rationale": "保持编排和业务执行边界。",
                "implementationSummary": "基于任务类型与能力约束选择下游。",
                "loopExitCondition": None,
                "requirementIds": ["REQ-001"],
                "decisionIds": [],
                "riskIds": [],
                "referenceIds": ["REF-INTERFACE"],
                "extensions": {},
            },
        ],
        "edges": [
            {
                "id": "LOGIC-TARGET-EDGE-01",
                "fromNodeId": "LOGIC-TARGET-INPUT",
                "toNodeId": "LOGIC-TARGET-OUTPUT",
                "kind": "flow",
                "condition": "",
                "dataSummary": "规范化任务",
                "order": 0,
                "referenceIds": ["REF-INTERFACE"],
                "extensions": {},
            }
        ],
        "boundaryPorts": [
            {
                "id": "LOGIC-TARGET-PORT-IN",
                "name": "console_request",
                "direction": "input",
                "internalNodeId": "LOGIC-TARGET-INPUT",
                "externalEntityRef": {"type": "module", "id": "MOD-CONSOLE"},
                "bindingKind": "connection",
                "bindingId": "CONN-CONSOLE-ORCH",
                "dataSummary": "项目任务与上下文",
                "protocol": "HTTPS",
                "referenceIds": ["REF-INTERFACE"],
                "extensions": {},
            }
        ],
        "requirementIds": ["REQ-001"],
        "decisionIds": [],
        "riskIds": [],
        "acceptanceCriteriaIds": ["ACC-REQ-001"],
        "gateIds": [],
        "referenceIds": ["REF-INTERFACE"],
        "extensions": {},
    }


def _with_target_design(reference_data: dict) -> dict:
    panorama = deepcopy(reference_data)
    panorama["architecture"]["moduleLogicDesigns"] = [_target_design()]
    panorama["workItems"][0]["relatedArchitectureRefs"] = [
        {"type": "logic_node", "id": "LOGIC-TARGET-INPUT"}
    ]
    panorama["decisions"][0]["relatedArchitectureRefs"] = [
        {"type": "module_logic_design", "id": "MLD-ORCHESTRATOR-TARGET"}
    ]
    panorama["risks"][0]["relatedArchitectureRefs"] = [
        {"type": "boundary_port", "id": "LOGIC-TARGET-PORT-IN"}
    ]
    return panorama


def _codes(report) -> set[str]:
    return {issue.code for issue in report.errors}


def test_v080_module_logic_schemas_are_meta_valid(project_root):
    for schema_name in (
        "module-logic-observation.schema.v0.1.json",
        "panorama.schema.v0.1.json",
        "panorama.schema.v0.2.json",
        "panorama-model-ir.schema.v0.1.json",
        "panorama-view-ir.schema.v0.1.json",
        "panorama-explain-pack.schema.v0.1.json",
        "architecture-session.schema.v0.1.json",
    ):
        schema = json.loads(
            (project_root / "schema" / schema_name).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)


def test_v080_current_observation_is_source_and_core_bound(reference_data):
    observation = materialize_observation(_observation_candidate(reference_data))

    assert validate_module_logic_observation(observation, reference_data) == []
    assert observation["observationId"].startswith("MLO-")
    assert observation["nodes"][0]["factStatus"] == "derived"
    assert observation["nodes"][0]["authority"] == "inferred"


def test_v080_current_observation_rejects_tamper_and_unsafe_source(reference_data):
    observation = materialize_observation(_observation_candidate(reference_data))
    observation["nodes"][0]["purpose"] = "tampered"
    errors = validate_module_logic_observation(observation, reference_data)
    assert any("semanticHash" in error for error in errors)

    candidate = _observation_candidate(reference_data)
    candidate["evidencePins"][0]["ref"] = "../.env"
    with pytest.raises(ModuleLogicObservationError, match="Source Location"):
        materialize_observation(candidate)


def test_v080_current_observation_never_claims_partial_source_is_current(reference_data):
    candidate = _observation_candidate(reference_data)
    candidate["sourceBinding"]["coverage"] = "partial"
    candidate["coverage"]["status"] = "partial"
    with pytest.raises(ModuleLogicObservationError, match="partial/unknown"):
        materialize_observation(candidate)


def test_v080_current_observation_discloses_unresolved_and_transformation_loss(reference_data):
    candidate = _observation_candidate(reference_data)
    candidate["sourceBinding"]["coverage"] = "partial"
    candidate["sourceBinding"]["currentness"] = "recorded_as_of"
    candidate["coverage"]["status"] = "partial"
    candidate["unresolved"] = [
        {
            "id": "UNRESOLVED-DYNAMIC-DISPATCH",
            "kind": "dynamic_call",
            "summary": "运行时注册表决定最终调用目标。",
            "evidencePinIds": ["EVIDENCE-ORCHESTRATOR-01"],
        }
    ]
    candidate["transformationLoss"] = [
        {
            "code": "DYNAMIC_TARGET_COLLAPSED",
            "sourceConstruct": "registry based dynamic dispatch",
            "lossReason": "静态归纳无法确定所有运行时目标。",
            "impact": "high",
            "evidencePinIds": ["EVIDENCE-ORCHESTRATOR-01"],
        }
    ]
    candidate["informationGaps"] = ["dynamic_dispatch_target_unknown"]

    observation = materialize_observation(candidate)
    assert validate_module_logic_observation(observation, reference_data) == []

    invalid = _observation_candidate(reference_data)
    invalid["unresolved"] = candidate["unresolved"]
    with pytest.raises(ModuleLogicObservationError, match="complete"):
        materialize_observation(invalid)


def test_v080_adversarial_agent_flow_preserves_explicit_complex_constructs(
    reference_data,
):
    candidate = _observation_candidate(reference_data)
    evidence_id = candidate["evidencePins"][0]["evidenceId"]

    def node(node_id: str, node_type: str, *, exit_condition=None) -> dict:
        return {
            "id": node_id,
            "name": node_id.casefold(),
            "displayName": node_id,
            "type": node_type,
            "purpose": f"验证 {node_type} 语义可被显式表达。",
            "rationale": "只保留带源码证据的结构化候选。",
            "implementationSummary": "不根据节点相邻关系推断运行时行为。",
            "loopExitCondition": exit_condition,
            "factStatus": "derived",
            "authority": "inferred",
            "confidence": "medium",
            "evidencePinIds": [evidence_id],
            "extensions": {},
        }

    def edge(
        edge_id: str,
        source: str,
        target: str,
        kind: str = "flow",
        condition: str = "",
    ) -> dict:
        return {
            "id": edge_id,
            "fromNodeId": source,
            "toNodeId": target,
            "kind": kind,
            "condition": condition,
            "dataSummary": "证据绑定的数据或控制状态",
            "order": None,
            "factStatus": "derived",
            "authority": "inferred",
            "confidence": "medium",
            "evidencePinIds": [evidence_id],
            "extensions": {},
        }

    candidate["nodes"] = [
        node("LOGIC-ADV-PREPROCESS", "preprocess"),
        node("LOGIC-ADV-STATE-READ", "state_read"),
        node("LOGIC-ADV-LLM", "llm_call"),
        node("LOGIC-ADV-LOOP", "loop", exit_condition="达到最大轮次或验证通过"),
        node("LOGIC-ADV-TOOL", "tool_call"),
        node("LOGIC-ADV-VALIDATE", "validation"),
        node("LOGIC-ADV-RETRY", "retry"),
        node("LOGIC-ADV-FALLBACK", "fallback"),
        node("LOGIC-ADV-STATE-WRITE", "state_write"),
        node("LOGIC-ADV-OUTPUT", "output"),
    ]
    candidate["edges"] = [
        edge("LOGIC-ADV-E01", "LOGIC-ADV-PREPROCESS", "LOGIC-ADV-STATE-READ"),
        edge("LOGIC-ADV-E02", "LOGIC-ADV-STATE-READ", "LOGIC-ADV-LLM"),
        edge("LOGIC-ADV-E03", "LOGIC-ADV-LLM", "LOGIC-ADV-LOOP"),
        edge("LOGIC-ADV-E04", "LOGIC-ADV-LOOP", "LOGIC-ADV-TOOL", "conditional", "继续执行工具"),
        edge("LOGIC-ADV-E05", "LOGIC-ADV-TOOL", "LOGIC-ADV-VALIDATE"),
        edge("LOGIC-ADV-E06", "LOGIC-ADV-VALIDATE", "LOGIC-ADV-LOOP", "loop_back", "未满足退出条件"),
        edge("LOGIC-ADV-E07", "LOGIC-ADV-VALIDATE", "LOGIC-ADV-RETRY", "retry", "可重试错误"),
        edge("LOGIC-ADV-E08", "LOGIC-ADV-RETRY", "LOGIC-ADV-TOOL"),
        edge("LOGIC-ADV-E09", "LOGIC-ADV-VALIDATE", "LOGIC-ADV-FALLBACK", "fallback", "重试耗尽"),
        edge("LOGIC-ADV-E10", "LOGIC-ADV-LOOP", "LOGIC-ADV-STATE-WRITE", "conditional", "退出条件成立"),
        edge("LOGIC-ADV-E11", "LOGIC-ADV-STATE-WRITE", "LOGIC-ADV-OUTPUT"),
        edge("LOGIC-ADV-E12", "LOGIC-ADV-FALLBACK", "LOGIC-ADV-OUTPUT"),
    ]
    candidate["boundaryPorts"] = []
    candidate["externalReferences"] = []
    candidate["sourceBinding"]["coverage"] = "partial"
    candidate["sourceBinding"]["currentness"] = "recorded_as_of"
    candidate["coverage"]["status"] = "partial"
    candidate["unresolved"] = [
        {
            "id": "UNRESOLVED-ADV-DYNAMIC",
            "kind": "dynamic_call",
            "summary": "动态注册表的最终调用目标不能由静态证据确定。",
            "evidencePinIds": [evidence_id],
        }
    ]
    candidate["informationGaps"] = [
        "dynamic_call_target_unknown",
        "generated_code_not_observed",
    ]

    observation = materialize_observation(candidate)
    assert validate_module_logic_observation(observation, reference_data) == []
    assert {item["type"] for item in observation["nodes"]} >= {
        "preprocess",
        "llm_call",
        "loop",
        "retry",
        "fallback",
        "state_read",
        "state_write",
    }
    assert {item["kind"] for item in observation["edges"]} >= {
        "loop_back",
        "retry",
        "fallback",
    }
    assert observation["coverage"]["status"] == "partial"


def test_v080_target_module_logic_and_precise_architecture_refs_validate(reference_data):
    panorama = _with_target_design(reference_data)
    assert validate_data(panorama).errors == []


def test_v080_target_module_logic_rejects_broken_graph_and_binding(reference_data):
    panorama = _with_target_design(reference_data)
    panorama["architecture"]["moduleLogicDesigns"][0]["edges"][0][
        "toNodeId"
    ] = "LOGIC-TARGET-UNKNOWN"
    panorama["architecture"]["moduleLogicDesigns"][0]["boundaryPorts"][0][
        "direction"
    ] = "output"
    report = validate_data(panorama)
    assert "MODULE_LOGIC_BROKEN_ENDPOINT" in _codes(report)
    assert "MODULE_LOGIC_CONNECTION_MISMATCH" in _codes(report)


def test_v080_target_loop_requires_loop_back(reference_data):
    panorama = _with_target_design(reference_data)
    node = panorama["architecture"]["moduleLogicDesigns"][0]["nodes"][0]
    node["type"] = "loop"
    node["loopExitCondition"] = "已生成可执行的下游任务"
    report = validate_data(panorama)
    assert "MODULE_LOGIC_LOOP_BACK_MISSING" in _codes(report)


def test_v080_architecture_refs_cannot_point_to_ephemeral_or_unknown_ids(reference_data):
    panorama = _with_target_design(reference_data)
    panorama["workItems"][0]["relatedArchitectureRefs"][0][
        "id"
    ] = "LOGIC-OBS-EPHEMERAL"
    report = validate_data(panorama)
    assert "BROKEN_REFERENCE" in _codes(report)


def test_v080_observation_hash_changes_with_semantics_not_layout(reference_data):
    observation = materialize_observation(_observation_candidate(reference_data))
    original_hash = compute_observation_semantic_hash(observation)
    changed = deepcopy(observation)
    changed["nodes"][0]["implementationSummary"] += " 并保留边界契约。"
    assert compute_observation_semantic_hash(changed) != original_hash


def test_v080_module_logic_studio_separates_semantic_and_layout_hash(reference_data):
    session = create_session(reference_data)
    candidate = session["candidates"][0]
    candidate["moduleLogicCanvases"] = [
        {
            "canvasId": "CANVAS-ORCHESTRATOR-TARGET",
            "rootModuleId": "MOD-ORCHESTRATOR",
            "architectureScope": "target",
            "currentObservationBinding": None,
            "baseTargetDesignId": None,
            "nodes": [
                {
                    "nodeId": "STUDIO-LOGIC-INPUT",
                    "entityRef": None,
                    "name": "normalize_request",
                    "displayName": "规范化请求",
                    "type": "preprocess",
                    "purpose": "统一输入。",
                    "rationale": "稳定下游契约。",
                    "implementationSummary": "验证并补齐请求上下文。",
                    "loopExitCondition": None,
                    "requirementIds": ["REQ-001"],
                    "decisionIds": [],
                    "riskIds": [],
                    "referenceIds": ["REF-INTERFACE"],
                    "isDraft": True,
                    "x": 120,
                    "y": 80,
                    "extensions": {},
                }
            ],
            "edges": [],
            "boundaryPorts": [],
            "externalEntityRefs": [],
            "createdAt": "2026-08-23T08:00:00Z",
            "updatedAt": "2026-08-23T08:00:00Z",
            "extensions": {},
        }
    ]
    semantic_before = semantic_hash(candidate)
    layout_before = layout_hash(candidate)

    candidate["moduleLogicCanvases"][0]["nodes"][0]["x"] += 40
    assert semantic_hash(candidate) == semantic_before
    assert layout_hash(candidate) != layout_before

    layout_after_move = layout_hash(candidate)
    candidate["moduleLogicCanvases"][0]["nodes"][0]["purpose"] += " 并记录校验结果。"
    assert semantic_hash(candidate) != semantic_before
    assert layout_hash(candidate) == layout_after_move
