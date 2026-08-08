"""Generate the smallest useful Schema-valid Panorama project skeleton."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from panorama_cli import ChineseArgumentParser


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def focus_option(option_id: str, title: str, project_id: str) -> dict:
    return {
        "id": option_id,
        "title": title,
        "whyNow": "在扩大实现范围前先建立证据。",
        "recommendationReason": "此选项在保留备选路径的同时减少未知项。",
        "benefits": ["形成明确且可评审的下一步。"],
        "risks": ["新增项目证据可能改变推荐结论。"],
        "impact": "在证据完成评审前不做架构承诺。",
        "prerequisites": [],
        "expectedOutcome": "形成下一次 Panorama 更新所需的已评审输入。",
        "relatedEntities": [{"type": "project", "id": project_id}],
    }


def build_minimal_project(
    project_id: str,
    name: str,
    objective: str,
    current_focus: str,
    requirements: list[str],
) -> dict:
    now = timestamp()
    requirement_records = [
        {
            "id": f"REQ-{index:03d}", "type": "functional", "title": text,
            "summary": text, "priority": "unset", "definitionStatus": "draft",
            "fulfillmentStatus": "not_started", "targetReleaseIds": [], "moduleIds": [],
            "acceptanceCriteriaIds": [], "sourceReferenceIds": [], "reviewId": None,
            "notes": "初始需求；证据与验收条件仍待澄清。",
            "createdAt": now, "updatedAt": now, "extensions": {},
        }
        for index, text in enumerate(requirements, start=1)
    ]
    return {
        "schemaVersion": "0.1",
        "meta": {"templateVersion": "0.1.1", "revision": 0, "createdAt": now, "updatedAt": now,
                 "lastValidatedAt": None, "latestUpdateBatchId": None, "sourceMode": "manual",
                 "notes": "由最小骨架生成；未虚构未知事实。", "extensions": {}},
        "project": {"id": project_id, "name": name, "summary": objective, "lifecycle": "idea",
                    "currentStageId": "STG-INIT", "currentReleaseId": None, "owner": "", "tags": [],
                    "createdAt": now, "updatedAt": now, "extensions": {}},
        "intent": {"objective": objective, "coreScenarios": [], "currentFocus": current_focus,
                   "inScope": [], "outOfScope": [], "constraints": [], "successDefinition": [],
                   "reviewId": None, "updatedAt": now, "extensions": {}},
        "requirements": requirement_records,
        "architecture": {"currentVersionId": "ARCH-V00", "targetVersionId": None, "baselines": [],
                         "layers": [{"id": "LAYER-UNASSIGNED", "name": "未分配", "order": 0,
                                     "summary": "仅为有证据的模块提供草稿位置。", "extensions": {}}],
                         "versions": [{"id": "ARCH-V00", "label": "初始草稿", "status": "draft",
                                       "createdAt": now, "acceptedAt": None, "summary": "架构尚未定义。",
                                       "rationale": "从未知项开始，而不是虚构模块。", "baselineIds": [],
                                       "delta": {"addedModuleIds": [], "modifiedModuleIds": [], "removedModuleIds": [],
                                                 "addedConnectionIds": [], "modifiedConnectionIds": [], "removedConnectionIds": []},
                                       "reviewId": None, "referenceIds": [], "extensions": {}}],
                         "modules": [], "connections": [], "transitions": [], "extensions": {}},
        "stages": [{"id": "STG-INIT", "name": "需求澄清", "order": 1, "status": "current",
                    "purpose": "澄清项目意图、需求、证据与架构基线。",
                    "currentFocus": current_focus, "entryAcceptanceIds": [], "exitAcceptanceIds": [],
                    "moduleIds": [], "blockerDecisionIds": [], "blockerRiskIds": [], "plannedStart": None,
                    "plannedEnd": None, "actualStart": now[:10], "actualEnd": None, "reviewId": None,
                    "extensions": {"requireEntryAcceptance": False}}],
        "workItems": [], "releases": [], "deployments": [], "resources": [], "decisions": [], "risks": [],
        "acceptanceCriteria": [], "gates": [], "references": [], "reviews": [], "changes": [], "updateBatches": [],
        "guidance": {"generatedAt": now, "status": "proposed", "currentFocusSummary": current_focus,
                     "options": [focus_option("FOCUS-CLARIFY", "澄清需求", project_id),
                                 focus_option("FOCUS-BASELINE", "收集架构证据", project_id)],
                     "recommendedOptionId": "FOCUS-CLARIFY", "selectedOptionId": None, "extensions": {}},
        "extensions": {},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="创建最小 Panorama v0.1 JSON 数据。")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--current-focus", required=True)
    parser.add_argument("--requirement", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = build_minimal_project(args.project_id, args.name, args.objective, args.current_focus, args.requirement)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成最小项目数据：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
