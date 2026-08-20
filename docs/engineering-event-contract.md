# Engineering Event Fabric Contract v0.1

状态：Frozen；Event Core、Approval Policy Core 与 Proposal/Apply Governance Outbox 已实现

批准范围：2026-08-20 用户批准“先进行设计闭合然后再开始 0.5 的迭代”

兼容基线：Panorama Schema `0.1/0.2`、Renderer `0.4.0`

对应 Finding：SF-28～SF-37，复用 SF-24～SF-27

## 1. 目的与边界

Engineering Event Fabric 是 Project-local、Sidecar-first、append-oriented 的工程事件层。它记录
Panorama 正式状态如何被观察、提出、批准、应用、验证或失效，但不替代 Panorama Core，也不成为
Agent 平台、任务队列、训练平台或新的治理权威。

本合同中的 `MUST / MUST NOT / SHOULD` 是 V0.5 Foundation 的规范要求。

- Event Store MUST 位于项目自己的 `.panorama-work/event-store/v0.1/`；
- Event 写入 MUST NOT 修改正式 Panorama JSON、Presentation、业务源码或治理状态；
- Event MUST 只保存有界工程元数据，不保存源码、日志正文、Prompt、模型回答正文、隐藏推理或 Secret；
- Observation、Receipt、Agent Review 和 Renderer Projection MUST NOT 制造 Approval、Acceptance、Waiver、
  Gate Passed、Formal Finding 或 Deployment Active；
- V0.5 Foundation 不执行测试、不启动 Runtime、不访问网络、不安装依赖、不训练模型、不托管向量库。

## 2. 唯一 Event Envelope

正式格式为 `panorama-engineering-event.v0.1`。实验中出现的 `recordedDate`、`capturedAt`、
`timestampPrecision` 和 `trainingEligibility` 不进入正式 v0.1。

### 2.1 时间

- `recordedAt`：Recorder clock 写入的 UTC RFC 3339 时间，必需；
- `occurredAt`：来源明确提供的发生时间；没有证据时为 `null`；
- `timePrecision`：`millisecond | second | minute | date | inferred | unknown`；
- `recordedAt` MUST NOT 被当作缺失的 `occurredAt`；
- `inferred` 只表示来源明确提供了可解释的推断时间，mtime 不能单独提升为 occurred time。

### 2.2 身份、幂等与完整性

调用方提供 64 位小写 SHA-256 `idempotencyKey`，它标识一个来源事务或一次有界捕获。Recorder 计算：

```text
eventId = "EVT-" + upper(first_32_hex(sha256({projectId,eventType,idempotencyKey})))
requestHash = sha256(canonical_json(event_request))
eventHash = sha256(canonical_json(event_without_integrity.eventHash))
```

- Canonical JSON 使用 UTF-8、对象键排序、无非必要空白；数组顺序保留；
- `eventId` 是幂等定位，不替代 `eventHash`；
- 同一 `eventId + requestHash` 再次写入 MUST 返回现有 Event，不新增 Sequence；
- 同一 `eventId` 对应不同 `requestHash` MUST fail closed，不能覆盖、合并或静默重命名；
- `eventHash` 覆盖 Event ID、时间、Sequence、Previous Hash、Binding、Payload 和所有 Extensions。

### 2.3 Stream

每个 Project 使用一个逻辑 Stream 和同一独占写锁：

```text
.panorama-work/event-store/v0.1/
├── events/<event-id>.json
├── checkpoints/
├── redactions/
├── stream-head.json
└── .event-store.lock
```

- Sequence 从 1 开始且连续；
- 第一条 Event 的 `previousEventHash` 为 `null`；
- 后续 Event 必须精确绑定上一条 Event Hash；
- `stream-head.json` 是可重建索引，不是事实源；Event 文件和 Hash Chain 才是权威记录；
- Head 缺失、落后或漂移时，普通写入 MUST 停止并要求确定性恢复；不得跳过 Sequence 或自动忽略孤儿 Event；
- 只有完整 Chain 有效、Sequence 连续、Project/Stream/Epoch 一致、Tail 唯一且 Head 仅为 missing/behind 时，
  才能由已批准的 `event_head.recover` Policy 自动重建派生 Head；没有 Policy 时仍要求显式用户请求；
- Head ahead、Fork、Hash mismatch、重复 Sequence、Schema invalid、Project mismatch 或 Epoch/Redaction
  歧义不得自动恢复；
- Writer 使用同目录临时文件、flush/fsync 和 `os.replace` 原子发布单个文件。

