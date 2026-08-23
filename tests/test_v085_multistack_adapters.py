from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from panorama_explain_pack import build_explain_pack, validate_explain_pack
from panorama_view_ir import (
    compile_dependency_dataflow_view_ir,
    compile_deployment_runtime_view_ir,
    compile_evolution_risk_view_ir,
    compile_lifecycle_view_ir,
    compile_model_ir,
    compile_module_view_ir,
    compile_sequence_view_ir,
    validate_model_ir,
    validate_view_ir,
)
from panorama_view_set import build_view_set
from source_module_mapping import (
    compile_mapping_proposal,
    validate_mapping_proposal,
)
from source_topology import (
    compute_observation_semantic_hash,
    extract_source_topology,
    validate_source_observation,
)


OBSERVED_AT = "2026-08-24T08:00:00Z"


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _multistack_project(tmp_path: Path) -> Path:
    root = tmp_path / "personal-multistack"
    _write(
        root / "src/main/kotlin/com/acme/App.kt",
        """package com.acme
// import ignored.comment.Fake
import com.acme.shared.Helper
class App
""",
    )
    _write(
        root / "src/main/kotlin/com/acme/shared/Helper.kt",
        "package com.acme.shared\nclass Helper\n",
    )
    _write(
        root / "go.mod",
        """module example.com/personal

go 1.23

require github.com/gin-gonic/gin v1.10.0
""",
    )
    _write(
        root / "cmd/api/main.go",
        """package main
import (
  "example.com/personal/internal/service"
  "github.com/gin-gonic/gin"
)
func main() {}
""",
    )
    _write(root / "internal/service/service.go", "package service\n")
    _write(
        root / "App.csproj",
        """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <FrameworkReference Include="Microsoft.AspNetCore.App" />
    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
    )
    _write(
        root / "src/Web/Program.cs",
        "namespace Demo.Web;\nusing Demo.Core;\n",
    )
    _write(root / "src/Core/Core.cs", "namespace Demo.Core;\n")
    _write(
        root / "Cargo.toml",
        """[package]
name = "personal-core"
version = "0.1.0"

[dependencies]
axum = "0.8"
""",
    )
    _write(root / "src/lib.rs", "mod domain;\nuse crate::domain;\n")
    _write(root / "src/domain.rs", "pub struct Domain;\n")
    _write(
        root / "composer.json",
        json.dumps(
            {"name": "personal/php", "require": {"laravel/framework": "^12.0"}}
        ),
    )
    _write(
        root / "src/php/App.php",
        "<?php\nnamespace Demo\\App;\nuse Demo\\Shared\\Helper;\n",
    )
    _write(
        root / "src/php/Shared/Helper.php",
        "<?php\nnamespace Demo\\Shared;\n",
    )
    _write(root / "Gemfile", "source 'https://rubygems.org'\ngem 'rails'\n")
    _write(root / "app/models/user.rb", "require_relative '../../lib/helper'\n")
    _write(root / "lib/helper.rb", "module Helper\nend\n")
    _write(
        root / "Package.swift",
        """// swift-tools-version: 6.0
import PackageDescription
let package = Package(
  name: "Personal",
  dependencies: [.package(url: "https://github.com/Alamofire/Alamofire.git", from: "5.0.0")]
)
""",
    )
    _write(root / "Sources/Personal/Main.swift", "import Foundation\n")
    _write(
        root / "src/main/scala/com/acme/Main.scala",
        "package com.acme\nimport com.acme.Shared\nobject Main\n",
    )
    _write(
        root / "CMakeLists.txt",
        "add_executable(personal src/main/c/main.c src/main/cpp/main.cpp)\n"
        "target_link_libraries(personal PRIVATE local)\n",
    )
    _write(root / "src/main/c/main.c", '#include "local.h"\n')
    _write(root / "src/main/c/local.h", "#pragma once\n")
    _write(root / "src/main/cpp/main.cpp", '#include "local.hpp"\n')
    _write(root / "src/main/cpp/local.hpp", "#pragma once\n")
    _write(
        root / "package.json",
        json.dumps(
            {
                "name": "personal-web",
                "dependencies": {
                    "next": "15.0.0",
                    "react": "19.0.0",
                    "openai": "5.0.0",
                },
            }
        ),
    )
    _write(root / "src/web/app.ts", "import React from 'react';\n")
    _write(
        root / "pyproject.toml",
        """[project]
name = "personal-api"
dependencies = ["fastapi>=0.100", "sqlalchemy>=2"]
""",
    )
    _write(root / "src/api/main.py", "from fastapi import FastAPI\n")
    _write(root / "Dockerfile", "FROM python:3.12-slim\n")
    _write(root / ".github/workflows/ci.yml", "name: ci\n")
    _write(root / "openapi.yaml", "openapi: 3.1.0\n")
    return root


def test_v085_multistack_observation_is_deterministic_and_evidence_bound(tmp_path):
    root = _multistack_project(tmp_path)
    first = extract_source_topology(root, project_id="PRJ-MULTI", observed_at=OBSERVED_AT)
    second = extract_source_topology(root, project_id="PRJ-MULTI", observed_at=OBSERVED_AT)
    assert first == second

    observation = first["observation"]
    assert observation["formatVersion"] == "panorama-source-topology-observation.v0.2"
    assert observation["sourceBinding"]["coverage"] == "complete"
    assert validate_source_observation(observation) == []

    support = {
        item["language"]: item
        for item in observation["extensions"]["supportMatrix"]
    }
    assert support["kotlin"]["status"] == "supported"
    assert support["go"]["status"] == "supported"
    assert support["csharp"]["status"] == "supported"
    assert support["rust"]["status"] == "preview"
    assert support["kotlin"]["level"] == "L1"

    relations = observation["relations"]
    assert any(
        item["attributes"]["adapterId"] == "kotlin-bounded-imports"
        and item["resolution"] == "resolved"
        for item in relations
    )
    assert any(
        item["attributes"]["adapterId"] == "go-bounded-imports"
        and item["attributes"]["specifier"] == "example.com/personal/internal/service"
        and item["resolution"] == "resolved"
        for item in relations
    )
    assert any(
        item["attributes"]["adapterId"] == "csharp-bounded-usings"
        and item["resolution"] == "resolved"
        for item in relations
    )
    serialized = json.dumps(first, ensure_ascii=False)
    assert "ignored.comment.Fake" not in serialized
    assert "class Helper" not in serialized
    assert "func main" not in serialized


def test_v085_stack_profile_covers_framework_data_ai_interface_and_delivery(tmp_path):
    observation = extract_source_topology(
        _multistack_project(tmp_path), project_id="PRJ-MULTI", observed_at=OBSERVED_AT
    )["observation"]
    technologies = {
        item["technology"] for item in observation["extensions"]["stackProfile"]
    }
    assert {
        "React", "Next.js", "OpenAI", "FastAPI", "SQLAlchemy", "Gin",
        "ASP.NET Core", "Entity Framework Core", "Axum", "Docker",
        "GitHub Actions", "OpenAPI",
    }.issubset(technologies)
    assert observation["extensions"]["monorepoBoundaries"]
    assert all(
        signal["factStatus"] in {"declared", "derived"}
        and signal["authority"] in {"declared", "inferred"}
        for signal in observation["extensions"]["stackProfile"]
    )


def test_v085_multistack_source_to_module_candidates_remain_pending(tmp_path):
    observation = extract_source_topology(
        _multistack_project(tmp_path), project_id="PRJ-MULTI", observed_at=OBSERVED_AT
    )["observation"]
    proposal = compile_mapping_proposal(observation, generated_at=OBSERVED_AT)
    assert validate_mapping_proposal(proposal, observation) == []
    assert proposal["formatVersion"] == "panorama-source-module-mapping-proposal.v0.2"
    assert proposal["status"] == "pending_review"
    assert all(
        item["reviewStatus"] == "pending" and item["suggestedModuleId"] is None
        for item in proposal["candidates"]
    )
    languages = proposal["extensions"]["candidateLanguages"]
    assert {
        "python", "typescript", "kotlin", "go", "csharp", "rust", "php",
        "ruby", "swift", "scala", "c", "cpp",
    }.issubset(languages)


def test_v085_preview_language_and_manifest_matrix_is_explicit(tmp_path):
    observation = extract_source_topology(
        _multistack_project(tmp_path), project_id="PRJ-MULTI", observed_at=OBSERVED_AT
    )["observation"]
    support = {
        item["language"]: item
        for item in observation["extensions"]["supportMatrix"]
    }
    assert all(
        support[language]["status"] == "preview"
        for language in {"rust", "php", "ruby", "swift", "scala", "c", "cpp"}
    )
    relation_adapters = {
        item["attributes"].get("adapterId") for item in observation["relations"]
    }
    assert {
        "rust-bounded-uses", "php-bounded-uses", "ruby-bounded-requires",
        "swift-bounded-imports", "scala-bounded-imports",
        "c-cpp-bounded-includes", "multistack-manifest-dependencies",
    }.issubset(relation_adapters)
    assert {
        "Laravel", "Rails",
    }.issubset(
        {item["technology"] for item in observation["extensions"]["stackProfile"]}
    )


def test_v085_registry_stack_and_mapping_tamper_fail_closed(tmp_path):
    observation = extract_source_topology(
        _multistack_project(tmp_path), project_id="PRJ-MULTI", observed_at=OBSERVED_AT
    )["observation"]
    broken = deepcopy(observation)
    broken["extensions"]["adapterRegistry"]["version"] = "tampered"
    broken["integrity"]["semanticHash"] = compute_observation_semantic_hash(broken)
    assert any("Multistack Registry" in error for error in validate_source_observation(broken))

    broken = deepcopy(observation)
    broken["extensions"]["stackProfile"][0]["evidencePinIds"] = ["MISSING-EVIDENCE"]
    broken["integrity"]["semanticHash"] = compute_observation_semantic_hash(broken)
    assert any("Evidence Pin" in error for error in validate_source_observation(broken))

    proposal = compile_mapping_proposal(observation, generated_at=OBSERVED_AT)
    proposal["candidates"][0]["groupingKey"] = "forged"
    assert validate_mapping_proposal(proposal, observation)


def test_v085_multistack_observation_projects_into_model_and_dependency_view(
    project_root: Path, tmp_path: Path
):
    observation = extract_source_topology(
        _multistack_project(tmp_path),
        project_id="PRJ-MARW",
        observed_at=OBSERVED_AT,
    )["observation"]
    panorama = json.loads(
        (project_root / "examples" / "reference-project.v0.2.json").read_text(
            encoding="utf-8"
        )
    )
    model = compile_model_ir(panorama, source_observation=observation)
    assert validate_model_ir(model) == []
    view = compile_dependency_dataflow_view_ir(model, max_nodes=200)
    assert validate_view_ir(view, model) == []

    source_edges = [
        edge
        for edge in view["edges"]
        if edge["relationRef"]["id"].startswith("SRCREL-")
    ]
    assert source_edges
    model_entities = {item["id"]: item for item in model["entities"]}
    projected_languages = {
        model_entities[node["entityRef"]["id"]]["attributes"]["sourceAttributes"].get(
            "adapterId"
        )
        for node in view["nodes"]
        if model_entities[node["entityRef"]["id"]]["attributes"].get(
            "sourceObservationId"
        )
    }
    assert {
        "kotlin-bounded-imports",
        "go-bounded-imports",
        "csharp-bounded-usings",
    }.issubset(projected_languages)
    assert all(edge["order"] is None for edge in source_edges)

    views = [
        compile_module_view_ir(model),
        view,
        compile_deployment_runtime_view_ir(model),
        compile_sequence_view_ir(model),
        compile_lifecycle_view_ir(model),
        compile_evolution_risk_view_ir(model),
    ]
    view_set = build_view_set(model, views)
    explain_pack = build_explain_pack(panorama, model, views, view_set)
    assert validate_explain_pack(explain_pack, panorama, model, views, view_set) == []
    dependency_edge_ids = {edge["id"] for edge in view["edges"]}
    explained_edge_ids = {
        edge_id
        for story in explain_pack["stories"]
        if story.get("viewBinding", {}).get("profile") == "dependency_dataflow"
        for step in story["steps"]
        for claim in step["claimBlocks"]
        for edge_id in claim["edgeIds"]
    }
    assert dependency_edge_ids & explained_edge_ids
