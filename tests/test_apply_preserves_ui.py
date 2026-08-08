from __future__ import annotations

from pathlib import Path
from copy import deepcopy

import pytest
import apply_patch as patch_module

from panorama_io import (
    PanoramaIOError,
    compute_presentation_hash,
    create_backup,
    extract_data,
    replace_data,
    compute_data_hash,
)
from apply_patch import (
    ApplyPatchError,
    PatchOperationError,
    RevisionConflictError,
    apply_operations,
    apply_update_package,
    compute_proposal_hash,
)


TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head><style>body { color: #14213d; }</style></head>
<body><main id="app"></main>
<!-- PANORAMA_DATA_START -->
<script id="project-panorama-data" type="application/json">
{}
</script>
<!-- PANORAMA_DATA_END -->
<script>window.rendererVersion = '0.1';</script>
</body></html>
"""


def write_exact(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def read_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def test_replace_data_preserves_presentation_hash_and_unicode(tmp_path):
    source = tmp_path / "template.html"
    output = tmp_path / "panorama.html"
    write_exact(source, TEMPLATE)
    before = compute_presentation_hash(TEMPLATE)
    payload = {"project": {"name": "项目全景"}, "unsafe": "</script>"}

    replace_data(source, payload, output)

    assert extract_data(output) == payload
    assert compute_presentation_hash(read_exact(output)) == before
    assert "<\\/script>" in read_exact(output)


def test_replace_data_in_place_changes_only_payload(tmp_path):
    source = tmp_path / "panorama.html"
    write_exact(source, TEMPLATE)
    presentation_hash = compute_presentation_hash(TEMPLATE)
    replace_data(source, {"revision": 1})
    replace_data(source, {"revision": 2, "extensions": {"kept": True}})
    assert extract_data(source)["extensions"]["kept"] is True
    assert compute_presentation_hash(read_exact(source)) == presentation_hash


def test_backup_is_byte_identical(tmp_path):
    source = tmp_path / "panorama.html"
    write_exact(source, TEMPLATE)
    backup = create_backup(source)
    assert backup != source
    assert backup.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        TEMPLATE.replace("<!-- PANORAMA_DATA_END -->", ""),
        TEMPLATE + TEMPLATE,
    ],
)
def test_malformed_or_duplicate_anchor_is_rejected(tmp_path, html):
    source = tmp_path / "broken.html"
    write_exact(source, html)
    with pytest.raises(PanoramaIOError):
        extract_data(source)


def make_reference_html(tmp_path, template_path, reference_data):
    html = tmp_path / "project-panorama.html"
    replace_data(template_path, reference_data, html)
    return html


def base_package(reference_data):
    return {
        "baseRevision": reference_data["meta"]["revision"],
        "baseDataHash": compute_data_hash(reference_data),
        "operations": [],
        "changeRecords": [],
        "reviewDraft": {},
        "updateBatchDraft": {},
        "guidanceDraft": {},
    }


def approve_package(package):
    package["proposalHash"] = compute_proposal_hash(package)
    package["approval"] = {
        "status": "approved",
        "approvedBy": "user",
        "approvedAt": "2026-08-08T12:00:00Z",
        "proposalHash": package["proposalHash"],
    }
    return package


def test_minimal_patch_add_replace_remove():
    original = {"value": 1, "items": ["a", "b"], "extensions": {"keep": True}}
    updated = apply_operations(
        original,
        [
            {"op": "replace", "path": "/value", "value": 2},
            {"op": "add", "path": "/items/-", "value": "c"},
            {"op": "remove", "path": "/items/0"},
            {"op": "add", "path": "/extensions/new", "value": "ok"},
        ],
    )
    assert updated == {
        "value": 2,
        "items": ["b", "c"],
        "extensions": {"keep": True, "new": "ok"},
    }
    assert original["value"] == 1


def test_patch_apply_increments_revision_backs_up_and_preserves_ui(
    tmp_path, template_path, reference_data, schema_path
):
    html = make_reference_html(tmp_path, template_path, reference_data)
    before_bytes = html.read_bytes()
    before_presentation = compute_presentation_hash(read_exact(html))
    package = base_package(reference_data)
    package["operations"] = [
        {
            "op": "replace",
            "path": "/intent/currentFocus",
            "value": "完成经评审的 Patch Apply 验证。",
        }
    ]

    backup, updated, warnings = apply_update_package(
        html, approve_package(package), schema_path
    )

    assert backup.read_bytes() == before_bytes
    assert updated["meta"]["revision"] == reference_data["meta"]["revision"] + 1
    assert extract_data(html)["intent"]["currentFocus"] == "完成经评审的 Patch Apply 验证。"
    assert compute_presentation_hash(read_exact(html)) == before_presentation
    assert any("EMBEDDED_SECRET_PRESENT" in warning for warning in warnings)


def test_patch_apply_appends_change_review_batch_and_guidance(
    tmp_path, template_path, reference_data, schema_path
):
    html = make_reference_html(tmp_path, template_path, reference_data)
    package = base_package(reference_data)
    stamp = "2026-08-08T12:00:00+08:00"
    package["operations"] = [
        {"op": "replace", "path": "/intent/currentFocus", "value": "Apply test"}
    ]
    package["changeRecords"] = [
        {
            "id": "CHG-TEST",
            "occurredAt": stamp,
            "category": "implementation",
            "impactLevel": "local",
            "summary": "Apply approved local update.",
            "reason": "Test update protocol.",
            "source": "human",
            "entityRefs": [{"type": "module", "id": "MOD-RETRIEVAL", "label": "Retrieval"}],
            "beforeSummary": "Before",
            "afterSummary": "After",
            "reviewId": "REV-TEST",
            "significant": False,
            "architectureVersionId": "ARCH-V03",
            "referenceIds": [],
            "extensions": {},
        }
    ]
    package["reviewDraft"] = {
        "id": "REV-TEST",
        "type": "panorama_update",
        "subjectRefs": [{"type": "project", "id": "PRJ-MARW", "label": "Project"}],
        "status": "approved",
        "impactLevel": "local",
        "requestedBy": "human",
        "requestedAt": stamp,
        "reviewedBy": "user",
        "reviewedAt": stamp,
        "summary": "Approved test update.",
        "comments": "",
        "decisionIds": [],
        "changeIds": ["CHG-TEST"],
        "extensions": {},
    }
    package["updateBatchDraft"] = {
        "id": "UPD-TEST",
        "revisionFrom": 0,
        "revisionTo": 1,
        "periodStart": stamp,
        "periodEnd": stamp,
        "createdAt": stamp,
        "status": "applied",
        "reviewId": "REV-TEST",
        "changeIds": ["CHG-TEST"],
        "summaryItems": ["Applied test update."],
        "attentionItems": [],
        "nextFocusOptionIds": ["FOCUS-A"],
        "projectStageBefore": "STG-MVP",
        "projectStageAfter": "STG-MVP",
        "changeLevel": "local",
        "extensions": {},
    }
    package["guidanceDraft"] = deepcopy(reference_data["guidance"])

    _, updated, _ = apply_update_package(html, approve_package(package), schema_path)

    assert updated["changes"][-1]["id"] == "CHG-TEST"
    assert updated["reviews"][-1]["id"] == "REV-TEST"
    assert updated["updateBatches"][-1]["id"] == "UPD-TEST"
    assert updated["updateBatches"][-1]["revisionFrom"] == 3
    assert updated["updateBatches"][-1]["revisionTo"] == 4
    assert updated["meta"]["latestUpdateBatchId"] == "UPD-TEST"
    assert updated["guidance"]["recommendedOptionId"] == "FOCUS-A"


@pytest.mark.parametrize("field,value", [("baseRevision", 99), ("baseDataHash", "bad-hash")])
def test_stale_package_is_rejected_without_backup(
    field, value, tmp_path, template_path, reference_data, schema_path
):
    html = make_reference_html(tmp_path, template_path, reference_data)
    before = html.read_bytes()
    package = base_package(reference_data)
    package[field] = value
    approve_package(package)
    with pytest.raises(RevisionConflictError):
        apply_update_package(html, package, schema_path)
    assert html.read_bytes() == before
    assert list(tmp_path.glob("*.backup-*.html")) == []


def test_failed_operation_keeps_original_and_backup(
    tmp_path, template_path, reference_data, schema_path
):
    html = make_reference_html(tmp_path, template_path, reference_data)
    before = html.read_bytes()
    package = base_package(reference_data)
    package["operations"] = [
        {"op": "replace", "path": "/project/not-a-field", "value": "x"}
    ]
    approve_package(package)
    with pytest.raises(PatchOperationError):
        apply_update_package(html, package, schema_path)
    assert html.read_bytes() == before
    backups = list(tmp_path.glob("*.backup-*.html"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before


def test_schema_invalid_patch_keeps_original(
    tmp_path, template_path, reference_data, schema_path
):
    html = make_reference_html(tmp_path, template_path, reference_data)
    before = html.read_bytes()
    package = base_package(reference_data)
    package["operations"] = [{"op": "remove", "path": "/project/name"}]
    approve_package(package)
    with pytest.raises(ApplyPatchError):
        apply_update_package(html, package, schema_path)
    assert html.read_bytes() == before


def test_concurrent_change_before_commit_is_not_overwritten(
    monkeypatch, tmp_path, template_path, reference_data, schema_path
):
    html = make_reference_html(tmp_path, template_path, reference_data)
    package = base_package(reference_data)
    package["operations"] = [
        {
            "op": "replace",
            "path": "/intent/currentFocus",
            "value": "STALE APPROVED UPDATE",
        }
    ]
    approve_package(package)
    real_validate = patch_module.validate_data

    def validate_after_concurrent_write(updated, schema, *, base_dir, **kwargs):
        report = real_validate(updated, schema, base_dir=base_dir, **kwargs)
        newer = deepcopy(reference_data)
        newer["meta"]["revision"] = 13
        newer["intent"]["currentFocus"] = "CONCURRENT NEWER STATE"
        replace_data(html, newer, html)
        return report

    monkeypatch.setattr(
        patch_module, "validate_data", validate_after_concurrent_write
    )

    with pytest.raises(RevisionConflictError):
        patch_module.apply_update_package(html, package, schema_path)

    final = extract_data(html)
    assert final["meta"]["revision"] == 13
    assert final["intent"]["currentFocus"] == "CONCURRENT NEWER STATE"
    assert not html.with_name(f".{html.name}.panorama.lock").exists()
