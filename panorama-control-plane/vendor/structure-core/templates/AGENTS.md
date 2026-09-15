# Structure contract

Read `state.json`, `manifest.json` and the affected module knowledge under `modules/` when they are relevant to a task. Machine keys are stable across display languages. Code and executable evidence determine actual behavior; stored facts can become stale.

Follow the selected policy profile. Update module truths or assumptions when behavior, interfaces, boundaries or architecture change. Mechanical edits do not automatically require knowledge updates. Do not hand-maintain generated tree, file inventory or decision indexes; the coordinator runs `views`.

Each work item owns an explicit scope and affected modules. A module's `owns`, `may_touch` and `forbidden` refine that scope. Cross-module coupling does not automatically grant write permission. Under enforced coordination, acquire all affected module leases, keep owner tokens, renew before expiration, then release after work. Leases are checkout-local runtime state.

Use tools for shared protocol state, work transitions and decisions. Known write/edit tools in an enforced Pi session can update affected module knowledge only; arbitrary shared state writes are blocked. Raw shell and external edits are not sandboxed by this protocol.

Gate requests are pending until a real human decision is recorded through the interactive adapter or explicit local attestation. A model-authored actor string is not human authorization. Approval applies to the exact subject revision. Conditional decisions remain pending until a new approval is recorded. Do not edit or erase historical event JSON through normal workflows.

If state is missing or corrupt, inspect, verify, repair or migrate before dependent writes. This does not prohibit reading code or discussing design. Project mode and individual work-item workflow are separate; spikes, bug fixes and incidents do not need a project-wide phase rollback.

User instructions and existing authorization boundaries remain in force. Protocol setup does not authorize deployment, publishing, external messages or service startup.
