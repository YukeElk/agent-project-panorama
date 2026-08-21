"""CLI for the V0.5.1 governed Event Head Recovery Adapter."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import approval_policy
import event_store
from event_head_policy_adapter import (
    EventHeadPolicyAdapterError,
    execute,
    preview,
    resume,
    validate_transactions,
)
from panorama_cli import ChineseArgumentParser


def _emit(value: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    status = value.get("status") or ("valid" if value.get("valid") else "invalid")
    code = value.get("code", "")
    transaction_id = value.get("transactionId")
    if transaction_id is None and value.get("formatVersion") == "panorama-event-head-recovery-transaction.v0.1":
        transaction_id = value.get("transactionId")
    print(f"状态: {status}")
    if code:
        print(f"代码: {code}")
    if transaction_id:
        print(f"事务: {transaction_id}")
    if "transactionCount" in value:
        print(f"事务数: {value['transactionCount']}")
        print(f"需要恢复: {'是' if value.get('recoveryRequired') else '否'}")
    result = value.get("result")
    if isinstance(result, dict) and result.get("summary"):
        print(f"摘要: {result['summary']}")


def build_parser() -> ChineseArgumentParser:
    parser = ChineseArgumentParser(
        description="按已批准 Policy 预览、执行、恢复或验证 Event Head Recovery。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "execute", "resume"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--project-root", type=Path, required=True, help="项目根目录")
        sub.add_argument("--panorama", type=Path, required=True, help="正式 Panorama JSON/HTML")
        sub.add_argument("--policy-id", required=True, help="已激活 Policy ID")
        sub.add_argument("--json", action="store_true", help="输出机器可读 JSON")
        if name == "resume":
            sub.add_argument("--transaction-id", required=True, help="EHR Transaction ID")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--project-root", type=Path, required=True, help="项目根目录")
    validate.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preview":
            result = preview(args.project_root, args.panorama, args.policy_id)
            _emit(result, as_json=args.json)
            return 0 if result["status"] != "blocked" else 1
        if args.command == "execute":
            result = execute(args.project_root, args.panorama, args.policy_id)
            _emit(result, as_json=args.json)
            return 0
        if args.command == "resume":
            result = resume(
                args.project_root,
                args.panorama,
                args.policy_id,
                args.transaction_id,
            )
            _emit(result, as_json=args.json)
            return 0 if result.get("status") not in {"failed", "conflict"} else 1
        result = validate_transactions(args.project_root)
        _emit(result, as_json=args.json)
        return 0 if result["valid"] else 1
    except EventHeadPolicyAdapterError as exc:
        payload = {"status": "error", "code": exc.code, "message": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"错误 {exc.code}: {exc}", file=sys.stderr)
        return 1
    except (approval_policy.ApprovalPolicyError, event_store.EventStoreError) as exc:
        payload = {"status": "error", "code": "CORE_VALIDATION_FAILED", "message": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"错误 CORE_VALIDATION_FAILED: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        payload = {"status": "runtime_error", "code": "RUNTIME_FAILURE", "message": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"运行时错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
