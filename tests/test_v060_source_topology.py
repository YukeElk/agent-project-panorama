from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import pytest

import source_topology
from panorama_view_ir import (
    PanoramaViewIRError,
    compile_dependency_dataflow_view_ir,
    compile_model_ir,
    compile_module_view_ir,
    validate_model_ir,
    validate_view_ir,
)
from source_topology import (
    LOSS_SCHEMA,
    MAX_FILE_BYTES,
    OBSERVATION_SCHEMA,
    RECEIPT_SCHEMA,
    SourceTopologyError,
    compute_observation_semantic_hash,
    extract_source_topology,
    validate_extraction_receipt,
    validate_source_observation,
)


OBSERVED_AT = "2026-08-21T12:00:00Z"
SECRET_VALUE = "SOURCE_TOPOLOGY_SECRET_MUST_NOT_LEAK"


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _source_project(tmp_path: Path) -> Path:
    root = tmp_path / "source-project"
    _write(root / "pkg" / "__init__.py", "")
    _write(root / "pkg" / "helper.py", "VALUE = 1\n")
    _write(
        root / "pkg" / "main.py",
        """import importlib
import os
from typing import TYPE_CHECKING
from . import helper

if TYPE_CHECKING:
    import typing_extensions

def load(name):
    return importlib.import_module(name)
""",
    )
    _write(root / "src" / "helper.ts", "export const helper = 1;\n")
    _write(root / "src" / "types.ts", "export type Thing = string;\n")
    _write(
        root / "src" / "app.ts",
        """// import ignored from 'comment-only-package';
import type { Thing } from './types';
import { helper } from './helper';
const react = require('react');
const lazy = import('./missing');
export { helper };
""",
    )
    _write(
        root / "package.json",
        json.dumps(
            {
                "name": "source-project",
                "dependencies": {"react": "1.0.0"},
                "devDependencies": {"typescript": "5.0.0"},
            }
        ),
    )
    _write(
        root / "pyproject.toml",
        """[project]
name = "source-project"
dependencies = ["jsonschema>=4", "requests>=2"]
""",
    )
    _write(root / ".env", f"TOKEN={SECRET_VALUE}\n")
    _write(root / "README.md", "private narrative is not source topology\n")
    return root


def _reference(project_root: Path) -> dict:
    return json.loads(
        (project_root / "examples" / "reference-project.v0.2.json").read_text(
            encoding="utf-8"
        )
    )


def test_v060_source_extraction_schemas_are_meta_valid():
    for path in (OBSERVATION_SCHEMA, RECEIPT_SCHEMA, LOSS_SCHEMA):
        schema = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)


def test_v060_source_extraction_is_deterministic_and_bound(tmp_path):
    root = _source_project(tmp_path)
    first = extract_source_topology(
        root, project_id="PRJ-MARW", observed_at=OBSERVED_AT
    )
    second = extract_source_topology(
        root, project_id="PRJ-MARW", observed_at=OBSERVED_AT
    )

    assert first == second
    observation = first["observation"]
    receipt = first["receipt"]
    loss_report = first["lossReport"]
    assert validate_source_observation(observation) == []
    assert validate_extraction_receipt(receipt, observation, loss_report) == []
    assert observation["sourceBinding"]["mode"] == "content_digest"
    assert observation["sourceBinding"]["coverage"] == "complete"
    assert observation["sourceBinding"]["includedFileCount"] == 8
    assert receipt["counts"]["secretRiskExcluded"] == 1
    assert sum(item["outputCount"] for item in receipt["adapters"]) == receipt[
        "counts"
    ]["relations"]
    assert receipt["safety"] == {
        "networkAccessed": False,
        "projectMutated": False,
        "sourceBodiesReadTransiently": True,
        "sourceBodyPersisted": False,
        "classifiedSecretPathsRead": False,
        "embeddedSecretScan": "not_performed",
        "externalPrivateDataRead": False,
        "symlinkPolicy": "reject_in_scope",
    }


