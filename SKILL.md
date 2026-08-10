---
name: agent-project-panorama
description: "操作 Agent Project Panorama V0.1–V0.1.4 Single HTML，理解陌生或已有项目、安全获取 Operational Evidence、输出并确定性物化 INIT Preview，并执行 INSPECT、PROPOSE UPDATE、APPLY UPDATE、VALIDATE 和 Renderer 升级。Use when Codex needs to model an unfamiliar project, safely distinguish project content from operational metadata, observe current verification/runtime, materialize an approved INIT Preview, or apply an architecture-centric Panorama update with approval-hash binding and Presentation Layer preservation."
---

# Agent 项目全景

将本文件所在目录作为 Skill 根目录，并从该目录运行脚本。把一个 Panorama 视为单项目、以架构为主轴的工程认知界面；不要扩展成任务看板或通用项目管理平台。V0.1.4 支持 Schema `0.1`、Data Template `0.1.0/0.1.1` 与中文 Renderer `0.1.2`。

## 不变量

- 保持 HTML 对用户只读；已有 HTML 的普通数据更新必须经过 `scripts/apply_patch.py`。正式 INIT 先经 `scripts/materialize_init.py` 生成首版 JSON，再由 `scripts/init_panorama.py` 生成 HTML。
- 普通更新只能修改 `project-panorama-data` 内的 JSON payload。
- 保留两个数据 Marker、DOM、CSS、Renderer JavaScript、未知字段与所有 `extensions`。
- Apply 前要求用户评审并批准准确的 Proposal Hash；不得制造或推断批准。
- Schema 重构前先记录 Schema Finding；普通更新不得修改冻结的 Schema v0.1。
- 不确定信息标记为 unknown/draft/pending，或向用户索取证据；不得虚构 ID、路径、评审、验收、部署或凭据。
- 除非用户明确批准迁移，否则保持现有凭据模式。
- 摘要不得输出 Embedded 明文；除非用户明确要求对应敏感值，否则不得使用 `--unsafe-include-secrets`。
- 中文仅用于显示层与用户消息；Schema Key、枚举原值、ID、Finding Code、JSON Patch Path 和 CLI Flag 保持英文。
- 正常 Skill 调用不得读取 executing Skill 自身的 `evals/` 或任何 Oracle；目标项目自己的 `evals/` 可以作为 Verification Evidence，但不得作为生产实现根。

## INIT

接受模糊想法、明确需求、已有项目或 OSS 改造；只采集已有证据。先判断项目类型：

- 新建极简项目：允许轻量骨架 INIT，但所有实现、运行、验证和批准状态保持 unknown/draft/pending。
- 已有、Legacy 或复杂项目：必须完整读取并执行 [Semantic Modeling Protocol](docs/semantic-modeling-protocol.md)，不得直接生成 JSON 或正式 HTML。

### 已有项目强制流程

```text
DISCOVER
→ CLASSIFY EVIDENCE
→ ACQUIRE OPERATIONAL EVIDENCE
→ ASSESS FRESHNESS
→ DETECT CONFLICTS
→ MODEL PROJECT
→ INFER CURRENT / TARGET / TRANSITION
→ ASSESS GAPS / RISKS
→ GENERATE INIT PREVIEW
→ HUMAN REVIEW
→ DETERMINISTIC MATERIALIZATION
→ INIT HTML
```

1. 使用只读 Evidence Discovery 建立候选地图：

   ```powershell
   python scripts/discover_project_evidence.py path/to/project-root
   ```

   该工具只负责 evidence discovery、Access Class、格式、Git metadata、mtime、Secret 风险与 Managed/Legacy marker detection。不得读取 `.env`、私钥、Project Content 或其他 Secret value，不得越出 project root，不得调用网络。
