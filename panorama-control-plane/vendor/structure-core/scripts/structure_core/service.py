"""Protocol operations. All transports call dispatch; none implement policy."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import uuid

from . import VERSION
from .model import (PHASES, KINDS, IMPACTS, discover, load_manifest, matches,
                    module_template, profile, scan_files, validate_manifest)
from .storage import (StructureError, atomic_text, contained, digest, find_root, key,
                      now, project_mutex, read_json, write_json, rename_directory)

PACKAGE = Path(__file__).resolve().parents[2]
ADAPTERS = {"agents": ("AGENTS.md", "AGENTS.md"), "claude": ("CLAUDE.md", "CLAUDE.md"),
            "cursor": ("cursor-structure.mdc", ".cursor/rules/structure.mdc"),
            "cline": ("clinerules-structure.md", ".clinerules/structure.md"),
            "copilot": ("copilot-instructions.md", ".github/copilot-instructions.md")}


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.sd = contained(self.root, ".structure")

    def manifest(self):
        return load_manifest(self.root)

    def state(self):
        state = read_json(contained(self.root, ".structure/state.json"))
        if state.get("schema_version") != 2 or state.get("phase") not in PHASES:
            raise StructureError("Invalid state or legacy schema; inspect and migrate")
        if state.get("operating_mode") not in ("bootstrap", "active", "maintenance", "archived"):
            raise StructureError("Invalid operating_mode")
        if state.get("lifecycle_status") not in ("ready", "uninitialized") or state.get("discovery_status") not in ("draft", "reviewed"):
            raise StructureError("Invalid lifecycle or discovery status")
        return state

    def module_path(self, module):
        if module not in self.manifest()["modules"]:
            raise StructureError(f"Unknown module: {module}")
        return contained(self.root, f".structure/modules/{key(module)}.json")

    def work_path(self, subject):
        return contained(self.root, f".structure/work-items/{key(subject)}.json")

    def revision(self, subject="project"):
        # Changes to policy, module boundary or work scope invalidate old approvals.
        manifest = self.manifest()
        if subject == "project":
            return digest({"manifest": manifest, "phase": self.state()["phase"],
                           "operating_mode": self.state()["operating_mode"]})
        work = read_json(self.work_path(subject))
        return digest({"manifest": manifest, "work": {k: v for k, v in work.items()
                       if k not in ("state", "updated_at", "validation")}})

    def events(self):
        directory = contained(self.root, ".structure/events")
        return sorted((read_json(p) for p in directory.glob("*.json")), key=lambda e: e["sequence"])

    def event(self, kind, **fields):
        events = self.events()
        event = {"schema_version": 2, "id": uuid.uuid4().hex,
                 "sequence": max((e["sequence"] for e in events), default=0) + 1,
                 "type": kind, "timestamp": now(), **fields}
        write_json(contained(self.root, f".structure/events/{event['id']}.json"), event, exclusive=True)
        return event

    def latest_decision(self, gate, subject, revision):
        decisions = [e for e in self.events() if e["type"] == "decision" and e["gate"] == gate
                     and e["subject"] == subject and e["subject_revision"] == revision]
        return decisions[-1] if decisions else None

    def require_gate(self, gate, subject="project"):
        revision = self.revision(subject)
        event = self.latest_decision(gate, subject, revision)
        if not event or event["decision"] != "APPROVED":
            raise StructureError(f"PENDING: {gate} requires APPROVED for {subject} revision {revision}")
        return event["id"]

    def initialize(self, args):
        mode = args.get("mode", "auto")
        if mode not in ("new", "existing", "auto"):
            raise StructureError("mode must be new, existing or auto")
        selected = args.get("profile", "solo-light")
        policy = profile(selected)
        discovery = discover(self.root)
        has_code = any(any(matches(file, pattern) for pattern in policy["code_globs"]) for file in discovery["files"])
        if mode == "new" and has_code:
            raise StructureError("Code already exists; choose existing or auto instead of new")
        mode = ("existing" if has_code else "new") if mode == "auto" else mode
        exists = self.sd.exists()
        if exists and not args.get("force"):
            raise StructureError(".structure exists; force performs a preserving rescan, not a reset")
        if exists:
            manifest, state = self.manifest(), self.state()
            if "profile" in args and manifest["policy"]["profile"] != selected:
                raise StructureError("Rescan cannot silently change profile; edit manifest policy explicitly")
            modules = dict(manifest["modules"])
            # Keep reviewed entries; add discoveries only if their path is new.
            known_paths = {m["path"] for m in modules.values()}
            for name, module in discovery["modules"].items():
                if name not in modules and module["path"] not in known_paths:
                    modules[name] = module
            manifest = {**manifest, "modules": modules}
        else:
            manifest = {"schema_version": 2, "modules": discovery["modules"],
                        "coupling": {"red_lines": []}, "policy": policy}
            state = {"schema_version": 2, "project": self.root.name, "package_version": VERSION,
                     "lifecycle_status": "ready", "operating_mode": "bootstrap",
                     "phase": PHASES[0], "updated_at": now(), "discovery_status": "draft"}
        validate_manifest(self.root, manifest)
        requested = args.get("adapters", [])
        if not isinstance(requested, list) or any(name not in ADAPTERS for name in requested):
            raise StructureError("Unknown adapter; choose agents, claude, cursor, cline, copilot")
        adapter_plan = []
        for name in requested:
            source, target = ADAPTERS[name]
            file = contained(self.root, target)
            adapter_plan.append({"adapter": name, "target": target,
                                 "action": "sidecar" if file.exists() else "create"})
        plan = {"mode": mode, "profile": manifest["policy"]["profile"],
                "modules": manifest["modules"], "discovery": discovery["discovery"],
                "warnings": discovery["warnings"], "adapters": adapter_plan,
                "preserves_existing": exists, "guarantees": self.guarantees(manifest)}
        if args.get("dry_run"):
            return {"applied": False, "plan": plan}
        staging = self.root / f".structure-stage-{uuid.uuid4().hex}"
        backup = self.root / f".structure-backup-{uuid.uuid4().hex}"
        try:
            if exists:
                # Reject links in protocol state, including links into the project.
                if any(p.is_symlink() for p in self.sd.rglob("*")):
                    raise StructureError("Rescan does not copy symlinked protocol state")
                shutil.copytree(self.sd, staging)
            else:
                staging.mkdir()
                for name in ("AGENTS.md", "STRUCTURE.md", "INDEX.md"):
                    shutil.copyfile(PACKAGE / "templates" / name, staging / name)
            write_json(staging / "manifest.json", manifest)
            write_json(staging / "state.json", state)
            if not (staging / "identity.json").exists():
                write_json(staging / "identity.json", {"schema_version": 1, "kind": "project",
                           "project_id": str(uuid.uuid4()), "name": state["project"]})
            write_json(staging / "discovery.json", discovery)
            atomic_text(staging / ".gitignore", "runtime/\n*.tmp\n")
            for name in manifest["modules"]:
                target = staging / "modules" / f"{key(name)}.json"
                if not target.exists():
                    write_json(target, {"schema_version": 2, "module": name,
                                       "truths": [], "dont_assume": [], "notes": "Discovery draft; verify against code"})
            if args.get("vendor"):
                self.copy_vendor(staging)
            if exists:
                rename_directory(self.sd, backup)
            try:
                rename_directory(staging, self.sd)
            except BaseException:
                if backup.exists():
                    rename_directory(backup, self.sd)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        # External adapter installation is reported separately from state transaction.
        # Existing files are never merged or overwritten.
        adapter_results = []
        for item in adapter_plan:
            source, target = ADAPTERS[item["adapter"]]
            dest = contained(self.root, target)
            text = (PACKAGE / "templates" / "adapters" / source).read_text(encoding="utf-8")
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("x", encoding="utf-8") as stream:
                    stream.write(text)
                adapter_results.append({**item, "result": "installed"})
            except FileExistsError:
                sidecar = contained(self.root, f".structure/adapters/{source}.suggested")
                atomic_text(sidecar, text)
                adapter_results.append({**item, "result": "conflict", "suggestion": sidecar.relative_to(self.root).as_posix()})
            except OSError as exc:
                adapter_results.append({**item, "result": "error", "error": str(exc)})
        self.event("initialized" if not exists else "rescanned", profile=manifest["policy"]["profile"])
        self.views({})
        return {"applied": True, "plan": plan, "adapter_results": adapter_results,
                "backup": backup.name if backup.exists() else None}

    @staticmethod
    def guarantees(manifest):
        policy = manifest["policy"]
        return {"coordination": policy["coordination"]["mode"],
                "lease_scope": "same canonical checkout on one machine",
                "pi_known_write_interception": policy["runtime_enforcement"],
                "shell_sandbox": False, "authenticated_identity": False,
                "immutable_events": "append-only through API; repository writers remain trusted",
                "hook": policy["hook"]}

    def copy_vendor(self, target):
        files = {}
        for directory in ("scripts", "templates"):
            for source in sorted((PACKAGE / directory).rglob("*")):
                if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
                    continue
                rel = source.relative_to(PACKAGE)
                dest = target / "vendor" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
                import hashlib
                files[rel.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
        write_json(target / "vendor" / "version.json", {"package_version": VERSION, "files": files})

    def gate(self, args, channel):
        action = args.get("action", "request")
        if action == "list":
            return {"events": [e for e in self.events() if e["type"] in ("gate_requested", "decision")]}
        if action == "request":
            subject = args.get("subject", "project")
            gate = args.get("gate")
            if not isinstance(gate, str) or not gate.strip():
                raise StructureError("gate is required")
            gate = "bootstrap-review" if gate in ("G0-bootstrap", "G0-panoramic-analysis", "G0-panoramic") else gate
            event = self.event("gate_requested", gate=gate, subject=subject,
                               subject_revision=self.revision(subject),
                               rationale=args.get("rationale", ""), decision="PENDING")
            return {"status": "PENDING", "request": event}
        if action != "decide":
            raise StructureError("gate action must be request, decide or list")
        if channel not in ("pi-ui", "human-cli"):
            raise StructureError("Decision requires Pi UI confirmation or explicit local human-cli attestation; MCP remains PENDING")
        request = next((e for e in self.events() if e["id"] == args.get("request_id") and e["type"] == "gate_requested"), None)
        if not request:
            raise StructureError("Unknown gate request")
        if self.revision(request["subject"]) != request["subject_revision"]:
            raise StructureError("Gate request is stale; request approval for the current revision")
        decision = args.get("decision")
        if decision not in ("APPROVED", "REJECTED", "CONDITIONAL"):
            raise StructureError("Invalid decision; CONDITIONAL does not authorize a transition")
        actor, evidence = args.get("actor"), args.get("evidence")
        if not isinstance(actor, str) or not actor.strip() or not isinstance(evidence, list) or not evidence:
            raise StructureError("Decision requires actor and evidence array")
        if not isinstance(args.get("conditions", []), list):
            raise StructureError("conditions must be an array")
        event = self.event("decision", gate=request["gate"], subject=request["subject"],
                           subject_revision=request["subject_revision"], request_id=request["id"],
                           decision=decision, actor=actor, channel=channel,
                           rationale=args.get("rationale", ""), conditions=args.get("conditions", []), evidence=evidence)
        return {"status": decision, "event": event}

    def phase(self, args):
        state = self.state()
        if args.get("action", "get") == "get":
            return {"state": state, "revision": self.revision(), "guarantees": self.guarantees(self.manifest())}
        if args.get("action") != "advance":
            raise StructureError("phase action must be get or advance")
        target, current = args.get("to"), state["phase"]
        if target not in PHASES:
            raise StructureError(f"Unknown phase: {target}")
        if target == current:
            return {"advanced": False, "state": state}
        required = self.manifest()["policy"]["phase_transitions"].get(current, {}).get(target)
        if args.get("bootstrap"):
            if state["operating_mode"] != "bootstrap" or current != PHASES[0] or target not in (PHASES[2], PHASES[3], PHASES[4]):
                raise StructureError("bootstrap is only an initial reviewed import into P2/P3/P4")
            required = ["bootstrap-review"]
        if required is None:
            raise StructureError(f"Transition not allowed: {current} -> {target}")
        evidence = [self.require_gate(gate) for gate in required]
        # State is a snapshot. Event is written first so failure remains auditable.
        event = self.event("phase_transition", source=current, target=target,
                           subject_revision=self.revision(), evidence=evidence, reason=args.get("reason", ""))
        state.update(phase=target, updated_at=now(), last_transition=event["id"])
        if target in PHASES[4:]:
            state.update(operating_mode="active", discovery_status="reviewed")
        write_json(self.sd / "state.json", state)
        return {"advanced": True, "state": state, "event": event}

    def lease(self, args):
        action = args.get("action", "status")
        folder = contained(self.root, ".structure/runtime/leases")
        folder.mkdir(parents=True, exist_ok=True)
        if action in ("status", "cleanup"):
            result = []
            for file in sorted(folder.glob("*.json")):
                lease = read_json(file)
                expired = datetime.fromisoformat(lease["expires_at"]) <= datetime.now(timezone.utc)
                if action == "cleanup" and expired:
                    file.unlink()
                result.append({k: v for k, v in {**lease, "expired": expired,
                              "removed": action == "cleanup" and expired}.items() if k != "token"})
            return {"leases": result}
        modules = args.get("modules") or ([args["module"]] if args.get("module") else [])
        if not isinstance(modules, list) or not modules or not all(isinstance(m, str) for m in modules):
            raise StructureError("module or modules is required")
        modules = sorted(set(modules))
        owner = args.get("session")
        if not isinstance(owner, str) or not owner.strip():
            raise StructureError("Stable session ID is required")
        tokens = args.get("tokens", {})
        if not isinstance(tokens, dict):
            raise StructureError("tokens must be an object keyed by module ID")
        if args.get("token") and len(modules) == 1:
            tokens = {modules[0]: args["token"]}
        ttl = args.get("ttl_seconds", self.manifest()["policy"]["coordination"]["lease_seconds"])
        if type(ttl) is not int or not 1 <= ttl <= 86400:
            raise StructureError("ttl_seconds must be an integer in 1..86400")
        current = datetime.now(timezone.utc)
        existing = {}
        # Validate every resource before mutating any: same-machine all-or-none group.
        for module in modules:
            self.module_path(module)
            file = folder / f"{key(module)}.json"
            lease = read_json(file) if file.exists() else None
            active = lease and datetime.fromisoformat(lease["expires_at"]) > current
            if action == "acquire":
                if active:
                    raise StructureError(f"Lease already held for {module}; renew with owner token")
            elif action in ("release", "renew"):
                if not lease or lease["session"] != owner or not secrets.compare_digest(lease["token"], str(tokens.get(module, ""))):
                    raise StructureError(f"Lease owner/token mismatch: {module}")
                if action == "renew" and not active:
                    raise StructureError(f"Lease expired: {module}; acquire a new lease")
            else:
                raise StructureError("Unknown lease action")
            existing[module] = lease
        updated = []
        try:
            for module in modules:
                file = folder / f"{key(module)}.json"
                if action == "release":
                    file.unlink()
                    updated.append({"module": module, "released": True})
                    continue
                lease = existing[module] if action == "renew" else {
                    "schema_version": 2, "module": module, "session": owner,
                    "token": secrets.token_hex(32), "acquired_at": now(), "task": args.get("task", "")}
                lease = {**lease, "heartbeat_at": now(), "expires_at": (current + timedelta(seconds=ttl)).isoformat()}
                if action == "acquire":
                    file.unlink(missing_ok=True)  # Only expired file, under process mutex.
                write_json(file, lease, exclusive=action == "acquire")
                updated.append(lease)
        except BaseException:
            for module, old in existing.items():
                file = folder / f"{key(module)}.json"
                if old is None:
                    file.unlink(missing_ok=True)
                else:
                    write_json(file, old)
            raise
        return {"action": action, "leases": updated, "coordination_mode": self.manifest()["policy"]["coordination"]["mode"]}

    def work(self, args):
        action = args.get("action", "list")
        if action == "list":
            return {"work_items": [read_json(p) for p in sorted((self.sd / "work-items").glob("*.json"))]}
        subject = args.get("id")
        if subject == "project":
            raise StructureError("Work item ID 'project' is reserved for project-level decisions")
        path = self.work_path(subject)
        policy = self.manifest()["policy"]
        if action == "create":
            kind, impact = args.get("kind", "feature"), args.get("impact", "behavior")
            if kind not in KINDS or impact not in IMPACTS:
                raise StructureError("Unknown kind or impact")
            modules = args.get("modules", [])
            if not isinstance(modules, list) or not modules:
                raise StructureError("Work item requires affected modules")
            for module in modules:
                self.module_path(module)
            scope = args.get("scope", [])
            if not isinstance(scope, list) or not scope:
                raise StructureError("Work item requires an explicit file/glob scope")
            for pattern in scope:
                contained(self.root, pattern)
            acceptance = args.get("acceptance", [])
            if not isinstance(acceptance, list) or not all(isinstance(x, str) for x in acceptance):
                raise StructureError("acceptance must be a string array")
            work = {"schema_version": 2, "id": subject, "title": args.get("title", subject),
                    "kind": kind, "impact": impact, "state": "proposed", "modules": modules,
                    "scope": scope, "acceptance": args.get("acceptance", []), "created_at": now()}
            identity = self.sd / "identity.json"
            if identity.exists():
                work["project_id"] = read_json(identity)["project_id"]
            write_json(path, work, exclusive=True)
            return {"work_item": work, "revision": self.revision(subject)}
        work = read_json(path)
        if action == "get":
            return {"work_item": work, "revision": self.revision(subject)}
        if work.get("managed_by") == "pi-development-harness":
            raise StructureError("Harness-managed tasks must transition through the development harness")
        if action != "transition":
            raise StructureError("work action must be create, get, list or transition")
        target = args.get("to")
        if target not in policy["workflow"]["transitions"].get(work["state"], []):
            raise StructureError(f"Work transition not allowed: {work['state']} -> {target}")
        evidence = []
        if target == "implementing" and work["impact"] in policy["gate_impacts"]:
            evidence.append(self.require_gate("change-review", subject))
        validation = args.get("validation", [])
        if target == "done" and (not isinstance(validation, list) or not validation):
            raise StructureError("Completion requires validation evidence")
        self.event("work_transition", subject=subject, subject_revision=self.revision(subject),
                   source=work["state"], target=target, evidence=evidence, validation=validation)
        work.update(state=target, updated_at=now())
        if validation:
            work["validation"] = validation
        write_json(path, work)
        return {"work_item": work, "revision": self.revision(subject)}

    def policy_check(self, args):
        manifest, state = self.manifest(), self.state()
        policy = manifest["policy"]
        paths = args.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise StructureError("paths must be a string array")
        paths = [contained(self.root, p).relative_to(self.root).as_posix() for p in paths]
        reasons, warnings = [], []
        staged_review = bool(args.get("staged")) and not bool(args.get("runtime"))
        work = read_json(self.work_path(args["work_item"])) if args.get("work_item") else None
        module_ids = work["modules"] if work else args.get("modules", [])
        code = [p for p in paths if not p.startswith(".structure/") and any(matches(p, g) for g in policy["code_globs"])]
        for module in module_ids:
            self.module_path(module)
        if code and state["operating_mode"] == "archived":
            reasons.append("Project is archived")
        allowed_states = ("implementing", "verifying", "done") if staged_review else ("implementing", "verifying")
        if work and work["state"] not in allowed_states:
            reasons.append("Work item must be implementing or verifying before writes")
        if work and work["impact"] in policy["gate_impacts"]:
            try:
                self.require_gate("change-review", work["id"])
            except StructureError as exc:
                reasons.append(str(exc))
        if policy["runtime_enforcement"] and paths and not work and not staged_review:
            reasons.append("Enforced runtime requires an explicit work_item")
        for file in paths:
            if file.startswith(".structure/"):
                # Known write tools may update only affected module knowledge.
                allowed = [f".structure/modules/{key(m)}.json" for m in module_ids]
                if args.get("runtime") and file not in allowed:
                    reasons.append(f"Use protocol tools/coordinator for shared state: {file}")
                continue
            if work and not any(matches(file, pattern) for pattern in work["scope"]):
                reasons.append(f"Outside work scope: {file}")
            if module_ids:
                authorized = any(any(matches(file, p) for p in manifest["modules"][m]["owns"] + manifest["modules"][m]["may_touch"])
                                 and not any(matches(file, p) for p in manifest["modules"][m]["forbidden"]) for m in module_ids)
                if not authorized:
                    reasons.append(f"Outside module owns/may_touch: {file}")
        if not args.get("runtime"):
            for line in manifest["coupling"]["red_lines"]:
                if any(matches(p, line["change"]) for p in paths):
                    for required in line["must_also_change"]:
                        if not any(matches(p, required) for p in paths):
                            reasons.append(f"Coupled change missing: {required}")
        if args.get("runtime") and policy["coordination"]["mode"] == "enforced":
            leases = {e["module"]: e for e in self.lease({"action": "status"})["leases"] if not e["expired"]}
            # All declared affected modules must be leased before first write.
            for module in module_ids:
                lease = leases.get(module)
                if not lease or lease["session"] != args.get("session"):
                    reasons.append(f"Session lacks active lease: {module}")
        impact = work["impact"] if work else args.get("impact", "unknown")
        if code and impact == "unknown":
            warnings.append("Impact is unknown; declare a work item or an explicit impact")
        if args.get("skip_reason"):
            if impact != "mechanical" or not policy["allow_mechanical_skip"]:
                reasons.append("Skip is allowed only for mechanical changes in a permissive profile")
        needs_update = code and impact in policy["update_impacts"]
        if args.get("staged") and needs_update:
            affected = module_ids or [m for m, info in manifest["modules"].items()
                                      if any(any(matches(p, scope) for scope in info["owns"]) for p in code)]
            for module in affected:
                if f".structure/modules/{key(module)}.json" not in paths:
                    reasons.append(f"Knowledge update required for affected module: {module}")
        if args.get("staged") and code and impact == "unknown" and policy["hook"] == "enforced":
            reasons.append("Enforced hook needs an impact declaration")
        return {"decision": "deny" if reasons else "allow", "reasons": reasons,
                "warnings": warnings, "impact": impact, "knowledge_update_required": bool(needs_update),
                "guarantees": self.guarantees(manifest)}

    def context(self, args):
        manifest, state = self.manifest(), self.state()
        module = args.get("module")
        if module:
            knowledge = read_json(self.module_path(module))
            modules = {module: manifest["modules"][module]}
        else:
            knowledge, modules = None, manifest["modules"]
        contract = self.sd / "AGENTS.md"
        if not contract.exists():
            contract = self.sd / "AGENT.md"
        result = {"schema_version": 2, "state": state, "policy": manifest["policy"],
                  "modules": modules, "knowledge": knowledge,
                  "contract": contract.read_text(encoding="utf-8") if contract.exists() else "",
                  "work_items": [w for w in self.work({})["work_items"] if not module or module in w["modules"]],
                  "guarantees": self.guarantees(manifest)}
        if args.get("level", "L1") in ("L2", "L3"):
            result["coupling"] = manifest["coupling"]
        if args.get("level") == "L3":
            result["events"] = [e for e in self.events() if e.get("subject") in ("project", *[w["id"] for w in result["work_items"]])][-10:]
        return result

    def verify(self, args):
        errors, warnings = [], []
        try:
            manifest, state = self.manifest(), self.state()
            if not (self.sd / "AGENTS.md").exists():
                errors.append("Missing AGENTS.md contract")
            for module in manifest["modules"]:
                data = read_json(self.module_path(module))
                if data.get("module") != module or data.get("schema_version") != 2:
                    errors.append(f"Invalid module knowledge: {module}")
                for field in ("truths", "dont_assume"):
                    if not isinstance(data.get(field), list):
                        errors.append(f"{module}.{field} must be an array")
                for fact in data.get("truths", []):
                    if not isinstance(fact, dict) or not fact.get("statement"):
                        errors.append(f"Invalid fact in {module}")
                    elif not fact.get("evidence") or not fact.get("verified_at"):
                        warnings.append(f"Fact lacks verification evidence in {module}: {fact['statement']}")
            events = self.events()
            ids = {e["id"] for e in events}
            if len(ids) != len(events) or len({e["sequence"] for e in events}) != len(events):
                errors.append("Duplicate event ID or sequence")
            requests = {e["id"]: e for e in events if e["type"] == "gate_requested"}
            for event in events:
                if event["type"] == "decision":
                    request = requests.get(event.get("request_id"))
                    if not request or any(event.get(f) != request.get(f) for f in ("gate", "subject", "subject_revision")):
                        errors.append(f"Broken decision request reference: {event['id']}")
                    if event.get("decision") not in ("APPROVED", "REJECTED", "CONDITIONAL"):
                        errors.append(f"Invalid decision: {event['id']}")
                if event["type"] in ("phase_transition", "work_transition"):
                    for ref in event.get("evidence", []):
                        if ref not in ids:
                            errors.append(f"Missing transition evidence: {ref}")
            transitions = [e for e in events if e["type"] == "phase_transition"]
            if transitions and state.get("last_transition") != transitions[-1]["id"]:
                errors.append("State snapshot differs from latest phase event; inspect interrupted transition")
            for work in self.work({})["work_items"]:
                if work.get("state") not in manifest["policy"]["workflow"]["states"]:
                    errors.append(f"Invalid work state: {work.get('id')}")
                for module in work.get("modules", []):
                    if module not in manifest["modules"]:
                        errors.append(f"Dangling work module: {module}")
            if state.get("discovery_status") == "draft":
                warnings.append("Discovered modules are drafts; structural validity does not prove project facts")
            vendor = self.sd / "vendor/version.json"
            if vendor.exists():
                import hashlib
                for rel, expected in read_json(vendor)["files"].items():
                    file = contained(self.sd / "vendor", rel)
                    if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                        errors.append(f"Vendor hash mismatch: {rel}")
        except (StructureError, KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
        return {"valid": not errors, "errors": errors, "warnings": warnings,
                "checks": ["schema", "references", "event-links", "snapshot", "vendor-hashes"],
                "facts_executed": False}

    def views(self, args):
        manifest = self.manifest()
        rows = ["# Module map (generated)", "", "| Module | Path | Health | Responsibility |", "|---|---|---|---|"]
        def cell(value):
            return str(value).replace("|", "\\|").replace("\n", " ")
        for name, module in manifest["modules"].items():
            rows.append("| " + " | ".join(cell(x) for x in (name, module["path"], module["health"], module["responsibility"])) + " |")
            data = read_json(self.module_path(name))
            text = f"# {name}\n\nGenerated from modules/{key(name)}.json.\n\n## Current truths\n\n"
            text += "\n".join("- " + fact["statement"] for fact in data["truths"]) or "No verified facts recorded."
            text += "\n\n## Don't assume\n\n" + ("\n".join("- " + str(x) for x in data["dont_assume"]) or "No assumptions recorded.")
            atomic_text(self.sd / "views/modules" / f"{key(name)}.md", text + "\n")
        atomic_text(self.sd / "tree.md", "\n".join(rows) + "\n")
        atomic_text(self.sd / "files.md", "# Files (generated)\n\n" + "\n".join("- " + f for f in scan_files(self.root)) + "\n")
        rows = ["# Decisions (generated)", "", "| ID | Gate | Subject | Revision | Decision | Actor | Time |", "|---|---|---|---|---|---|---|"]
        for e in self.events():
            if e["type"] == "decision":
                rows.append("| " + " | ".join(cell(e.get(k, "")) for k in ("id", "gate", "subject", "subject_revision", "decision", "actor", "timestamp")) + " |")
        atomic_text(self.sd / "human-gates/INDEX.md", "\n".join(rows) + "\n")
        work = self.work({})["work_items"]
        atomic_text(self.sd / "tasks/BACKLOG.md", "# Work items (generated)\n\n" + "\n".join(f"- {w['id']} [{w['state']}]: {w['title']}" for w in work) + "\n")
        graph = self.export({})
        write_json(self.sd / "views/graph.json", graph)
        return {"generated": ["tree.md", "files.md", "human-gates/INDEX.md", "tasks/BACKLOG.md", "views/graph.json", "views/modules/*.md"]}

    def export(self, args):
        manifest, state = self.manifest(), self.state()
        nodes = [{"id": "project", "type": "project", "label": state["project"], "data": state}]
        edges = []
        def edge(source, target, kind):
            edges.append({"id": digest([source, target, kind]), "source": source, "target": target, "type": kind})
        for name, module in manifest["modules"].items():
            node = "module:" + name
            nodes.append({"id": node, "type": "module", "label": name, "data": module,
                          "knowledge": read_json(self.module_path(name)),
                          "source": f".structure/modules/{key(name)}.json"})
            edge("project", node, "contains")
            for dep in module["depends_on"]:
                edge(node, "module:" + dep, "depends_on")
            for target in module["tests_for"]:
                edge(node, "module:" + target, "tests_for")
        for work in self.work({})["work_items"]:
            node = "work:" + work["id"]
            nodes.append({"id": node, "type": "work_item", "label": work["title"], "data": work})
            for module in work["modules"]:
                edge(node, "module:" + module, "affects")
        for event in self.events():
            node = "event:" + event["id"]
            nodes.append({"id": node, "type": "event", "label": event["type"], "data": event})
            subject = event.get("subject", "project")
            edge(node, "project" if subject == "project" else "work:" + subject, "records")
            if event.get("request_id"):
                edge(node, "event:" + event["request_id"], "decides")
        node_ids = {n["id"] for n in nodes}
        if len(node_ids) != len(nodes) or any(e["source"] not in node_ids or e["target"] not in node_ids for e in edges):
            raise StructureError("Graph contains duplicate IDs or dangling references")
        return {"schema_version": 2, "graph_kind": "project-instance", "nodes": nodes, "edges": edges,
                "policy": manifest["policy"], "coupling": manifest["coupling"],
                "guarantees": self.guarantees(manifest), "leases_included": False}


def dispatch(operation, args=None, root=None, channel="cli"):
    args = args or {}
    if not isinstance(args, dict):
        raise StructureError("Arguments must be an object")
    project = Project(find_root(root))
    with project_mutex(project.root):
        if operation == "init":
            return project.initialize(args)
        if operation == "migrate":
            from .migration import migrate
            return migrate(project, args)
        project.manifest()
        project.state()
        methods = {"phase": project.phase, "lock": project.lease, "work": project.work,
                   "context": project.context, "verify": project.verify, "views": project.views,
                   "export": project.export, "policy": project.policy_check}
        if operation == "gate":
            return project.gate(args, channel)
        if operation == "vendor":
            project.copy_vendor(project.sd)
            return {"vendored": True, "package_version": VERSION}
        if operation not in methods:
            raise StructureError(f"Unknown operation: {operation}")
        return methods[operation](args)
