from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import jsonschema


def _load(project_root, relative_path):
    return json.loads((project_root / relative_path).read_text(encoding="utf-8"))


def _validator(project_root):
    schema = _load(project_root, "schema/approval-policy.schema.v0.1.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )


def _example(project_root):
    return _load(
        project_root, "examples/approval-policy.verification-receipt.v0.1.json"
    )


def _canonical_hash_without_approval(policy):
    semantics = deepcopy(policy)
    semantics.pop("approvalBinding")
    canonical = json.dumps(
        semantics, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_execution_receipt_hash(receipt):
    semantics = deepcopy(receipt)
    semantics["integrity"].pop("receiptHash")
    canonical = json.dumps(
        semantics, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_v050_approval_policy_schema_and_example_are_valid(project_root):
    policy = _example(project_root)
    assert list(_validator(project_root).iter_errors(policy)) == []
    assert policy["approvalBinding"]["policyHash"] == _canonical_hash_without_approval(
        policy
    )


def test_v050_approval_policy_rejects_human_only_operation(project_root):
    policy = _example(project_root)
    policy["operation"] = "governance.apply"
    assert list(_validator(project_root).iter_errors(policy))


def test_v050_policy_path_scope_uses_bounded_segments_not_regex(project_root):
    policy = _example(project_root)
    policy["scope"]["allowedPanoramaPathTemplates"] = ["^/references/.*$"]
    assert list(_validator(project_root).iter_errors(policy))


def test_v050_receipt_policy_requires_schema_02_and_trusted_producer(project_root):
    policy = _example(project_root)
    policy["projectBinding"]["panoramaSchemaVersions"] = ["0.1"]
    assert list(_validator(project_root).iter_errors(policy))

    policy = _example(project_root)
    policy["projectBinding"]["panoramaSchemaVersions"] = ["0.1", "0.2"]
    assert list(_validator(project_root).iter_errors(policy))

    policy = _example(project_root)
    policy["scope"]["producerBindings"] = []
    assert list(_validator(project_root).iter_errors(policy))

    policy = _example(project_root)
    policy["scope"]["producerBindings"][0]["producerArtifactHash"] = None
    assert list(_validator(project_root).iter_errors(policy))


def test_v050_command_policy_requires_exact_command_binding(project_root):
    policy = _example(project_root)
    policy["operation"] = "validation_manifest.execute"
    policy["effectClass"] = "local_execution"
    policy["scope"]["receiptRules"] = None
    policy["scope"]["producerBindings"] = []
    assert list(_validator(project_root).iter_errors(policy))


def test_v050_policy_stop_conditions_are_fail_closed(project_root):
    policy = _example(project_root)
    policy["stopConditions"]["onProtectedPathTouch"] = False
    assert list(_validator(project_root).iter_errors(policy))


def test_v050_policy_cannot_allow_external_publish_or_secret_access(project_root):
    policy = _example(project_root)
    command = {
        "commandId": "tests-reference",
        "argvSha256": "b" * 64,
        "cwd": ".",
        "timeoutSeconds": 300,
        "allowedExitCodes": [0],
        "networkIsolation": "not_enforced",
        "filesystemIsolation": "not_enforced",
        "projectWriteCheck": "post_execution_metadata_check",
        "secretAccessAllowed": False,
        "stdoutStderrRetentionAllowed": False,
        "dependencyInstallationAllowed": False,
        "externalPublishAllowed": True,
    }
    policy["operation"] = "validation_manifest.execute"
    policy["effectClass"] = "local_execution"
    policy["scope"]["receiptRules"] = None
    policy["scope"]["producerBindings"] = []
    policy["scope"]["commandBindings"] = [command]
    assert list(_validator(project_root).iter_errors(policy))


def test_v050_execution_receipt_is_valid_hash_bound_and_non_governing(project_root):
    schema = _load(
        project_root, "schema/policy-execution-receipt.schema.v0.1.json"
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    receipt = _load(project_root, "examples/policy-execution-receipt.v0.1.json")
    policy = _example(project_root)

    assert list(validator.iter_errors(receipt)) == []
    assert receipt["policyBinding"]["policyHash"] == policy["approvalBinding"][
        "policyHash"
    ]
    assert receipt["integrity"]["receiptHash"] == _canonical_execution_receipt_hash(
        receipt
    )

    broken = deepcopy(receipt)
    broken["effect"]["governanceMutationObserved"] = True
    assert list(validator.iter_errors(broken))

    broken = deepcopy(receipt)
    broken["validation"]["scopeMatched"] = False
    assert list(validator.iter_errors(broken))


def test_v050_policy_revocation_is_exact_policy_bound(project_root):
    schema = _load(
        project_root, "schema/approval-policy-revocation.schema.v0.1.json"
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    policy = _example(project_root)
    revocation = {
        "formatVersion": "panorama-approval-policy-revocation.v0.1",
        "revocationId": "PRV-0123456789ABCDEF0123456789ABCDEF",
        "projectId": "REFERENCE-PROJECT",
        "policyId": policy["policyId"],
        "policyHash": policy["approvalBinding"]["policyHash"],
        "revokedAt": "2026-08-20T08:02:00Z",
        "revokedBy": "reference-user",
        "revocationMethod": "explicit_policy_reference",
        "reasonClass": "user_request",
        "summary": "Stop future delegated receipt imports.",
        "privacy": {
            "containsProjectContent": False,
            "containsSecret": False,
            "containsSensitivePersonalData": False,
        },
        "integrity": {
            "hashAlgorithm": "sha256",
            "revocationHash": "a" * 64,
            "hashScope": "revocation_without_integrity.revocationHash",
        },
        "extensions": {},
    }
    assert list(validator.iter_errors(revocation)) == []

    broken = deepcopy(revocation)
    broken["revocationMethod"] = "agent_decision"
    assert list(validator.iter_errors(broken))
