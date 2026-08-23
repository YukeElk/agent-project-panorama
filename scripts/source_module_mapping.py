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
SCHEMA = ROOT / "schema" / "source-module-mapping-proposal.schema.v0.2.json"
LEGACY_SCHEMA = ROOT / "schema" / "source-module-mapping-proposal.schema.v0.1.json"
COMPILER = {"id": "panorama-source-module-mapping-compiler", "version": "0.85.0"}


class SourceModuleMappingError(ValueError):
    """The mapping proposal is invalid or not safely bound."""


def _derived_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{compute_canonical_hash(value)[:24].upper()}"


def compute_mapping_proposal_hash(proposal: dict[str, Any]) -> str:
    value = deepcopy(proposal)
    value.pop("integrity", None)
    return compute_canonical_hash(value)


def _workspace_root(path: str, boundaries: list[dict[str, Any]]) -> str:
    matches = []
    for boundary in boundaries:
        root = boundary.get("root") or ""
        if not root or path == root or path.startswith(root.rstrip("/") + "/"):
            matches.append(root)
    return max(matches, key=len) if matches else ""


def _grouping_for(
    element: dict[str, Any], boundaries: list[dict[str, Any]]
) -> tuple[str, str, str] | None:
    if element["kind"] != "source_file" or not element.get("path"):
        return None
    language = element.get("language")
    path = element["path"]
    parts = Path(path).parts
    package = element["attributes"].get("package")
    if language == "java" and path.startswith("src/main/") and package:
        return "java_main_package", package, language
    if language == "kotlin" and path.startswith("src/main/") and package:
        return "kotlin_main_package", package, language
    if language == "go" and not Path(path).name.endswith("_test.go"):
        key = f"{Path(path).parent.as_posix()}:{package or Path(path).parent.name or 'main'}"
        return "go_package_directory", key, language
    if language == "csharp" and package:
        root = _workspace_root(path, boundaries)
        return "csharp_namespace_project", f"{root or '<root>'}:{package}", language
    if language == "python" and "tests" not in {part.casefold() for part in parts}:
        source_parts = list(parts)
        if source_parts and source_parts[0] == "src":
            source_parts = source_parts[1:]
        if len(source_parts) >= 2:
            return "python_import_package", source_parts[0], language
    if language in {"javascript", "typescript"} and "test" not in Path(path).stem.casefold():
        root = _workspace_root(path, boundaries)
        relative = Path(path).relative_to(root) if root else Path(path)
        relative_parts = list(relative.parts)
        if relative_parts and relative_parts[0] == "src" and len(relative_parts) > 1:
            key = f"{root or '<root>'}:src/{relative_parts[1]}"
        else:
            key = root or "<root>"
        return "javascript_workspace_package", key, language
    if language == "rust":
        return "rust_crate", _workspace_root(path, boundaries) or "<root>", language
    if language == "php" and package:
        return "php_namespace_package", package, language
    if language == "ruby":
        return "ruby_application", _workspace_root(path, boundaries) or "<root>", language
    if language == "swift":
        return "swift_module", _workspace_root(path, boundaries) or Path(path).parent.as_posix(), language
    if language == "scala" and package:
        return "scala_package", package, language
    if language in {"c", "cpp"}:
        return "c_cpp_build_target", _workspace_root(path, boundaries) or Path(path).parent.as_posix() or "<root>", language
    return None


def validate_mapping_proposal(
    proposal: dict[str, Any], observation: dict[str, Any] | None = None
) -> list[str]:
    schema_path = (
        LEGACY_SCHEMA
        if proposal.get("formatVersion") == "panorama-source-module-mapping-proposal.v0.1"
        else SCHEMA
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
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
        boundaries = observation.get("extensions", {}).get("monorepoBoundaries", [])
        for candidate in proposal["candidates"]:
            expected_pins = {
                pin["evidenceId"]
                for element_id in candidate["sourceElementIds"]
                for pin in element_by_id[element_id]["evidencePins"]
            }
            if set(candidate["evidencePinIds"]) != expected_pins:
                errors.append(f"/candidates/{candidate['candidateId']}: Evidence Pin 不匹配")
            if proposal["formatVersion"] == "panorama-source-module-mapping-proposal.v0.1":
                if any(
                    not (element_by_id[element_id].get("path") or "").startswith("src/main/")
                    for element_id in candidate["sourceElementIds"]
                ):
                    errors.append(f"/candidates/{candidate['candidateId']}: 非 main source 被提升为候选")
            else:
                expected_groups = {
                    _grouping_for(element_by_id[element_id], boundaries)
                    for element_id in candidate["sourceElementIds"]
                }
                expected = (
                    candidate["groupingBasis"], candidate["groupingKey"], candidate["language"]
                )
                if expected_groups != {expected}:
                    errors.append(f"/candidates/{candidate['candidateId']}: 分组基础与 Source Element 不匹配")
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
    boundaries = observation.get("extensions", {}).get("monorepoBoundaries", [])
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    unmapped: list[str] = []
    for element in local_elements:
        grouping = _grouping_for(element, boundaries)
        if grouping is None:
            unmapped.append(element["id"])
        else:
            groups.setdefault(grouping, []).append(element)

    candidates = []
    for (basis, grouping_key, language), elements in sorted(groups.items()):
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
        identity = {
            "basis": basis, "groupingKey": grouping_key,
            "language": language, "sourceElementIds": element_ids,
        }
        candidates.append(
            {
                "candidateId": _derived_id("MODCAND", identity),
                "name": f"{grouping_key.rpartition(':')[2].rpartition('.')[2]} candidate",
                "groupingBasis": basis,
                "groupingKey": grouping_key,
                "language": language,
                "sourceElementIds": element_ids,
                "evidencePinIds": evidence_ids,
                "signals": [f"type_annotation:{item}" for item in annotations],
                "rationale": (
                    f"{basis} 是 {language} 责任边界候选信号；"
                    "源码分组不等于 Module，仍需评审职责、接口、状态所有权和部署边界。"
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
        "formatVersion": "panorama-source-module-mapping-proposal.v0.2",
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
            "source_groups_are_candidates_not_formal_modules",
            "responsibilities_interfaces_state_ownership_and_deployment_boundaries_require_review",
            "test_configuration_manifest_and_unmapped_elements_are_not_auto_promoted",
            "mapping_proposal_does_not_modify_panorama",
        ],
        "extensions": {
            "sourceObservationFormat": observation["formatVersion"],
            "candidateLanguages": sorted({item["language"] for item in candidates}),
        },
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
