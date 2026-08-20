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


def test_runtime_preflight_supports_python_310_locale_api(project_root, monkeypatch):
    import runtime_preflight

    monkeypatch.delattr(runtime_preflight.locale, "getencoding", raising=False)
    monkeypatch.setattr(
        runtime_preflight.locale,
        "getpreferredencoding",
        lambda do_setlocale=False: "UTF-8",
    )

    report = runtime_preflight.run_preflight(project_root)

    assert report["readyForCore"] is True
    assert report["checks"]["textEncoding"] == {
        "status": "compatible",
        "preferred": "UTF-8",
        "pythonUtf8Mode": bool(runtime_preflight.sys.flags.utf8_mode),
    }
