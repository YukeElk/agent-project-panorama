from __future__ import annotations

from copy import deepcopy

import pytest

from validate_panorama import validate_data


def error_codes(data, schema_path, reference_path):
    report = validate_data(data, schema_path, base_dir=reference_path.parent)
    return {issue.code for issue in report.errors}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["architecture"]["modules"][0].__setitem__(
            "id", "MOD-BROKEN-RENAME"
        ),
        lambda data: data["architecture"]["connections"][0].__setitem__(
            "toModuleId", "MOD-MISSING-ENDPOINT"
        ),
        lambda data: data["deployments"][0]["resourceIds"].__setitem__(
            0, "RES-MISSING"
        ),
    ],
    ids=["module-id", "connection-endpoint", "deployment-resource"],
)
def test_broken_entity_references_fail(
    mutate, reference_data, schema_path, reference_path
):
    broken = deepcopy(reference_data)
    mutate(broken)
    assert "BROKEN_REFERENCE" in error_codes(
        broken, schema_path, reference_path
    )


def test_invalid_decision_option_fails(reference_data, schema_path, reference_path):
    broken = deepcopy(reference_data)
    broken["decisions"][0]["recommendedOptionId"] = "OPT-NOT-IN-DECISION"
    assert "BROKEN_DECISION_OPTION" in error_codes(
        broken, schema_path, reference_path
    )


def test_invalid_guidance_option_fails(reference_data, schema_path, reference_path):
    broken = deepcopy(reference_data)
    broken["guidance"]["recommendedOptionId"] = "FOCUS-MISSING"
    assert "BROKEN_GUIDANCE_OPTION" in error_codes(
        broken, schema_path, reference_path
    )


def test_duplicate_global_id_fails(reference_data, schema_path, reference_path):
    broken = deepcopy(reference_data)
    broken["risks"][0]["id"] = broken["requirements"][0]["id"]
    assert "DUPLICATE_ID" in error_codes(broken, schema_path, reference_path)


def test_exactly_one_current_stage_is_required(
    reference_data, schema_path, reference_path
):
    broken = deepcopy(reference_data)
    broken["stages"][3]["status"] = "current"
    assert "CURRENT_STAGE_COUNT" in error_codes(
        broken, schema_path, reference_path
    )
