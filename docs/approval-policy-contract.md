# Approval Gate and Delegated Policy Contract v0.1

状态：V0.5.0 Core 已发布；V0.6.0 发布 `event_head.recover` 与 `verified_delivery.promote` Adapter；其余 Adapter 仍分阶段接入

批准范围：2026-08-20 用户批准完成“减少不必要人工门禁”的设计闭合

对应 Finding：SF-37；复用 SF-16、SF-27、SF-32、SF-36

## 1. 目标

Panorama 的审批必须绑定到真实决策和不可逆影响，而不是绑定到同一决策之后的每个机械步骤。
V0.5 使用四类门禁：

| 门禁 | 作用 | 人工审批 |
| --- | --- | --- |
| Decision Gate | 改变治理语义、风险接受或外部影响 | 每个精确语义变更一次 |
| Delegated Policy Gate | 在预先批准的有界策略内重复执行 | 策略启用一次；每次执行不再审批 |
| Automatic Quality Gate | Schema、语义、来源、隐私、几何、浏览器和完整性检查 | 不审批；失败即停止 |
| Read-only Gate | Inspect、Discovery、Preview、Diff、Validation、Draft、Advisory Review | 不审批 |

原则固定为：`one approval per risk boundary, not per mechanical step`。Human Review、Machine Validation
和 Authorization 是三种不同事实，不得互相冒充。

## 2. 必须由人批准的边界

以下操作不能进入 Delegated Policy Allowlist：

- 首次复杂/Legacy INIT 的完整语义物化，以及 Adopt Existing Presentation；
- Target、Requirement、Decision、Review、Acceptance/Gate 状态、Waiver、Guidance、Credential 或
  Architecture Candidate 的正式语义变更；
- 修改、扩展、续期、替换或撤销 Approval Policy；
- 对 Conflict 的裁决、High/Critical Finding 的豁免或风险接受；
- Hook 安装、外部系统写入、外部发布、Dataset/Training Export；
- Retention/Redaction 的破坏性删除、密钥销毁或法律处置；
- 任意未登记命令、依赖安装、未批准网络访问或无法证明为确定性的恢复。

这些操作继续使用完整 Proposal、64 位 canonical Hash、独立 write-once Approval 和 Apply 事务。
Agent、Hook、Receipt、Validator 或 Policy Runtime 都不能生成批准身份或自行扩展授权范围。

## 3. 无需人工审批的操作

以下操作不创建授权事实，因此不需要批准：

- Evidence Discovery、INSPECT、Source Freshness Audit 和 Runtime Preflight；
- Schema/Cross-reference/Formal Validation、差异计算、Proposal Freeze 和 Preview；
- Studio 草稿与布局编辑、Candidate 比较、Formal Validation 和只读 Agent Advisory Review；
- Formal Finding、Risk Candidate、Trace Gap Candidate 和 Conflict Candidate 的生成或展示；
- 本地只读 View IR/Renderer Candidate 生成，但不包含 last-good 替换或外部发布。

失败的自动质量门禁必须保留证据并停止，不得通过增加人工点击来绕过机器失败。

## 4. 可委托策略操作

独立 `panorama-approval-policy.v0.1` 只允许下列单一 Operation：

- `continuous_observation.apply`：现有 V0.2 Observation Policy 范围内的 fact-only 更新；
- `verification_receipt.import`：受信 Receipt 的 Reference/Evidence/Provenance 追加；
- `validation_manifest.execute`：执行预先冻结的准确命令；
- `engineering_event.record`：追加有界工程元数据 Event；
- `event_head.recover`：对完全有效且唯一的 Event Chain 重建派生 Head；
- `verified_delivery.promote`：机器门禁通过后原子替换本地 last-good 制品；
- `renderer.generate`：确定性生成本地候选制品；
- `source_freshness.reconcile`：只更新 Current/Freshness/Conflict 披露。