def test_v060_extraction_preserves_typed_unresolved_and_declared_boundaries(
    tmp_path,
):
    bundle = extract_source_topology(
        _source_project(tmp_path),
        project_id="PRJ-MARW",
        observed_at=OBSERVED_AT,
    )
    observation = bundle["observation"]
    relations = observation["relations"]

    assert any(
        item["kind"] == "type_import"
        and item["attributes"]["specifier"] == "typing_extensions"
        for item in relations
    )
    assert any(
        item["kind"] == "type_import"
        and item["attributes"]["specifier"] == "./types"
        and item["resolution"] == "resolved"
        for item in relations
    )
    assert any(
        item["kind"] == "dynamic_import"
        and item["resolution"] == "dynamic"
        for item in relations
    )
    assert any(
        item["kind"] == "declared_dependency"
        and item["attributes"]["specifier"] == "react"
        and item["authority"] == "declared"
        for item in relations
    )
    assert not any(
        item["attributes"].get("specifier") == "comment-only-package"
        for item in relations
    )
    assert all(item["attributes"]["runtimeObserved"] is False for item in relations)
    assert all(item["attributes"]["sequenceOrder"] is None for item in relations)
    assert "dynamic_or_unresolved_dependencies_present" in observation["informationGaps"]


def test_v060_javascript_parent_relative_imports_resolve_lexically(tmp_path):
    root = tmp_path / "nested-source-project"
    _write(root / "services" / "api" / "src" / "orders.ts", "import { reserve } from '../../inventory/src/stock';\n")
    _write(root / "services" / "inventory" / "src" / "stock.ts", "export const reserve = true;\n")

    observation = extract_source_topology(
        root, project_id="PRJ-NESTED", observed_at=OBSERVED_AT
    )["observation"]
    relation = next(
        item
        for item in observation["relations"]
        if item["attributes"].get("specifier") == "../../inventory/src/stock"
    )
    target = next(
        item for item in observation["elements"] if item["id"] == relation["toElementId"]
    )

    assert relation["resolution"] == "resolved"
    assert target["kind"] == "source_file"
    assert target["path"] == "services/inventory/src/stock.ts"


def test_v060_unsupported_source_language_is_partial_not_empty_success(tmp_path):
    root = tmp_path / "dart-project"
    _write(root / "src" / "main.dart", "void main() {}\n")
    _write(root / "README.md", "documentation is not a source adapter input\n")

    bundle = extract_source_topology(
        root, project_id="PRJ-JAVA", observed_at=OBSERVED_AT
    )

    observation = bundle["observation"]
    receipt = bundle["receipt"]
    assert observation["sourceBinding"]["coverage"] == "partial"
    assert observation["sourceBinding"]["currentness"] == "unknown"
    assert observation["inventory"]["excluded"]["unsupported"] == 2
    assert "unsupported_source_languages_present" in observation["informationGaps"]
    assert "unsupported_source_snapshot_not_content_bound" in observation["informationGaps"]
    assert receipt["status"] == "partial"
    assert receipt["counts"]["filesRead"] == 0
    assert receipt["safety"]["sourceBodiesReadTransiently"] is False
    assert bundle["lossReport"]["status"] == "partial"
    assert any(
        item["kind"] == "source_language.unsupported"
        for item in bundle["lossReport"]["losses"]
    )


def test_v060_java_static_import_adapter_is_bounded_and_evidence_bound(tmp_path):
    root = tmp_path / "java-project"
    _write(
        root / "src/main/java/com/acme/util/Helper.java",
        """package com.acme.util;
public class Helper { public static final int VALUE = 1; }
""",
    )
    _write(
        root / "src/main/java/com/acme/app/Main.java",
        '''package com.acme.app;
// import comment.only.Fake;
import com.acme.util.Helper;
import static com.acme.util.Helper.VALUE;
import java.util.List;
import com.acme.missing.*;
class Main {
  String ignored = "import string.only.Fake;";
  List<Helper> values;
}
''',
    )

    bundle = extract_source_topology(
        root, project_id="PRJ-JAVA", observed_at=OBSERVED_AT
    )
    observation = bundle["observation"]
    receipt = bundle["receipt"]
    relations = observation["relations"]

    assert observation["sourceBinding"]["coverage"] == "complete"
    assert observation["sourceBinding"]["currentness"] == "current"
    assert receipt["counts"]["filesRead"] == 2
    java_adapter = next(item for item in receipt["adapters"] if item["id"] == "java-static-imports")
    assert java_adapter == {
        "id": "java-static-imports",
        "version": "0.1.0",
        "status": "partial",
        "inputCount": 2,
        "outputCount": 4,
        "errorCount": 0,
    }
    helper_imports = [
        item for item in relations if item["attributes"]["specifier"].startswith("com.acme.util.Helper")
    ]
    assert len(helper_imports) == 2
    assert all(item["resolution"] == "resolved" for item in helper_imports)
    assert any(
        item["attributes"]["specifier"] == "java.util.List"
        and item["resolution"] == "external"
        for item in relations
    )
    list_relation = next(
        item for item in relations if item["attributes"]["specifier"] == "java.util.List"
    )
    list_target = next(
        item for item in observation["elements"] if item["id"] == list_relation["toElementId"]
    )
    assert list_target["name"] == "java.util.List"
    assert any(
        item["attributes"]["specifier"] == "com.acme.missing.*"
        and item["resolution"] == "external"
        for item in relations
    )
    assert not any("comment.only" in (item["attributes"]["specifier"] or "") for item in relations)
    assert not any("string.only" in (item["attributes"]["specifier"] or "") for item in relations)
    assert all(item["attributes"]["runtimeObserved"] is False for item in relations)
    assert all(item["attributes"]["sequenceOrder"] is None for item in relations)
    assert "java_full_parser_not_used" in observation["informationGaps"]
    assert "java_reflection_generated_sources_and_calls_not_resolved" in observation["informationGaps"]
    assert bundle["lossReport"]["status"] == "lossless"


