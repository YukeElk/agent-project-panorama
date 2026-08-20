from __future__ import annotations

from copy import deepcopy
import json

import jsonschema

from event_store import build_event


def _request(project_root):
    return json.loads(
        (project_root / "examples" / "engineering-event-request.v0.1.json").read_text(
            encoding="utf-8"
        )
    )


def _validator(project_root, name):
    schema = json.loads((project_root / "schema" / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )


def test_v050_machine_contract_schemas_are_meta_valid(project_root):
    names = [
        "engineering-event.schema.v0.1.json",
        "engineering-event-outbox.schema.v0.1.json",
        "event-retention-policy.schema.v0.1.json",
        "event-redaction-manifest.schema.v0.1.json",
        "transformation-loss-report.schema.v0.1.json",
        "approval-policy.schema.v0.1.json",
        "approval-policy-revocation.schema.v0.1.json",
        "approval-policy-use-ledger.schema.v0.1.json",
        "policy-execution-receipt.schema.v0.1.json",
    ]
    for name in names:
        _validator(project_root, name)


def test_v050_event_example_request_builds_schema_valid_event(project_root):
    event = build_event(
        _request(project_root),
        stream_id="project-REFERENCE-PROJECT",
        epoch=1,
        sequence=1,
        previous_event_hash=None,
        recorded_at="2026-08-20T02:00:01Z",
        project_root=project_root,
    )

    errors = list(
        _validator(project_root, "engineering-event.schema.v0.1.json").iter_errors(
            event
        )
    )
    assert errors == []
    assert event["recordedAt"] == "2026-08-20T02:00:01Z"
    assert event["eventId"].startswith("EVT-")
    assert len(event["integrity"]["eventHash"]) == 64


def test_v050_event_schema_rejects_training_eligibility(project_root):
    event = build_event(
        _request(project_root),
        stream_id="project-REFERENCE-PROJECT",
        epoch=1,
        sequence=1,
        previous_event_hash=None,
        recorded_at="2026-08-20T02:00:01Z",
    )
    event["trainingEligibility"] = {"sft": True}

    errors = list(
        _validator(project_root, "engineering-event.schema.v0.1.json").iter_errors(
            event
        )
    )
    assert errors


def test_v050_content_digest_requires_digest_and_explicit_coverage(project_root):
    event = build_event(
        _request(project_root),
        stream_id="project-REFERENCE-PROJECT",
        epoch=1,
        sequence=1,
        previous_event_hash=None,
        recorded_at="2026-08-20T02:00:01Z",
    )
    broken = deepcopy(event)
    broken["sourceBinding"] = {
        "mode": "content_digest",
        "gitHead": None,
        "sourceSnapshotHash": None,
        "sourceContentDigest": None,
        "coverage": "unknown",
    }

    errors = list(
        _validator(project_root, "engineering-event.schema.v0.1.json").iter_errors(
            broken
        )
    )
    assert errors


def test_v050_outbox_finalized_requires_event_binding(project_root):
    outbox = {
        "formatVersion": "panorama-engineering-event-outbox.v0.1",
        "transactionId": "TXN-0123456789ABCDEF",
        "operationClass": "governance",
        "policy": "fail_closed",
        "status": "finalized",
        "createdAt": "2026-08-20T02:00:00Z",
        "updatedAt": "2026-08-20T02:00:01Z",
        "projectBinding": {
            "projectId": "REFERENCE-PROJECT",
            "baseRevision": 1,
            "expectedResultRevision": 2,
            "baseDataHash": "a" * 64,
            "expectedResultDataHash": "b" * 64,
        },
        "eventRequestHash": "c" * 64,
        "observedResult": {
            "revision": 2,
            "dataHash": "b" * 64,
            "observedAt": "2026-08-20T02:00:01Z",
        },
        "resolution": {"eventId": None, "eventHash": None, "reason": None},
        "extensions": {},
    }

    errors = list(
        _validator(
            project_root, "engineering-event-outbox.schema.v0.1.json"
        ).iter_errors(outbox)
    )
    assert errors


def test_v050_outbox_governance_is_fail_closed_and_finalized_is_valid(project_root):
    outbox = {
        "formatVersion": "panorama-engineering-event-outbox.v0.1",
        "transactionId": "TXN-0123456789ABCDEF",
        "operationClass": "governance",
        "policy": "fail_closed",
        "status": "finalized",
        "createdAt": "2026-08-20T02:00:00Z",
        "updatedAt": "2026-08-20T02:00:01Z",
        "projectBinding": {
            "projectId": "REFERENCE-PROJECT",
            "baseRevision": 1,
            "expectedResultRevision": 2,
            "baseDataHash": "a" * 64,
            "expectedResultDataHash": "b" * 64,
        },
        "eventRequestHash": "c" * 64,
        "observedResult": {
            "revision": 2,
            "dataHash": "b" * 64,
            "observedAt": "2026-08-20T02:00:01Z",
        },
        "resolution": {
            "eventId": "EVT-" + "A" * 32,
            "eventHash": "d" * 64,
            "reason": None,
        },
        "integrity": {
            "hashAlgorithm": "sha256",
            "stateHash": "e" * 64,
            "hashScope": "outbox_without_integrity",
        },
        "extensions": {},
    }
    validator = _validator(
        project_root, "engineering-event-outbox.schema.v0.1.json"
    )

    assert list(validator.iter_errors(outbox)) == []
    outbox["policy"] = "compensatable"
    assert list(validator.iter_errors(outbox))


def test_v050_retention_redaction_and_loss_contract_examples(project_root):
    retention = {
        "formatVersion": "panorama-event-retention-policy.v0.1",
        "policyId": "RETENTION-REFERENCE",
        "projectId": "REFERENCE-PROJECT",
        "effectiveAt": "2026-08-20T02:00:00Z",
        "retentionDays": None,
        "maxEventBytes": 1048576,
        "maxEvents": None,
        "allowedAccessClasses": ["PROJECT_OPERATIONAL_METADATA"],
        "containsProjectContentAllowed": False,
        "containsSecretAllowed": False,
        "containsSensitivePersonalDataAllowed": False,
        "deletionMode": "redaction_manifest_then_new_epoch",
        "extensions": {},
    }
    assert list(
        _validator(
            project_root, "event-retention-policy.schema.v0.1.json"
        ).iter_errors(retention)
    ) == []
    broken_retention = deepcopy(retention)
    broken_retention["containsSecretAllowed"] = True
    assert list(
        _validator(
            project_root, "event-retention-policy.schema.v0.1.json"
        ).iter_errors(broken_retention)
    )

    redaction = {
        "formatVersion": "panorama-event-redaction-manifest.v0.1",
        "manifestId": "RDX-0123456789ABCDEF",
        "projectId": "REFERENCE-PROJECT",
        "streamId": "project-REFERENCE-PROJECT",
        "previousEpoch": 1,
        "nextEpoch": 2,
        "createdAt": "2026-08-20T02:00:00Z",
        "reasonClass": "user_withdrawal",
        "affectedEventIds": ["EVT-" + "A" * 32],
        "previousEpochHeadHash": "b" * 64,
        "payloadDisposition": "deleted",
        "authorization": {
            "method": "explicit_user_authorization",
            "referenceId": "AUTH-REFERENCE",
            "recordedAt": "2026-08-20T02:00:00Z",
        },
        "replacementGenesisEventId": "EVT-" + "C" * 32,
        "extensions": {},
    }
    assert list(
        _validator(
            project_root, "event-redaction-manifest.schema.v0.1.json"
        ).iter_errors(redaction)
    ) == []

    loss = {
        "formatVersion": "panorama-transformation-loss-report.v0.1",
        "reportId": "LOSS-0123456789ABCDEF",
        "generatedAt": "2026-08-20T02:00:00Z",
        "adapter": {"id": "structurizr-dynamic", "version": "0.1.0"},
        "inputBinding": {"format": "structurizr-json", "revision": "example", "sha256": "a" * 64},
        "outputBinding": {"format": "archify-sequence", "revision": None, "sha256": "b" * 64},
        "status": "partial",
        "counts": {"input": 11, "output": 1, "preserved": 1, "lost": 10, "degraded": 0, "unknown": 0},
        "preservation": {
            "elements": "partial",
            "relationships": "partial",
            "views": "partial",
            "order": "preserved",
            "timestamps": "not_applicable",
            "evidence": "unknown",
            "layout": "lost",
            "documentsAndAdrs": "lost",
        },
        "losses": [
            {
                "kind": "view.unsupported",
                "sourceRef": "views[1..10]",
                "severity": "unsupported",
                "reason": "The prototype handles one dynamic view only.",
                "preservedAs": None,
            }
        ],
        "informationGaps": [],
        "extensions": {},
    }
    assert list(
        _validator(
            project_root, "transformation-loss-report.schema.v0.1.json"
        ).iter_errors(loss)
    ) == []
