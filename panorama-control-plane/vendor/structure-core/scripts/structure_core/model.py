"""Single manifest loader, file classification and configurable workflow policy."""
import fnmatch
from pathlib import Path, PurePosixPath
import tomllib
from .storage import StructureError, contained, read_json

PHASES = ["PHASE_0_EMPTY", "PHASE_1_REQUIREMENTS", "PHASE_2_ARCHITECTURE",
          "PHASE_3_MODULE_DESIGN", "PHASE_4_IMPLEMENTATION", "PHASE_5_EVOLUTION"]
STATES = ["proposed", "approved", "implementing", "verifying", "done", "cancelled"]
KINDS = ["feature", "bugfix", "spike", "refactor", "migration", "incident"]
IMPACTS = ["mechanical", "internal", "behavior", "interface", "boundary", "architecture"]
EXCLUDED = {".git", ".structure", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target"}
CODE_GLOBS = [f"**/*.{ext}" for ext in
              ("py", "js", "ts", "jsx", "tsx", "mjs", "cjs", "go", "rs", "java", "kt", "c", "h", "cpp", "cs", "rb", "php", "swift", "css", "html", "vue", "svelte", "sh", "ps1", "sql")]


def profile(name):
    if name not in ("solo-light", "team-standard", "multi-agent", "regulated"):
        raise StructureError(f"Unknown profile: {name}")
    strict = name in ("multi-agent", "regulated")
    return {
        "profile": name,
        "coordination": {"mode": "enforced" if strict else "advisory" if name == "team-standard" else "none",
                         "lease_seconds": 1800},
        "runtime_enforcement": strict,
        "hook": "enforced" if name != "solo-light" else "advisory",
        "code_globs": CODE_GLOBS,
        "workflow": {"states": STATES, "transitions": {
            "proposed": ["approved", "cancelled"], "approved": ["implementing", "cancelled"],
            "implementing": ["verifying", "cancelled"], "verifying": ["implementing", "done"],
            "done": [], "cancelled": []}},
        "gate_impacts": ["architecture", "boundary"] if name != "solo-light" else [],
        "allow_mechanical_skip": not strict,
        "update_impacts": ["behavior", "interface", "boundary", "architecture"],
        "phase_transitions": {**{PHASES[i]: {PHASES[i+1]: ([] if i in (0, 4) else [f"G{i}"])}
                                 for i in range(5)},
                              PHASES[5]: {PHASES[2]: ["evolution-review"], PHASES[3]: ["evolution-review"]}},
    }


def matches(value, pattern):
    """Project-relative globs; directory scopes end in / or use /**."""
    value, pattern = value.replace("\\", "/"), pattern.replace("\\", "/")
    if pattern in (".", "./", "**", "**/*"):
        return True
    if pattern.endswith("/"):
        return value == pattern.rstrip("/") or value.startswith(pattern)
    if pattern.endswith("/**") and value == pattern[:-3]:
        return True
    return fnmatch.fnmatchcase(value, pattern) or (pattern.startswith("**/") and fnmatch.fnmatchcase(value, pattern[3:]))


def module_template(name, path, responsibility="Discovered module; review its boundary"):
    return {"id": name, "path": path, "responsibility": responsibility,
            "owns": ["**" if path == "." else path.rstrip("/") + "/**"],
            "may_touch": [], "forbidden": [], "depends_on": [], "tests_for": [],
            "health": "unverified"}


def validate_manifest(root, manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise StructureError("manifest requires schema_version 2; run migrate for v1.1")
    modules = manifest.get("modules")
    if not isinstance(modules, dict):
        raise StructureError("manifest.modules must be an object keyed by stable module ID")
    for name, module in modules.items():
        if not name.strip() or not isinstance(module, dict) or module.get("id") != name:
            raise StructureError(f"Invalid module ID: {name}")
        if not isinstance(module.get("responsibility"), str) or module.get("health") not in ("unverified", "stable", "active", "unstable", "retired"):
            raise StructureError(f"Invalid responsibility or health: {name}")
        contained(root, module.get("path", "."))
        for field in ("owns", "may_touch", "forbidden", "depends_on", "tests_for"):
            if not isinstance(module.get(field), list) or not all(isinstance(s, str) and s for s in module[field]):
                raise StructureError(f"{name}.{field} must be a string array")
        for pattern in module["owns"] + module["may_touch"] + module["forbidden"]:
            if PurePosixPath(pattern.replace("\\", "/")).is_absolute() or ".." in PurePosixPath(pattern.replace("\\", "/")).parts or ":" in pattern:
                raise StructureError(f"Unsafe scope in {name}: {pattern}")
        for target in module["depends_on"] + module["tests_for"]:
            if target not in modules:
                raise StructureError(f"Unknown module reference: {name} -> {target}")
    policy = manifest.get("policy", {})
    if not isinstance(policy, dict) or not isinstance(policy.get("profile"), str) or type(policy.get("allow_mechanical_skip")) is not bool:
        raise StructureError("Policy requires profile and boolean allow_mechanical_skip")
    if policy.get("coordination", {}).get("mode") not in ("none", "advisory", "enforced"):
        raise StructureError("Invalid coordination mode")
    ttl = policy["coordination"].get("lease_seconds")
    if type(ttl) is not int or not 1 <= ttl <= 86400:
        raise StructureError("lease_seconds must be an integer in 1..86400")
    if policy.get("hook") not in ("advisory", "enforced") or type(policy.get("runtime_enforcement")) is not bool:
        raise StructureError("Invalid hook or runtime enforcement policy")
    for field in ("code_globs", "gate_impacts", "update_impacts"):
        if not isinstance(policy.get(field), list) or not all(isinstance(x, str) for x in policy[field]):
            raise StructureError(f"Invalid policy.{field}")
    if not set(policy["gate_impacts"] + policy["update_impacts"]).issubset(IMPACTS):
        raise StructureError("Unknown impact in policy")
    workflow = policy.get("workflow", {})
    states = workflow.get("states", [])
    if not isinstance(states, list) or not {"proposed", "implementing", "verifying", "done", "cancelled"}.issubset(states):
        raise StructureError("Workflow must include the core work item states")
    for source, targets in workflow.get("transitions", {}).items():
        if source not in states or not isinstance(targets, list) or any(x not in states for x in targets):
            raise StructureError("Invalid workflow transition table")
    for source, targets in policy.get("phase_transitions", {}).items():
        if source not in PHASES or not isinstance(targets, dict):
            raise StructureError("Invalid legacy phase transition table")
        for target, gates in targets.items():
            if target not in PHASES or not isinstance(gates, list) or not all(isinstance(x, str) for x in gates):
                raise StructureError("Invalid legacy phase gate table")
    lines = manifest.get("coupling", {}).get("red_lines", [])
    if not isinstance(lines, list):
        raise StructureError("coupling.red_lines must be an array")
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get("change"), str) or not isinstance(line.get("must_also_change"), list):
            raise StructureError("Red line requires change glob and must_also_change glob array")
    return manifest


def load_manifest(root):
    return validate_manifest(root, read_json(contained(root, ".structure/manifest.json")))


def scan_files(root):
    """Do not follow symlinks or scan generated/vendor trees."""
    import os
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith(".structure-")
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink():
                files.append(path.relative_to(root).as_posix())
    return files


def discover(root):
    files = scan_files(root)
    modules = {}
    warnings = []
    # Node workspaces and Cargo workspaces are explicit discovery hints.
    patterns = []
    package = root / "package.json"
    if package.exists():
        data = read_json(package)
        workspace = data.get("workspaces", [])
        patterns += workspace.get("packages", []) if isinstance(workspace, dict) else workspace
    cargo = root / "Cargo.toml"
    if cargo.exists():
        with cargo.open("rb") as stream:
            patterns += tomllib.load(stream).get("workspace", {}).get("members", [])
    for pattern in patterns:
        contained(root, pattern)
        for path in sorted(root.glob(pattern)):
            if not path.is_dir() or path.is_symlink():
                continue
            contained(root, str(path))
            rel = path.relative_to(root).as_posix()
            name = read_json(path / "package.json").get("name", rel) if (path / "package.json").exists() else rel
            if name in modules:
                raise StructureError(f"Duplicate discovered module ID: {name}")
            modules[name] = module_template(name, rel)
    if not modules:
        tops = sorted({f.split("/")[0] for f in files if "/" in f and not f.startswith(".")
                       and any(matches(f, p) for p in CODE_GLOBS)})
        for name in tops:
            modules[name] = module_template(name, name)
        if len(modules) == 1 and "src" in modules:
            warnings.append("src is a discovery hint only; split into logical modules after review")
    # Exact remaining files avoid an overlapping catch-all root module.
    remaining = [f for f in files if not any(any(matches(f, p) for p in m["owns"]) for m in modules.values())]
    if remaining or not modules:
        name = "root" if "root" not in modules else "project-root"
        modules[name] = module_template(name, ".", "Project-level files")
        modules[name]["owns"] = remaining or ["**"]
    for name, module in modules.items():
        pkg = root / module["path"] / "package.json"
        if pkg.exists():
            data = read_json(pkg)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            module["depends_on"] = sorted(d for d in deps if d in modules and d != name)
    return {"modules": modules, "files": files, "warnings": warnings,
            "discovery": {"methods": ["node-workspaces", "cargo-workspaces", "top-level-fallback"],
                          "confidence": "draft", "requires_review": True}}
