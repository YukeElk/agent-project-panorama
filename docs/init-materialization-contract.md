# INIT Materialization Contract

本契约定义 V0.1.4 的 INIT Approval Binding。Materializer 只映射已经预校验、按精确 Hash 显式批准的完整 Preview，不执行架构理解、LLM 推理或批准后语义修正。

## 1. Boundary

固定工作流：

```text
BUILD COMPLETE DRAFT
→ PREPARE / PREVALIDATE
→ HASH THE EXACT PREVIEW
→ DISPLAY PREVIEW + EXACT HASH
→ STOP
→ EXPLICIT USER HASH CONFIRMATION
→ RECORD APPROVAL ONCE
→ MATERIALIZE PREPARED + APPROVAL
→ VALIDATE / RENDER
```

`PREPARE` 前必须完成 Panorama Data、Evidence Inventory、Review Subjects、Semantic Change Intents、Guidance、Summary、Source Snapshot 与所有必填语义。PREPARE 失败不得产生 prepared artifact；批准后不得改变 Preview。任何修改都必须重新 PREPARE、展示新 Hash、停止并重新批准。

## 2. Draft and Prepared Artifact

输入草稿格式：

```json
{
  "draftVersion": "draft-init-model.v0.1",
  "preview": {}
}
```

`preview` 至少包含 `generatedAt`、`sourceSnapshot`、`panoramaData`、`reviewSubjectRefs`、`changeIntents`、`evidenceInventory`、`guidance`、`updateSummaryItems`、`changeLevel`、`reviewSummary` 与 `reviewDecisionIds`。

PREPARE 运行 Schema、跨实体引用、风险规则和派生 Review/Change 的枚举及引用预校验。全部通过后才计算 canonical JSON SHA-256，并生成：

```json
{
  "preparedVersion": "prepared-init-review.v0.1",
  "preparedAt": "T-PREPARE",
  "preview": {},
  "previewHash": "64 lowercase hex",
  "sourceSnapshot": {},
  "validation": {},
  "approvalState": "awaiting_user_approval"
}
```

机器生成的 Review Markdown 必须展示 Project、Stage、Current Focus、Current/Target Architecture、Transitions、Modules、Decisions、Risks、Verification/Runtime、Attention Candidates 与 Next Focus，并以以下绑定信息结束：

```text
Approval Binding
Preview SHA-256: <HASH>
Prepared Artifact: prepared-init-review.json
Status: Awaiting explicit hash approval
```

## 3. Explicit Approval Artifact

普通“批准”“继续”或对旧 Preview 的批准不足以授权物化。有效动作必须明确确认准备制品显示的精确 Hash，例如：

```text
批准 INIT Preview <HASH>
```

批准记录器重新计算 Preview Hash，验证 `recomputed == prepared.previewHash == user-confirmed hash`，并一次性写入独立制品：

```json
{
  "approvalVersion": "init-approval.v0.1",
  "status": "approved",
  "previewHash": "same 64 lowercase hex",
  "approvedBy": "real user identity",
  "approvalRecordedAt": "T2",
  "approvalMethod": "explicit_hash_confirmation",
  "approvalTimeSource": "approval_recorder_clock"
}
```

`approvalRecordedAt` 只能来自 recorder clock，不从草稿或 Preview 复制，且不得早于 `preparedAt`。Approval 文件 write-once：存在时拒绝覆盖。缺少独立 Approval 文件时 Materializer 必须失败。

## 4. Source Snapshot Binding

Prepared 顶层 Snapshot、Preview Snapshot 与物化时实际 Snapshot 必须完全一致。Git Snapshot 使用 tracked + 非忽略 untracked 文件，并排除 Panorama-owned artifacts：`.panorama-work/`、`.*.panorama.lock`、`*.backup-*.html` 与 `*.local.html`。真实 HEAD 或项目源文件变化时停止，重新生成 Draft/PREPARE/Approval。

## 5. Materialization Sequence

```text
VERIFY PREPARED VERSION / STATE
→ RECOMPUTE PREVIEW HASH
→ ASSERT RECOMPUTED = PREPARED = APPROVAL HASH
→ VERIFY EXPLICIT APPROVAL METHOD / RECORDER TIME
→ VERIFY SOURCE SNAPSHOT
→ ASSERT RELEASE SEMANTICS
→ COPY APPROVED PANORAMA DATA + GUIDANCE
→ CAPTURE APPROVED SEMANTIC PROJECTION
→ CREATE INITIAL REVIEW + SEMANTIC CHANGES
→ BIND REVIEW TO SUBJECTS
→ CREATE PROVISIONAL INITIAL UPDATEBATCH
→ RUN VALIDATOR / RECONCILE FINAL FINDINGS（最多 2 次稳定化）
→ ASSERT SEMANTIC PROJECTION UNCHANGED
→ ATOMIC JSON WRITE
```

