"""Compatibility flags only. No duplicated protocol implementation."""
import argparse
import json
import sys
from .service import dispatch


def main(operation):
    parser = argparse.ArgumentParser(description=f"Compatibility wrapper for structure.py {operation}")
    parser.add_argument("root", nargs="?")
    parser.add_argument("--project-root")
    parser.add_argument("--input")
    parser.add_argument("--to")
    parser.add_argument("--module")
    parser.add_argument("--session")
    parser.add_argument("--task", default="")
    parser.add_argument("--token")
    parser.add_argument("--release")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--reason", default="")
    parser.add_argument("--level", default="L1", choices=["L0", "L1", "L2", "L3"])
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--gate")
    parser.add_argument("--approve", help="Creates pending request; use structure.py gate for a human decision")
    args = parser.parse_args()
    try:
        payload = json.loads(args.input) if args.input else {}
        if not args.input:
            if operation == "init":
                payload = {"mode": "existing", "force": args.force, "dry_run": args.dry_run}
                if args.profile:
                    payload["profile"] = args.profile
            elif operation == "phase":
                payload = {"action": "advance" if args.to else "get", "to": args.to, "bootstrap": args.bootstrap, "reason": args.reason}
            elif operation == "lock":
                payload = {"action": "release" if args.release else "cleanup" if args.cleanup else "status" if args.status else "acquire",
                           "module": args.release or args.module, "session": args.session, "task": args.task, "token": args.token}
            elif operation == "context":
                payload = {"module": args.module, "level": args.level}
            elif operation == "gate":
                payload = {"action": "request", "gate": args.gate or args.approve, "rationale": args.reason}
        result = dispatch(operation, payload, args.project_root or args.root)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 1 if result.get("valid") is False or result.get("decision") == "deny" else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
