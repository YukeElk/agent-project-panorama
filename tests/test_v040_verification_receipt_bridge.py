from __future__ import annotations

from copy import deepcopy
import http.client
import json
from pathlib import Path
import threading

import pytest

from import_verification_receipt import compute_receipt_hash
from panorama_io import compute_data_hash, compute_presentation_hash, extract_data, replace_data
from studio_bridge import StudioBridge


@pytest.fixture
def receipt_bridge(tmp_path, template_path, reference_data):
    panorama = tmp_path / "project-panorama.local.html"
    replace_data(template_path, reference_data, panorama)
    app = StudioBridge(panorama, tmp_path)
    server = app.bind(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, panorama
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=5)


def post(app: StudioBridge, path: str, body: dict):
    connection = http.client.HTTPConnection("127.0.0.1", app.server.server_address[1])
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    connection.request(
        "POST",
        path,
        body=raw,
        headers={
            "Host": app.host,
            "Origin": app.origin,
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "X-Panorama-Capability": app.capability,
            "X-Panorama-CSRF": app.csrf,
        },
    )
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, payload


def bound_receipt(project_root: Path, current: dict) -> dict:
    value = json.loads(
        (project_root / "examples" / "verification-receipt.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    value["projectBinding"].update(
        {
            "projectId": current["project"]["id"],
            "panoramaRevision": current["meta"]["revision"],
            "panoramaDataHash": compute_data_hash(current),
            "gitHead": None,
            "sourceSnapshotHash": None,
        }
    )
    value["producer"]["receiptHash"] = compute_receipt_hash(value)
    return value


def test_bridge_receipt_preview_proposal_exact_approval_and_apply(
    receipt_bridge, project_root: Path
):
    app, panorama = receipt_bridge
    current = extract_data(panorama)
    before_presentation = compute_presentation_hash(panorama.read_text(encoding="utf-8"))
    gate_before = deepcopy(next(item for item in current["gates"] if item["id"] == "GATE-WS-CONC"))
    acceptance_before = deepcopy(
        next(
            item
            for item in current["acceptanceCriteria"]
            if item["id"] == "ACC-WS-CONSISTENCY"
        )
    )
    receipt = bound_receipt(project_root, current)

    status, envelope = post(
        app, "/api/v1/verification-receipts/preview", {"receipt": receipt}
    )
    assert status == 200
    preview = envelope["data"]["preview"]
    assert preview["status"] == "ready"
    assert preview["experimental"] is True
    assert preview["boundary"]["setsGatePassed"] is False

    status, envelope = post(
        app,
        "/api/v1/verification-receipts/proposals",
        {"receiptHash": preview["receiptHash"]},
    )
    assert status == 201
    proposal = envelope["data"]["proposal"]
    assert proposal["kind"] == "verification_receipt"
    assert proposal["validation"]["valid"] is True
    assert proposal["confirmationPhrase"] == (
        "批准 Receipt Proposal " + proposal["proposalHash"]
    )

    status, envelope = post(
        app,
        "/api/v1/approvals",
        {
            "proposalId": proposal["proposalId"],
            "approvedHash": proposal["proposalHash"],
            "approvedBy": "release-reviewer",
            "confirmation": proposal["confirmationPhrase"],
        },
    )
    assert status == 201
    approval = envelope["data"]["approval"]

    status, envelope = post(
        app,
        "/api/v1/apply",
        {
            "proposalId": proposal["proposalId"],
            "proposalHash": proposal["proposalHash"],
            "approvalId": approval["approvalId"],
        },
    )
    assert status == 200
    updated = extract_data(panorama)
    assert envelope["data"]["apply"]["revision"] == current["meta"]["revision"] + 1
    assert compute_presentation_hash(panorama.read_text(encoding="utf-8")) == before_presentation
    gate_after = next(item for item in updated["gates"] if item["id"] == "GATE-WS-CONC")
    acceptance_after = next(
        item
        for item in updated["acceptanceCriteria"]
        if item["id"] == "ACC-WS-CONSISTENCY"
    )
    assert gate_after["status"] == gate_before["status"]
    assert acceptance_after["status"] == acceptance_before["status"]
    assert acceptance_after["verificationStatus"] == acceptance_before["verificationStatus"]
    assert any(
        item.get("extensions", {}).get("verificationReceiptHash")
        == preview["receiptHash"]
        for item in updated["references"]
    )


def test_bridge_rejects_receipt_proposal_after_panorama_drift(
    receipt_bridge, project_root: Path
):
    app, panorama = receipt_bridge
    current = extract_data(panorama)
    receipt = bound_receipt(project_root, current)
    status, envelope = post(
        app, "/api/v1/verification-receipts/preview", {"receipt": receipt}
    )
    assert status == 200
    preview = envelope["data"]["preview"]
    drifted = extract_data(panorama)
    drifted["meta"]["revision"] += 1
    replace_data(panorama, drifted)
    status, envelope = post(
        app,
        "/api/v1/verification-receipts/proposals",
        {"receiptHash": preview["receiptHash"]},
    )
    assert status == 409
    assert envelope["error"]["code"] == "OPERATION_FAILED"
