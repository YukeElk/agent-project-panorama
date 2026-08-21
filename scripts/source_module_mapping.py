"""Compile evidence-bound source-to-module candidates without promoting Modules."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from panorama_io import compute_canonical_hash
from source_topology import validate_source_observation


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "source-module-mapping-proposal.schema.v0.1.json"
COMPILER = {"id": "panorama-source-module-mapping-compiler", "version": "0.1.0"}


class SourceModuleMappingError(ValueError):
    """The mapping proposal is invalid or not safely bound."""


def _derived_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{compute_canonical_hash(value)[:24].upper()}"


def compute_mapping_proposal_hash(proposal: dict[str, Any]) -> str:
    value = deepcopy(proposal)
    value.pop("integrity", None)
    return compute_canonical_hash(value)


def validate_mapping_proposal(
    proposal: dict[str, Any], observation: dict[str, Any] | None = None
) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = [
        f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(proposal),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]
    if errors:
        return errors
    if proposal["integrity"]["mappingProposalHash"] != compute_mapping_proposal_hash(proposal):
        errors.append("/integrity/mappingProposalHash: 与 Proposal 内容不匹配")

    mapped_ids = [
        element_id
        for candidate in proposal["candidates"]
        for element_id in candidate["sourceElementIds"]
    ]
    if len(mapped_ids) != len(set(mapped_ids)):
        errors.append("/candidates: 同一 Source Element 被映射到多个候选")
    if set(mapped_ids) & set(proposal["unmappedSourceElementIds"]):
        errors.append("/unmappedSourceElementIds: 与候选映射重复")

    if observation is not None:
        observation_errors = validate_source_observation(observation)
        if observation_errors:
            errors.append("/sourceBinding: Source Observation 无效")
            return errors
        binding = proposal["sourceBinding"]
        expected = {
            "observationId": observation["observationId"],
            "observationHash": observation["integrity"]["semanticHash"],
            "contentDigest": observation["sourceBinding"]["contentDigest"],
            "gitHead": observation["sourceBinding"]["gitHead"],
        }
        if binding != expected:
            errors.append("/sourceBinding: 与 Source Observation 不匹配")
        element_by_id = {item["id"]: item for item in observation["elements"]}
        source_ids = {
            item["id"]
            for item in observation["elements"]
            if item["kind"] in {"source_file", "manifest", "configuration"}
        }
        if set(mapped_ids) | set(proposal["unmappedSourceElementIds"]) != source_ids:
            errors.append("/candidates: 未精确覆盖全部 project-local Source Element")
        for candidate in proposal["candidates"]:
            expected_pins = {
                pin["evidenceId"]
                for element_id in candidate["sourceElementIds"]
                for pin in element_by_id[element_id]["evidencePins"]
            }
            if set(candidate["evidencePinIds"]) != expected_pins:
                errors.append(f"/candidates/{candidate['candidateId']}: Evidence Pin 不匹配")
            if any(
                not (element_by_id[element_id].get("path") or "").startswith("src/main/")
                for element_id in candidate["sourceElementIds"]
            ):
                errors.append(f"/candidates/{candidate['candidateId']}: 非 main source 被提升为候选")
    return errors


def compile_mapping_proposal(
    observation: dict[str, Any], *, generated_at: str
) -> dict[str, Any]:
    source_errors = validate_source_observation(observation)
    if source_errors:
        raise SourceModuleMappingError("Source Observation 无效：" + "; ".join(source_errors[:10]))

    local_elements = [
        item
        for item in observation["elements"]
        if item["kind"] in {"source_file", "manifest", "configuration"}
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    unmapped: list[str] = []
    for element in local_elements:
        package = element["attributes"].get("package")
        path = element.get("path") or ""
        if element["language"] == "java" and path.startswith("src/main/") and package:
            groups.setdefault(package, []).append(element)
        else:
            unmapped.append(element["id"])

    candidates = []
    for package, elements in sorted(groups.items()):
        element_ids = sorted(item["id"] for item in elements)
        evidence_ids = sorted(
            {
                pin["evidenceId"]
                for element in elements
                for pin in element["evidencePins"]
            }
        )
        annotations = sorted(
            {
                annotation["name"].rpartition(".")[2]
                for element in elements
                for annotation in element["attributes"].get("typeAnnotations", [])
            }
        )
        identity = {"package": package, "sourceElementIds": element_ids}
        candidates.append(
            {
                "candidateId": _derived_id("MODCAND", identity),
                "name": f"{package.rpartition('.')[2]} package candidate",
                "groupingBasis": "java_main_package",
                "groupingKey": package,
                "sourceElementIds": element_ids,
                "evidencePinIds": evidence_ids,
                "signals": [f"type_annotation:{item}" for item in annotations],
                "rationale": (
                    "同一 main-source Java package 是责任边界候选信号；"
                    "package 不等于 Module，仍需评审职责、接口、状态所有权和部署边界。"
                ),
                "confidence": "medium" if len(elements) > 1 or annotations else "low",
                "reviewStatus": "pending",
                "suggestedModuleId": None,
            }
        )

    binding = {
        "observationId": observation["observationId"],
        "observationHash": observation["integrity"]["semanticHash"],
        "contentDigest": observation["sourceBinding"]["contentDigest"],
        "gitHead": observation["sourceBinding"]["gitHead"],
    }
    proposal: dict[str, Any] = {
        "formatVersion": "panorama-source-module-mapping-proposal.v0.1",
        "proposalId": _derived_id(
            "MAP", {"compiler": COMPILER, "sourceBinding": binding, "generatedAt": generated_at}
        ),
        "generatedAt": generated_at,
        "compiler": COMPILER,
        "projectBinding": deepcopy(observation["projectBinding"]),
        "sourceBinding": binding,
        "status": "pending_review",
        "promotionPolicy": "exact_approved_mapping_proposal_required",
        "candidates": candidates,
        "unmappedSourceElementIds": sorted(unmapped),
        "informationGaps": [
            "java_packages_are_candidates_not_formal_modules",
            "responsibilities_interfaces_state_ownership_and_deployment_boundaries_require_review",
            "test_configuration_manifest_and_non_java_elements_are_not_auto_promoted",
            "mapping_proposal_does_not_modify_panorama",
        ],
        "extensions": {},
    }
    proposal["integrity"] = {
        "hashAlgorithm": "sha256",
        "mappingProposalHash": compute_mapping_proposal_hash(proposal),
        "hashScope": "proposal_without_integrity",
    }
    errors = validate_mapping_proposal(proposal, observation)
    if errors:
        raise SourceModuleMappingError("Mapping Proposal 无效：" + "; ".join(errors[:10]))
    return proposal
