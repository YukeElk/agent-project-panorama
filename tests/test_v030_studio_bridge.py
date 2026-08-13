from __future__ import annotations

from copy import deepcopy
import http.client
import os
from pathlib import Path
import threading

import pytest
import studio_bridge

from panorama_io import (
    compute_presentation_hash,
    extract_data,
    replace_data,
)
from studio_bridge import StudioBridge, StudioBridgeError


@pytest.fixture
def bridge(tmp_path, template_path, reference_data):
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


def _request(
    app: StudioBridge,
    method: str,
    path: str,
    body: dict | None = None,
    *,
    cap: str | None = None,
    csrf: str | None = None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str = "application/json",
    extra: dict[str, str] | None = None,
):
    connection = http.client.HTTPConnection("127.0.0.1", app.server.server_address[1])
    payload = None
    headers = {"Host": host or app.host}
    if cap is not None:
        headers["X-Panorama-Capability"] = cap
    if csrf is not None:
        headers["X-Panorama-CSRF"] = csrf
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        import json

        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(payload))
    if extra:
        headers.update(extra)
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    if response_headers.get("content-type", "").startswith("application/json"):
        import json

        content = json.loads(raw.decode("utf-8"))
    else:
        content = raw.decode("utf-8")
    return response.status, response_headers, content


def _post(app: StudioBridge, path: str, body: dict):
    return _request(
        app,
        "POST",
        path,
        body,
        cap=app.capability,
        csrf=app.csrf,
        origin=app.origin,
    )


def test_security_gates_and_json_envelopes(bridge):
    app, _ = bridge

    status, _, body = _request(app, "GET", "/api/v1/health")
    assert status == 401
    assert body == {
        "ok": False,
        "error": {"code": "UNAUTHORIZED", "message": "capability is invalid"},
    }

    status, _, body = _request(
        app, "GET", "/api/v1/health", cap=app.capability
    )
    assert status == 200
    assert body["ok"] is True
    assert body["data"]["csrfToken"] == app.csrf
    assert body["data"]["agent"]["reason"] == "codex_cli_not_enabled_at_startup"
    assert body["data"]["features"]["approvals"] is True
    assert body["data"]["features"]["factsRefresh"] is True

    status, _, body = _request(
        app,
        "POST",
        "/api/v1/sessions",
        {},
        cap=app.capability,
        csrf=app.csrf,
        origin="http://evil.invalid",
    )
    assert status == 403
    assert body["error"]["code"] == "ORIGIN_REJECTED"

    status, _, body = _request(
        app,
        "POST",
        "/api/v1/sessions",
        {},
        cap=app.capability,
        csrf="wrong",
        origin=app.origin,
    )
    assert status == 403
    assert body["error"]["code"] == "CSRF_REJECTED"

    status, _, body = _request(
        app,
        "POST",
        "/api/v1/sessions",
        {},
        cap=app.capability,
        csrf=app.csrf,
        origin=app.origin,
        content_type="text/plain",
    )
    assert status == 415
    assert body["error"]["code"] == "CONTENT_TYPE_REJECTED"

    status, _, body = _request(
        app,
        "POST",
        "/api/v1/sessions",
        {},
        cap=app.capability,
        csrf=app.csrf,
        origin=app.origin,
        extra={"Content-Encoding": "gzip"},
    )
    assert status == 400
    assert body["error"]["code"] == "CONTENT_ENCODING_REJECTED"

    status, _, body = _request(
        app, "GET", "/api/v1/health", cap=app.capability, host="localhost"
    )
    assert status == 403
    assert body["error"]["code"] == "INVALID_HOST"

    status, _, body = _request(app, "OPTIONS", "/api/v1/health")
    assert status == 405
    assert body["error"]["code"] == "CORS_DISABLED"

    for method in ("DELETE", "PATCH", "TRACE", "CONNECT"):
        status, _, body = _request(app, method, "/api/v1/health")
        assert status == 405
        assert body["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_startup_rejects_link_or_reparse_in_work_ancestry(
    monkeypatch, tmp_path, template_path, reference_data
):
    panorama = tmp_path / "project-panorama.local.html"
    replace_data(template_path, reference_data, panorama)
    real_check = studio_bridge._path_is_link_or_reparse

    def fake_check(path: Path) -> bool:
        return path.name == ".panorama-work" or real_check(path)

    monkeypatch.setattr(studio_bridge, "_path_is_link_or_reparse", fake_check)
    with pytest.raises(StudioBridgeError) as raised:
        StudioBridge(panorama, tmp_path)
    assert raised.value.code == "UNSAFE_WORK_PATH"


