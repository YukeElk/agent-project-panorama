---
name: agent-project-panorama
description: "Operate a local-first Agent Project Panorama Single HTML through INIT, INSPECT, PROPOSE UPDATE, APPLY UPDATE, and VALIDATE workflows. Use when Codex needs to initialize, inspect, validate, preview a controlled data-only update to, or apply an approved revision-guarded patch to a Project Panorama HTML without changing its Presentation Layer."
---

# Agent Project Panorama

Resolve the skill root as the directory containing this file. Run bundled commands from that root.
Treat the Panorama as a single-project engineering cognition surface, not as a task board or a
general project-management platform.

## Invariants

- Keep the HTML read-only to users. Route writes through `scripts/apply_patch.py`.
- For an ordinary update, change only the JSON payload inside `project-panorama-data`.
- Preserve `PANORAMA_DATA_START`, `PANORAMA_DATA_END`, DOM, CSS, Renderer JavaScript,
  unknown fields, and every `extensions` object.
- Require an explicit user review of the exact preview before applying it.
- Record a Schema Finding before proposing any Schema refactor; do not change the frozen Schema
  as part of an ordinary data update.
- Mark uncertain information as unknown or ask for evidence. Never invent IDs, paths, reviews,
  acceptance results, deployment state, or credentials.
- Keep the existing credential mode unless the user explicitly approves a migration. Never print
  an Embedded credential value in a summary.
- Summarize engineering meaning and impact. Do not return a file-operation transcript.

## INIT

Accept a fuzzy idea, explicit requirements, an existing project, or an OSS adaptation as input.
Collect only available evidence. If no Panorama JSON exists, construct the smallest Schema-valid
document that contains Project/Intent, evidenced initial Requirements, one default Stage, a Draft
Architecture or Baseline, initial References, and 2–3 Next Focus options. Use `unknown`, `draft`,
`not_defined`, or equivalent Schema states where evidence is absent. Do not invent Modules,
documents, reviews, deployments, or runtime facts to fill the UI.

1. Validate the source or newly constructed JSON before generation:

   ```powershell
   python scripts/validate_panorama.py path/to/project.json
   ```

2. Generate a new HTML from the stable template:

   ```powershell
   python scripts/init_panorama.py `
     --template templates/panorama.html `
     --data path/to/project.json `
     --output path/to/project-panorama.html
   ```

3. Validate and inspect the generated HTML. Do not overwrite an existing Panorama unless the user
   selected that exact target and a recoverable copy exists.

## INSPECT

1. Read the stable orientation summary:

   ```powershell
   python scripts/read_panorama.py path/to/project-panorama.html
   ```

2. Run `VALIDATE` before trusting detailed relationships.
3. Load complete data only when needed with `--json`; restrict inspection to relevant fields and
   avoid exposing Embedded credential values.
4. Report Project/Stage, Current → Target Architecture, Current Release/Deployment, Latest Update,
   active Attention, and Next Focus as a semantic summary.

## PROPOSE UPDATE

Do not mutate the target HTML in this workflow.

1. Read the latest Revision and canonical Data Hash. Prepare the proposal from that exact base.
2. Express data changes as minimal JSON Pointer operations using only `add`, `replace`, or `remove`.
3. Add the required audit entities: Change Records, optional Review, Update Batch, and updated
   Guidance. Preserve existing IDs and generate new globally unique IDs where needed.
4. Classify `changeLevel` with the Schema vocabulary:
   `local`, `module`, `architecture`, `deployment`, or `project`.
5. For Next Focus, provide multiple viable options. For each option explain why now, benefits,
   risks, dependencies, and evidence; mark one recommendation without hiding alternatives.
6. Preview the proposed semantic diff, change level, affected entities, expected warnings,
   validation result, and exact update package. Stop and wait for user review.

Use this package shape:

```json
{
  "baseRevision": 3,
  "baseDataHash": "<canonical SHA-256>",
  "operations": [
    {"op": "replace", "path": "/intent/currentFocus", "value": "..."}
  ],
  "changeRecords": [],
  "reviewDraft": {},
  "updateBatchDraft": {},
  "guidanceDraft": {}
}
```

Compute the base hash with `compute_data_hash(extract_data(html_path))` from
`scripts/panorama_io.py`. Before presenting the preview, apply the operations to an in-memory copy
and use `validate_data` from `scripts/validate_panorama.py`; do not write the target.

## APPLY UPDATE

Apply only after the user explicitly approves the exact previewed package.

1. Reconfirm the target HTML and approved package.
2. Run:

   ```powershell
   python scripts/apply_patch.py `
     path/to/project-panorama.html `
     path/to/approved-update-package.json
   ```

3. Let the script enforce Base Revision/Data Hash, pre-mutation backup, Schema and cross-reference
   validation, unchanged Presentation Hash, and atomic replacement.
4. If a conflict or validation failure occurs, do not rebase or repair silently. Re-run `INSPECT`,
   produce a new preview, and request review again.
5. After success, run `VALIDATE` and `INSPECT`. Report the new Revision, semantic outcome, remaining
   warnings, backup path, and HTML path.

## VALIDATE

Run validation against either JSON or HTML:

```powershell
python scripts/validate_panorama.py path/to/project-panorama.html
```

Treat exit code `0` as no Schema/cross-reference Error; warnings can still represent real work.
Treat exit code `1` as invalid engineering data and exit code `2` as an input/runtime failure.
Summarize findings by severity and rule, including Architecture Gap, Scope Drift,
Implementation Drift, Review Gap, Verification Gap, Baseline Drift, Deployment Drift,
Transition Risk, Resource Risk, Stage Exit Gap, Missing Reference Path, and Embedded Secret rules.
Never auto-fix a warning unless the user approves the corresponding data update preview.
