from __future__ import annotations

from runtime_preflight import run_preflight


def test_runtime_preflight_is_offline_and_core_ready(project_root):
    report = run_preflight(project_root)

    assert report["formatVersion"] == "panorama-runtime-preflight.v0.1"
    assert report["readyForCore"] is True
    assert report["checks"]["python"]["status"] == "available"
    assert report["checks"]["jsonschema"]["status"] == "available"
    assert report["checks"]["schemas"]["status"] == "compatible"
    assert report["checks"]["schemas"]["count"] >= 11
    assert report["checks"]["textEncoding"]["status"] in {
        "compatible",
        "legacy_default",
    }
    assert report["networkAccessed"] is False
    assert report["dependenciesInstalled"] is False
    assert report["secretValuesRead"] is False