def test_write_rechecks_work_ancestry_and_containment(
    monkeypatch, tmp_path, template_path, reference_data
):
    panorama = tmp_path / "project-panorama.local.html"
    replace_data(template_path, reference_data, panorama)
    app = StudioBridge(panorama, tmp_path)
    try:
        with pytest.raises(StudioBridgeError) as escaped:
            app._write_artifact(tmp_path / "outside.json", {"value": 1})
        assert escaped.value.code == "UNSAFE_WORK_PATH"

        real_check = studio_bridge._path_is_link_or_reparse

        def fake_check(path: Path) -> bool:
            return path == app.sessions or real_check(path)

        monkeypatch.setattr(studio_bridge, "_path_is_link_or_reparse", fake_check)
        with pytest.raises(StudioBridgeError) as redirected:
            app.create_session()
        assert redirected.value.code == "UNSAFE_WORK_PATH"
    finally:
        app.close()


def test_artifact_read_rejects_reparse_target(
    monkeypatch, tmp_path, template_path, reference_data
):
    panorama = tmp_path / "project-panorama.local.html"
    replace_data(template_path, reference_data, panorama)
    app = StudioBridge(panorama, tmp_path)
    try:
        target = app.proposals / "PROPOSAL-UNSAFE.json"
        target.write_text("{}", encoding="utf-8")
        real_check = studio_bridge._path_is_link_or_reparse
        monkeypatch.setattr(
            studio_bridge,
            "_path_is_link_or_reparse",
            lambda path: path == target or real_check(path),
        )
        with pytest.raises(StudioBridgeError) as raised:
            app._read_artifact(target, "Proposal")
        assert raised.value.code == "UNSAFE_WORK_PATH"
    finally:
        app.close()


def test_context_does_not_read_or_list_redirected_session(
    monkeypatch, tmp_path, template_path, reference_data
):
    panorama = tmp_path / "project-panorama.local.html"
    replace_data(template_path, reference_data, panorama)
    app = StudioBridge(panorama, tmp_path)
    try:
        valid = app.create_session()
        malicious = app.sessions / "SESSION-REDIRECT.json"
        malicious.write_text(
            '{"sessionId":"SESSION-EXTERNAL","secret":"must-not-read"}',
            encoding="utf-8",
        )
        real_check = studio_bridge._path_is_link_or_reparse
        monkeypatch.setattr(
            studio_bridge,
            "_path_is_link_or_reparse",
            lambda path: path == malicious or real_check(path),
        )
        context = app.context()
        assert [item["sessionId"] for item in context["sessions"]] == [
            valid["sessionId"]
        ]
    finally:
        app.close()


def test_formal_validation_revision_matches_disk_api_and_session(bridge):
    import json

    app, _ = bridge
    session = app.create_session()
    artifact = app.formal_validation(session["sessionId"], None)
    disk = json.loads(
        (
            app.validations / f"{artifact['validationId']}.json"
        ).read_text(encoding="utf-8")
    )
    persisted = app.get_session(session["sessionId"])

    assert disk["sessionRevision"] == artifact["sessionRevision"]
    assert persisted["sessionRevision"] == artifact["sessionRevision"]
    assert persisted["formalValidation"]["validationId"] == artifact["validationId"]


def test_response_only_csp_variant_does_not_modify_source(bridge):
    app, panorama = bridge
    before = panorama.read_bytes()
    canonical_presentation = compute_presentation_hash(
        before.decode("utf-8")
    )

    status, headers, launcher = _request(app, "GET", "/studio/")

    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    csp = headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "nonce-" in csp
    assert "project-panorama-data" not in launcher
    assert reference_project_marker(panorama) not in launcher
    assert 'fetch("/studio/document"' in launcher
    assert '"X-Panorama-Capability": capability' in launcher

    status, _, unauthorized = _request(app, "GET", "/studio/document")
    assert status == 401
    assert unauthorized["error"]["code"] == "UNAUTHORIZED"

    status, headers, body = _request(
        app, "GET", "/studio/document", cap=app.capability
    )
    assert status == 200
    csp = headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "http-equiv=\"Content-Security-Policy\"" not in body
    assert '<script nonce="' in body
    assert '<style nonce="' in body
    assert panorama.read_bytes() == before
    assert (
        compute_presentation_hash(panorama.read_text(encoding="utf-8"))
        == canonical_presentation
    )


