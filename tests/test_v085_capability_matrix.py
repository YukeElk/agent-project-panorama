from pathlib import Path

from validate_v085_capability_matrix import validate_capability_matrix


def test_v085_fixed_capability_matrix_passes(project_root: Path):
    result = validate_capability_matrix(
        project_root / "examples" / "v0.85-capability-expectations.json"
    )
    assert result["status"] == "passed"
    assert result["metrics"] == {
        "expectedFactCount": 42,
        "matchedFactCount": 42,
        "supportedFactRecall": 1.0,
        "evidenceCoverage": 1.0,
        "forbiddenClaimAvoidance": 1.0,
        "deterministic": True,
    }
    assert result["missingFacts"] == []
    assert result["forbiddenClaims"] == []