2. 完整读取 [Operational Evidence Contract](docs/operational-evidence-contract.md)。只对 Discovery 标记为 `PROJECT_OPERATIONAL_METADATA` 的候选使用 bounded Inspector：

   ```powershell
   python scripts/inspect_operational_evidence.py path/to/project-root `
     --path relative/operational-candidate.json
   ```

   保持 `PUBLIC_PROJECT_EVIDENCE`、`PROJECT_OPERATIONAL_METADATA`、`PROJECT_CONTENT`、`SECRET`、`EXTERNAL_PRIVATE_DATA` 五类边界。Knowledge Root 名称使用统一的 delimiter-aware terminal matcher：支持 exact 或以 `vault`、`wiki`、`knowledge`、`knowledge-base`、`kb` 结尾的分隔复合名称，禁止 substring/prefix 猜测；识别后普通内容仍保持 `PROJECT_CONTENT`。Project Content、Secret、外部路径与符号链接不得读取；structured Secret key 必须递归脱敏。
3. 需要 Current Runtime / Verification 时，只执行 [Operational Evidence Contract](docs/operational-evidence-contract.md) 允许的只读观察：

   ```powershell
   python scripts/observe_project_runtime.py path/to/project-root
   ```

   只自动执行 Git、文件存在性、dependency availability 与指定 process/scheduler name 等只读观察。项目 test manifest 中的 `safe` 只是声明，不是安全证明；默认只校验声明并返回 `requires_explicit_authorization`，不得自动执行通用项目代码。只有用户明确授权准确命令后才可增加 `--authorize-test-execution`；此时仍必须报告 `networkIsolation=not_enforced`、`filesystemIsolation=not_enforced` 与 `projectWriteCheck=post_execution_metadata_check`，不得声称未使用网络或未读取 Secret。不得返回进程参数或测试 stdout/stderr。Historical test report 保持 `historical_test`，实际授权执行结果才是 `current_test`。
4. 主动寻找 Intent、structured state、current implementation、verification、Decision/ADR、runtime/resource 与 narrative docs。再按 Fact Class 判断 authority，至少区分 `INTENT`、`REQUIREMENT`、`CURRENT_IMPLEMENTATION`、`CURRENT_RUNTIME`、`TARGET_DESIGN`、`HISTORICAL_RATIONALE`、`VERIFICATION`、`DECISION`、`RESOURCE`、`REFERENCE`；不得使用一个全局证据 Tier 替代事实类别判断。
5. 对关键来源执行 Source Freshness Audit，使用 current / likely_current / stale / historical / unknown。mtime 只能作为信号，Narrative Documentation cannot silently override fresher structured evidence。
6. 主动检测 Machine vs Narrative、Runtime vs Historical Test、Decision vs Old Design、Generated Snapshot vs Current Git、Current Implementation vs Accepted Architecture、Target vs Current。Preserve Conflict，解释时间/环境/范围差异并列出 Needs Human Review；不得静默消解。
7. 建模时执行以下不可推导规则：

   - Historical Verification ≠ Current Runtime Availability；
   - Planned ≠ Implemented ≠ Deployed ≠ Active；
   - Accepted Design ≠ Current Architecture；
   - Installed ≠ Running；
   - Not Detected ≠ Absent，优先使用 unknown / not_detected / not_observed；
   - Active Contract / Configuration Intent ≠ Active Runtime；
   - Test Passed Historically ≠ Current Test Baseline Passed。
8. Requirement 来自业务场景、用户结果、能力、约束和质量目标；不要把框架、文件、目录或测试工具直接当 Requirement。Module 是可独立解释职责、接口、状态所有权、部署边界和演进的责任单元，不得使用“一目录/一文件/一任务 = 一 Module”。
9. 按 Current / Target / Transition 独立建模。Current 只来自 actual implementation/config/runtime 或 accepted-and-implemented structure。confirmed Target 必须有 approved Decision/ADR、reviewed architecture 或 explicit user-approved target；idea/proposal 保持 draft/proposed/pending。只有 Current 与 Target 有证据化差异时建立 Transition。
10. 对每个 Module 独立判断 Design、Implementation、Verification、Runtime。长期项目尝试重建 2–5 个 architecture-changing versions；证据不足时写 `historical architecture incomplete`。
11. Agent 推理只能产生 Risk Candidate / Attention Candidate。只有实际运行 Validator 返回的代码才是 Formal Finding，禁止伪造 Finding Code。
12. 检测现有总览：有 Managed Marker 时转入 INSPECT；没有 Marker 的 Legacy/Narrative Panorama 默认 COEXIST，禁止覆盖。只有用户明确批准 Adopt Existing Presentation 才提出迁移。

### INIT Preview Gate

已有、Legacy 或复杂项目必须先输出以下固定结构：Project Intent、Current Stage、Current Architecture、Target Architecture、Architecture Versions、Current → Target Transitions、Modules、Requirements、Decisions、Acceptance / Gates、Runtime / Resources、Source Freshness、Conflicts、Formal Findings、Risk Candidates、Unknown / Needs Human Review、Next Focus。

Next Focus 基于 Stage、blocker、exit criteria、Formal Findings、Risk Candidates、verification 与 transition，提供 2–3 个包含 Why now、Benefits、Risks、Impact、Prerequisites、Expected outcome 的方案并推荐一个。

输出 Preview 后停止。用户明确批准 INIT Preview 后，完整读取 [INIT Materialization Contract](docs/init-materialization-contract.md)，将 Preview、Source Snapshot、Evidence Provenance、语义 Change Intent、Guidance 和真实批准动作写入 `approved-init-model.v0.1`。先计算并展示准确 `previewHash`；批准必须绑定该 Hash、`approvedBy`、`approvedAt` 与未变化的 Source Snapshot。

批准后执行：

```powershell
python scripts/materialize_init.py .panorama-work/approved-init-model.json `
  --project-root path/to/project-root `
  --output .panorama-work/initial-panorama-data.json
