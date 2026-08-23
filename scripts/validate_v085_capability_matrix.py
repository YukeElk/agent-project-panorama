"""Validate the fixed V0.85 multistack capability fixture and safety claims."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from source_topology import extract_source_topology, validate_source_observation


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPECTATIONS = ROOT / "examples" / "v0.85-capability-expectations.json"


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def validate_capability_matrix(expectations_path: Path = DEFAULT_EXPECTATIONS) -> dict[str, Any]:
    expectations = json.loads(Path(expectations_path).read_text(encoding="utf-8"))
    fixture = ROOT / expectations["fixture"]
    kwargs = {
        "project_id": expectations["projectId"],
        "observed_at": expectations["observedAt"],
    }
    first_bundle = extract_source_topology(fixture, **kwargs)
    second_bundle = extract_source_topology(fixture, **kwargs)
    observation = first_bundle["observation"]
    validation_errors = validate_source_observation(observation)

    actual_languages = {
        item["language"]
        for item in observation["elements"]
        if item["kind"] == "source_file"
    }
    actual_adapters = {
        item["attributes"].get("adapterId") for item in observation["relations"]
    }
    actual_technologies = {
        item["technology"] for item in observation["extensions"]["stackProfile"]
    }
    expected_facts = {
        *(f"language:{item}" for item in expectations["supportedLanguages"]),
        *(f"language:{item}" for item in expectations["previewLanguages"]),
        *(f"adapter:{item}" for item in expectations["relationAdapters"]),
        *(f"technology:{item}" for item in expectations["technologies"]),
    }
    actual_facts = {
        *(f"language:{item}" for item in actual_languages),
        *(f"adapter:{item}" for item in actual_adapters if item),
        *(f"technology:{item}" for item in actual_technologies),
    }
    matched = expected_facts & actual_facts
    recall = len(matched) / len(expected_facts) if expected_facts else 1.0

    evidence_ids = {
        pin["evidenceId"]
        for item in observation["elements"] + observation["relations"]
        for pin in item["evidencePins"]
    }
    evidence_checks = [
        bool(item["evidencePins"])
        for item in observation["elements"] + observation["relations"]
    ] + [
        bool(item["evidencePinIds"])
        and set(item["evidencePinIds"]).issubset(evidence_ids)
        for item in observation["extensions"]["stackProfile"]
    ]
    evidence_coverage = (
        sum(1 for item in evidence_checks if item) / len(evidence_checks)
        if evidence_checks
        else 1.0
    )

    forbidden_claims: list[str] = []
    for relation in observation["relations"]:
        attributes = relation["attributes"]
        if attributes.get("runtimeObserved") is not False:
            forbidden_claims.append(f"{relation['id']}:runtimeObserved")
        if attributes.get("sequenceOrder") is not None:
            forbidden_claims.append(f"{relation['id']}:sequenceOrder")
    for signal in observation["extensions"]["stackProfile"]:
        if signal["factStatus"] == "observed" or signal["authority"] == "observed":
            forbidden_claims.append(f"{signal['signalId']}:observed_stack_signal")
    forbidden_keys = {"body", "sourceBody", "sourceText", "prompt", "modelOutput"}
    leaked_keys = forbidden_keys & set(_walk_keys(observation))
    forbidden_claims.extend(f"persisted_key:{item}" for item in sorted(leaked_keys))
    for item in observation["elements"] + observation["relations"]:
        for pin in item["evidencePins"]:
            if pin["ref"].startswith("/") or re.match(r"^[A-Za-z]:[\\/]", pin["ref"]):
                forbidden_claims.append(f"{pin['evidenceId']}:absolute_path")
    forbidden_claim_avoidance = 1.0 if not forbidden_claims else 0.0

    support = {
        item["language"]: item
        for item in observation["extensions"]["supportMatrix"]
    }
    support_mismatches = []
    for language in expectations["supportedLanguages"]:
        if support.get(language, {}).get("status") != "supported":
            support_mismatches.append(f"{language}:expected_supported")
    for language in expectations["previewLanguages"]:
        if support.get(language, {}).get("status") != "preview":
            support_mismatches.append(f"{language}:expected_preview")

    deterministic = first_bundle == second_bundle
    failures = []
    if validation_errors:
        failures.append("observation_validation_failed")
    if recall < expectations["minimumRecall"]:
        failures.append("supported_fact_recall_below_threshold")
    if evidence_coverage < expectations["requiredEvidenceCoverage"]:
        failures.append("evidence_coverage_below_threshold")
    if forbidden_claim_avoidance < expectations["requiredForbiddenClaimAvoidance"]:
        failures.append("forbidden_claim_detected")
    if support_mismatches:
        failures.append("support_matrix_mismatch")
    if not deterministic:
        failures.append("non_deterministic_output")

    return {
        "formatVersion": "panorama-v085-capability-matrix.v0.1",
        "projectId": expectations["projectId"],
        "observedAt": expectations["observedAt"],
        "fixture": expectations["fixture"],
        "registry": deepcopy(observation["extensions"]["adapterRegistry"]),
        "metrics": {
            "expectedFactCount": len(expected_facts),
            "matchedFactCount": len(matched),
            "supportedFactRecall": round(recall, 6),
            "evidenceCoverage": round(evidence_coverage, 6),
            "forbiddenClaimAvoidance": forbidden_claim_avoidance,
            "deterministic": deterministic,
        },
        "missingFacts": sorted(expected_facts - actual_facts),
        "unexpectedFacts": sorted(actual_facts - expected_facts),
        "supportMismatches": support_mismatches,
        "forbiddenClaims": forbidden_claims,
        "validationErrors": validation_errors,
        "observation": {
            "id": observation["observationId"],
            "semanticHash": observation["integrity"]["semanticHash"],
            "elementCount": len(observation["elements"]),
            "relationCount": len(observation["relations"]),
            "stackSignalCount": len(observation["extensions"]["stackProfile"]),
        },
        "failures": failures,
        "status": "passed" if not failures else "failed",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="验证 V0.85 固定多语言能力矩阵。")
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_capability_matrix(args.expectations)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Capability Matrix 验证失败：{exc}", file=sys.stderr)
        return 2
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        atomic_write(args.output, payload)
    print(payload, end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