def reference_project_marker(panorama: Path) -> str:
    data = extract_data(panorama)
    return str(data.get("project", {}).get("name", ""))


def test_session_create_load_save_cas_and_no_capability_on_disk(bridge):
    app, _ = bridge
    status, _, created = _post(app, "/api/v1/sessions", {})
    assert status == 201
    session = created["data"]["session"]
    assert session["mode"] == "bridge"
    assert session["sessionRevision"] == 1
    assert "sourceObservation" in session
    assert "bridgeSourceObservation" not in session

    status, _, loaded = _request(
        app,
        "GET",
        f"/api/v1/sessions/{session['sessionId']}",
        cap=app.capability,
    )
    assert status == 200
    edited = deepcopy(loaded["data"]["session"])
    edited["scope"]["level"] = "agent"
    status, _, saved = _request(
        app,
        "PUT",
        f"/api/v1/sessions/{session['sessionId']}",
        {"expectedRevision": 1, "session": edited},
        cap=app.capability,
        csrf=app.csrf,
        origin=app.origin,
    )
    assert status == 200
    assert saved["data"]["session"]["sessionRevision"] == 2

    status, _, conflict = _request(
        app,
        "PUT",
        f"/api/v1/sessions/{session['sessionId']}",
        {"expectedRevision": 1, "session": edited},
        cap=app.capability,
        csrf=app.csrf,
        origin=app.origin,
    )
    # CAS is checked before idempotence: stale retries never bypass revision.
    assert status == 409
    assert conflict["ok"] is False
    divergent = deepcopy(edited)
    divergent["scope"]["level"] = "module"
    status, _, divergent_conflict = _request(
        app,
        "PUT",
        f"/api/v1/sessions/{session['sessionId']}",
        {"expectedRevision": 1, "session": divergent},
        cap=app.capability,
        csrf=app.csrf,
        origin=app.origin,
    )
    assert status == 409
    assert divergent_conflict["ok"] is False

    work = app.project_root / ".panorama-work"
    all_bytes = b"".join(path.read_bytes() for path in work.rglob("*.json"))
    assert app.capability.encode() not in all_bytes
    assert app.csrf.encode() not in all_bytes