```

将 Panorama 自有临时文件放入 `.panorama-work/`；Source Snapshot 对 Git 项目使用 tracked + 非忽略 untracked 文件，并排除已忽略文件、`.panorama-work/`、Panorama lock、backup HTML 与 `*.local.html`，避免 Skill 自身制造 Source Drift。

Materializer 必须生成 Initial approved Review、语义 Change、Initial UpdateBatch、Finding Reconciliation、聚类 Attention 和原样 Guidance。Finding Reconciliation 必须在 provisional UpdateBatch 已存在后运行最终 Validator，并最多进行 2 次稳定化；最终 Validator Finding 必须与 stored reconciliation 一致，最终 actionable High/Critical 必须与 CONTROL Attention 一致。存在 actionable High/Critical Formal Finding 时 CONTROL Attention 不得为空。Verification Evidence 已存在但未映射时使用 `PANORAMA_EVIDENCE_GAP`，没有证据时使用 `PROJECT_GAP`；blocked Transition 使用 `CONTROL_BLOCKER`。Candidate Release 不得写入 `project.currentReleaseId`。Materializer 成功并通过 Validator 后，才允许生成 HTML、VALIDATE 与 INSPECT。

### 新建极简项目

没有 Panorama JSON 时，生成最小骨架：

```powershell
python scripts/new_project_data.py `
  --project-id PRJ-ID `
  --name "项目名称" `
  --objective "项目目标" `
  --current-focus "澄清需求" `
  --requirement "初始草稿需求" `
  --output project.v0.1.json
```

骨架只创建一个当前澄清阶段、草稿架构、合法的 Unknown/Draft 状态和两个下一步选项；不得虚构模块、部署、评审、文档或运行事实。若输入实际是已有项目，不得用极简路径绕过 Preview Gate。

只通过带校验的初始化脚本生成 HTML：

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data project.v0.1.json `
  --output project-panorama.local.html
```

初始化脚本在生成前后都执行校验。输出已存在时默认停止；只有用户明确授权覆盖时才增加
`--overwrite-existing`，脚本会先生成同目录时间戳备份再原子替换。输出不得与模板路径相同。
`--allow-invalid` 仅用于显式调试，不得用于正式交付。

## INSPECT

1. 先执行 VALIDATE，再信任实体关系。
2. 读取项目定位摘要：

   ```powershell
   python scripts/read_panorama.py path/to/project-panorama.html
   ```

3. 只有确有需要时才读取完整数据：

   ```powershell
   python scripts/read_panorama.py path/to/project-panorama.html --json
   ```

   输出默认脱敏。不要读取 External File 内容，也不要解析 External Store 值。
4. 汇报当前阶段、主线、当前→目标架构、活跃/部署中运行状态、最近更新、High/Critical 关注事项、验证缺口和当前下一步焦点。

## PROPOSE UPDATE

不得修改目标 HTML。

1. 使用最小 `add`、`replace`、`remove` JSON Pointer 操作表达变更。
2. 保留 ID 与 extensions。下一步焦点提供 2–3 个可行选项，说明为什么现在做、收益、风险、影响、前置条件、证据、预期结果和一个推荐项。
3. 生成确定性 Proposal：

   ```powershell
   python scripts/propose_update.py `
     path/to/project-panorama.html `
     candidate-update.json `
     --output pending-update.json
   ```

