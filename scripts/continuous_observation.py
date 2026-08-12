"""Continuously reconcile observed project facts into a V0.2 Panorama."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apply_patch import apply_operations
from observation_policy import policy_hash
from observe_project_runtime import git_observation, source_snapshot
from panorama_cli import ChineseArgumentParser
from panorama_io import (
    PanoramaIOError,
    atomic_write,
    compute_canonical_hash,
    compute_data_hash,
    compute_presentation_hash,
    create_backup,
    extract_data,
    replace_data,
)
from standard_pack import StandardPackError, assess_standard_pack, load_document
from validate_panorama import validate_data


class ObservationError(RuntimeError):
    """An observation transaction is unsafe, stale, or invalid."""


def _read_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


@contextmanager
def _lock(source: Path):
    lock = source.with_name(f".{source.name}.panorama.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ObservationError(f"Continuous Observation 正在运行：{lock.name}") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _pointer_parts(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ObservationError(f"Observation Operation 必须使用 JSON Pointer：{pointer!r}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _protected(pointer: str, policy: dict[str, Any]) -> bool:
    for protected in policy.get("protectedPaths", []):
        if pointer == protected or pointer.startswith(protected.rstrip("/") + "/"):
            return True
    parts = _pointer_parts(pointer)
    if parts[:2] == ["architecture", "modules"] and len(parts) >= 4:
        return parts[3] in {
            "targetDesign", "decisionIds", "reviewIds", "requirementIds",
            "acceptanceCriteriaIds", "gateIds", "workItemIds",
        }
    if parts[:1] == ["acceptanceCriteria"] and len(parts) >= 3:
        return parts[2] not in {"verificationStatus", "evidenceReferenceIds", "extensions"}
    if parts[:1] == ["resources"] and len(parts) >= 3 and parts[2] == "access":
        return True
    if parts and parts[0] in {"stages", "workItems", "releases"}:
        return True
    return False


def _validate_new_entity(operation: dict[str, Any]) -> None:
    parts = _pointer_parts(operation["path"])
    value = operation.get("value")
    if operation.get("op") != "add" or not isinstance(value, dict):
        return
    if parts[:2] == ["architecture", "modules"] and len(parts) == 3:
        if value.get("architectureScope") != "current" or value.get("targetDesign") is not None:
            raise ObservationError("自动新增 Module 必须是 current-only 且 targetDesign=null。")
        for field in (
            "decisionIds", "reviewIds", "requirementIds", "acceptanceCriteriaIds",
            "gateIds", "workItemIds",
        ):
            if value.get(field):
                raise ObservationError(f"自动新增 Module 不得声明治理关系：{field}。")
    if parts[:1] == ["risks"] and len(parts) == 2:
        if value.get("source") not in {"ai", "rule"} or value.get("status") == "accepted":
            raise ObservationError("自动新增 Risk 只能是未接受的 Risk Candidate。")


def validate_fact_operations(
    operations: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") not in {"add", "replace", "remove"}:
            raise ObservationError("Observation Operation 只支持 add/replace/remove。")
        pointer = operation.get("path")
        if not isinstance(pointer, str) or _protected(pointer, policy):
            raise ObservationError(f"自动观察不得修改受保护语义：{pointer!r}")
        _validate_new_entity(operation)


def _git_changed_paths(root: Path, previous: str | None, current: str | None) -> list[str]:
    if not current:
        return []
    argv = ["git", "diff", "--name-only", "-z", previous, current] if previous else [
        "git", "ls-tree", "-r", "--name-only", "-z", current
    ]
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(path for path in result.stdout.split("\0") if path) if result.returncode == 0 else []


def _path_at_or_below(path: str, root: str) -> bool:
    left = Path(path).as_posix().strip("/")
    right = Path(root).as_posix().strip("/")
    return bool(right) and (left == right or left.startswith(right + "/"))


def _module_observation_operations(
    data: dict[str, Any], root: Path, changed_paths: list[str], head: str | None, observed_at: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    operations: list[dict[str, Any]] = []
    mapped: set[str] = set()
    observed_module_ids: list[str] = []
    modules = data.get("architecture", {}).get("modules", [])
    for index, module in enumerate(modules):
        if not isinstance(module, dict) or module.get("architectureScope") not in {"current", "both"}:
            continue
        observed_module_ids.append(module["id"])
        code_path = str(module.get("codePath", "")).strip()
        if not code_path:
            continue
        if Path(code_path).is_absolute() or ".." in Path(code_path).parts:
            path_status = "external_private_data"
            changed = []
        else:
            candidate = root / code_path
            path_status = "present" if candidate.exists() else "not_detected"
            changed = [path for path in changed_paths if _path_at_or_below(path, code_path)]
            mapped.update(changed)
        observation = {
            "authority": "observed",
            "sourceCommit": head,
            "observedAt": observed_at,
            "pathStatus": path_status,
            "changedPaths": changed[:100],
            "notDetectedIsNotAbsent": True,
        }
        current = module.get("extensions", {}).get("continuousObservation")
        path = f"/architecture/modules/{index}/extensions/continuousObservation"
        operations.append({
            "op": "replace" if current is not None else "add",
            "path": path,
            "value": observation,
            "factClass": "CURRENT_IMPLEMENTATION",
            "authority": "observed",
            "confidence": "high",
            "evidence": [code_path, *(changed[:20])],
        })
    unmapped = [path for path in changed_paths if path not in mapped]
    return operations, observed_module_ids, unmapped


def _load_fact_package(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("operations"), list):
        raise ObservationError("Fact Package 必须包含 operations 数组。")
    return copy.deepcopy(value["operations"])


def _operation_for_apply(operation: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(operation[key]) for key in ("op", "path", "value") if key in operation}


def _risk_operations(
    data: dict[str, Any], assessments: list[dict[str, Any]], observed_at: str
) -> list[dict[str, Any]]:
    risks = data.get("risks", [])
    existing = {
        item.get("id"): index
        for index, item in enumerate(risks)
        if isinstance(item, dict)
    }
    desired: dict[str, tuple[dict[str, Any], list[str]]] = {}
    category_map = {
        "architecture": "architecture", "security": "security", "quality": "verification",
        "release": "deployment", "runtime": "deployment", "resource": "resource",
        "access": "security", "privacy": "security",
    }
    for assessment in assessments:
        for result in assessment["results"]:
            if result["status"] != "fail" or result["severity"] not in {"high", "critical"}:
                continue
            risk_id = "RISK-STD-" + compute_canonical_hash(
                [assessment["standardPackId"], result["ruleId"]]
            )[:16].upper()
            module_ids = [ref["id"] for ref in result["relatedEntities"] if ref.get("type") == "module"]
            risk = {
                "id": risk_id,
                "title": f"规范风险：{result['ruleId']}",
                "category": category_map.get(result.get("category"), "other"),
                "severity": result["severity"],
                "status": "monitoring",
                "description": result["message"],
                "impact": "项目当前证据未满足适用规范；影响需结合相关实体复核。",
                "mitigation": "补充证据或调整实现；本记录不自动选择方案。",
                "relatedModuleIds": module_ids,
                "relatedRequirementIds": [],
                "stageId": None,
                "relatedReleaseIds": [],
                "source": "rule",
                "detectedAt": observed_at,
                "resolvedAt": None,
                "referenceIds": [],
                "extensions": {
                    "candidate": True,
                    "authority": "inferred",
                    "standardAssessmentId": assessment["id"],
                    "standardRuleId": result["ruleId"],
                },
            }
            desired[risk_id] = (risk, [assessment["id"], result["ruleId"]])

    operations: list[dict[str, Any]] = []
    # Replace surviving candidates before removals so all indices still refer to
    # the same pre-operation risk list.  Manual risks never satisfy this marker.
    for risk_id, (risk, evidence) in desired.items():
        if risk_id not in existing:
            continue
        operations.append({
                "op": "replace",
                "path": f"/risks/{existing[risk_id]}",
                "value": risk,
                "factClass": "RISK_CANDIDATE",
                "authority": "inferred",
                "confidence": "medium",
                "evidence": evidence,
            })

    stale_indices = []
    for index, risk in enumerate(risks):
        if not isinstance(risk, dict) or risk.get("id") in desired:
            continue
        extensions = risk.get("extensions", {})
        if (
            extensions.get("candidate") is True
            and extensions.get("authority") == "inferred"
            and extensions.get("standardAssessmentId")
            and extensions.get("standardRuleId")
        ):
            stale_indices.append(index)
    for index in sorted(stale_indices, reverse=True):
        risk = risks[index]
        operations.append({
            "op": "remove",
            "path": f"/risks/{index}",
            "factClass": "RISK_CANDIDATE",
            "authority": "inferred",
            "confidence": "high",
            "evidence": [risk["id"], "standard finding no longer active"],
        })

    for risk_id, (risk, evidence) in desired.items():
        if risk_id in existing:
            continue
        operations.append({
            "op": "add",
            "path": "/risks/-",
            "value": risk,
            "factClass": "RISK_CANDIDATE",
            "authority": "inferred",
            "confidence": "medium",
            "evidence": evidence,
        })
    return operations


def _provenance_records(
    operations: list[dict[str, Any]], batch_id: str, head: str | None, observed_at: str
) -> list[dict[str, Any]]:
    records = []
    for index, operation in enumerate(operations):
        suffix = compute_canonical_hash([batch_id, index, operation["path"]])[:16].upper()
        records.append({
            "id": f"PROV-{suffix}",
            "path": operation["path"],
            "authority": operation.get("authority", "unknown"),
            "confidence": operation.get("confidence", "unknown"),
            "evidence": list(operation.get("evidence", [])),
            "sourceCommit": head,
            "observedAt": observed_at,
            "observationBatchId": batch_id,
            "extensions": {"factClass": operation.get("factClass")},
        })
    return records


def reconcile_data(
    current: dict[str, Any], project_root: Path, *, standard_paths: list[Path],
    fact_package: Path | None, observed_at: str
) -> tuple[dict[str, Any], bool]:
    if current.get("schemaVersion") != "0.2":
        raise ObservationError("Continuous Observation 只支持 Schema 0.2。")
    policy = current.get("observationPolicy", {})
    if not policy.get("enabled") or policy.get("policyHash") != policy_hash(policy):
        raise ObservationError("Observation Policy 未启用或 Hash 无效。")
    git = git_observation(project_root)
    snapshot = source_snapshot(project_root)
    snapshot_hash = compute_canonical_hash(snapshot)
    previous_binding = current.get("sourceBinding", {})
    head = git.get("head")
    packs = [load_document(path) for path in standard_paths]
    pack_hashes = [compute_canonical_hash(pack) for pack in packs]
    binding_extensions = previous_binding.get("extensions", {})
    if "standardPackHashes" in binding_extensions:
        previous_pack_hashes = binding_extensions["standardPackHashes"]
    else:
        # Compatibility fallback for early V0.2 documents that predate the
        # active-pack marker.  Assessments themselves remain immutable history.
        previous_pack_hashes = [
            item.get("standardPackHash")
            for item in current.get("standardAssessments", [])
            if isinstance(item, dict)
        ]
    supplied_operations = _load_fact_package(fact_package)
    if (
        head == previous_binding.get("gitHead")
        and snapshot_hash == previous_binding.get("sourceSnapshotHash")
        and pack_hashes == previous_pack_hashes
        and not supplied_operations
    ):
        return current, False

    changed_paths = _git_changed_paths(project_root, previous_binding.get("gitHead"), head)
    generated, module_ids, unmapped = _module_observation_operations(
        current, project_root, changed_paths, head, observed_at
    )
    assessments = [
        assess_standard_pack(
            pack, project_root, current, source_commit=head, assessed_at=observed_at
        )
        for pack in packs
    ]
    operations = generated + supplied_operations
    for operation in operations:
        fact_class = operation.get("factClass")
        if fact_class not in policy.get("allowedFactClasses", []):
            raise ObservationError(f"Policy 不允许 Fact Class：{fact_class!r}")
    validate_fact_operations(operations, policy)
    preview = apply_operations(current, [_operation_for_apply(op) for op in operations])
    risk_operations = _risk_operations(preview, assessments, observed_at)
    for operation in risk_operations:
        if operation["factClass"] not in policy.get("allowedFactClasses", []):
            raise ObservationError("Policy 不允许自动披露 Risk Candidate。")
    validate_fact_operations(risk_operations, policy)
    operations.extend(risk_operations)
    updated = apply_operations(current, [_operation_for_apply(op) for op in operations])

    batch_suffix = compute_canonical_hash(
        [current["meta"]["revision"], head, snapshot_hash, pack_hashes, operations]
    )[:16].upper()
    batch_id = f"OBS-{batch_suffix}"
    provenance = _provenance_records(operations, batch_id, head, observed_at)
    provenance_ids = [item["id"] for item in provenance]
    snapshot_id = f"ARCH-SNAP-{batch_suffix}"
    architecture_snapshot = {
        "id": snapshot_id,
        "sourceCommit": head,
        "sourceSnapshotHash": snapshot_hash,
        "observedAt": observed_at,
        "moduleIds": module_ids,
        "connectionIds": [
            item["id"] for item in updated.get("architecture", {}).get("connections", [])
            if item.get("architectureScope") in {"current", "both"}
        ],
        "unmappedPaths": unmapped[:500],
        "provenanceIds": provenance_ids,
        "extensions": {"changedPathCount": len(changed_paths)},
    }
    batch = {
        "id": batch_id,
        "revisionFrom": current["meta"]["revision"],
        "revisionTo": current["meta"]["revision"] + 1,
        "sourceCommitFrom": previous_binding.get("gitHead"),
        "sourceCommitTo": head,
        "policyHash": policy["policyHash"],
        "evidenceSnapshotHash": snapshot_hash,
        "standardAssessmentIds": [item["id"] for item in assessments],
        "operations": [_operation_for_apply(item) for item in operations],
        "provenanceIds": provenance_ids,
        "conflicts": [],
        "findings": [],
        "validationResult": {"valid": True, "errors": 0, "warnings": 0},
        "observedAt": observed_at,
        "status": "applied",
        "extensions": {"unmappedPathCount": len(unmapped)},
    }
    updated["factProvenance"] = current.get("factProvenance", []) + provenance
    updated["currentArchitectureSnapshots"] = current.get("currentArchitectureSnapshots", []) + [architecture_snapshot]
    updated["standardAssessments"] = current.get("standardAssessments", []) + assessments
    updated["observationBatches"] = current.get("observationBatches", []) + [batch]
    updated["sourceBinding"] = {
        "mode": snapshot.get("mode", "git"),
        "gitHead": head,
        "gitBranch": git.get("branch"),
        "sourceSnapshotHash": snapshot_hash,
        "observedAt": observed_at,
        "lastObservationBatchId": batch_id,
        "extensions": {
            "gitDirty": git.get("dirty"),
            "standardPackHashes": pack_hashes,
        },
    }
    updated["meta"]["revision"] = current["meta"]["revision"] + 1
    updated["meta"]["updatedAt"] = observed_at
    updated["meta"]["lastValidatedAt"] = observed_at
    updated["meta"]["sourceMode"] = "mixed"
    report = validate_data(updated, base_dir=project_root)
    batch["findings"] = [item.to_dict() for item in report.issues if item.level != "INFO"]
    batch["validationResult"] = {
        "valid": not report.errors,
        "errors": len(report.errors),
        "warnings": len(report.warnings),
    }
    final_report = validate_data(updated, base_dir=project_root)
    if final_report.errors:
        raise ObservationError("自动更新校验失败：\n" + "\n".join(i.render() for i in final_report.errors))
    return updated, True


def reconcile_html(
    panorama: Path, project_root: Path, *, standard_paths: list[Path],
    fact_package: Path | None = None, observed_at: str | None = None
) -> tuple[bool, Path | None, dict[str, Any]]:
    panorama = panorama.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    with _lock(panorama):
        original_text = _read_exact(panorama)
        original_presentation = compute_presentation_hash(original_text)
        current = extract_data(panorama)
        initial_head = git_observation(project_root).get("head")
        updated, changed = reconcile_data(
            current,
            project_root,
            standard_paths=standard_paths,
            fact_package=fact_package,
            observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
        )
        if not changed:
            return False, None, current
        backup = create_backup(panorama)
        descriptor, stage_name = tempfile.mkstemp(
            dir=panorama.parent, prefix=f".{panorama.name}.", suffix=".observation-staged"
        )
        os.close(descriptor)
        staged = Path(stage_name)
        staged.unlink(missing_ok=True)
        try:
            replace_data(panorama, updated, staged)
            staged_text = _read_exact(staged)
            if compute_presentation_hash(staged_text) != original_presentation:
                raise ObservationError("Continuous Observation 改变了 Presentation Layer。")
            if _read_exact(panorama) != original_text:
                raise ObservationError("Panorama 在观察事务中发生并发变化。")
            if git_observation(project_root).get("head") != initial_head:
                raise ObservationError("Git HEAD 在观察事务中发生变化；稍后补偿同步。")
            atomic_write(panorama, staged_text)
        finally:
            staged.unlink(missing_ok=True)
        return True, backup, updated


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="将项目事实持续同步到 V0.2 Panorama。")
    parser.add_argument("panorama", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--standard", type=Path, action="append", default=[])
    parser.add_argument("--facts", type=Path, help="Agent 生成的结构化 fact-only Operations")
    parser.add_argument("--observed-at", help="测试或可重现同步使用的 ISO 时间")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        changed, backup, updated = reconcile_html(
            args.panorama,
            args.project_root,
            standard_paths=args.standard,
            fact_package=args.facts,
            observed_at=args.observed_at,
        )
    except (OSError, ValueError, json.JSONDecodeError, PanoramaIOError, StandardPackError, ObservationError) as exc:
        print(f"持续观察错误：{exc}", file=sys.stderr)
        return 1
    if not changed:
        print("Panorama 已覆盖当前 HEAD 与规范版本，无需更新。")
        return 0
    print(f"持续观察已应用：Revision {updated['meta']['revision']}")
    print(f"Source HEAD：{updated['sourceBinding']['gitHead']}")
    print(f"Observation Batch：{updated['sourceBinding']['lastObservationBatchId']}")
    print(f"备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