def test_formal_proposal_approval_apply_end_to_end(bridge):
    app, panorama = bridge
    original_presentation = compute_presentation_hash(
        panorama.read_text(encoding="utf-8")
    )
    original_revision = extract_data(panorama)["meta"]["revision"]
    _, _, created = _post(app, "/api/v1/sessions", {})
    session = created["data"]["session"]
    candidate = next(
        item
        for item in session["candidates"]
        if item["candidateId"] == session["activeCandidateId"]
    )
    candidate["nodes"][0]["purpose"] += " Studio applied"
    session["semanticOperations"].append(
        {
            "opId": "OP-STUDIO-TEST",
            "seq": 1,
            "at": "2026-08-12T09:00:00Z",
            "actor": "human",
            "kind": "node.update",
            "target": {"nodeId": candidate["nodes"][0]["nodeId"]},
            "before": None,
            "after": candidate["nodes"][0]["purpose"],
            "affectsSemanticHash": True,
        }
    )
    _, _, saved = _request(
        app,
        "PUT",
        f"/api/v1/sessions/{session['sessionId']}",
        {"expectedRevision": 1, "session": session},
        cap=app.capability,
        csrf=app.csrf,
        origin=app.origin,
    )
    session = saved["data"]["session"]

    status, _, validated = _post(
        app,
        "/api/v1/formal-validations",
        {
            "sessionId": session["sessionId"],
            "candidateId": session["activeCandidateId"],
        },
    )
    assert status == 200
    assert validated["data"]["artifact"]["validation"]["valid"] is True

    status, _, proposed = _post(
        app,
        "/api/v1/proposals",
        {
            "sessionId": session["sessionId"],
            "candidateId": session["activeCandidateId"],
            "summary": "Apply Studio target architecture edit",
            "reason": "Reviewed architecture canvas change",
            "changeLevel": "architecture",
        },
    )
    assert status == 201
    proposal = proposed["data"]["proposal"]
    proposal_path = app.project_root / proposal["artifact"]
    wrapper = __import__("json").loads(proposal_path.read_text(encoding="utf-8"))
    package = wrapper["proposal"]
    assert package["approval"]["status"] == "pending"
    review_binding = package["reviewDraft"]["extensions"][
        "architectureStudioBinding"
    ]
    assert review_binding == package["updateBatchDraft"]["extensions"][
        "architectureStudioBinding"
    ]
    assert review_binding["semanticHash"] == session["semanticHash"]
    assert proposal["sessionRevision"] > session["sessionRevision"]
    assert review_binding["studioSourceDigest"]["coverageComplete"] is True
    assert review_binding["sessionRevision"] == proposal["sessionRevision"]
    assert proposal["operations"] == package["operations"]
    assert proposal["affectedEntities"] == wrapper["facts"]["affectedEntities"]
    assert proposal["validation"] == wrapper["validation"]

    status, _, rejected = _post(
        app,
        "/api/v1/approvals",
        {
            "proposalId": proposal["proposalId"],
            "approvedHash": proposal["proposalHash"],
            "approvedBy": "Architecture Reviewer",
            "confirmation": "批准",
        },
    )
    assert status == 400
    assert rejected["error"]["code"] == "CONFIRMATION_MISMATCH"

    status, _, approved = _post(
        app,
        "/api/v1/approvals",
        {
            "proposalId": proposal["proposalId"],
            "approvedHash": proposal["proposalHash"],
            "approvedBy": "Architecture Reviewer",
            "confirmation": proposal["confirmationPhrase"],
        },
    )
    assert status == 201
    approval = approved["data"]["approval"]
    assert approval["status"] == "approved"

    status, _, mismatched_apply = _post(
        app,
        "/api/v1/apply",
        {
            "proposalId": proposal["proposalId"],
            "proposalHash": "0" * 64,
            "approvalId": approval["approvalId"],
        },
    )
    assert status == 409
    assert mismatched_apply["error"]["code"] == "PROPOSAL_HASH_MISMATCH"

    status, _, applied = _post(
        app,
        "/api/v1/apply",
        {
            "proposalId": proposal["proposalId"],
            "proposalHash": proposal["proposalHash"],
            "approvalId": approval["approvalId"],
        },
    )
    assert status == 200
    result = applied["data"]["apply"]
    assert result["revision"] == original_revision + 1
    assert Path(result["backup"]).exists()
    assert result["backupPath"] == result["backup"]
    assert result["presentationHash"] == original_presentation
    assert (
        compute_presentation_hash(panorama.read_text(encoding="utf-8"))
        == original_presentation
    )
    updated = extract_data(panorama)
    assert any(
        item.get("extensions", {}).get("architectureStudioBinding")
        for item in updated["reviews"]
    )
    # Session export stores only the Proposal pointer/summary, never package or approval.
    persisted = __import__("studio_session").load_session(
        app.project_root, session["sessionId"]
    )
    assert "operations" not in persisted["proposalArtifact"]
    assert "approval" not in persisted["proposalArtifact"]


def test_same_size_content_change_with_restored_mtime_blocks_formal(bridge):
    app, _ = bridge
    source = app.project_root / "source.txt"
    source.write_text("AAAA", encoding="utf-8")
    session = app.create_session()
    before = source.stat()
    source.write_text("BBBB", encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))

    status, _, response = _post(
        app,
        "/api/v1/formal-validations",
        {"sessionId": session["sessionId"]},
    )
    assert status == 409
    assert response["error"]["code"] == "SOURCE_DRIFT"


def test_incomplete_source_coverage_blocks_proposal(
    monkeypatch, tmp_path, template_path, reference_data
):
    panorama = tmp_path / "project-panorama.local.html"
    replace_data(template_path, reference_data, panorama)
    complete = studio_bridge._studio_source_digest(tmp_path, panorama)
    incomplete = dict(complete, coverageComplete=False, incompleteReason="test_limit")
    monkeypatch.setattr(studio_bridge, "_studio_source_digest", lambda *_: incomplete)
    app = StudioBridge(panorama, tmp_path)
    try:
        session = app.create_session()
        with pytest.raises(StudioBridgeError) as raised:
            app.create_proposal(
                {
                    "sessionId": session["sessionId"],
                    "summary": "test",
                    "reason": "test",
                }
            )
        assert raised.value.code == "SOURCE_COVERAGE_INCOMPLETE"
    finally:
        app.close()