def test_v060_java_declaration_metadata_is_bounded_not_framework_inference(tmp_path):
    root = tmp_path / "java-semantics"
    _write(
        root / "src/main/java/com/acme/OrderController.java",
        """package com.acme;
import org.springframework.stereotype.Controller;
@Controller
final class OrderController {
  OrderController(OrderRepository orders, Clock clock) {}
}
""",
    )

    observation = extract_source_topology(
        root, project_id="PRJ-JAVA-SEM", observed_at=OBSERVED_AT
    )["observation"]
    element = next(item for item in observation["elements"] if item["path"])

    assert element["attributes"]["package"] == "com.acme"
    assert element["attributes"]["declarations"] == [
        {"kind": "class", "name": "OrderController", "line": 4}
    ]
    assert element["attributes"]["typeAnnotations"] == [
        {"name": "Controller", "line": 3}
    ]
    assert element["attributes"]["constructors"] == [
        {"line": 5, "parameterTypes": ["OrderRepository", "Clock"]}
    ]
    assert element["attributes"]["semanticBoundary"] == "declarations_only_not_framework_behavior"


def test_v060_properties_and_maven_metadata_are_safe_and_declared(tmp_path):
    root = tmp_path / "java-config"
    _write(
        root / "src/main/resources/application.properties",
        """database=h2
spring.datasource.url=jdbc:mysql://db.example/petclinic
spring.datasource.password=DO_NOT_EXPORT
management.endpoints.web.exposure.include=*
custom.message=private body text
""",
    )
    _write(
        root / "pom.xml",
        """<project xmlns="http://maven.apache.org/POM/4.0.0">
<dependencies><dependency><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-thymeleaf</artifactId></dependency></dependencies>
</project>
""",
    )

    bundle = extract_source_topology(
        root, project_id="PRJ-JAVA-CONFIG", observed_at=OBSERVED_AT
    )
    observation = bundle["observation"]
    config = next(item for item in observation["elements"] if item["kind"] == "configuration")
    entries = {item["key"]: item for item in config["attributes"]["entries"]}
    assert entries["database"]["safeValue"] == "h2"
    assert entries["spring.datasource.url"]["safeValue"] == "jdbc:mysql"
    assert entries["spring.datasource.password"]["valuePolicy"] == "omitted_by_policy"
    assert "safeValue" not in entries["spring.datasource.password"]
    assert "safeValue" not in entries["custom.message"]
    rendered = json.dumps(bundle, ensure_ascii=False)
    assert "DO_NOT_EXPORT" not in rendered
    assert "private body text" not in rendered
    assert any(
        item["kind"] == "declared_dependency"
        and item["attributes"]["specifier"] == "org.springframework.boot:spring-boot-starter-thymeleaf"
        and item["attributes"]["parser"] == "xml"
        for item in observation["relations"]
    )

    unsafe = deepcopy(observation)
    unsafe_config = next(item for item in unsafe["elements"] if item["kind"] == "configuration")
    password = next(item for item in unsafe_config["attributes"]["entries"] if item["key"] == "spring.datasource.password")
    password["safeValue"] = "leaked"
    password["valuePolicy"] = "recorded_safe_value"
    unsafe["integrity"]["semanticHash"] = compute_observation_semantic_hash(unsafe)
    assert any("非白名单" in item for item in validate_source_observation(unsafe))

    unpinned = deepcopy(observation)
    java_free = unpinned  # Properties line binding is independently enforced without Java input.
    config = next(item for item in java_free["elements"] if item["kind"] == "configuration")
    config["attributes"]["entries"][0]["line"] += 1
    java_free["integrity"]["semanticHash"] = compute_observation_semantic_hash(java_free)
    assert any("Evidence Pin" in item for item in validate_source_observation(java_free))


