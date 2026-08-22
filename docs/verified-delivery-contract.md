# Verified Projection Delivery Contract v0.1

状态：V0.6.0 Machine Contract Implemented；V0.7 Explain Pack 兼容扩展已实现；独立人工视觉 Acceptance 必须继续外部 Hash-bound

对应 Finding：SF-32、SF-36、SF-38、SF-43

## 1. 目标

Verified Delivery 只负责将已验证的本地候选晋升为 last-good 派生制品。它不修改 Panorama Core、Event Store、
治理状态或业务项目，也不等于外部发布。

## 2. 固定门禁

```text
Input/Model/View Binding
→ Schema/Cross-reference
→ Semantic Invariant
→ Geometry/Composition
→ HTML/SVG/Security/Privacy
→ Browser/Viewport Evidence（适用时）
→ Candidate Hash Freeze
→ Atomic last-good Promote
→ Machine Receipt
```

任一门禁失败都保留原 last-good 字节。Receipt 必须记录稳定 rule code、subject、measured evidence、supported
fix、输入/制品 Hash、Compiler/Renderer Version、Source/Event/`asOf` Binding 和自动/人工视觉状态。

## 3. 视觉状态

- `pending`：自动检查通过但无人完成视觉评审；
- `accepted`：独立人工评审明确接受精确 Artifact Hash；
- `rejected`：人工拒绝，禁止 Promote；
- `skipped`：环境无法采集浏览器证据，不得宣传交互/视觉通过。

内部 last-good 可以在合同允许时接受 pending/skipped，但公开发布的代表性制品必须绑定 `accepted`。任何外部
发布仍要求独立人工批准。

## 4. Policy 与恢复

`verified_delivery.promote` Adapter 使用单 Operation Policy，并在替换前绑定 Candidate/last-good
Before/Expected Hash。post-effect/pre-receipt 中断必须使用独立 Operation Transaction 确定性 resume；不得把
Policy Pending 当作效果证明，也不得自动重渲染或 rebase。

机器合同已经冻结为：

- `panorama-renderer-browser-evidence.v0.1`：绑定精确 HTML 字节、四档 Desktop 与可选窄屏截图 Hash、DOM/
  Geometry/Console 测量，视觉状态固定为 `pending`；
- `panorama-verified-delivery-receipt.v0.1`：新候选绑定 Project/Model/Bundle/Guided View Set/1–24 View/Artifact
  与机器门禁；View Set 字段保持可选以继续验证既有 v0.1 Receipt，但不得从旧 Receipt 推断 View Set 能力；
- `panorama-verified-delivery-promotion-transaction.v0.1`：绑定 Policy Use、Receipt/Candidate、last-good
  Before/Expected、Effect/Receipt/Event 与每状态 `stateHash`。

Candidate 生成只写 immutable `candidates/receipts/evidence`，绝不触碰 last-good。Promotion 以
`.panorama-work/verified-delivery/v0.1/last-good/index.html` 为唯一效果目标，使用原子文件替换；若观察到 Before、
Expected 之外的第三状态，保留现场并 fail closed。`effect_observed` 可以恢复 Audit Event/Execution Receipt/
Ledger，但不能重渲染或重复覆盖。Core Receipt 已 durable 而 Ledger 未推进时，只能复用精确 Use Receipt。

Candidate/Receipt/Browser Evidence/Transaction 篡改、缺失 viewport、overflow、几何失败、控制台错误、过期/
错配 Policy、并发 Writer、第三状态和 post-effect 中断均为负向门禁。V0.6.0 三份代表性制品的机器 Evidence
继续固定为 `visualReview=pending`；独立人工接受通过
[V0.6 Human Visual Acceptance](v0.6-human-visual-acceptance.md) 绑定精确 Artifact Hash。不得把外部接受反写进
机器 Receipt，也不得仅凭本地 last-good 宣称外部发布完成。

## 5. V0.7 Explain Pack 兼容扩展

V0.7 不修改冻结的 `panorama-verified-delivery-receipt.v0.1` Schema。`prepare_delivery` 在调用方同时提供
Panorama Core 与 Explain Pack 时，先执行完整 Core/Model/View/View Set/Explain Pack Binding 校验，再由
Renderer 0.3 生成 `panorama-multi-view-bundle.v0.2`。Bundle Hash 的身份语义显式包含
`explainPackId + semanticHash`，因此 Receipt 的既有 `bundleBinding.bundleHash` 会传递绑定精确 Explain Pack；
缺少 Core、包被篡改或任一输入漂移都在候选写入前失败。

这一传递绑定不能被解释为人工视觉接受。带讲解 Dialog 的精确候选仍须经过 Browser/Viewport/Console 机器门禁，
机器 Receipt 继续写 `visualReview=pending`；代表性公开制品只有在独立人工记录绑定其精确 Artifact SHA-256 后才可
宣称接受。
