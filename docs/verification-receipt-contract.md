# Verification Receipt Contract V0.1

## 1. 定位

Verification Receipt 是外部测试、Trial、Eval 或 CI 结果的脱敏导入格式，不是测试运行器、沙箱、
Agent 调度器或自动选优系统。功能默认标记为 experimental，且不新增 Panorama 主视图。

## 2. 信任边界

- Receipt 内容全部视为外部声明；`passed` 只描述该 Receipt 的 Verification，不等于 Panorama Gate passed。
- External Finding 不是 Panorama Formal Finding，不能使用或伪造 Validator Finding Code。
- `enforced / not_enforced / not_executed / unknown` 必须原样保存；缺少隔离证据时不能提升为 enforced。
- Receipt 不得包含测试日志正文、项目源码、环境变量、请求头、Cookie、凭据或其他 Secret value。
- Redaction manifest 只说明生产方声明删除的字段，不证明所有敏感信息已经移除；Adapter 仍执行本地扫描。

## 3. 固定预检与两种 Apply 路径

```text
Schema Validate
→ Redaction Validate
→ Receipt Hash Validate
→ Project / Revision / Data / Source Binding Check
→ Evidence Path 与 Mapping ID Check
→ Mapping Preview
→ Panorama Formal Validator
```

以上步骤不会修改 Panorama。预检通过后只能进入下列一条路径：

```text
人工路径（Schema 0.1/0.2）
→ Proposal → 精确 Hash 人工批准 → write-once Approval → Apply

策略路径（仅 Schema 0.2）
→ Approval Policy Validate → Receipt-specific Policy Transaction
→ Execution Receipt / Engineering Event → Apply Result Validate
```

策略路径要求独立 `verification_receipt.import` Policy，且只允许 Evidence Append。它不能向普通 Proposal
注入伪造的用户 Approval，也不能绕过 Panorama Formal Validator。当前 V0.4 Adapter 尚未实现该路径，
在 Policy Runtime 落地前继续使用人工路径。

## 4. 输入限制

- 格式固定为 `panorama-verification-receipt.v0.1`，并通过
  `schema/verification-receipt.schema.v0.1.json`。
- JSON 上限 1 MiB、最大嵌套 20 层；Verification/Evidence/Finding 数量受 Schema 限制。
- `producer.receiptHash` 为排除自身字段后的 canonical JSON SHA-256，任何语义变化都要求重新计算。
- `projectBinding` 必须匹配 Project ID、Panorama Revision 与 Data Hash；已有正式 V0.2 Source Binding 时，
  Git HEAD 和 Source Snapshot 也必须一致。
- `relative_path` 必须位于 project root 内，拒绝 `..`、绝对路径、符号链接和 reparse 逃逸；URL 仅允许
  不含凭据的 HTTPS；`inline_note` 仅允许 `receipt:` 或 `urn:` 标识。
- Evidence 与 Finding 只能引用 Receipt 内存在的 Evidence ID；Gate/Acceptance ID 必须在 Panorama 存在。

## 5. 映射合同

Adapter 为每个新 Receipt Hash 生成稳定 `Reference(test_report)`，并只把该 Reference ID 追加到显式
映射的 Gate/Acceptance `evidenceReferenceIds`。它不得修改：

- `Gate.status`、`lastRunAt`、`resultSummary`；
- `Acceptance.status`、`verificationStatus`；
- Review、Approval、Waiver、Decision、Guidance 或 Target；
- 项目源代码、测试配置或运行环境。

Schema 0.2 同时生成 `authority=declared` 的 Fact Provenance 和 Receipt-owned Observation Batch；
Schema 0.1 没有这些字段，只保存 Reference extension 与 Evidence mapping。Observation Batch 的 Findings
保持空数组，External Finding 只存在 Receipt extension 中。

## 6. 去重与时间线

- 相同 Receipt Hash 为同一收据，重复导入返回 `duplicate` 且不产生新操作。
- Receipt ID 相同但 Hash 不同表示不同语义版本，允许并列显示，不静默覆盖历史。
- Gate/Acceptance Drawer 按 Receipt Hash 去重，按 producer `generatedAt` 倒序显示 Verification、Evidence、
  External Finding、Isolation、Redaction 和截断声明。

## 7. Bridge

- `POST /api/v1/verification-receipts/preview` 只接受 `{receipt}`，执行全部预览门禁并保存不可变 Preview。
- `POST /api/v1/verification-receipts/proposals` 只接受 `{receiptHash}`，重新检查 Panorama 与 Source，生成
  普通 Proposal wrapper。
- Approval 要求精确输入 `批准 Receipt Proposal <64位Hash>`；Apply 前再次检查 Receipt Preview、
  Operations Hash、Revision、Data Hash、Git/Snapshot、Source Content Digest 和 Presentation Hash。
- 后续 Policy Adapter 只能对 Schema 0.2、受信 Producer、完整 Binding 且无 Conflict 的 Receipt 提供
  policy apply；Policy ID/Hash、Use Count、输入/结果 Hash 和 Execution Receipt 必须可见。Schema 0.1、
  未识别 Producer、旧/漂移 Source 或任何治理状态修改继续进入人工路径。
- Bridge 继续只绑定随机 `127.0.0.1`、使用 capability/Origin/CSRF/no-store；Receipt API 不接受路径、
  executable、argv 或测试命令。

## 8. CLI

```powershell
python scripts/import_verification_receipt.py project-panorama.local.html `
  verification-receipt.json `
  --project-root path/to/project `
  --output .panorama-work/verification-receipt-preview.json `
  --proposal-output .panorama-work/pending-receipt-update.json
```

没有 `--proposal-output` 时只生成 Preview。输出已存在时拒绝覆盖；任何失败都不修改输入 Panorama。
