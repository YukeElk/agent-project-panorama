from __future__ import annotations

from copy import deepcopy

from apply_patch import apply_update_package, compute_proposal_hash
from panorama_io import compute_data_hash, extract_data, replace_data
from validate_panorama import validate_data


def approve(package):
    package["proposalHash"] = compute_proposal_hash(package)
    package["approval"] = {
        "status": "approved",
        "approvedBy": "user",
        "approvedAt": "2026-08-08T12:00:00Z",
        "proposalHash": package["proposalHash"],
    }
    return package


def option(option_id):
    return {
        "id": option_id,
        "title": option_id,
        "whyNow": "Current lifecycle step",
        "recommendationReason": "Deterministic test",
        "benefits": ["Traceable"],
        "risks": [],
        "impact": "Local test",
        "prerequisites": [],
        "expectedOutcome": "Applied",
        "relatedEntities": [],
    }


def test_three_sequential_updates_keep_historical_next_focus_valid(
    tmp_path, template_path, reference_data, schema_path
):
    data = deepcopy(reference_data)
    html = tmp_path / "lifecycle.html"
    replace_data(template_path, data, html)

    groups = [("A", "B", "C"), ("D", "E", "F"), ("G", "H", "I")]
    for index, letters in enumerate(groups, start=1):
        current = extract_data(html)
        review_id = f"REV-LIFE-{index}"
        update_id = f"UPD-LIFE-{index}"
        focus_ids = [f"FOCUS-{letter}" for letter in letters]
        review = {
            "id": review_id, "type": "panorama_update", "subjectRefs": [{"type": "project", "id": current["project"]["id"]}],
            "status": "approved", "impactLevel": "local", "requestedBy": "human", "requestedAt": "2026-08-08T12:00:00Z",
            "reviewedBy": "user", "reviewedAt": "2026-08-08T12:00:00Z", "summary": "Approved lifecycle update",
            "comments": "", "decisionIds": [], "changeIds": [], "extensions": {},
        }
        batch = {
            "id": update_id, "revisionFrom": 0, "revisionTo": 0, "periodStart": "2026-08-08T12:00:00Z",
            "periodEnd": "2026-08-08T12:00:00Z", "createdAt": "2026-08-08T12:00:00Z", "status": "applied",
            "reviewId": review_id, "changeIds": [], "summaryItems": [f"Lifecycle {index}"], "attentionItems": [],
            "nextFocusOptionIds": focus_ids, "projectStageBefore": current["project"]["currentStageId"],
            "projectStageAfter": current["project"]["currentStageId"], "changeLevel": "local", "extensions": {},
        }
        guidance = {
            "generatedAt": "2026-08-08T12:00:00Z", "status": "approved", "currentFocusSummary": f"Lifecycle {index}",
            "options": [option(item) for item in focus_ids], "recommendedOptionId": focus_ids[0], "selectedOptionId": None,
            "extensions": {},
        }
        package = approve({
            "baseRevision": current["meta"]["revision"], "baseDataHash": compute_data_hash(current), "operations": [],
            "changeRecords": [], "reviewDraft": review, "updateBatchDraft": batch, "guidanceDraft": guidance,
        })
        apply_update_package(html, package, schema_path)

    final = extract_data(html)
    assert [item["id"] for item in final["guidance"]["options"]] == ["FOCUS-G", "FOCUS-H", "FOCUS-I"]
    historical = final["guidance"]["extensions"]["historicalOptions"]
    assert {item["option"]["id"] for item in historical} >= {"FOCUS-A", "FOCUS-B", "FOCUS-C", "FOCUS-D", "FOCUS-E", "FOCUS-F"}
    assert validate_data(final, schema_path, base_dir=tmp_path).errors == []