def test_uninitialized_v02_source_binding_creates_stale_session_but_blocks_formal(
    tmp_path, template_path
):
    import json

    data = json.loads(
        (Path(__file__).parents[1] / "examples" / "reference-project.v0.2.json").read_text(
            encoding="utf-8"
        )
    )
    panorama = tmp_path / "project-panorama.v0.2.local.html"
    replace_data(template_path, data, panorama)
    app = StudioBridge(panorama, tmp_path)
    try:
        session = app.create_session()
        assert session["status"] == "stale"
        assert session["formalSourceBindingStatus"] == "uninitialized"
        with pytest.raises(StudioBridgeError) as raised:
            app.formal_validation(session["sessionId"], None)
        assert raised.value.code == "FORMAL_SOURCE_UNINITIALIZED"
    finally:
        app.close()


def test_session_semantic_edit_after_proposal_blocks_approval(bridge):
    app, _ = bridge
    session = app.create_session()
    proposal = app.create_proposal(
        {
            "sessionId": session["sessionId"],
            "summary": "Freeze candidate",
            "reason": "Verify stale Proposal rejection",
        }
    )
    edited = app.get_session(session["sessionId"])
    candidate = next(
        item
        for item in edited["candidates"]
        if item["candidateId"] == edited["activeCandidateId"]
    )
    candidate["nodes"][0]["purpose"] += " changed after proposal"
    app.put_session(
        edited["sessionId"], edited["sessionRevision"], edited
    )

    with pytest.raises(StudioBridgeError) as raised:
        app.record_approval(
            {
                "proposalId": proposal["proposalId"],
                "approvedHash": proposal["proposalHash"],
                "approvedBy": "Reviewer",
                "confirmation": proposal["confirmationPhrase"],
            }
        )
    assert raised.value.code == "SESSION_DRIFT"


def test_identical_session_put_is_idempotent(bridge):
    app, _ = bridge
    session = app.create_session()
    saved = app.put_session(
        session["sessionId"], session["sessionRevision"], deepcopy(session)
    )
    assert saved["sessionRevision"] == session["sessionRevision"]


def test_filesystem_formal_binding_without_git_head_is_not_uninitialized():
    assert studio_bridge._formal_source_status(
        {
            "mode": "filesystem_metadata",
            "gitHead": None,
            "sourceSnapshotHash": "a" * 64,
        }
    ) == "bound"


def test_agent_and_fact_refresh_are_explicit_when_unavailable(bridge):
    app, _ = bridge
    _, _, created = _post(app, "/api/v1/sessions", {})
    session = created["data"]["session"]
    status, _, review = _post(
        app,
        "/api/v1/review-jobs",
        {"sessionId": session["sessionId"]},
    )
    assert status == 409
    assert review["error"]["code"] == "AGENT_UNAVAILABLE"

    status, _, refresh = _post(app, "/api/v1/facts/refresh", {})
    assert status == 409
    assert refresh["error"]["code"] == "FACT_REFRESH_REQUIRES_V02"


def test_review_job_projects_advisory_envelope(monkeypatch, tmp_path, template_path, reference_data):
    panorama = tmp_path / "review-project.html"
    replace_data(template_path, reference_data, panorama)
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"stub")
    monkeypatch.setattr("studio_bridge.locate_codex_cli", lambda _path, **_kwargs: executable)
    app = StudioBridge(panorama, tmp_path, codex_cli=executable)
    monkeypatch.setattr(
        "studio_bridge.codex_capability",
        lambda _path, **_kwargs: {"available": True, "authenticated": True, "version": "test"},
    )
    monkeypatch.setattr(
        "studio_bridge.review_with_codex",
        lambda bundle, **_kwargs: {
            "format": "panorama-architecture-agent-review-envelope.v0.1",
            "inputHash": "a" * 64,
            "advisory": True,
            "review": {
                "verdict": "changes_requested",
                "summary": "Add explicit state ownership.",
                "findings": [{"title": "State ownership unknown"}],
            },
            "adapter": {"canApprove": False, "canApply": False},
        },
    )
    session = app.create_session()
    expected_semantic = session["semanticHash"]
    job = app.create_review_job(session["sessionId"], None)
    future_done = False
    for _ in range(100):
        result = app.get_job(job["jobId"])
        if result["status"] in {"succeeded", "failed"}:
            future_done = True
            break
        threading.Event().wait(0.01)
    app.close()

    assert future_done is True
    assert result["status"] == "succeeded"
    assert result["result"] == {
        "reviewId": "REVIEW-" + "A" * 24,
        "verdict": "changes_requested",
        "summary": "Add explicit state ownership.",
        "findings": [{"title": "State ownership unknown"}],
        "inputHash": "a" * 64,
        "semanticHash": expected_semantic,
    }
