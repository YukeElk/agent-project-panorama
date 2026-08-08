---
name: agent-project-panorama
description: "Operate Agent Project Panorama V0.1/V0.1.1 Single HTML files through INIT, INSPECT, PROPOSE UPDATE, APPLY UPDATE, and VALIDATE. Use when Codex needs to create, inspect, validate, preview, or apply a controlled architecture-centric project panorama update with secret redaction, approval-hash binding, revision guards, and Presentation Layer preservation."
---

# Agent Project Panorama

Resolve the skill root as the directory containing this file. Run bundled commands from that root.
Treat one Panorama as a single-project engineering cognition surface, not as a task board or general
project-management platform. Support Schema `0.1` and Template `0.1.0` / `0.1.1` only.

## Invariants

- Keep the HTML read-only to users. Route writes through `scripts/apply_patch.py`.
- For an ordinary update, change only the JSON payload inside `project-panorama-data`.
- Preserve both data Markers, DOM, CSS, Renderer JavaScript, unknown fields, and every `extensions` object.
- Require user review of the exact Proposal Hash before Apply. Never manufacture or infer approval.
- Record a Schema Finding before proposing a Schema refactor. Do not change frozen Schema v0.1 during
  an ordinary update.
- Mark uncertain information unknown/draft/pending or ask for evidence. Never invent IDs, paths,
  reviews, acceptance results, deployment state, or credentials.
- Keep the existing credential mode unless the user explicitly approves migration.
- Never print Embedded values in a summary. Do not use `--unsafe-include-secrets` unless the user
  explicitly requests those exact sensitive values.
- Summarize engineering meaning and impact, not file-operation transcripts.

## INIT

Accept a fuzzy idea, explicit requirements, an existing project, or an OSS adaptation. Collect only
available evidence.

If no Panorama JSON exists, generate the minimum skeleton:

```powershell
python scripts/new_project_data.py `
  --project-id PRJ-ID `
  --name "Project" `
  --objective "Objective" `
  --current-focus "Clarify requirements" `
  --requirement "Initial draft requirement" `
  --output project.v0.1.json
```

The skeleton creates one current clarification Stage, a Draft Architecture, legal Unknown/Draft
states, and two Next Focus options. It does not invent Modules, deployments, reviews, documents, or
runtime facts.

Generate only through the validating initializer:

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data project.v0.1.json `
  --output project-panorama.local.html
```

The initializer validates before and after generation. Treat `--allow-invalid` as explicit debugging;
do not use it for a formal deliverable. Do not overwrite an existing target unless the user selected
that exact file and a recoverable copy exists.

## INSPECT

1. Run `VALIDATE` before trusting relationships.
2. Read the orientation summary:

   ```powershell
   python scripts/read_panorama.py path/to/project-panorama.html
   ```

3. Load complete data only when required:

   ```powershell
   python scripts/read_panorama.py path/to/project-panorama.html --json
   ```

   This output is redacted. Never add `--unsafe-include-secrets` without an explicit user request.
   Do not read External File contents or resolve External Store values.
4. Report Current Stage, mainline, Current → Target Architecture, active/deploying runtime state,
   latest Update, High/Critical Attention, verification gaps, and current Next Focus.

## PROPOSE UPDATE

Do not mutate the target HTML.

1. Express changes as minimal `add`, `replace`, or `remove` JSON Pointer operations.
2. Preserve IDs and extensions. For Next Focus, provide 2–3 viable options with Why Now, benefits,
   risks, impact, prerequisites, evidence, expected outcome, and one recommendation.
3. Create the deterministic Proposal:

   ```powershell
   python scripts/propose_update.py `
     path/to/project-panorama.html `
     candidate-update.json `
     --output pending-update.json
   ```

4. Use its facts for revision/hash, operations, affected entities, validation, structured Findings,
   audit skeletons, Attention, and Proposal Hash. Do not parse validator terminal strings.
5. Include every High/Critical Validator Finding in Preview and UpdateBatch Attention. Explain or
   improve wording, but never hide a finding.
6. Present semantic What/Why/Impact, Change Level, Review Requirement, uncertainty, Next Focus, and
   the exact package. Stop for user review.

The generated package is pending. User approval changes only `approval.status`, `approvedBy`, and
`approvedAt`, while retaining the exact `proposalHash`. Any change to base revision/hash, operations,
changes, review, update batch, or guidance requires a new Proposal Hash and new review.

## APPLY UPDATE

Apply only when the user explicitly approves the exact Proposal Hash.

```powershell
python scripts/apply_patch.py `
  path/to/project-panorama.html `
  pending-update.json
```

Let the script enforce approval/hash binding, Review/UpdateBatch relation, Base Revision/Data Hash,
exclusive lock, backup, validation, unchanged Presentation Hash, commit-time conflict detection, and
atomic replacement.

If validation, approval, or conflict fails, do not repair, rebase, or reapprove silently. Re-run
INSPECT and PROPOSE UPDATE. After success, run VALIDATE and INSPECT; report the new Revision,
semantic outcome, remaining High/Critical findings, backup path, and HTML path.

V0.1.1 preserves historical Next Focus options in an inactive compatibility record so old Update
Batches do not break. Do not expose historical options as current Guidance or describe this as the
final data model; the formal snapshot belongs to the V0.2 migration proposal.

## VALIDATE

```powershell
python scripts/validate_panorama.py path/to/project-panorama.html
python scripts/validate_panorama.py path/to/project-panorama.html --json
```

Treat exit `0` as no Schema/cross-reference Error, `1` as invalid engineering data, and `2` as an
input/runtime failure. Warnings remain actionable work. Prioritize severity `high` / `critical`.

Summarize Architecture Gap, Scope Drift, Implementation Drift, Review Gap, Verification Gap,
Baseline Drift, Deployment Drift, Transition Risk, Resource Risk, Stage Entry/Exit Gap,
Missing Reference Path, Embedded Secret, and Git Secret Risk. Never auto-fix a finding without an
approved update preview.

## Security and Git

- Prefer `*.local.html` for Embedded local credentials; recommend adding it to the host repository's
  `.gitignore`.
- Treat High `GIT_SECRET_RISK` as a sharing/commit decision, not permission to delete or migrate data.
- Prefer External File or External Store for Production, while respecting the user's chosen mode.
- Dynamic renderer links are sanitized; rejected URLs remain visible but non-clickable.

## Stop Boundary

Do not add new primary views, a backend, React/Vue, Kanban, people/budget/calendar management,
cloud collaboration, automatic Git commits, automatic secret migration, or a broad Agent platform.
Do not execute a V0.2 Schema migration unless the user separately authorizes that phase.
