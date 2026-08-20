"""Offline runtime and compatibility preflight for Panorama V0.4.1/V0.5."""

from __future__ import annotations

import argparse
import importlib.util
import json
import locale
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from jsonschema import Draft202012Validator

from panorama_cli import ChineseArgumentParser


ROOT = Path(__file__).resolve().parents[1]


def _preferred_encoding() -> str:
    getencoding = getattr(locale, "getencoding", None)
    if callable(getencoding):
        return getencoding()
    return locale.getpreferredencoding(False)


def _command_version(command: str, flag: str = "--version") -> dict[str, Any]:
    executable = shutil.which(command)
    if executable is None:
        return {"status": "unavailable", "version": None}
    try:
        completed = subprocess.run(
            [executable, flag],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {"status": "detected_but_unreadable", "version": None}
    text = (completed.stdout or completed.stderr).strip().splitlines()
    return {
        "status": "available" if completed.returncode == 0 else "detected_but_unreadable",
        "version": text[0][:256] if text else None,
    }


def run_preflight(root: Path = ROOT) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    python_ready = sys.version_info >= (3, 10)
    checks["python"] = {
        "status": "available" if python_ready else "incompatible",
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "minimum": "3.10",
    }
    for module, label in (("jsonschema", "jsonschema"), ("yaml", "PyYAML")):
        available = importlib.util.find_spec(module) is not None
        checks[label] = {"status": "available" if available else "unavailable"}
    checks["git"] = _command_version("git")
    checks["node"] = _command_version("node")
    preferred_encoding = _preferred_encoding()
    checks["textEncoding"] = {
        "status": "compatible" if preferred_encoding.lower().replace("-", "") == "utf8" else "legacy_default",
        "preferred": preferred_encoding,
        "pythonUtf8Mode": bool(sys.flags.utf8_mode),
    }

    schema_errors: list[str] = []
    schema_files = sorted((root / "schema").glob("*.json"))
    for path in schema_files:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # validator exposes several schema/read exception types
            schema_errors.append(f"{path.name}: {exc}")
    checks["schemas"] = {
        "status": "compatible" if not schema_errors else "incompatible",
        "count": len(schema_files),
        "errors": schema_errors,
    }
    required_ready = (
        python_ready
        and checks["jsonschema"]["status"] == "available"
        and checks["schemas"]["status"] == "compatible"
    )
    limitations = []
    if checks["PyYAML"]["status"] != "available":
        limitations.append("Standard Pack YAML support is unavailable; JSON Standard Pack remains possible.")
    if checks["git"]["status"] != "available":
        limitations.append("Git-bound freshness and continuous observation are unavailable.")
    if checks["node"]["status"] != "available":
        limitations.append("Node-based renderer tests/export helpers are unavailable; Single HTML reading remains possible.")
    if checks["textEncoding"]["status"] != "compatible":
        limitations.append(
            "The process default text encoding is not UTF-8; run third-party Python helpers that omit an explicit encoding with PYTHONUTF8=1."
        )
    return {
        "formatVersion": "panorama-runtime-preflight.v0.1",
        "readyForCore": required_ready,
        "checks": checks,
        "limitations": limitations,
        "networkAccessed": False,
        "dependenciesInstalled": False,
        "secretValuesRead": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="离线检查 Panorama Runtime 与 Schema 兼容性。")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = run_preflight(args.root.resolve())
    except (OSError, ValueError) as exc:
        print(f"Runtime Preflight 错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Panorama Runtime Preflight")
        for name, check in report["checks"].items():
            print(f"- {name}: {check['status']}")
        for limitation in report["limitations"]:
            print(f"限制：{limitation}")
    return 0 if report["readyForCore"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
