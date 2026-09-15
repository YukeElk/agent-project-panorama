"""JSON CLI transport; all business behavior is in service.dispatch."""
import argparse
import json
import sys
from .service import dispatch
from .storage import StructureError


def main(argv=None, operation=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Persistent project state, schema 2")
    if operation is None:
        parser.add_argument("operation", choices=["init", "phase", "lock", "gate", "work", "context", "verify", "views", "export", "policy", "migrate", "vendor"])
    parser.add_argument("--project-root")
    parser.add_argument("--input", default="{}", help="JSON object, or - to read stdin")
    parser.add_argument("--human-confirmed", action="store_true", help="Local operator attests a gate decision; not identity authentication")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() if args.input == "-" else args.input)
        result = dispatch(operation or args.operation, payload, args.project_root,
                          channel="human-cli" if args.human_confirmed else "cli")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("valid") is False or result.get("decision") == "deny" else 0
    except (StructureError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