### 2.4 Authority、Outcome 与信息缺口

`authority` 复用 Panorama 的 `observed | declared | inferred | unknown | conflict`；`confidence` 保持
`high | medium | low | unknown`，不得换算为百分比。

`outcome.status` 使用 `succeeded | failed | rejected | no_op | unknown`；`labelStrength` 使用
`unlabeled | reported | observed | formally_validated | user_approved | unknown`。

- `formally_validated` 只表示对应 Validator/Receipt 事实获得验证，不意味着 Gate Passed；
- `user_approved` 必须绑定真实 Approval Event/Evidence，不得由 Agent 推断；
- 缺少模型、token、reward、logprobs、runtime span 或失败归因时不新增伪字段，使用
  `informationGaps[]` 明确记录；
- RAG/Eval/SFT/RL 资格不是 Event 永久事实，由后续 Export Policy 按 `asOf`、许可、split、隐私和人工审阅计算。

## 3. Event Request

Recorder 接受 `panorama-engineering-event-request.v0.1`，它包含 Envelope 的来源字段，但不允许调用方提供：

- `eventId`、`recordedAt`、`stream.sequence`、`previousEventHash`；
- `requestHash`、`eventHash`；
- 任何 `trainingEligibility`；
- Secret、凭据字段、本机绝对路径、`..` 路径逃逸或未授权 Project Content。

Recorder MUST 在加锁前完成大小、JSON 深度和 Secret/路径预检查，并在加锁后重新验证 Stream 与幂等状态。
单个 Request/Event 上限为 1 MiB，最大 JSON 深度为 20。

## 4. Project 与 Source Binding

`projectBinding` 保存 Project ID，以及可用时的 Panorama Schema、Base/Result Revision 和 Data Hash。
缺少绑定必须使用 `null`，不得伪造零值或复制旧 Hash。

`sourceBinding.mode`：

- `git`：必须保存精确 `gitHead`；
- `content_digest`：必须保存 `sourceContentDigest` 和 `coverage=complete|partial`；
- `not_applicable`：该事件不涉及 Source；
- `unknown`：来源状态无法可靠绑定。

只有 `gitHead` 与当前受控比较一致，或完整 Source Content Digest 精确一致时，Projection 才能把来源称为
`current/match`。离线 Event 只能说明 recorded binding，不能证明当前 Source 仍一致。

## 5. Evidence Binding

Event 的 `evidenceBindings[]` 只保存定位元数据：稳定 Evidence ID、类型、引用、可选 revision/digest、
Access Class 与 Freshness。它 MUST NOT 内嵌 Evidence 正文。

- `relative_path` 必须是 Project-relative POSIX 路径，不允许绝对路径、盘符、UNC、`..` 或符号链接逃逸；
- `json_pointer` 必须以 `/` 开始；
- `reference_id`、`receipt_id`、`event_id` 必须保存准确 ID，不按显示名称匹配；
- URL 不进入 Event v0.1；URL 继续由正式 Reference 和协议白名单治理。

## 6. Transactional Outbox

Outbox 格式为 `panorama-engineering-event-outbox.v0.1`，生命周期固定为：

```text
pending → finalized
        ↘ abandoned
        ↘ conflict
```

### 6.1 Prepare

Adapter 在原事务锁内先写 `pending` Outbox，绑定：

- Transaction ID、Operation Class、Policy；
- Project、Base Revision/Data Hash；
- Expected Result Revision/Data Hash；
- Event Request Hash；
- 删除顶层 `integrity` 后对完整 Outbox 状态计算的 canonical SHA-256 `stateHash`。

Outbox 尚未绑定结果时不得生成 `succeeded` Event。`status`、`updatedAt`、Observed Result、Resolution、
Event Request Binding 和 Extensions 全部受 `stateHash` 保护；任何未由 Runtime 重算并原子发布 Hash 的
终态修改都必须被 Store Validator 识别为篡改。

### 6.2 Apply 与 Finalize

Adapter 执行原有事务后重新读取真实结果：

- observed result = expected result：记录 Event，并把 Outbox 原子改为 `finalized`；
- observed result = base result：原事务未生效，Outbox 改为 `abandoned`；
- 其他结果：Outbox 改为 `conflict`，停止，不猜测、不 rebase。

每次合法转换都必须在写入前重算 `stateHash`。`finalized` 还必须验证实际 durable Event 的 Event Hash 与
Request Hash；仅伪造一个 Hash 一致的终态不能替代真实状态对账与 Event 绑定。

