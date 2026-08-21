# Event Checkpoint and Projection Contract v0.1

状态：V0.6 WP3/WP5 Implementation Candidate

对应 Finding：SF-41、SF-43、SF-48

## 1. Event Checkpoint

Event Checkpoint 是对现有 Engineering Event Store 的只读、可删除投影，不是第二套 Event Store。只有同时满足
以下条件时可以形成 Checkpoint：

- Event Store Validator 完整通过；
- Head 状态精确为 `current`，missing/behind 即使可恢复也不能隐式修复；
- Event Chain 非空，Project、Stream、Epoch、Sequence、Previous Hash 和 Tail 全部一致；
- Project ID 与 Panorama Core 精确匹配；
- 读取期间 Event 数量未变化。

Checkpoint Hash 覆盖 Project、Stream、Epoch、Tail Event ID/Hash、as-of Sequence 以及按序排列的全部
Event ID/Hash/Sequence。完整 Event 只作为已验证编译输入，不复制进 Model IR。

## 2. Sequence

通用 Event 只允许生成 `engineering_event_action`：Actor → Subject，`order` 来自 `stream.sequence`。它表示工程
动作，不是网络消息或 Runtime Call；protocol、async、return、retry 均保持 unknown/null。Subject ID 精确匹配
正式 Model Entity 时复用该实体，否则创建 Event-derived Subject Candidate。

## 3. Lifecycle

Lifecycle 只读取 Event `extensions.panoramaProjection.lifecycleTransitions[]`。每项必须严格包含：

```json
{
  "subjectType": "approval_policy",
  "subjectId": "POLICY-ID",
  "fromState": "allocated",
  "toState": "succeeded",
  "trigger": "verification_receipt.import"
}
```

Subject Type/ID 必须精确存在于同一 Event 的 `subjectRefs`；字段必须是有界非空字符串；未知字段拒绝。缺少显式
transition 时输出 `event_lifecycle_transition_not_provided`，不得从 outcome 或相邻 Event 猜状态。Governance
Proposal Apply 与 Approval Policy Execution Producer 已生成受约束 transition。

## 4. Evolution / Risk

Evolution Timeline 只使用 Event Hash Chain 的 previous → next `trace`。Event outcome 为 failed/rejected 时只对
对应 Event Node 使用风险强调；这不是 Formal Finding、根因或 blast radius。Correlation 过滤使用精确 ID。

## 5. 失败边界

Checkpoint/Event Hash 篡改、Head Drift、Project mismatch、无效 lifecycle extension、未知 Subject、重复过滤 ID
和 profile 参数串用全部 fail closed。编译不修改 Event Store、Panorama Core、Source 或审批状态。
