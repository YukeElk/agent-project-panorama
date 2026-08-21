"""Normalize the fixed Spring PetClinic Panorama observation for lab scoring.

This evaluator reads only the persisted Source Observation.  It does not read
the project source tree or the reviewer-only oracle and cannot create runtime,
test-pass, incident, or service-layer facts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _location(path: str, line: int | None = None) -> str:
    return f"{path}#L{line}" if line else path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    observation = _load(args.observation)
    local = [item for item in observation["elements"] if item.get("path")]
    main_java = [
        item
        for item in local
        if item["language"] == "java" and item["path"].startswith("src/main/")
    ]

    def annotations(element: dict) -> set[str]:
        return {
            item["name"].rpartition(".")[2]
            for item in element["attributes"].get("typeAnnotations", [])
        }

    def declaration_names(element: dict) -> set[str]:
        return {item["name"] for item in element["attributes"].get("declarations", [])}

    claims = []
    boot = [
        item
        for item in main_java
        if "PetClinicApplication" in declaration_names(item)
        and "SpringBootApplication" in annotations(item)
    ]
    if len(boot) == 1:
        annotation = next(item for item in boot[0]["attributes"]["typeAnnotations"] if item["name"].endswith("SpringBootApplication"))
        claims.append({
            "id": "PAN-PET-BOOT",
            "text": "PetClinicApplication is declared with @SpringBootApplication.",
            "claimClass": "current-implementation",
            "assertionStatus": "supported",
            "oracleFactIds": ["PETCLINIC_BOOT_APP"],
            "artifactEvidence": [_location(boot[0]["path"], annotation["line"])],
            "notes": "Bounded Java declaration metadata; not runtime evidence."
        })

    controllers = [item for item in main_java if "Controller" in annotations(item)]
    controller_evidence = []
    for element in controllers:
        annotation = next(item for item in element["attributes"]["typeAnnotations"] if item["name"].endswith("Controller"))
        controller_evidence.append(_location(element["path"], annotation["line"]))
    claims.append({
        "id": "PAN-PET-CONTROLLERS",
        "text": f"The main source set contains {len(controllers)} classes declared with @Controller.",
        "claimClass": "current-implementation",
        "assertionStatus": "supported" if len(controllers) == 6 else "partial",
        "oracleFactIds": ["PETCLINIC_MVC_CONTROLLERS"],
        "artifactEvidence": sorted(controller_evidence),
        "notes": "Counts type-level annotation metadata in src/main only."
    })

    repository_types = sorted({
        parameter
        for element in controllers
        for constructor in element["attributes"].get("constructors", [])
        for parameter in constructor["parameterTypes"]
        if parameter.endswith("Repository")
    })
    repository_evidence = sorted({
        _location(element["path"], constructor["line"])
        for element in controllers
        for constructor in element["attributes"].get("constructors", [])
        if any(parameter.endswith("Repository") for parameter in constructor["parameterTypes"])
    })
    expected_repositories = ["OwnerRepository", "PetTypeRepository", "VetRepository"]
    claims.append({
        "id": "PAN-PET-DIRECT-REPOSITORIES",
        "text": "@Controller constructors directly declare repository parameters: " + ", ".join(repository_types) + ".",
        "claimClass": "current-implementation",
        "assertionStatus": "supported" if repository_types == expected_repositories else "partial",
        "oracleFactIds": ["PETCLINIC_DIRECT_REPOSITORIES"],
        "artifactEvidence": repository_evidence,
        "notes": "Constructor parameter declarations are implementation evidence; no runtime injection is claimed."
    })

    thymeleaf = next((
        relation for relation in observation["relations"]
        if relation["attributes"].get("specifier") == "org.springframework.boot:spring-boot-starter-thymeleaf"
    ), None)
    if thymeleaf:
        claims.append({
            "id": "PAN-PET-THYMELEAF",
            "text": "The Maven manifest declares the Spring Boot Thymeleaf starter.",
            "claimClass": "configuration",
            "assertionStatus": "partial",
            "oracleFactIds": ["PETCLINIC_THYMELEAF"],
            "artifactEvidence": [_location(thymeleaf["evidencePins"][0]["ref"], thymeleaf["evidencePins"][0]["line"])],
            "notes": "Dependency declaration alone does not prove which controller return values resolve to templates."
        })

    configurations = {item["path"]: item for item in local if item["language"] == "properties"}
    def config_entry(path: str, key: str) -> tuple[dict, dict] | None:
        element = configurations.get(path)
        if not element:
            return None
        entry = next((item for item in element["attributes"].get("entries", []) if item["key"] == key), None)
        return (element, entry) if entry else None

    default_db = config_entry("src/main/resources/application.properties", "database")
    if default_db and default_db[1].get("safeValue") == "h2":
        claims.append({
            "id": "PAN-PET-H2-DEFAULT", "text": "The default configuration declares database=h2.",
            "claimClass": "configuration", "assertionStatus": "supported", "oracleFactIds": ["PETCLINIC_H2_DEFAULT"],
            "artifactEvidence": [_location(default_db[0]["path"], default_db[1]["line"])],
            "notes": "Declared configuration; not an active runtime database claim."
        })

    profile_entries = [
        config_entry("src/main/resources/application-mysql.properties", "database"),
        config_entry("src/main/resources/application-postgres.properties", "database"),
    ]
    if all(profile_entries) and {item[1].get("safeValue") for item in profile_entries} == {"mysql", "postgres"}:
        claims.append({
            "id": "PAN-PET-PROFILE-DATABASES", "text": "MySQL and PostgreSQL database profiles are declared.",
            "claimClass": "configuration", "assertionStatus": "supported", "oracleFactIds": ["PETCLINIC_PROFILE_DATABASES"],
            "artifactEvidence": [_location(item[0]["path"], item[1]["line"]) for item in profile_entries],
            "notes": "Profile declarations do not establish an active profile."
        })

    claims.extend([
        {
            "id": "PAN-PET-RUNTIME-UNKNOWN", "text": "Current runtime status is unknown; the artifact contains source/configuration observations only.",
            "claimClass": "runtime", "assertionStatus": "supported", "oracleFactIds": ["PETCLINIC_RUNTIME_UNKNOWN"],
            "artifactEvidence": [f"source-observation:{observation['observationId']}"],
            "notes": "No runtime observation receipt is bound."
        },
        {
            "id": "PAN-PET-TEST-UNKNOWN", "text": "Current test status is unknown; test source presence is not an execution result.",
            "claimClass": "verification", "assertionStatus": "supported", "oracleFactIds": ["PETCLINIC_TEST_STATUS_UNKNOWN"],
            "artifactEvidence": [f"source-observation:{observation['observationId']}"],
            "notes": "No current test execution receipt is bound."
        }
    ])
    actuator = config_entry("src/main/resources/application.properties", "management.endpoints.web.exposure.include")
    if actuator and actuator[1].get("safeValue") == "*":
        claims.append({
            "id": "PAN-PET-ACTUATOR", "text": "The application configuration declares exposure of all Actuator web endpoints.",
            "claimClass": "configuration", "assertionStatus": "supported", "oracleFactIds": ["PETCLINIC_ACTUATOR_REVIEW"],
            "artifactEvidence": [_location(actuator[0]["path"], actuator[1]["line"])],
            "notes": "Review signal only; no deployment exposure or incident is claimed."
        })

    result = {
        "schemaVersion": "panorama-normalized-claims.v0.1",
        "participant": "agent-project-panorama-v0.6",
        "case": "spring-petclinic-88e37c1",
        "normalizationMethod": "tool-native",
        "claims": claims,
        "forbiddenClaimChecks": [
            {"id": item, "detected": False, "artifactEvidence": [], "notes": "No such assertion is emitted by the normalizer."}
            for item in ["NO_SERVICE_LAYER_CURRENT", "NO_ACTIVE_H2", "NO_ACTIVE_PROFILE_DB", "NO_CURRENT_TEST_PASS", "NO_ACTUATOR_INCIDENT", "NO_SECRET_EXPORT"]
        ],
        "limitations": [
            "Source topology does not inspect comments, so the service-comment conflict is unmapped.",
            "Thymeleaf dependency declaration is partial evidence; controller-to-template resolution is not extracted.",
            "Java parsing remains bounded and does not establish runtime call order or reflection."
        ]
    }
    schema = _load(args.schema)
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError(errors[0].message)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"claimCount": len(claims), "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