### 6.3 Policy

- `governance/fail_closed`：Event 未 finalized 时不得向调用方报告完整成功；项目进入
  `recovery_required`，后续治理写入必须停止；
- `observation/compensatable`：允许正式 Current 已更新而事件稍后补偿，但调用结果必须披露 pending；
- 两种 Policy 都不得生成与真实结果不一致的成功 Event，也不得自动回滚无法安全逆转的事实。

Proposal/Apply Adapter 已在原 Panorama Writer Lock 内接入本状态机。启用 `apply_patch.py --event-store`
时，Adapter 在正式写入前发布 `pending`，写入后按精确 Base/Expected/Third State 解析为
`abandoned/finalized/conflict`。Event 或 Outbox finalize 失败时保留 `pending` 并阻断后续治理写；恢复只能
通过 `validate_governance_outbox.py --recover` 显式执行。Observation、Receipt、Studio Session 与 INIT
Capture Adapter 仍未接入。

## 7. Retention、Redaction 与 Stream Epoch

Retention Policy 是 Project-local 显式制品，默认：

- `containsSecret=false`；
- `containsProjectContent=false`；
- 没有显式期限时 `retentionDays=null`，表示未设置，不表示永久合法保存；
- Event Export 必须再次执行 Secret、绝对路径、URL、记录数、嵌套和大小检查。

合法删除或 Secret 清除按以下顺序：

1. 记录 write-once Authorization Reference；
2. 生成 `panorama-event-redaction-manifest.v0.1`，绑定受影响 Event ID、旧 Epoch Head 和处置方式；
3. 真正删除、脱敏或密钥销毁目标载荷；
4. 结束旧 Stream Epoch；
5. 以新 Genesis Event 绑定 Redaction Manifest 和旧 Head，不重算旧链。

Redaction Manifest 不能保存被删除的敏感值。没有授权或 Manifest 时，Validator 必须把缺失/改变的旧 Event
报告为 tamper/corruption，而不是合法删除。

## 8. Adapter 与 Transformation Loss

所有 Raw Trajectory、ATIF、MLflow、OTel、Structurizr/LikeC4 或语言 Extractor 转换都必须生成
`panorama-transformation-loss-report.v0.1`。

Loss Report 至少记录：

- Adapter ID/Version、输入/输出 Hash 和 Source Binding；
- 输入、输出、保留、丢失、降级、Unknown 的数量；
- 每项 Loss 的稳定 kind、sourceRef、severity、reason 和 preservedAs；
- element、relationship、view、order、timestamp、evidence、layout、document/ADR 的保留状态；
- `status=lossless|partial|blocked`。

`partial` 不能宣传为无损导入；`blocked` 不得发布正式 View/Event 投影。Loss Report 是转换证明，不是
模型准确率、人工验收或 Governance Approval。

## 9. Validator

Event Store Validator MUST 检查：

- 所有 Event 通过 Draft 2020-12 Schema 与 Format 校验；
- 文件名 = Event ID，ID/Request Hash/Event Hash 可重算；
- Sequence 从 1 连续、Event ID/Sequence 唯一、Previous Hash 精确；
- Stream/Project 一致，Head 与最后 Event 精确一致；
- Secret/敏感键、绝对路径、路径逃逸、超限和不允许的字段不存在；
- Unknown、not observed 和 partial 不被提升；
- 缺少合法 Redaction Manifest 的链断裂是 Error。

CLI 退出码：`0` 有效，`1` 工程数据无效，`2` 输入、依赖或运行失败。

## 10. V0.5 Foundation 退出条件

首个切片只有同时满足以下条件才可进入 Adapter 工作：

1. 所有新增 Schema meta-valid；
2. Event 创建、幂等、同键冲突、Sequence、Hash Chain、Head、篡改检测通过；
3. 锁竞争、Event 已写但 Head 未更新的恢复路径有确定性测试；
4. Secret、绝对路径、路径逃逸、超限和 `trainingEligibility` 被拒绝；
5. V0.1/V0.2/V0.4 全部既有测试继续通过；
6. Runtime Preflight 不访问网络、不安装依赖、不读取 Secret；
7. SKILL.md 只增加必要路由，详细合同继续保留在本文件；
8. Proposal/Apply Outbox 的 finalized/abandoned/conflict/recovery-required 故障路径通过；未实现的其他
   Capture Adapter、Redaction 执行器、Projection、Renderer 和 Export 明确保持未完成。
