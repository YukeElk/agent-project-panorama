# Verified Projection Delivery Contract v0.1

状态：Behavioral Design Closed；WP6 Machine Schema/Implementation Pending

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

未来 `verified_delivery.promote` Adapter 必须使用单 Operation Policy，并在替换前绑定 Candidate/last-good
Before/Expected Hash。post-effect/pre-receipt 中断必须使用独立 Operation Transaction 确定性 resume；不得把
Policy Pending 当作效果证明，也不得自动重渲染或 rebase。

只有 Candidate/Receipt/Transaction 故障注入通过后才冻结 Delivery Receipt Schema。
