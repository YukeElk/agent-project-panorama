"""Identify projects and module contexts without trusting directory names."""
import os
from pathlib import Path
import uuid
from .storage import StructureError, contained, key, read_json


def classify(directory):
    root = Path(directory).resolve()
    sd = root / ".structure"
    if not sd.exists():
        return None
    if sd.is_symlink() or not sd.is_dir():
        raise StructureError(f"Invalid .structure directory: {sd}")
    marker = sd / "identity.json"
    if marker.exists():
        value = read_json(marker)
        if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("kind") not in ("project", "module"):
            raise StructureError(f"Invalid structure identity: {marker}")
        try:
            uuid.UUID(value["project_id"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise StructureError(f"Invalid project_id: {marker}") from exc
        if value["kind"] == "project":
            if not (sd / "manifest.json").is_file() or not (sd / "state.json").is_file():
                raise StructureError(f"Project identity has incomplete state: {sd}")
        elif (sd / "manifest.json").exists() or (sd / "state.json").exists():
            raise StructureError(f"Module identity conflicts with project authority: {sd}")
        return value
    if (sd / "manifest.json").is_file() and (sd / "state.json").is_file():
        return {"kind": "project", "project_id": None, "legacy": True}
    if (sd / "manifest.yaml").is_file() and (sd / "state.json").is_file():
        return {"kind": "project", "project_id": None, "legacy": True, "requires_migration": True}
    if (sd / "STATUS.md").is_file() and not (sd / "state.json").exists():
        return {"kind": "module", "legacy": True}
    raise StructureError(f"Ambiguous .structure at {sd}; identify or repair it explicitly")


def resolve_project(start, expected_project_id=None):
    origin = Path(start).resolve()
    if not origin.is_dir():
        raise StructureError(f"Project context must be a directory: {origin}")
    module_context = None
    selected = None
    for directory in (origin, *origin.parents):
        identity = classify(directory)
        if not identity:
            continue
        if identity["kind"] == "project":
            selected = (directory, identity)
            break
        if module_context is None:
            module_context = (directory, identity)
        if not identity.get("legacy"):
            pointer = identity.get("project_root")
            if not isinstance(pointer, str) or Path(pointer).is_absolute():
                raise StructureError("Module project_root must be a relative path from its module directory")
            target = (directory / pointer).resolve()
            if target == directory or not directory.is_relative_to(target):
                raise StructureError("Module must refer to an ancestor project")
            owner = classify(target)
            if not owner or owner["kind"] != "project" or owner["project_id"] != identity["project_id"]:
                raise StructureError("Module project identity does not match its owner")
            # Do not jump across a nested independent project boundary.
            for ancestor in directory.parents:
                if ancestor == target:
                    break
                marker = classify(ancestor)
                if marker and marker["kind"] == "project":
                    raise StructureError("Module pointer crosses a nested project boundary")
            selected = (target, owner)
            break
    if selected is None:
        raise StructureError(f"No owning project found for {origin}")
    root, identity = selected
    if expected_project_id is not None and identity["project_id"] != expected_project_id:
        raise StructureError("Context belongs to a different project")
    if identity.get("requires_migration"):
        modules = {}
    else:
        manifest = read_json(root / ".structure/manifest.json")
        modules = manifest.get("modules", {})
        if not isinstance(modules, dict):
            raise StructureError("Project modules must be an object")
    candidates = []
    for name, definition in modules.items():
        path = contained(root, definition.get("path", "."))
        if origin.is_relative_to(path):
            candidates.append((len(path.parts), name))
    if module_context:
        directory, marker = module_context
        if marker.get("legacy"):
            exact = [name for name, definition in modules.items() if contained(root, definition.get("path", ".")) == directory]
            if len(exact) != 1:
                raise StructureError("Legacy module context is not uniquely mapped by project manifest")
            module_id = exact[0]
        else:
            module_id = marker.get("module_id")
            if module_id not in modules or contained(root, modules[module_id].get("path", ".")) != directory:
                raise StructureError("Module identity does not match manifest module path")
        candidates = [(len(directory.parts), module_id)]
    deepest = max((depth for depth, name in candidates), default=-1)
    module_ids = sorted(name for depth, name in candidates if depth == deepest)
    return {"kind": "module" if module_context or origin != root and module_ids else "project",
            "project_root": str(root), "project_id": identity["project_id"],
            "project_name": identity.get("name", root.name), "module_ids": module_ids,
            "context_path": origin.relative_to(root).as_posix(),
            "identity_source": "legacy-inferred" if identity.get("legacy") else "explicit",
            "requires_migration": identity.get("requires_migration", False),
            "checkout_id": key(os.path.normcase(str(root)))}
