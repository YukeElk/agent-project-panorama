# Structure handbook

`identity.json` identifies a project (`kind: project`, stable `project_id`) or a module (`kind: module`, owner `project_id`, `module_id`, relative `project_root`). A module marker is context, not independent project authority. Resolve it through the shared identity loader; do not pick the nearest `.structure` directory by name alone.

`manifest.json` defines modules, ownership, coupling and policy. `state.json` records the project mode and legacy phase snapshot. `modules/*.json` stores facts and assumptions; names are SHA-256 encodings of stable module IDs, resolved through the tools. `work-items/*.json` stores independent work lifecycles. `events/*.json` is the append-only API decision and transition journal. `runtime/leases/*.json` contains temporary owner-token leases and is ignored by Git.

`tree.md`, `files.md`, `tasks/BACKLOG.md`, `human-gates/INDEX.md`, `views/modules/*.md` and `views/graph.json` are generated views. Run the installed package's `scripts/structure.py views --project-root <project>` to rebuild them. Do not maintain a second authoritative copy.

The default profile is solo-light and enables no mandatory leases or Pi write interception. Select a stronger profile for shared-checkout concurrency. OS process exclusion only coordinates the same machine and canonical checkout; worktrees and remote hosts require other coordination.

Use `verify` for schema and reference integrity. It does not execute commands embedded in facts or prove factual freshness. Run relevant validation separately and store its evidence and date.
