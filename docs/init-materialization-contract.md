# INIT Materialization Contract

本契约定义 V0.1.4 如何把一个已批准的 INIT Preview 确定性物化为首版 Managed Panorama Data。Materializer 只映射已经批准的语义模型，不进行架构理解或 LLM 推理。

## 目录

1. [Boundary](#1-boundary)
2. [Approved INIT Model](#2-approved-init-model)
3. [Approval and Source Binding](#3-approval-and-source-binding)
4. [Materialization Sequence](#4-materialization-sequence)
5. [Initial Review and Changes](#5-initial-review-and-changes)
6. [Finding Reconciliation](#6-finding-reconciliation)
7. [CONTROL Attention](#7-control-attention)
8. [Guidance](#8-guidance)
9. [Candidate Release](#9-candidate-release)
10. [CLI Workflow](#10-cli-workflow)
11. [Invariants](#11-invariants)

## 1. Boundary

输入语义由 Semantic Agent 与用户批准的 Preview 决定。Materializer 只能：

- 校验 Preview Hash 与批准绑定；
- 校验 Source Snapshot 未变化；
- 生成稳定 ID；
- 映射 Initial Review、Change、UpdateBatch；
- 运行 Validator；
- 确定性分类和聚类 Formal Finding；
- 复制批准的 Guidance；
- 写出 Schema-valid Panorama JSON。

Materializer 不得决定 Project Architecture、Module Boundary、Target Architecture、Requirement 或 Next Focus。

## 2. Approved INIT Model

内部格式名固定为：

```text
approved-init-model.v0.1
```

它是临时工作格式，不是新的 Panorama Schema。顶层结构：

```json
{
  "modelVersion": "approved-init-model.v0.1",
  "preview": {},
  "previewHash": "sha256 hex",
  "approval": {
    "status": "approved",
    "previewHash": "same sha256 hex",
    "approvedBy": "real user identity",
    "approvedAt": "T2"
  }
}
```

`preview` 至少包含：

- `generatedAt`（T1）；
- `sourceSnapshot`；
- `panoramaData`（revision 0，初始 Review/Change/UpdateBatch 为空）；
- `reviewSubjectRefs`；
- `changeIntents`；
- `evidenceInventory`；
- `guidance`（2–3 个已展示选项）；
- `updateSummaryItems`；
- `changeLevel`。

`changeIntents` 是语义级变更，不是一字段一 Change。每项必须已经包含 category、impactLevel、summary、reason、source、entityRefs、before/after summary、significant、referenceIds。

## 3. Approval and Source Binding

Preview Hash 是 canonical JSON SHA-256，覆盖完整 `preview`，包括 Panorama Data、Evidence、Finding 输入、Guidance 与 Source Snapshot。

批准必须同时满足：

- `approval.status=approved`；
- `approval.previewHash` 等于重新计算的 Preview Hash；
- `approvedBy` 非空并来自真实用户动作；
- `approvedAt=T2`，且不早于 Preview `generatedAt=T1`；
- 当前 Source Snapshot 与 Preview 中的 Snapshot 完全一致。

Git 项目 Snapshot 包含 HEAD、branch、dirty、状态条目数量、状态 Hash，以及不读取正文的 worktree path/size/mtime metadata Hash。非 Git 项目使用同样受限的 filesystem metadata snapshot。HEAD 或工作树状态变化时停止，重新生成 Preview 并重新批准。

Initial Review 的 `reviewedAt` 必须使用 T2，不得使用 T1。

## 4. Materialization Sequence

固定顺序：

```text
VERIFY MODEL VERSION
→ RECOMPUTE PREVIEW HASH
→ VERIFY APPROVAL
→ VERIFY SOURCE SNAPSHOT
→ COPY APPROVED PANORAMA DATA
→ COPY APPROVED GUIDANCE
→ CREATE INITIAL REVIEW
→ CREATE SEMANTIC CHANGES
→ BIND REVIEW TO SUBJECTS
→ NORMALIZE CANDIDATE RELEASE SEMANTICS
→ RUN VALIDATOR
→ CLASSIFY FORMAL FINDINGS
→ CLUSTER CONTROL ATTENTION
→ CREATE INITIAL UPDATEBATCH
→ SET REVISION / LATEST UPDATE
→ FINAL VALIDATE
→ ATOMIC JSON WRITE
```

相同 Approved Model 与相同 Source Snapshot 必须生成相同数据、ID、Review/Change/UpdateBatch 和 Attention。

## 5. Initial Review and Changes

Initial Review：

- ID：`REV-INIT-<preview-hash-prefix>`；
- type：`panorama_update`；
- status：`approved`；
- requestedAt：T1；
- reviewedBy / reviewedAt：真实批准动作中的用户与 T2；
- `extensions.approvalBinding` 保存 Preview Hash 与 Source Snapshot。

Initial Change：

- ID：`CHG-INIT-<preview-hash-prefix>-NN`；
- occurredAt：T2；
- reviewId：Initial Review；
- 内容严格来自批准的 `changeIntents`。

Initial UpdateBatch：

- ID：`UPD-INIT-<preview-hash-prefix>`；
- status：`applied`；
- revision：`0 → 1`；
- 关联 Initial Review、全部 Initial Change、Attention 与 Guidance option IDs；
- 成为 `meta.latestUpdateBatchId`，供 CONTROL “最近已评审更新”使用。

## 6. Finding Reconciliation

只有实际运行 Validator 得到的 Finding 才进入 Reconciliation。分类固定为：

- `PROJECT_GAP`：项目确实缺少验证、运行、验收或决策证据；
- `PANORAMA_EVIDENCE_GAP`：证据存在，但尚未映射进对应实体；
- `PANORAMA_MODEL_GAP`：Panorama 关系或模型表达不完整；
- `CONTROL_BLOCKER`：直接阻塞阶段、发布、部署或迁移；
- `INFORMATIONAL`：无需进入首屏关注。

`VERIFICATION_GAP` 必须检查 approved Evidence Inventory：存在相关 VERIFICATION Evidence 时分类为 `PANORAMA_EVIDENCE_GAP`；没有时分类为 `PROJECT_GAP`。

被明确标记 blocked 的 Transition Finding 分类为 `CONTROL_BLOCKER`。

完整分类结果保存在 Initial UpdateBatch `extensions.findingReconciliation`，不扩展 Schema。

## 7. CONTROL Attention

只有 High/Critical 且非 Informational 的 Formal Finding进入 Attention。Attention 以 code、业务优先级和 Finding Class 聚类，而不是逐条复制。

默认优先级：

1. Stage / Release blocker；
2. Architecture / Deployment / Transition；
3. Current Verification uncertainty；
4. Review / unresolved decision；
5. Resource / Runtime；
6. Reference / Evidence mapping。

例如，多个 Module 的同类 Verification Mapping Finding 聚类成一条 Attention，保留所有 `relatedEntities`；blocked Transition 保持独立 blocker。

一致性 Gate：存在 actionable High/Critical Finding 时，Initial UpdateBatch 的 `attentionItems` 不得为空。不得出现 Validator 有 High，而 CONTROL 显示没有 Attention。

## 8. Guidance

`preview.guidance` 必须是用户在 Preview 中看到的完整 Guidance，包含 Why now、Benefits、Risks、Impact、Prerequisites、Expected outcome、Recommendation 与 2–3 个选项。

Materializer 深拷贝该对象，不重新生成、不重新排序、不改变推荐项：

```text
Approved Preview Guidance = Initial Panorama Guidance
```

## 9. Candidate Release

只有 `status=deployed` 的 Release 可以成为 `project.currentReleaseId`。如果批准数据把 planned、development、candidate 或 retired Release 误设为 current，Materializer 确定性设置：

```json
{"project":{"currentReleaseId":null}}
```

Release 实体继续保留。被规范化的 ID 写入 UpdateBatch `extensions.candidateReleaseNormalizedFrom`，供审计使用。

## 10. CLI Workflow

获取 Source Snapshot：

```powershell
python scripts/materialize_init.py `
  --project-root path/to/project `
  --print-source-snapshot
```

计算 Preview Hash：

```powershell
python scripts/materialize_init.py draft-init-model.json --print-preview-hash
```

用户批准准确 Hash 后物化：

```powershell
python scripts/materialize_init.py approved-init-model.json `
  --project-root path/to/project `
  --output work/initial-panorama-data.json
```

输出存在时拒绝覆盖。成功后再使用 `scripts/init_panorama.py` 将 JSON 嵌入稳定 Renderer。

## 11. Invariants

- Schema 保持 0.1；
- Renderer 保持 0.1.2；
- Semantic Modeling Protocol 不在 Materializer 中重复实现；
- 普通 Update 继续只修改 `project-panorama-data`；
- Approval Hash、Source Snapshot 与 reviewedAt 真实性不可绕过；
- Evidence Inventory 只持久化路径、Access Class、Fact Class、Provenance、Hash、时间和 Entity 关系，不持久化 Secret 或正文；
- Materializer 不执行网络、安装、构建或外部系统访问；
- 失败不写正式 Panorama Data，不覆盖已有输出。