每个 Policy 只授权一个 Operation，避免把不同风险等级合并成一个长期通行证。Schema 的 Operation
枚举本身就是 allowlist；人类专属操作不能出现在 Policy 中。

## 5. Policy Envelope

机器合同位于 `schema/approval-policy.schema.v0.1.json`。一个可执行 Policy 必须包含：

- Project ID、允许的 Panorama Schema 版本；
- 唯一 Operation 和 Effect Class；
- 允许的 JSON Pointer Segment Template、Artifact Root、Producer 或 Command Binding；
- 固定 `panorama-governance-v0.5` 保护配置；
- 生效时间、到期时间、最大使用次数和 Source 重验证规则；
- fail-closed Stop Conditions；
- Execution Receipt/Event/Policy Binding 审计要求；
- 人工批准身份、Recorder 时间、固定 `approvalTimeSource=approval_recorder_clock` 和 Policy Hash。

Policy、Execution Receipt、Revocation 与 Use Ledger 只保存有界工程元数据和用户提供的非 Secret 身份；
不得保存源码、日志正文、Prompt、模型回答正文、Credential、Secret 或敏感个人数据，写入前必须执行与
Engineering Event 相同的大小、路径和敏感键扫描；JSON 最大 1 MiB、容器嵌套最多 20 层。

Policy Hash 使用 canonical JSON SHA-256，Hash Scope 为删除顶层 `approvalBinding` 后的完整 Policy。
激活流程固定为：

```text
Prepare Policy
→ Schema Validate
→ 展示完整 Scope / Stop Conditions / Expiry / Policy Hash
→ 用户精确确认 `批准 Approval Policy <EXACT-HASH>`
→ 独立 write-once Approval
→ 先 publish 或精确复用 Active Policy Envelope
→ 激活到 Policy Store
```

Publish 必须先于 Store 激活，避免“命令失败但 Policy 已生效”。若进程在初始 Use Ledger 落盘后、Active
Policy 激活标记落盘前崩溃，只能复用与 Policy ID/Hash 精确绑定且仍为 `nextUseNumber=1`、无前序 Receipt、
无 Pending、无 Extension 的初始 Ledger；任何已使用或未知状态的 orphan ledger 都必须 fail closed。

任何 Scope、Producer、Command、Project、有效期或停止条件变化都会产生新 Hash、新 Policy ID，并要求
重新批准；新 Policy 用 `supersedesPolicyId` 精确引用旧 Policy。Policy 不能自动续期，也不能由某次成功
执行扩大范围。

Path Scope 使用按 JSON Pointer Segment 匹配的 Template；`*` 只匹配一个 Segment，`-` 只表示数组追加，
不支持正则、`**`、名称相似度或任意深度通配。Runtime 必须先应用内置治理保护配置，再检查允许 Template；
允许与保护冲突时以保护为准。Allow Template 保持精确段数匹配；内置与额外 Forbidden Template 表示
受保护根，因此同时保护该路径及其全部后代。例如 `/intent` 必须阻止 `/intent/currentFocus`，
`/resources/*/access` 必须阻止 `/resources/0/access/credentials/mode`。

Policy 撤销不等待普通 Proposal：用户精确确认 `撤销 Approval Policy <POLICY-ID> <POLICY-HASH>` 后，
Recorder 立即写入 `panorama-approval-policy-revocation.v0.1` 并把该 Policy 加入本地 deny ledger。Revocation
必须先于后续审计 Event 生效；即使 Event 写入失败也不得重新启用已撤销 Policy。撤销只能减少权限，不能
携带替代 Scope；需要新 Scope 时创建并单独批准新 Policy。

## 6. 固定治理保护配置

`panorama-governance-v0.5` 至少保护：

- Schema、Intent、Requirement、Target Architecture、Decision、Review、Guidance 和 Policy；
- Module `targetDesign` 与治理关联；
- Gate `status/lastRunAt/resultSummary`；
- Acceptance `status/verificationStatus`；
- Resource Access/Credential；
- Template Version、Created Time 和 Presentation Layer。

