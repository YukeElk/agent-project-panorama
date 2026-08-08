# UI Design Basis — V0.1

The four generated desktop concepts in `docs/visual-concepts/` are the visual
reference for the first renderer implementation:

- `control.png`
- `system-logical-drawer.png`
- `system-runtime.png`
- `evolution.png`

## Color lock

- Page: true cool white `#ffffff`
- Subtle band: `#f7f8fb`
- Ink: `#111c3a`
- Muted ink: `#5f687d`
- Rule: `#d9deea`
- Strong rule: `#b8c0d4`
- Primary: `#2448d8`
- Primary tint: `#eef2ff`
- Passed: `#168653`
- Warning: `#c7790a`
- Critical: `#d62f3d`
- Inactive: `#80889a`

No gradient, glow, tinted image overlay, warm/off-white reinterpretation, or
large soft shadow is allowed.

## Typography

- Local system sans stack for all UI and content.
- Local monospace stack for IDs, paths, versions, ports, and artifact versions.
- 13px UI chrome, 14px body, 16–18px section headings, 23px project name.
- Compact controls use explicit 12–13px sizing; browser defaults are not used.

## Container model

- Quiet top navigation and metadata rail.
- Open bands, lists, tables, layer lanes, and inspector/drawer surfaces.
- Border radius is 8px maximum; one level of framing, no nested card grid.
- Hairline borders and slim left-edge semantic bars carry most hierarchy.

## Component families

- Text navigation with selected underline.
- Segmented mode/environment switches.
- Status dot + label.
- Compact disclosure row.
- Data table with selected/hover rows.
- Architecture module node with four status rows.
- Native SVG connector layer with arrowheads and protocol labels.
- Right-side module drawer.
- Timeline spine and stage rail.
- Resource inspector and credential mode blocks.

## Interaction lock

- CONTROL / SYSTEM / EVOLUTION switch without reload.
- SYSTEM subview and Current / Target / Transition switches.
- Search and Layer / Source / Status filters.
- Module selection opens Drawer; Escape/backdrop/close button closes it.
- Architecture hover highlights upstream and downstream.
- Stage selection changes the stage detail and module table.
- Environment/resource selection updates runtime details.
- Embedded credentials begin masked; Reveal/Hide and Copy are explicit.
- External File and External Store never synthesize or reveal secret values.

## Above-the-fold copy lock

Only project data plus these renderer labels may appear in the first viewport:

`CONTROL`, `SYSTEM`, `EVOLUTION`, `Latest Reviewed Update`, `Attention`,
`Next Focus`, `Requirement Snapshot`, `Logical Architecture`,
`Runtime & Resources`, `Architecture Source`, `CURRENT`, `TARGET`,
`TRANSITION`, and the security notice that masking is not encryption.

No hero kicker, fake KPI, alignment percentage, task-board language, or editing
control is permitted.
