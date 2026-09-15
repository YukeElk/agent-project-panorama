"""Local JSON transport for the unmodified, pinned Structure core."""
import json
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    print(json.dumps({"ok": False, "error": "Structure core requires Python 3.11 or newer"}))
    raise SystemExit(1)
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vendor/structure-core/scripts"))
from structure_core.service import dispatch
from structure_core.identity import resolve_project
from structure_core import VERSION

for stream in (sys.stdin, sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8")

try:
    request = json.loads(sys.stdin.read(1048577))
    operation = request["operation"]
    if operation == "identity":
        result = resolve_project(request["projectRoot"])
    elif operation == "version":
        result = {"version": VERSION, "python": sys.version.split()[0]}
    elif operation in ("init", "work", "context", "policy", "export"):
        result = dispatch(operation, request.get("args", {}), request["projectRoot"], channel="cli")
    else:
        raise ValueError("Unsupported local adapter operation")
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
except Exception as error:
    print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
    raise SystemExit(1)