4. 使用其事实生成 Revision/Hash、Operations、Affected Entities、Validation、结构化 Findings、审计草稿、Attention 与 Proposal Hash；Affected Entities 必须来自 Pointer/value 和 current/preview 注册表，不得解析终端字符串或依赖自由文本碰撞。Finding 的 `relatedEntities` 必须原样进入对应 Attention。
5. Preview 与 UpdateBatch Attention 必须包含全部 High/Critical Finding；可以解释，但不得隐藏。
6. 向用户展示语义上的 What/Why/Impact、Change Level、Review Requirement、不确定项、Next Focus 与准确更新包，然后停止等待批准。

Proposal 的 `reviewDraft` 保持 pending，`reviewedBy` 为空、`reviewedAt` 为 null。用户批准时只填写 `approval.status`、`approvedBy`、`approvedAt`，并保留准确的 `proposalHash`。任何 Base、Operation、Change、Review、UpdateBatch 或 Guidance 变化都要求重新 Proposal 与批准。

## APPLY UPDATE

只有用户明确批准准确 Proposal Hash 时才能执行：

```powershell
python scripts/apply_patch.py `
  path/to/project-panorama.html `
  pending-update.json
```

让脚本强制执行 Approval/Hash 绑定、Review/UpdateBatch 关系、Base Revision/Data Hash、独占锁、备份、校验、Presentation Hash 不变、提交时并发检测和原子替换。Apply 必须用 Approval 的 `approvedBy` / `approvedAt` 确定性物化最终 approved/waived Review，不得沿用提案生成时间。

失败时不得静默修复、rebase 或重新批准；重新执行 INSPECT 与 PROPOSE UPDATE。成功后执行 VALIDATE 与 INSPECT，并汇报新 Revision、语义结果、剩余 High/Critical、备份和 HTML 路径。

V0.1.1 将历史 Next Focus 选项保存在 inactive 兼容记录中，避免旧 UpdateBatch 失效。不要把它描述为最终模型；正式快照属于 V0.2 Migration Proposal。

## UPGRADE RENDERER

Renderer 中文化是 Presentation Layer 升级，不是普通数据更新。升级已有 HTML 时执行：

```powershell
python scripts/upgrade_renderer.py `
  path/to/project-panorama.html `
  --output path/to/project-panorama.zh-CN.html
```

只有用户明确选择原文件时才使用 `--in-place`；该模式必须先生成时间戳备份。升级前后必须证明 Data Hash 和完整 JSON 相同，并让输出 Presentation Hash 与目标模板一致。不得顺便翻译用户项目数据、实体名称或技术标识。

## VALIDATE

```powershell
python scripts/validate_panorama.py path/to/project-panorama.html
python scripts/validate_panorama.py path/to/project-panorama.html --json
```

退出码 `0` 表示没有 Schema/跨引用 Error，`1` 表示工程数据无效，`2` 表示输入或运行失败。Warning 仍是可操作工作，优先处理 `high` / `critical`。

汇总 Architecture Gap、Scope Drift、Implementation Drift、Review Gap、Verification Gap、Baseline Drift、Deployment Drift、Transition Risk、Resource Risk、Stage Entry/Exit Gap、Missing Reference Path、Embedded Secret 与 Git Secret Risk。Finding Code 保持英文稳定，说明文字使用中文。未经批准不得自动修复 Finding。

## 安全与 Git

- Embedded 本地凭据优先放在 `*.local.html`，并建议宿主仓库忽略该模式。
- High `GIT_SECRET_RISK` 是分享/提交决策，不是删除或迁移数据的授权。
- Production 优先使用 External File 或 External Store，同时尊重用户当前凭据模式。
- 动态链接经过协议白名单；被拒绝 URL 保持可读但不可点击。

## 停止边界

不要新增主视图、后端、React/Vue、Kanban、人员/预算/日历管理、云协作、自动 Git 提交、自动凭据迁移或通用 Agent 平台。不要自动读取正式知识库或修改业务项目。除非用户单独批准，不得执行 V0.2 Schema Migration。