同一 Prepared Artifact、Approval Artifact 与 Source Snapshot 必须生成相同 Data、ID、Review、Change、UpdateBatch 和 Attention。

## 6. Approval Time and Review Binding

Initial Review 的 `requestedAt` 使用 Preview `generatedAt=T1`；`reviewedAt` 使用 Approval Recorder 的 `approvalRecordedAt=T2`。`extensions.approvalBinding` 保存：

- `approvedHash`；
- `previewHash`；
- `sourceSnapshot`；
- `approvedBy`；
- `approvalRecordedAt`；
- `approvalMethod=explicit_hash_confirmation`；
- `approvalTimeSource=approval_recorder_clock`。

Initial Change 的 `occurredAt` 与 Initial UpdateBatch 的 `createdAt/periodEnd` 同样使用 T2。

## 7. Semantic Immutability

Materializer 允许增加的只有派生内容：Review、Change、UpdateBatch、Revision/latest-update metadata、Review 绑定、Finding Reconciliation/Attention、Evidence Provenance 与 `extensions.initMaterialization`。对这些字段做投影排除后，最终数据必须与批准 Preview 的有效 Panorama Data + Guidance 完全相等；否则失败。

Materializer不得改变 Requirement、Module、Current/Target/Transition、Decision、Risk、Release、Deployment、Runtime、Resource、Access 或 Guidance 语义。

## 8. Candidate Release

只有 `status=deployed` 的 Release 可以成为 `project.currentReleaseId`。planned、development、candidate 或 retired Release 可以作为实体存在，但若草稿把它写成 current：

- PREPARE 必须失败；
- Materializer 必须再次断言并失败；
- 不得改为 `null`、不得记录“normalized”、不得静默修复。

修正草稿后必须重新 PREPARE 和批准。

## 9. Finding Reconciliation and Attention

只有实际 Validator Finding 才能进入 Reconciliation。分类仍为 `PROJECT_GAP`、`PANORAMA_EVIDENCE_GAP`、`PANORAMA_MODEL_GAP`、`CONTROL_BLOCKER`、`INFORMATIONAL`。写入 Reconciliation/Attention 后重新验证，最多两次稳定化；Final Findings、stored reconciliation 与 actionable High/Critical Attention 必须一致。

Guidance 必须原样深拷贝，不重新生成、不排序、不改变推荐项。

## 10. CLI Workflow

获取 Source Snapshot并据此构造完整 Draft：

```powershell
python scripts/prepare_init_review.py `
  --project-root path/to/project `
  --print-source-snapshot
```

PREPARE、预校验、冻结并展示精确 Hash：

```powershell
python scripts/prepare_init_review.py .panorama-work/draft-init-model.json `
  --project-root path/to/project `
  --output .panorama-work/prepared-init-review.json `
  --preview-markdown .panorama-work/prepared-init-review.md
```

此时停止。用户显式确认准确 Hash 后，一次性记录批准：

```powershell
python scripts/record_init_approval.py .panorama-work/prepared-init-review.json `
  --approved-hash <EXACT-HASH> `
  --approved-by <USER-IDENTITY> `
  --output .panorama-work/init-approval.json
```

使用两个独立制品物化：

```powershell
python scripts/materialize_init.py .panorama-work/prepared-init-review.json `
  --approval .panorama-work/init-approval.json `
  --project-root path/to/project `
  --output .panorama-work/initial-panorama-data.json
```

输出存在时拒绝覆盖。成功后才使用 `scripts/init_panorama.py` 生成 Single HTML。

## 11. Invariants

- Data Schema 保持 0.1，Renderer 保持 0.1.2，Semantic Modeling Protocol 不变；
- 普通 Update 继续只修改 `project-panorama-data`；
- Approval Hash、Source Snapshot、Approval Method 与 recorder time 不可绕过；
- Evidence Inventory 不持久化 Secret 或正文；
- Materializer 不执行网络、安装、构建或外部系统访问；
- 失败不写正式 Panorama Data，不覆盖已有输出。