Receipt Policy 可以追加 Gate/Acceptance 的 `evidenceReferenceIds`，但不能改变其状态。Observation Policy
可以更新 Current、Runtime、Freshness、Verification Observation、Resource Observation、Standard Assessment
和 Risk Candidate，但不能触碰上述治理语义。

## 7. Verification Receipt 策略路径

Schema 0.2 Receipt 只有同时满足以下条件才可免逐次人工批准：

1. Receipt Schema、Producer Version 和 Producer Artifact Hash 命中 Policy；
2. Redaction、Receipt Hash、Project/Revision/Data/Source Binding 全部有效；
3. Mapping 只包含新 Reference、Evidence ID、Fact Provenance 和 Receipt-owned Observation Batch；
4. Panorama Formal Validator 通过且没有未解决 Conflict；
5. Apply 使用 receipt-specific policy transaction，并记录 Policy Binding 与 Engineering Event。

Policy Runtime 不能把普通 `apply_patch.py` 的 pending Approval 伪造成用户批准。Schema 0.1 因没有正式
Fact Provenance/Observation Batch，继续要求逐次 Proposal Approval，或先由用户批准迁移到 0.2。
当 `receiptRules.requireFormalValidation=true` 时，成功或 no-op Receipt 的 `formalValidation` 必须精确为
`passed`；`failed` 和 `not_applicable` 都必须降级为失败，不能写成功 Event。

## 8. Validation Command 策略路径

每条可执行命令必须绑定 `argvSha256`、Project-relative cwd、timeout、允许退出码，以及实际
Network/Filesystem Isolation 声明。Policy 固定禁止 Secret Access、依赖安装、stdout/stderr 持久化和
外部发布。

命令、cwd、Manifest Hash、隔离声明或项目绑定任一变化都停止执行并要求新 Policy。`safe=true` 仍然只是
输入声明，不是授权或安全证明。

## 9. Deterministic Event Head Recovery

下列条件全部成立时可以在 Policy 下自动恢复：

- Event Schema、Request/Event Hash 和完整 Chain 有效；
- Sequence 连续、Project/Stream/Epoch 一致；
- 只有唯一 Tail；
- Head 只是 `missing` 或 `behind`；
- 恢复只重建派生 `stream-head.json`，不修改 Event。

`head_ahead`、Fork、Hash mismatch、重复 Sequence、Schema invalid、Project mismatch 或 Redaction/Epoch
歧义必须 fail closed 并转人工。

V0.5.1 Adapter 的完整 Project Path、Policy Profile、Input/Output Hash、Transaction、Lock Order 与 Resume
规则见 [Event Head Recovery Policy Adapter 合同](event-head-policy-adapter-contract.md)。该 Adapter 在恢复前
分配 Use，并用独立带 `stateHash` 的 Operation Transaction 覆盖 post-effect/pre-receipt 边界。Head 已 current
时只返回只读 no-op，不消耗 Use；没有已批准 Policy 时，仍沿用显式人工恢复路径。

## 10. Verified Delivery 与视觉评审

本地候选只有 semantic、schema、geometry、privacy 以及适用时 browser 门禁通过后，才可按 Policy
原子替换 last-good。`visualReview=pending|skipped` 可以用于内部 last-good，不得被描述为人工视觉通过；
`visualReview=rejected` 必须阻止晋升。任何外部发布仍要求人工批准。

## 11. Studio 单次批准

Studio Freeze 产生的规范 Proposal 就是 Apply 消费的 Proposal。用户精确批准 Studio Proposal Hash 一次后，
同一独立 Approval 直接满足 Apply，不再要求第二个普通 Proposal Approval。Candidate Semantic Hash、
Operations、Data/Git/Source/CAS 任一变化都会使该唯一 Approval stale。

## 12. 运行时停止条件与审计

