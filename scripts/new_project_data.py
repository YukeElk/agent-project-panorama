"""Generate the smallest useful Schema-valid Panorama project skeleton."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def focus_option(option_id: str, title: str, project_id: str) -> dict:
    return {
        "id": option_id,
        "title": title,
        "whyNow": "Establish evidence before expanding implementation scope.",
        "recommendationReason": "This option reduces unknowns while preserving alternatives.",
        "benefits": ["Creates an explicit, reviewable next step."],
        "risks": ["More project evidence may change the recommendation."],
        "impact": "No architecture commitment until evidence is reviewed.",
        "prerequisites": [],
        "expectedOutcome": "A reviewed input for the next Panorama update.",
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
            "notes": "Initial requirement; evidence and acceptance remain to be clarified.",
            "createdAt": now, "updatedAt": now, "extensions": {},
        }
        for index, text in enumerate(requirements, start=1)
    ]
    return {
        "schemaVersion": "0.1",
        "meta": {"templateVersion": "0.1.1", "revision": 0, "createdAt": now, "updatedAt": now,
                 "lastValidatedAt": None, "latestUpdateBatchId": None, "sourceMode": "manual",
                 "notes": "Generated minimal skeleton; unknown facts were not invented.", "extensions": {}},
        "project": {"id": project_id, "name": name, "summary": objective, "lifecycle": "idea",
                    "currentStageId": "STG-INIT", "currentReleaseId": None, "owner": "", "tags": [],
                    "createdAt": now, "updatedAt": now, "extensions": {}},
        "intent": {"objective": objective, "coreScenarios": [], "currentFocus": current_focus,
                   "inScope": [], "outOfScope": [], "constraints": [], "successDefinition": [],
                   "reviewId": None, "updatedAt": now, "extensions": {}},
        "requirements": requirement_records,
        "architecture": {"currentVersionId": "ARCH-V00", "targetVersionId": None, "baselines": [],
                         "layers": [{"id": "LAYER-UNASSIGNED", "name": "Unassigned", "order": 0,
                                     "summary": "Draft placement for evidenced modules only.", "extensions": {}}],
                         "versions": [{"id": "ARCH-V00", "label": "Initial Draft", "status": "draft",
                                       "createdAt": now, "acceptedAt": None, "summary": "Architecture not yet defined.",
                                       "rationale": "Start with unknowns instead of inventing modules.", "baselineIds": [],
                                       "delta": {"addedModuleIds": [], "modifiedModuleIds": [], "removedModuleIds": [],
                                                 "addedConnectionIds": [], "modifiedConnectionIds": [], "removedConnectionIds": []},
                                       "reviewId": None, "referenceIds": [], "extensions": {}}],
                         "modules": [], "connections": [], "transitions": [], "extensions": {}},
        "stages": [{"id": "STG-INIT", "name": "需求澄清", "order": 1, "status": "current",
                    "purpose": "Clarify intent, requirements, evidence, and architecture baseline.",
                    "currentFocus": current_focus, "entryAcceptanceIds": [], "exitAcceptanceIds": [],
                    "moduleIds": [], "blockerDecisionIds": [], "blockerRiskIds": [], "plannedStart": None,
                    "plannedEnd": None, "actualStart": now[:10], "actualEnd": None, "reviewId": None,
                    "extensions": {"requireEntryAcceptance": False}}],
        "workItems": [], "releases": [], "deployments": [], "resources": [], "decisions": [], "risks": [],
        "acceptanceCriteria": [], "gates": [], "references": [], "reviews": [], "changes": [], "updateBatches": [],
        "guidance": {"generatedAt": now, "status": "proposed", "currentFocusSummary": current_focus,
                     "options": [focus_option("FOCUS-CLARIFY", "Clarify requirements", project_id),
                                 focus_option("FOCUS-BASELINE", "Collect architecture evidence", project_id)],
                     "recommendedOptionId": "FOCUS-CLARIFY", "selectedOptionId": None, "extensions": {}},
        "extensions": {},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create minimal Panorama v0.1 JSON data.")
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
    print(f"Generated minimal data: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
