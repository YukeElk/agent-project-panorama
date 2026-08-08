from __future__ import annotations

from copy import deepcopy

from read_panorama import orientation_summary
from validate_panorama import validate_data


def by_id(items, entity_id):
    return next(item for item in items if item["id"] == entity_id)


def visible_modules(reference_data, mode):
    allowed = {mode, "both"}
    return {
        module["id"]
        for module in reference_data["architecture"]["modules"]
        if module["architectureScope"] in allowed
    }


def test_reference_current_target_and_transition_semantics(reference_data):
    current = visible_modules(reference_data, "current")
    target = visible_modules(reference_data, "target")
    assert "MOD-DIRECT-VECTOR" in current
    assert "MOD-DIRECT-VECTOR" not in target
    assert "MOD-METADATA" not in current
    assert "MOD-METADATA" in target
    transition = by_id(
        reference_data["architecture"]["transitions"], "TRANS-DIRECT-REMOVE"
    )
    direct_connection = by_id(
        reference_data["architecture"]["connections"], "CONN-AGENT-DIRECT"
    )
    assert transition["state"] == "in_progress"
    assert direct_connection["lifecycle"] == "deprecating"


def test_reference_control_orientation(reference_data):
    summary = dict(orientation_summary(reference_data))
    assert summary["当前阶段"].startswith("MVP 实现")
    assert summary["当前架构"].startswith("0.3 MVP Current")
    assert summary["目标架构"].startswith("0.4 Target Metadata Split")
    assert summary["当前发布"].startswith("0.8-dev")
    assert summary["最近更新批次"] == "UPD-003"
    assert summary["内嵌凭据"] == "存在"


def test_reference_latest_update_and_guidance(reference_data):
    latest = by_id(reference_data["updateBatches"], "UPD-003")
    assert 3 <= len(latest["summaryItems"]) <= 6
    assert len(latest["attentionItems"]) <= 5
    assert reference_data["guidance"]["recommendedOptionId"] == "FOCUS-A"
    assert len(reference_data["guidance"]["options"]) == 3


def test_reference_runtime_credentials(reference_data):
    admin = by_id(reference_data["resources"], "RES-DEV-ADMIN")
    dev_db = by_id(reference_data["resources"], "RES-DEV-DB")
    prod_db = by_id(reference_data["resources"], "RES-PROD-DB")
    assert admin["access"]["credentials"]["mode"] == "embedded"
    assert admin["access"]["credentials"]["maskedByDefault"] is True
    assert dev_db["access"]["credentials"]["mode"] == "external_file"
    assert "fields" not in dev_db["access"]["credentials"]
    assert prod_db["access"]["credentials"]["mode"] == "external_store"


def test_reference_rule_codes(schema_path, reference_path, reference_data):
    report = validate_data(
        reference_data, schema_path, base_dir=reference_path.parent
    )
    codes = {issue.code for issue in report.warnings}
    assert {
        "REVIEW_GAP",
        "VERIFICATION_GAP",
        "TRANSITION_RISK",
        "RESOURCE_RISK",
        "MISSING_REFERENCE_PATH",
        "EMBEDDED_SECRET_PRESENT",
    } <= codes


def test_review_gap_for_implemented_module_without_approved_review(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    module = by_id(broken["architecture"]["modules"], "MOD-CONSOLE")
    module["reviewIds"] = []

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "REVIEW_GAP" and "MOD-CONSOLE" in issue.message
        for issue in report.warnings
    )


def test_verification_gap_for_passed_acceptance_without_evidence(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    acceptance = by_id(broken["acceptanceCriteria"], "ACC-DEP-DEV")
    acceptance["evidenceReferenceIds"] = []

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "VERIFICATION_GAP"
        and "MOD-CONSOLE" in issue.message
        and "验收" in issue.message
        for issue in report.warnings
    )


def test_deployment_drift_for_missing_internal_release_module(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    deployment = by_id(broken["deployments"], "DEP-DEV")
    deployment["moduleDeployments"] = [
        item
        for item in deployment["moduleDeployments"]
        if item["moduleId"] != "MOD-CONSOLE"
    ]

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "DEPLOYMENT_DRIFT" and "MOD-CONSOLE" in issue.message
        for issue in report.warnings
    )


def test_scope_drift_for_active_work_assigned_to_future_stage(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    work_item = by_id(broken["workItems"], "WI-RETRIEVAL-CONC")
    work_item["stageId"] = "STG-INTEGRATION"

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "SCOPE_DRIFT" and "WI-RETRIEVAL-CONC" in issue.message
        for issue in report.warnings
    )


def test_verification_gap_for_unaccepted_acceptance(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    acceptance = by_id(broken["acceptanceCriteria"], "ACC-DEP-DEV")
    acceptance["status"] = "proposed"

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "VERIFICATION_GAP"
        and "MOD-CONSOLE" in issue.message
        and "尚未接受" in issue.message
        for issue in report.warnings
    )


def test_baseline_drift_for_missing_upgrade_strategy_at_low_divergence(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    baseline = broken["architecture"]["baselines"][0]
    baseline["divergence"] = "low"
    baseline["upgradeStrategy"] = ""

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "BASELINE_DRIFT" and baseline["id"] in issue.message
        for issue in report.warnings
    )


def test_deployment_drift_for_deployed_release_without_active_deployment(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    release = by_id(broken["releases"], "REL-V10")
    release["status"] = "deployed"

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "DEPLOYMENT_DRIFT" and "REL-V10" in issue.message
        for issue in report.warnings
    )


def test_resource_risk_for_empty_external_store_reference(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    resource = by_id(broken["resources"], "RES-PROD-DB")
    credentials = resource["access"]["credentials"]
    credentials["provider"] = ""
    credentials["reference"] = ""

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "RESOURCE_RISK" and "RES-PROD-DB" in issue.message
        for issue in report.warnings
    )


def test_stage_exit_gap_when_ready_to_advance_with_core_verification_blocker(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    stage_exit = by_id(broken["acceptanceCriteria"], "ACC-STG-MVP")
    stage_exit["verificationStatus"] = "passed"
    module = by_id(broken["architecture"]["modules"], "MOD-ORCHESTRATOR")
    module["status"]["verificationStatus"] = "failed"

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "STAGE_EXIT_GAP" and "MOD-ORCHESTRATOR" in issue.message
        for issue in report.warnings
    )


def test_production_embedded_credentials_warn(
    schema_path, reference_path, reference_data
):
    broken = deepcopy(reference_data)
    resource = by_id(broken["resources"], "RES-DEV-ADMIN")
    resource["environment"] = "production"

    report = validate_data(broken, schema_path, base_dir=reference_path.parent)

    assert any(
        issue.code == "EMBEDDED_SECRET_IN_PRODUCTION"
        and "RES-DEV-ADMIN" in issue.message
        for issue in report.warnings
    )