每次 Policy 执行都必须在执行前后验证 Policy Hash、Project、Expiry、Use Count、Producer/Command、Source
和允许路径。Runtime 使用 `panorama-approval-policy-use-ledger.v0.1` 在副作用前原子分配 Use Number，完成后
生成 `panorama-policy-execution-receipt.v0.1`；同时记录 Policy ID/Hash、递增 Use Number、
输入/输出 Hash、实际路径/制品、验证结果和影响。Receipt 使用 Previous Receipt Hash 形成每 Policy 的有序
Use Ledger；`receiptHash` 是删除 `integrity.receiptHash` 后对完整 Receipt 计算的 canonical SHA-256。

Use Number 必须在产生任何副作用前原子分配。已分配后即使执行失败也消耗一次 Use，避免通过重试绕过
`maxUses`；Policy 预检失败不形成授权执行，也不能占用 Use Number。Receipt 与 Engineering Event 的双写
必须使用 Outbox/Recovery 合同，不能出现 Receipt 声称成功而项目结果不匹配。

同一 Policy 同时最多一个 Pending Use。Receipt 已写而 Ledger 尚未推进时，只能按完全一致的 Receipt Hash
确定性恢复；不得跳过 Pending、并发分配下一 Use 或重算历史 Receipt。失败 Receipt 可以如实记录
`externalEffectObserved=true` 或 `governanceMutationObserved=true`，但任何成功/no-op Receipt 必须保持二者
为 false。

成功/no-op Receipt 必须绑定已落盘的 Engineering Event ID/Hash。Runtime 先以 Policy ID/Hash、Use Number
和 Input Hash 构造幂等审计 Event，再写 Receipt；Event 写入失败时保留 Pending 且不得报告成功。重试只能
复用同一幂等 Event，不能新增重复事件。失败 Receipt 可在审计 Event 不可用时使用 `eventBinding=null`，但
必须保持失败状态并披露审计缺口。

新 Policy 的精确 Hash 若包含 `supersedesPolicyId`，其一次人工批准同时授权将旧 Policy 以
`superseding_policy_approval` 方式撤销；Runtime 必须先收回旧 Policy，再激活新 Policy。激活新 Policy
失败时旧 Policy 仍保持撤销，遵循 fail-closed。

任何 Project mismatch、Policy Hash mismatch、Expiry、Use limit、Source conflict、Schema mismatch、
Protected path touch、未知 Producer/Command、Validation failure、Tamper/Fork 或 External effect 都必须
fail closed。若 Event/Receipt 审计尚未可靠落盘，不得把有正式状态影响的执行报告为完整成功。

## 13. V0.5 实现边界

Approval Policy Core 已实现：Prepare、exact-hash write-once Approval、Materialization、Activation、Use Ledger、
单 Pending Use、Execution Receipt/Event Binding、显式撤销、superseding 撤销、Store Validator 与确定性恢复。
失败执行消耗 Use；Event 已落盘但 Ledger 未推进时只能从精确 durable Receipt 恢复。
Store Validator 只在 `valid=true` 且 `recoveryRequired=false` 时返回成功退出码；Pending 或恢复需求不能被
自动化流程误判为可继续执行。

Core 可供 Operation Adapter 调用，但这不等于 allowlist 中所有 Operation 已自动化。当前普通 Studio
Proposal/Apply 使用自身一次性 Proposal Approval 与 Governance Outbox；它不会伪装成 Delegated Policy。
V0.5.1 把 `event_head.recover` 接成首个 Operation Adapter；V0.6 WP6 把 `verified_delivery.promote` 接成
第二个 Adapter。两者本地故障注入已通过，公开发布前仍须在最终 Commit 上通过远程 CI。它们不单独公开发布；
其他 Adapter 仍须逐个闭合。Receipt、Continuous Observation、Validation Manifest、Engineering Event Record、Renderer
和 Freshness 仍需逐个 Adapter 通过对应 fail-closed 测试后才能免逐次批准。不得仅修改提示词、跳过 Approval
字段或直接调用 `begin_execution` 来宣称完成授权执行。
