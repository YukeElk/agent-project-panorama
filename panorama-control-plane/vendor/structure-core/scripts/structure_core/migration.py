"""Explicit preserving v1.1 import; Markdown approvals remain untrusted."""
import shutil
import uuid
from . import VERSION
from .model import PHASES, module_template, profile, validate_manifest
from .service import PACKAGE
from .storage import StructureError, contained, key, now, read_json, write_json, atomic_text, rename_directory


def migrate(project, args):
    sd, root = project.sd, project.root
    if (sd / "manifest.json").exists():
        project.manifest()
        return {"migrated": False, "reason": "Already schema 2"}
    try:
        import yaml
    except ImportError as exc:
        raise StructureError("v1 YAML import requires optional PyYAML: python -m pip install PyYAML") from exc
    try:
        legacy = yaml.safe_load(contained(root, ".structure/manifest.yaml").read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise StructureError(f"Cannot import legacy YAML: {exc}") from exc
    if not isinstance(legacy, dict) or not isinstance(legacy.get("modules"), dict):
        raise StructureError("Legacy manifest requires a modules mapping")
    modules, notes = {}, {}
    for name, info in legacy["modules"].items():
        if not isinstance(name, str) or not isinstance(info, dict):
            raise StructureError("Invalid legacy module")
        path = info.get("path", ".")
        contained(root, path)
        module = module_template(name, path, info.get("responsibility", "Imported module"))
        scope = info.get("write_scope", [path])
        scope = scope if isinstance(scope, list) else [scope]
        module["owns"] = ["**" if p == "." else p.rstrip("/") + "/**" if (root / p).is_dir() or p.endswith("/") else p for p in scope]
        module["depends_on"] = info.get("depends_on", []) or []
        modules[name] = module
        status = contained(root, path.rstrip("/") + "/.structure/STATUS.md")
        notes[name] = {"schema_version": 2, "module": name, "truths": [], "dont_assume": [],
                       "notes": "Imported prose is unverified; extract facts and evidence after review",
                       "legacy_definition": info,
                       "legacy_status": status.read_text(encoding="utf-8-sig") if status.exists() else ""}
    red_lines = []
    for line in legacy.get("coupling", {}).get("red_lines", []) or []:
        line = dict(line)
        other = line.get("must_also_change", [])
        line["must_also_change"] = other if isinstance(other, list) else [other]
        red_lines.append(line)
    manifest = {"schema_version": 2, "modules": modules, "coupling": {"red_lines": red_lines},
                "policy": profile(args.get("profile", "solo-light"))}
    validate_manifest(root, manifest)
    old_state = read_json(sd / "state.json")
    phase = old_state.get("phase", PHASES[0])
    if phase not in PHASES:
        raise StructureError("Unknown legacy phase; repair before migration")
    plan = {"from": "1.1", "to": 2, "modules": list(modules), "profile": manifest["policy"]["profile"],
            "gate_import": "Historical Markdown retained only; no old approval authorizes new transitions",
            "knowledge_import": "Legacy prose preserved, not asserted as verified facts"}
    if not args.get("apply"):
        return {"migrated": False, "plan": plan}
    stage = root / f".structure-stage-{uuid.uuid4().hex}"
    backup = root / f".structure-backup-{uuid.uuid4().hex}"
    if any(p.is_symlink() for p in sd.rglob("*")):
        raise StructureError("Migration does not copy symlinked protocol state")
    try:
        shutil.copytree(sd, stage)
        (stage / "legacy").mkdir(exist_ok=True)
        for name in ("manifest.yaml", "scripts", "AGENT.md", "AGENTS.md"):
            if (stage / name).exists():
                (stage / name).rename(stage / "legacy" / name)
        for name in ("AGENTS.md", "STRUCTURE.md", "INDEX.md"):
            shutil.copyfile(PACKAGE / "templates" / name, stage / name)
        write_json(stage / "manifest.json", manifest)
        write_json(stage / "identity.json", {"schema_version": 1, "kind": "project",
                   "project_id": str(uuid.uuid4()), "name": old_state.get("project", root.name)})
        write_json(stage / "state.json", {"schema_version": 2, "project": old_state.get("project", root.name),
                   "package_version": VERSION, "phase": phase, "lifecycle_status": "ready",
                   "operating_mode": "active" if phase in PHASES[4:] else "bootstrap",
                   "updated_at": now(), "discovery_status": "draft", "legacy_state": old_state})
        for name, note in notes.items():
            write_json(stage / "modules" / f"{key(name)}.json", note)
        write_json(stage / "migration-report.json", plan)
        atomic_text(stage / ".gitignore", "runtime/\n*.tmp\n")
        rename_directory(sd, backup)
        try:
            rename_directory(stage, sd)
        except BaseException:
            rename_directory(backup, sd)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    project.event("migrated", source_version="1.1", target_version=2)
    project.views({})
    return {"migrated": True, "plan": plan, "backup": backup.name}