def test_v060_extraction_does_not_persist_source_or_secret_bodies(tmp_path):
    bundle = extract_source_topology(
        _source_project(tmp_path),
        project_id="PRJ-MARW",
        observed_at=OBSERVED_AT,
    )
    rendered = json.dumps(bundle, ensure_ascii=False)
    assert SECRET_VALUE not in rendered
    assert "private narrative is not source topology" not in rendered
    assert "return importlib.import_module(name)" not in rendered
    assert "TOKEN=" not in rendered


def test_v060_source_observation_tamper_fails_closed(tmp_path):
    bundle = extract_source_topology(
        _source_project(tmp_path),
        project_id="PRJ-MARW",
        observed_at=OBSERVED_AT,
    )
    observation = bundle["observation"]
    broken = deepcopy(observation)
    broken["relations"][0]["name"] = "tampered"
    assert any(
        "semanticHash" in error for error in validate_source_observation(broken)
    )

    rebound = deepcopy(observation)
    rebound["relations"][0]["evidencePins"][0]["digest"] = "0" * 64
    rebound["integrity"]["semanticHash"] = compute_observation_semantic_hash(rebound)
    assert any("Evidence digest" in error for error in validate_source_observation(rebound))

    false_runtime = deepcopy(observation)
    false_runtime["relations"][0]["attributes"]["runtimeObserved"] = True
    false_runtime["integrity"]["semanticHash"] = compute_observation_semantic_hash(
        false_runtime
    )
    assert any(
        "False was expected" in error
        for error in validate_source_observation(false_runtime)
    )

    rebound_adapter = deepcopy(observation)
    rebound_adapter["relations"][0]["attributes"]["adapterId"] = "java-static-imports"
    rebound_adapter["relations"][0]["attributes"]["parser"] = "bounded_regex"
    rebound_adapter["integrity"]["semanticHash"] = compute_observation_semantic_hash(
        rebound_adapter
    )
    assert any(
        "Adapter/Parser" in error
        for error in validate_source_observation(rebound_adapter)
    )

    broken_receipt = deepcopy(bundle["receipt"])
    broken_receipt["counts"]["elements"] += 1
    assert any(
        "receiptHash" in error
        for error in validate_extraction_receipt(
            broken_receipt, observation, bundle["lossReport"]
        )
    )


def test_v060_source_observation_merges_as_candidates_not_modules(
    project_root, tmp_path
):
    observation = extract_source_topology(
        _source_project(tmp_path),
        project_id="PRJ-MARW",
        observed_at=OBSERVED_AT,
    )["observation"]
    model = compile_model_ir(
        _reference(project_root), source_observation=observation
    )

    assert validate_model_ir(model) == []
    source_entities = [
        item for item in model["entities"] if item["kind"] == "source_element"
    ]
    assert source_entities
    assert all(item["kind"] != "module" for item in source_entities)
    assert model["sourceBinding"]["currentness"] == "current"
    assert model["sourceBinding"]["sourceContentDigest"] == observation[
        "sourceBinding"
    ]["contentDigest"]
    source_relations = [
        item
        for item in model["relations"]
        if item["attributes"].get("sourceObservationId")
    ]
    assert source_relations
    assert all(item["kind"] == "dependency" for item in source_relations)
    assert all(item["semantics"]["order"] is None for item in source_relations)

    module_view = compile_module_view_ir(model)
    assert len(module_view["nodes"]) == 7
    assert len(module_view["edges"]) == 6
    assert all(node["kind"] == "module" for node in module_view["nodes"])


def test_v060_dependency_view_projects_only_source_dependencies(
    project_root, tmp_path
):
    observation = extract_source_topology(
        _source_project(tmp_path),
        project_id="PRJ-MARW",
        observed_at=OBSERVED_AT,
    )["observation"]
    model = compile_model_ir(
        _reference(project_root), source_observation=observation
    )
    view = compile_dependency_dataflow_view_ir(model, max_nodes=100)

    assert validate_view_ir(view, model) == []
    assert {edge["kind"] for edge in view["edges"]} == {"dependency"}
    assert {edge["relationRef"]["id"] for edge in view["edges"]} == {
        item["id"]
        for item in model["relations"]
        if item["kind"] == "dependency"
    }
    assert all(edge["order"] is None for edge in view["edges"])
    assert "data_flow_relations_not_available" in view["informationGaps"]


