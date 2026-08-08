from __future__ import annotations

from copy import deepcopy
import json

import jsonschema

from validate_panorama import main as validate_main, validate_data


def test_schema_is_valid_draft_2020_12(schema_path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_reference_project_passes_schema(schema_path, reference_data):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    errors = list(validator.iter_errors(reference_data))
    assert errors == []


def test_reference_project_has_no_validation_errors(
    schema_path, reference_path, reference_data
):
    report = validate_data(
        reference_data, schema_path, base_dir=reference_path.parent
    )
    assert report.errors == []
    assert any(issue.code == "EMBEDDED_SECRET_PRESENT" for issue in report.warnings)


def test_schema_invalid_collection_item_returns_report(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    broken["requirements"][0] = 7

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(issue.code == "SCHEMA" for issue in report.errors)


def test_invalid_utf8_json_returns_cli_exit_2(tmp_path, capsys):
    source = tmp_path / "invalid-utf8.json"
    source.write_bytes(b"\xff")

    exit_code = validate_main([str(source)])

    assert exit_code == 2
    assert "ERROR FILE" in capsys.readouterr().err
