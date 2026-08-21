# Event Head Recovery Policy Adapter 合同

状态：V0.5.1 Internal Milestone；已完成本地验证，公开发布延迟

## 1. 目的与边界

`event_head.recover` 是首个真实接入 Approval Policy Core 的 Delegated Operation Adapter。它只把完整、唯一、
Hash 有效的 Engineering Event Chain Tail 重建为派生 `stream-head.json`，不修改任何已有 Event、Panorama Core、
Redaction Manifest、Epoch、业务源码或治理语义。

只读 Inspect、Preview、Validate 不分配 Policy Use。Head 已为 `current` 时返回
`no_recovery_needed`，不创建 Adapter Transaction，也不消耗 Use。

## 2. 固定项目路径

Adapter 只接受 Project Root、正式 Panorama 路径和已激活 Policy ID，并自行派生以下路径：

```text
<project-root>/.panorama-work/event-store/v0.1
<project-root>/.panorama-work/approval-policy-store/v0.1
<project-root>/.panorama-work/approval-policy-operations/v0.1/event-head-recover
```

正式 Panorama 与两个 Store 必须位于同一 Project Root；绝对路径逃逸、Project 外路径和任一既有符号链接组件
均 fail closed。调用方不能传入替代 Event/Policy Store 来放宽 Binding。

## 3. 可恢复状态

只有以下状态可执行：

- `head_missing`：Chain 完整且有唯一 Tail，但 `stream-head.json` 缺失；
- `head_behind`：Head 精确绑定同一 Project/Stream/Epoch Chain 中的较早 Event。

`head_ahead`、`head_mismatch`、`head_invalid`、`chain_invalid`、`ambiguous`、`uninitialized`、Fork、重复或
不连续 Sequence、Hash/Schema/Project/Stream/Epoch 错配、Redaction Epoch 歧义全部停止。Adapter 不执行
Event 修复、自动 rebase、Epoch 推断或静默降级。

## 4. 精确 Policy Profile

Adapter 只接受：

- `operation=event_head.recover`；
- `effectClass=deterministic_recovery`；
- 正式 Panorama Project ID 与 Schema Version 精确落在 Policy Binding 内；
- `sourceBindingRule=not_applicable`；
- Panorama Path、Forbidden Path、Producer、Command 均为空；
- `artifactRoots` 精确为 `.panorama-work/event-store/v0.1`；
- `receiptRules=null`、`deliveryRules=null`；
- Recovery Rules 的 Schema/Hash/Sequence/Unique Tail/Project-Stream-Epoch 校验全为 `true`，
  `eventMutationAllowed=false`；
- 当前 missing/behind 状态出现在 `allowedHeadStates`；
- 所有 Stop Conditions 为 `true`，Audit 使用 `failureMode=fail_closed`。

Policy 创建、修改、续期、替换、撤销仍要求精确 Hash 的人工批准。一次已批准 Policy 范围内的确定性恢复不再
逐次请求人工确认。

## 5. Input、Output 与事务 Binding

`inputHash` 是以下 canonical JSON 的 SHA-256：Operation、Policy ID/Hash、正式 Panorama Project/Schema/
Revision/Data Hash、固定 Store Ref、Preview Head State、Before Head、唯一 Expected Tail 和 Recovery Rules。

`transactionId` 从 Policy ID、Policy Hash 与 Input Hash 确定性派生。事务默认保存为：

```text
.panorama-work/approval-policy-operations/v0.1/event-head-recover/EHR-<32HEX>.json
```

事务使用 `panorama-event-head-recovery-transaction.v0.1`，状态为：

```text
prepared → allocated → effect_observed → finalized
                         └──────────────→ failed | conflict
```

每次状态发布都计算 `stateHash`。`outputHash` 绑定恢复后、Audit Event 写入前的精确 Recovered Head；最终 Audit
Event 追加后的 Head 由 `finalHead` 保存，Audit Event 则由 Execution Receipt 的 `eventBinding` 精确绑定。

## 6. 执行与 Lock Order

执行顺序固定为：

```text
Preview + exact Input Hash
→ durable prepared Transaction
→ begin_execution 分配 Use
→ durable allocated Binding
→ Event Store Lock 内重验 Before/State/Tail CAS 并恢复 Head
→ durable effect_observed Result
→ complete_execution 写幂等 Audit Event、Receipt、Ledger
→ Event/Policy Store Validate
→ finalized Transaction
```

Adapter 不在持有 Event Store Lock 时进入 Approval Policy Runtime。`complete_execution` 使用 Policy Lock 后写
Audit Event；禁止建立 Event Lock → Policy Lock 的反向嵌套。

## 7. Resume

- `prepared` 且无 Pending：重新验证原 Preview 后分配一次 Use；
- `allocated` 且 Head 仍精确等于原 Before：执行一次恢复；
- `allocated` 且 Head 精确等于原 Expected Tail：证明目标效果已发生，补持久化 Result；
- `effect_observed`：复用原 Use、Input/Output Hash 与幂等 Audit Event 完成 Core；
- Audit Event 已 durable、其 Head 发布失败且 Chain 精确为 `head_behind`：只把 Head 推进到该唯一 Audit Tail，
  再复用同一幂等 Event；
- durable Receipt 已存在而 Ledger 仍 Pending：只调用 Core `recover_pending()`；
- Core Receipt/Ledger 已完成而 Transaction 未 finalized：精确对账后 finalize；
- 任意第三状态、Policy/Project/Input/Output/Receipt/Event/State Hash 不匹配：`conflict` 或停止转人工，不 rebase。

失败若能证明仍在副作用前，则以 failed Execution Receipt 消耗已分配 Use；无法证明是否产生效果时保留
recovery-required，而不是生成猜测性失败或成功。

## 8. CLI

```powershell
python scripts/recover_event_head_with_policy.py preview `
  --project-root path/to/project --panorama path/to/panorama.json `
  --policy-id POLICY-ID --json

python scripts/recover_event_head_with_policy.py execute `
  --project-root path/to/project --panorama path/to/panorama.json `
  --policy-id POLICY-ID --json

python scripts/recover_event_head_with_policy.py resume `
  --project-root path/to/project --panorama path/to/panorama.json `
  --policy-id POLICY-ID --transaction-id EHR-... --json

python scripts/recover_event_head_with_policy.py validate `
  --project-root path/to/project --json
```

CLI 默认只显示状态、稳定代码、事务 ID 和脱敏摘要；`--json` 输出机器可读状态。它不创建或批准 Policy，
不执行网络访问、依赖安装、外部发布或业务 Git 写入。

## 9. 未包含能力

V0.5.1 不接入 `engineering_event.record` 或其他 Delegated Operation。当前 Policy v0.1 没有精确约束 Event
Type、Actor/Producer、Authority、Label Strength、Subject 与 Access Class，不能把“允许追加 Event”解释成
“允许声明任意工程事实”。RAG、Eval、Dataset Export、后训练、View IR、Renderer 改版与 cat-sys Backfill
继续属于后续版本。