def test_v060_dependency_focus_and_node_budget_fail_closed(project_root, tmp_path):
    observation = extract_source_topology(
        _source_project(tmp_path),
        project_id="PRJ-MARW",
        observed_at=OBSERVED_AT,
    )["observation"]
    model = compile_model_ir(
        _reference(project_root), source_observation=observation
    )
    root_id = next(
        item["id"]
        for item in model["entities"]
        if item["kind"] == "source_element"
    )
    focused = compile_dependency_dataflow_view_ir(
        model, root_entity_ids=[root_id], max_depth=0, max_nodes=1
    )
    assert [item["entityRef"]["id"] for item in focused["nodes"]] == [root_id]
    assert focused["edges"] == []

    with pytest.raises(PanoramaViewIRError, match="max_nodes"):
        compile_dependency_dataflow_view_ir(model, max_nodes=1)


def test_v060_source_observation_project_mismatch_is_rejected(
    project_root, tmp_path
):
    observation = extract_source_topology(
        _source_project(tmp_path),
        project_id="PRJ-OTHER",
        observed_at=OBSERVED_AT,
    )["observation"]
    with pytest.raises(PanoramaViewIRError, match="projectId"):
        compile_model_ir(_reference(project_root), source_observation=observation)


def test_v060_source_extraction_rejects_oversized_in_scope_file(tmp_path):
    root = tmp_path / "large-project"
    root.mkdir()
    (root / "large.py").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(SourceTopologyError, match="超过"):
        extract_source_topology(
            root, project_id="PRJ-MARW", observed_at=OBSERVED_AT
        )


def test_v060_source_extraction_rejects_in_scope_link_policy(
    tmp_path, monkeypatch
):
    root = tmp_path / "link-policy-project"
    _write(root / "linked.py", "import os\n")
    original = source_topology._is_link_or_reparse

    def fake_link(path: Path) -> bool:
        return path.name == "linked.py" or original(path)

    monkeypatch.setattr(source_topology, "_is_link_or_reparse", fake_link)
    with pytest.raises(SourceTopologyError, match="link/reparse"):
        extract_source_topology(
            root, project_id="PRJ-MARW", observed_at=OBSERVED_AT
        )


def test_v060_source_extraction_cli_and_model_cli_end_to_end(
    project_root, tmp_path
):
    source_root = _source_project(tmp_path)
    observation_path = tmp_path / "source-observation.json"
    receipt_path = tmp_path / "extraction-receipt.json"
    loss_path = tmp_path / "loss-report.json"
    model_path = tmp_path / "source-bound-model.json"
    dependency_view_path = tmp_path / "dependency.view-ir.json"
    extract_script = project_root / "scripts" / "extract_source_topology.py"
    model_script = project_root / "scripts" / "compile_panorama_model_ir.py"
    view_script = project_root / "scripts" / "compile_panorama_view_ir.py"

    extracted = subprocess.run(
        [
            sys.executable,
            str(extract_script),
            str(source_root),
            "--project-id",
            "PRJ-MARW",
            "--observed-at",
            OBSERVED_AT,
            "--observation-output",
            str(observation_path),
            "--receipt-output",
            str(receipt_path),
            "--loss-output",
            str(loss_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert extracted.returncode == 0, extracted.stderr
    assert json.loads(extracted.stdout)["status"] == "partial"

    compiled = subprocess.run(
        [
            sys.executable,
            str(model_script),
            str(project_root / "examples" / "reference-project.v0.2.json"),
            "--source-observation",
            str(observation_path),
            "--output",
            str(model_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    model = json.loads(model_path.read_text(encoding="utf-8"))
    assert validate_model_ir(model) == []

    view_run = subprocess.run(
        [
            sys.executable,
            str(view_script),
            str(model_path),
            "--profile",
            "dependency_dataflow",
            "--max-nodes",
            "100",
            "--output",
            str(dependency_view_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert view_run.returncode == 0, view_run.stderr
    dependency_view = json.loads(
        dependency_view_path.read_text(encoding="utf-8")
    )
    assert dependency_view["profile"] == "dependency_dataflow"
    assert validate_view_ir(dependency_view, model) == []

    overwrite_refused = subprocess.run(
        [
            sys.executable,
            str(extract_script),
            str(source_root),
            "--project-id",
            "PRJ-MARW",
            "--observed-at",
            OBSERVED_AT,
            "--observation-output",
            str(observation_path),
            "--receipt-output",
            str(receipt_path),
            "--loss-output",
            str(loss_path),
        ],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert overwrite_refused.returncode == 2
    assert "输出已存在" in overwrite_refused.stderr
