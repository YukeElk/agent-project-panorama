"""Continuous Observation policy definition and hash verification."""

from __future__ import annotations

import copy
from typing import Any

from panorama_io import compute_canonical_hash


DEFAULT_PROTECTED_PATHS = [
    "/schemaVersion",
    "/intent",
    "/requirements",
    "/decisions",
    "/reviews",
    "/guidance",
    "/observationPolicy",
    "/architecture/targetVersionId",
    "/meta/templateVersion",
    "/meta/createdAt",
]
DEFAULT_FACT_CLASSES = [
    "CURRENT_IMPLEMENTATION",
    "CURRENT_RUNTIME",
    "VERIFICATION",
    "RESOURCE",
    "REFERENCE",
    "SOURCE_FRESHNESS",
    "STANDARD_ASSESSMENT",
    "RISK_CANDIDATE",
]


def policy_hash(policy: dict[str, Any]) -> str:
    value = copy.deepcopy(policy)
    value["policyHash"] = ""
    return compute_canonical_hash(value)


def default_policy(created_at: str) -> dict[str, Any]:
    policy = {
        "id": "POLICY-CONTINUOUS-OBSERVATION",
        "version": "continuous-observation-policy.v0.2",
        "mode": "continuous_observation",
        "enabled": True,
        "policyHash": "",
        "allowedFactClasses": list(DEFAULT_FACT_CLASSES),
        "protectedPaths": list(DEFAULT_PROTECTED_PATHS),
        "automaticTestExecution": False,
        "stateRefMode": "local_only",
        "createdAt": created_at,
        "extensions": {},
    }
    policy["policyHash"] = policy_hash(policy)
    return policy


__all__ = ["DEFAULT_FACT_CLASSES", "DEFAULT_PROTECTED_PATHS", "default_policy", "policy_hash"]
