# Agent Project Panorama V0.5 Foundation（V0.4 Compatible）

`Agent Project Panorama` 是一个以架构为主轴、Local-first、单项目单 HTML 的
AI/Vibe Coding 工程认知控制面。它用于恢复和维持对需求、架构、模块、演进、验证、
部署与资源的理解，不是任务看板、文档编辑器或多人项目管理平台。

## V0.4 P0 / P1 / P2 能力

- 无构建、无 CDN、无远程字体的 Single HTML Renderer；canonical/file 模式无网络请求，
  Bridge 模式只访问同源 `127.0.0.1` API；
- `控制台 / 系统 / 演进` 三主视图；
- 当前 / 目标 / 迁移、原生 SVG 连接与通用实体详情；
- 同环境多 Deployment、部署视图与完整资源池；
- Module、Decision、Acceptance、Gate、Risk、Connection、Reference 详情；
- Embedded / External File / External Store / None 四种凭据模式；
- JSON Schema、全局 ID、跨实体引用、结构化 Findings 和工程规则校验；
- Approval Hash、Revision/Hash、独占锁、备份、原子写入与最小 JSON Patch Apply；
- 默认 Secret 脱敏、Git Secret Risk 与统一 URL Sanitizer；
- INIT 自动校验、最小项目骨架、确定性 Proposal 工具和 GitHub Actions CI。
- 陌生/已有项目的 Evidence Discovery、按 Fact Class 判断 Authority、Freshness Audit、Conflict Preservation 与强制 INIT Preview；
- Legacy / Managed Panorama 检测、Current / Target / Transition 语义协议、Module 四状态和隔离的 Semantic Eval Harness。
- 五类 Evidence Access Class、受限 Operational Metadata Inspector，以及不读取进程参数/命令输出的 Current Runtime / Verification Observation；
- Approved INIT Preview Hash/Source Snapshot 绑定，以及 Initial Review、Change、UpdateBatch、聚类 Attention 和 Guidance 的确定性物化。
- 正式 Current 的 Continuous Observation：Git Hook、启动补偿、Source Binding、Fact Provenance、Current Architecture Snapshot 与 Observation Batch；
- 项目规范 Standard Pack 输入、声明式安全评估、规范风险披露与 Commit/Pack/Data Hash 绑定；
- 自动更新只记录 observed/declared/inferred/unknown/conflict 事实，保护 Intent、Target、Decision、Review、Acceptance、Waiver、Guidance 和 Credential。
- SYSTEM 内嵌 Architecture Studio：隔离会话、多候选画布与对比、正式 Validator、可选 Codex
  只读评审、精确 Proposal Hash 批准和受控 Apply。
- `SYSTEM → 架构来源` 内嵌 Evidence Inspector：按事实 Path、Authority、枚举 Confidence、Observation
  Batch、Source Binding 与正式 Reference 连续检查证据；离线模式明确显示 Currentness Unknown，
  Bridge 模式显示实时 `match / drift / uninitialized / incomplete` 与具体原因。
- Observation Conflict 只显示 Batch 级 Summary；缺少结构化两侧证据时固定标记
  `Needs Human Review — structured sides unavailable`，Renderer 不自动裁决。
- Studio 持续显示 Bridge 权威 `semanticHash / layoutHash`、Semantic/Layout 双轨 Session
  Operations、字段级候选 Semantic Diff，以及可同时保留的具体 stale 原因。离线模式固定显示
  `not_computed_by_bridge`，不把浏览器计算冒充权威 Hash。
- Studio 候选使用完整 Tab 键盘模型；画布节点支持 `Alt + 方向键` 布局移动，每条 SVG 数据流同时
  提供可聚焦文本列表；Drawer/Dialog 具备焦点进入、闭环、Esc 关闭与触发控件返回。840px 以下使用
  组件 / 画布 / 属性检查分段面板。本阶段提供 WCAG 相关实现与键盘测试证据，不宣称完整 WCAG 认证。
- Module、Requirement、WorkItem、Acceptance、Gate、Release 与 Deployment Drawer 提供 Trace Route；
  每条边显示真实 ID 字段的 JSON Pointer，多跳 Route 保留所有中间实体。Renderer 派生缺口只标为
  `Trace Gap Candidate`，不会冒充 Validator 的 Formal Finding。
- Drawer 从现有 Gate、Acceptance、Evidence Reference、Release 与 active Deployment 派生
  `verified / accepted / deployed_observed`；Studio 从当前 Proposal、Formal Validation、Session/CAS 与
  Source 状态派生 `change_ready`。证据不足时只显示 `blocked / unknown`，不写回治理状态。
- Studio Proposal 区直接投影 Bridge 返回的真实 JSON Patch、Affected Entities 和 Validation；这些字段
  来自 `propose_update.py`，Renderer 不建立第二套 Reconciliation Preview。
- 独立 `panorama-verification-receipt.v0.1` Schema 与 experimental Adapter：拒绝超限、路径逃逸、
  敏感明文、Hash/Binding 不匹配；只生成候选 `Reference(test_report)`、Evidence mapping 与 V0.2
  Fact Provenance。Gate/Acceptance Drawer 按 Receipt Hash 去重显示验证时间线、External Finding、
  Isolation 和 Redaction，不把外部 Finding 冒充 Formal Finding，也不自动推进任何治理状态。

正式全景数据对页面直接写入保持只读。核心实体编辑先进入隔离的 Architecture Studio
会话；正式评审、精确 Hash 批准和文件写回仍由受控 Bridge 与既有更新事务完成。

## V0.5 Engineering Event Foundation

V0.5 增加 Project-local Engineering Event Sidecar、Approval Policy Core，以及 opt-in Proposal/Apply
Governance Outbox；V0.5.1 内部里程碑增加首个真实 Delegated Operation Adapter：`event_head.recover`。
不修改 Panorama Schema `0.1/0.2`。Observation、Receipt、Studio Session 和 INIT 事务尚未接入 Event Capture。

V0.5.0 已发布 `Foundation Slice A + Approval Policy Core + opt-in Proposal/Apply Outbox`。V0.5.1 只接入
`event_head.recover`，已通过本地验证但不单独公开发布；其余 Delegated Policy Operation Adapter、View IR、
Renderer 改版、Dataset/RAG/Eval Export 与后训练不能由当前能力外推为已经实现。下一次公开发布目标调整为
V0.6.0 多视图架构全景，完整规划见
[`docs/v0.6-multi-view-architecture-panorama-iteration-plan.md`](docs/v0.6-multi-view-architecture-panorama-iteration-plan.md)。

先执行不访问网络、不安装依赖、不读取 Secret 的 Runtime Preflight：

```powershell
python scripts/runtime_preflight.py --json
```

使用受约束的 Event Request 初始化并写入项目自己的 Store：

```powershell
python scripts/record_engineering_event.py `
  examples/engineering-event-request.v0.1.json `
  --store path/to/project/.panorama-work/event-store/v0.1 `
  --project-root path/to/project

python scripts/validate_event_store.py `
  path/to/project/.panorama-work/event-store/v0.1 --json
```

当前 Foundation 已实现：

- 唯一 `panorama-engineering-event.v0.1` Envelope；
- Recorder clock、可空 occurrence time 和显式 time precision；
- 确定性 Event ID、Request Hash、Event Hash、Sequence 与 Previous Hash；
- 一事件一文件、独占 Store Lock、原子 Event/Head 发布、幂等与同键冲突拒绝；
- Chain/Head/Schema/Safety Validator，以及显式 `--recover-head`；
- Outbox、Retention/Redaction、Transformation Loss 的冻结机器合同；
- Approval Policy Prepare/exact-hash Approval/Materialization、Use Ledger、Execution Receipt/Event Binding、
  Revocation/supersede、Store Validator 与 pending recovery；
- `apply_patch.py --event-store` 的 fail-closed Governance Outbox，以及 finalized/abandoned/conflict 和显式恢复；
- 只读 Head State Inspector，以及只接受 missing/behind 的 `event_head.recover` Policy Adapter；
- 带 `stateHash` 的 Recovery Transaction、精确 Head CAS、Audit Event/Receipt/Ledger 对账与 Resume；
- Secret、敏感字段、本机绝对路径、路径逃逸、Project Content 和 `trainingEligibility` 的边界。

详细规范见 [`docs/engineering-event-contract.md`](docs/engineering-event-contract.md)。当前未实现的其他
Capture Adapter、Redaction 执行器、View IR、Renderer 改版和 Dataset Export 不得被描述为可用能力。

### V0.5 Approval Policy Core

V0.5 已完成审批门禁设计闭合并实现 Policy Core。合同将门禁分为 Decision、Delegated Policy、
Automatic Quality 和 Read-only 四类：治理语义、Policy 自身、外部发布/导出、Hook、风险豁免和破坏性删除
继续逐次精确批准；只读分析与自动质量检查不需要批准；Receipt Evidence、准确命令、Event Append、确定性
Head Recovery、本地 last-good 和 fact-only 更新可在一次性批准的单 Operation Policy 内重复执行。

Policy 生命周期示例：

```powershell
python scripts/prepare_approval_policy.py policy-semantics.json `
  --output .panorama-work/policy/prepared.json
python scripts/record_approval_policy_approval.py .panorama-work/policy/prepared.json `
  --approved-hash <EXACT-HASH> --approved-by <USER> `
  --output .panorama-work/policy/approval.json
python scripts/materialize_approval_policy.py `
  .panorama-work/policy/prepared.json .panorama-work/policy/approval.json `
  --output .panorama-work/policy/active.json `
  --store .panorama-work/approval-policy-store/v0.1
python scripts/validate_approval_policy_store.py `
  .panorama-work/approval-policy-store/v0.1 --json
```

机器合同见 [`docs/approval-policy-contract.md`](docs/approval-policy-contract.md) 与
[`schema/approval-policy.schema.v0.1.json`](schema/approval-policy.schema.v0.1.json)；每次执行使用
[`schema/policy-execution-receipt.schema.v0.1.json`](schema/policy-execution-receipt.schema.v0.1.json)
形成带 Use Number 和 Previous Receipt Hash 的审计链，撤销使用
[`schema/approval-policy-revocation.schema.v0.1.json`](schema/approval-policy-revocation.schema.v0.1.json)。示例见
[`examples/approval-policy.verification-receipt.v0.1.json`](examples/approval-policy.verification-receipt.v0.1.json)。
Core 不等于所有 Operation Adapter 已实现；V0.5.1 只接入 `event_head.recover`，尚未接入的 Operation 继续
沿用现有逐次门禁。

Head Inspect/Preview 不消耗 Policy Use。只有完整且唯一的 Chain 与 `head_missing|head_behind` 可以执行；
current 返回 no-op，ahead/mismatch/invalid/Fork/Redaction 歧义全部 fail closed：

```powershell
python scripts/event_head_inspector.py `
  path/to/project/.panorama-work/event-store/v0.1 --json

python scripts/recover_event_head_with_policy.py preview `
  --project-root path/to/project --panorama path/to/project-panorama.json `
  --policy-id POLICY-ID --json

python scripts/recover_event_head_with_policy.py execute `
  --project-root path/to/project --panorama path/to/project-panorama.json `
  --policy-id POLICY-ID --json

python scripts/recover_event_head_with_policy.py resume `
  --project-root path/to/project --panorama path/to/project-panorama.json `
  --policy-id POLICY-ID --transaction-id EHR-... --json
```

完整约束见
[`docs/event-head-policy-adapter-contract.md`](docs/event-head-policy-adapter-contract.md) 与
[`schema/event-head-recovery-transaction.schema.v0.1.json`](schema/event-head-recovery-transaction.schema.v0.1.json)。
Policy 创建、变更、续期、替换和撤销仍要求准确 Hash 的人工批准。

为普通 Studio Proposal 启用事件双写：

```powershell
python scripts/apply_patch.py project-panorama.html pending-update.json `
  --approval studio-approval.json `
  --event-store .panorama-work/event-store/v0.1
```

默认 Outbox 位于 `.panorama-work/event-outbox/v0.1/`。若 Event finalize 失败，Panorama 可能已精确提交，
但 CLI 会返回失败并留下 pending；后续治理写被阻断，必须显式校验/恢复：

```powershell
python scripts/validate_governance_outbox.py `
  .panorama-work/event-outbox/v0.1 `
  --recover .panorama-work/event-outbox/v0.1/<TXN-ID>.json `
  --panorama project-panorama.html `
  --event-store .panorama-work/event-store/v0.1 --json
```

## Continuous Observation

V0.2 取消需要人工维护的 Current Baseline。Current 可以基于实际 Git、实现、验证、运行、资源与规范证据全量自动更新；Target 和治理事实保持受保护。自动化不会修改业务项目，也不会向业务分支提交。

先将 V0.1 迁移为新文件：

```powershell
python scripts/migrate_v01_to_v02.py project-panorama.local.html `
  --output project-panorama.v0.2.local.html
```

执行首次同步并登记项目规范：

```powershell
python scripts/continuous_observation.py project-panorama.v0.2.local.html `
  --project-root path/to/project `
  --standard path/to/project/standards/engineering.yaml
```

安装 Git Hooks；Hook 只入队并后台唤醒一次同步，失败不影响 Git Commit：

```powershell
python scripts/manage_git_hook.py install path/to/project `
  --panorama path/to/project/project-panorama.v0.2.local.html `
  --standard path/to/project/standards/engineering.yaml
```

支持 `post-commit`、`post-merge`、`post-checkout` 和 `post-rewrite`。每次 Skill 启动或显式 sync 都会比较 Source Binding 与 HEAD，因此遗漏 Hook 后仍能补偿追踪。

自动观察写入 Observation Batch，而不是伪造人工 Review。允许的事实类别和受保护路径由带 SHA-256 的 `observationPolicy` 固定；策略自身不能被自动修改。完整边界见 [`docs/continuous-observation-contract.md`](docs/continuous-observation-contract.md)。

## 项目规范

规范输入使用 JSON/YAML Standard Pack：

```powershell
python scripts/validate_standard_pack.py standards/engineering.yaml
python scripts/assess_project_standards.py project-panorama.v0.2.local.html `
  --project-root path/to/project `
  --standard standards/engineering.yaml
```

支持 `path_exists`、`path_absent`、`path_glob_exists`、`git_tracked`、`panorama_pointer_exists`、`panorama_pointer_equals` 和 `manual`。规范包不能携带任意命令或网络回调。示例位于 [`examples/standard-pack.example.yaml`](examples/standard-pack.example.yaml)，合同见 [`docs/standards-assessment-contract.md`](docs/standards-assessment-contract.md)。

## 环境

- Python 3.10+
- `jsonschema`
- `pytest`
- `PyYAML`
- `tomli`（Python 3.10 的 TOML 解析兼容；Python 3.11+ 使用标准库 `tomllib`）

```powershell
python -m pip install jsonschema pytest PyYAML tomli
```

只读 Renderer 与离线 Studio 不需要 Python，生成后的 HTML 可直接在桌面浏览器打开。
完整 Studio 闭环使用本机 Python Bridge，并可选调用已安装且已登录的 Codex CLI 做只读架构评审。

用户可见界面、CLI 帮助、Validator 说明与 Skill 工作流使用简体中文。Schema Key、枚举原值、
ID、Finding Code、JSON Patch Path 和 CLI Flag 保持英文稳定，以兼容现有数据与自动化工具。

## 已有项目的 Semantic INIT

已有、Legacy 或复杂项目不能直接生成正式 HTML。Skill 会完整执行
[`docs/semantic-modeling-protocol.md`](docs/semantic-modeling-protocol.md)：

```text
DISCOVER → CLASSIFY EVIDENCE → ACQUIRE OPERATIONAL EVIDENCE
→ ASSESS FRESHNESS → DETECT CONFLICTS
→ MODEL PROJECT → INFER CURRENT / TARGET / TRANSITION
→ INIT PREVIEW → HUMAN REVIEW → DETERMINISTIC MATERIALIZATION → INIT HTML
```

先运行只读 Evidence Inventory：

```powershell
python scripts/discover_project_evidence.py path/to/project-root
```

工具只发现候选、格式、Git/mtime、生成痕迹、Secret 风险和 Panorama Marker；除 Marker 检测所需
的 bounded HTML prefix 外保持 metadata-only，不调用网络、不读取 `.env`、密钥或正式知识库正文、
不越出 project root，也不自动判断架构。Evidence Authority、Requirement
抽象、Module 拆分、版本重建、Current/Target/Transition、Risk Candidate 与 Next Focus 仍由 Agent
按协议推理，并显式保留 Unknown 与来源冲突。

对于 `PROJECT_OPERATIONAL_METADATA` Candidate，使用受限 Inspector；普通知识正文即使是 YAML 也不会因此变为可读：

```powershell
python scripts/inspect_operational_evidence.py path/to/project-root `
  --path vault/90-System/tasks/registry.json
```

Current Runtime / Verification 使用只读 Observer。它只自动观察 Git、文件存在性、dependency availability
与指定 process/scheduler name，不读取进程参数：

```powershell
python scripts/observe_project_runtime.py path/to/project-root `
  --dependency json `
  --check-path reports/current-status.json
```

Test manifest 中的 `safe` 只是声明。Observer 默认只校验并返回 `requires_explicit_authorization`，不会执行
Python、Node 或其他通用项目代码。用户明确授权准确命令后才能使用 `--authorize-test-execution`；即使执行，
仍会诚实报告网络/文件系统隔离未实施，只做项目文件元数据事后检查，且不返回 stdout/stderr。

完整访问边界见 [`docs/operational-evidence-contract.md`](docs/operational-evidence-contract.md)。

复杂项目的 INIT Preview 固定包含 Intent、Stage、Current/Target/Transition、Architecture Versions、
Modules、Requirements、Decisions、Acceptance/Gates、Runtime/Resources、Freshness、Conflicts、Formal
Findings、Risk Candidates、Unknown 和 2–3 个 Next Focus。用户批准 Preview 前不写正式 Panorama JSON 或 HTML；
只允许在 `.panorama-work/` 写入 Draft、Prepared Review 与独立 Approval 工作制品。

按 [`docs/init-materialization-contract.md`](docs/init-materialization-contract.md) 完成完整 Draft 后，先 PREPARE。
脚本会在全部 Schema、跨引用、风险规则与派生记录预校验通过后冻结 exact Preview、计算 Hash，并生成供人审阅的 Markdown：

```powershell
python scripts/prepare_init_review.py .panorama-work/draft-init-model.json `
  --project-root path/to/project-root `
  --output .panorama-work/prepared-init-review.json `
  --preview-markdown .panorama-work/prepared-init-review.md
```

展示完整 Preview 和 Hash 后必须停止。只有用户显式确认 `批准 INIT Preview <EXACT-HASH>` 才能一次性记录独立批准：

```powershell
python scripts/record_init_approval.py .panorama-work/prepared-init-review.json `
  --approved-hash <EXACT-HASH> `
  --approved-by <USER-IDENTITY> `
  --output .panorama-work/init-approval.json
```

随后使用两个独立制品生成 Initial Review、Change、UpdateBatch、聚类 Attention 和原样 Guidance：

```powershell
python scripts/materialize_init.py .panorama-work/prepared-init-review.json `
  --approval .panorama-work/init-approval.json `
  --project-root path/to/project-root `
  --output .panorama-work/initial-panorama-data.json
```

Git Source Snapshot 使用 tracked + 非忽略 untracked 文件，并排除 `.panorama-work/`、Panorama lock/backup
和 `*.local.html`。真实 Source Snapshot 变化、三重 Hash 不一致、缺少独立 Approval、最终
Finding/Reconciliation/Attention 不一致、批准后语义投影变化或 Schema/Cross-reference Error 都会阻止物化。
Candidate Release 可以保留为实体，但若被写成 `project.currentReleaseId`，PREPARE 和 Materializer 都会失败，
不会静默改成 `null`。物化 JSON 验证通过后，再交给
`scripts/init_panorama.py` 生成 Single HTML。

`evals/semantic/` 是隔离评测 Harness，不属于生产 Skill 上下文；正常 INIT 禁止读取其中 case invariants
或 Oracle 信息。

## 最小项目初始化

先生成不虚构模块、部署或证据的最小 Schema-valid 数据：

```powershell
python scripts/new_project_data.py `
  --project-id PRJ-MY-PROJECT `
  --name "My Agent Project" `
  --objective "建立项目工程认知" `
  --current-focus "澄清首轮需求" `
  --requirement "首个待澄清需求" `
  --output project.v0.1.json
```

生成 HTML。INIT 默认执行 JSON 前置校验和生成后 HTML 校验；Error 阻止正式输出：

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data project.v0.1.json `
  --output project-panorama.local.html
```

`--allow-invalid` 只用于显式调试，不应生成正式 Panorama。若输出已存在，INIT 默认拒绝覆盖；
只有显式增加 `--overwrite-existing` 才会在同目录创建时间戳备份并原子替换。输出路径始终
不得与模板路径相同。

## Reference Project

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data examples/reference-project.v0.1.json `
  --output examples/reference-project.html
```

直接打开 `examples/reference-project.html`。Reference 的 development 环境同时包含 Active
与 Deploying Deployment，用于验证并行迁移和同环境多版本显示。

## 升级已有 HTML 到中文 Renderer

Renderer 中文化属于 Presentation Layer 升级，不是普通数据 Patch。默认生成新文件：

```powershell
python scripts/upgrade_renderer.py `
  path/to/project-panorama.html `
  --output path/to/project-panorama.zh-CN.html
```

只有明确要替换原文件时才使用 `--in-place`；脚本会先创建时间戳备份。升级前后完整 JSON 与
Data Hash 必须一致，输出的 Presentation Hash 必须与目标模板一致。

## 读取与脱敏

```powershell
python scripts/read_panorama.py examples/reference-project.html
python scripts/read_panorama.py examples/reference-project.html --json
```

`--json` 默认将所有 Embedded Credential 字段替换为 `***REDACTED***`，不会读取
External File 内容，也不会解析 External Store。只有用户明确需要明文时才允许：

```powershell
python scripts/read_panorama.py examples/reference-project.html `
  --json --unsafe-include-secrets
```

该命令会向 stderr 输出明文风险警告。

## 校验

```powershell
python scripts/validate_panorama.py examples/reference-project.html
python scripts/validate_panorama.py examples/reference-project.html --json
```

文本输出保持 `ERROR / WARNING / INFO`；`--json` 输出包含 `level`、`severity`、`code`、
`message`、`path`、`relatedEntities` 的结构化 Findings。规则 Finding 会尽可能绑定具体需求、
模块、连接、部署、发布、资源、阶段或验收实体。退出码：

- `0`：无 Schema/Cross-reference Error；
- `1`：Schema、跨引用或数据一致性 Error；
- `2`：文件、JSON、Marker 或运行依赖错误。

Warning 不阻止生成，但 High/Critical Findings 在 Proposal 阶段不得被隐藏，并默认进入
Update Batch Attention。

## Proposal → Review → Apply

准备候选操作文件：

```json
{
  "operations": [
    {"op": "replace", "path": "/intent/currentFocus", "value": "新的主线"}
  ],
  "summary": "更新当前主线",
  "changeLevel": "local"
}
```

确定性生成事实差异、Validation Findings、Attention、审计草稿和 Proposal Hash：

```powershell
python scripts/propose_update.py `
  examples/reference-project.html `
  candidate-update.json `
  --output pending-update.json
```

`propose_update.py` 输出的是严格 wrapper：`proposal` 是可 Apply 的更新包，`facts` 与
`validation` 是审阅上下文。`proposal.approval.status` 初始为 `pending`；不要修改 wrapper，
也不要由页面填写批准时间。用户核对完整 Proposal 并显式确认页面显示的精确
`proposal.proposalHash` 后，由本机 recorder clock 生成独立、write-once 的批准 artifact：

```powershell
python scripts/studio_approval.py `
  pending-update.json `
  --approved-hash <精确的-proposalHash> `
  --approved-by "user" `
  --output studio-approval.json
```

需要把批准额外绑定到 Studio session 的项目/源码状态时，可传入
`--source-binding studio-source-binding.json`。该对象只可包含 `projectId`、`schemaVersion`、
`templateVersion`、`baseRevision`、`baseDataHash`、`gitHead`、`sourceSnapshotHash`；recorder 会
补齐并核对 Proposal 的 Base Revision/Data Hash，Apply 会再与当前 Panorama 核对。

不得在批准后继续修改 `baseRevision`、`baseDataHash`、`operations`、`changeRecords`、
`reviewDraft`、`updateBatchDraft` 或 `guidanceDraft`。任一字段变化都会使 Approval Hash 失效，
必须重新 Proposal 和 Review。

Proposal 中 `reviewDraft` 保持 `pending`，`reviewedBy` 为空且 `reviewedAt` 为 `null`。Apply
验证独立 artifact 与当前 Proposal 的三方 Hash 一致后，只在内存中把 recorder 记录的
`approvedBy` / `approvalRecordedAt` 适配成旧版 `approval`，再写入最终 Review；Proposal 文件
本身始终不变。新增、删除与修改实体均通过 JSON Pointer、结构化 value 及前后状态注册表计算
Affected Entities，不通过自由文本字符串碰撞推断。

Apply：

```powershell
python scripts/apply_patch.py `
  examples/reference-project.html `
  pending-update.json `
  --approval studio-approval.json
```

Apply 严格区分 wrapper 与 legacy bare package；wrapper 与 bare 字段混合、独立批准与已经
approved/waived 的 inline approval 混用都会被拒绝。为兼容已有自动化，bare package 和旧版
inline approval 仍可直接 Apply。Apply 强制检查 Approval Hash、Review/UpdateBatch 关系、Base
Revision/Data Hash、可选 Source Binding、Schema、跨引用、Presentation Hash 和提交前并发状态。
失败不覆盖原 HTML；进入 Apply 后创建的备份保留用于审计和恢复。历史 Next Focus 会在 v0.1.1
兼容层中保留为 inactive historical record，当前 UI 只显示当前 Guidance。

## Security

- Embedded 允许用于本地开发，但遮蔽不是加密，HTML 源码仍可读取值；
- 推荐使用 `project-panorama.local.html`，仓库 `.gitignore` 已包含 `*.local.html`；
- Embedded 数据位于 Git tracked/staged 文件时，Validator 输出 High `GIT_SECRET_RISK`；
- Production Credential 优先使用 External File / External Store；不自动迁移用户数据；
- Renderer 的所有动态链接统一经过 `safeHref()`；允许 HTTP、HTTPS、file 和相对路径，
  拒绝 JavaScript、VBScript、data 和其他未批准 Scheme。

## 运行时与实体详情

`系统 → 运行时与资源` 包含：

- 部署视图：环境 → 部署列表 → 发布 → 模块 artifactVersion → 资源 → 访问；
- 资源池：按环境 / 类型 / 状态过滤所有项目资源，包括未绑定当前 Deployment 的资源；
- 资源访问与凭据模式；Embedded 默认遮蔽；
- 通用右侧实体详情：Module、Decision、Acceptance、Gate、Risk、Connection、Reference。

控制台环境状态聚合同环境全部 Deployment，例如“1 个运行中 / 1 个部署中”。

## 兼容性

- Data Schema：`0.1`、`0.2`
- Renderer：`0.4.0`（zh-CN，含 Architecture Studio、Evidence Inspector 与 Trace/Gate Projection）
- Studio Bridge：`0.4.0`（新增脱敏 `sourceFreshness` 实时比较）
- 支持 Template：`0.1.0`、`0.1.1`
- Skill 支持 Schema：`0.1`、`0.2`

不支持的 Schema/Template 会显示或返回明确错误，不静默渲染。

## Architecture Studio

进入 `系统 → 逻辑架构`，使用右侧的 `全景查看 / 架构设计 Studio` 开关。直接打开 HTML 时
使用离线模式，CSP 保持 `connect-src 'none'`；启动本机 Bridge 后，页面可进入完整治理闭环：

```powershell
Copy-Item examples/reference-project.html project-panorama.local.html
python scripts/studio_bridge.py project-panorama.local.html `
  --project-root . `
  --open
```

Bridge 的 Apply 会更新传入的 Panorama HTML 并创建备份，因此试用时先复制为已被 `.gitignore`
保护的 `*.local.html`，不要直接把受 Git 跟踪的 Reference HTML 当作可写目标。

需要 Codex 只读架构评审时，显式追加受信 CLI 路径；Bridge 不会从页面接收 executable：

```powershell
python scripts/studio_bridge.py project-panorama.local.html `
  --project-root . `
  --codex-cli "C:\path\to\codex.exe" `
  --open
```

Studio 支持：

- 从正式 Target（无 Target 时回退到现有架构）建立隔离草稿；
- 选择、拖动、增加和删除草稿节点；
- 编辑名称、用途、架构层、职责与状态所有权；
- 增加和移除草稿数据流；
- 撤销、重做、浏览器基础检查、本地浏览器保存和 JSON 导入/导出；
- 将语义操作与纯布局操作分开记录，并在 Inspector 中展示 `seq/at/actor/kind/target/before/after`
  与 `affectsSemanticHash`；
- 显示 Bridge 返回的完整 64 位 Semantic/Layout Hash；离线模式不计算权威 Hash；
- 新建、克隆、重命名、归档和最多三个候选的字段级 Semantic Diff；正式实体按
  `entityRef.type + entityRef.id` 对齐，草稿按 session-local ID 对齐，坐标与操作时间排除；
- 同时显示 semantic edit、候选切换、Data/Git/Source 漂移、CAS Conflict、覆盖不完整等 stale 原因；
- 候选 Tab 支持左右方向键与 Home/End；节点支持 `Alt + 方向键`（10px）和
  `Shift + Alt + 方向键`（40px）移动，且只追加 Layout Operation；
- SVG 连线同时提供可聚焦的数据流文本列表；Drawer/Dialog 关闭后焦点返回触发控件；移动端可在
  组件、画布、属性 / 检查三块之间切换；
- Session Revision 冲突保护、正式 Schema/跨引用/风险规则校验；
- 有界源码内容摘要与 Git/Source Binding 漂移门禁；覆盖不完整时保留草稿但阻止正式化；
- 可选 Codex advisory review，以及独立 Proposal、write-once Approval 与原子 Apply。

`--open` 会在浏览器打开一次性 capability URL；不要把该 URL 分享或提交到 Git。启动 URL 先加载
不含项目数据的 `/studio/` Launcher，经 capability header 验证后才读取受保护的
`/studio/document`；fragment 只进入页面内存并立即从地址栏清除。未提供 `--codex-cli` 时，Bridge
不会从环境变量、PATH 或用户目录自动发现 Codex，只有 Agent 评审不可用，正式 Validator、Proposal、
精确批准和 Apply 仍可运行。提供该参数时只探测指定的普通可执行文件；路径无效会拒绝启动，不回退
到其他 executable。
Codex CLI 已启用但网络/认证/结构化输出失败时，页面会保留失败原因；advisory 失败不冒充成功，也
不阻断用户基于 Formal Validator 继续生成 Proposal。

Source Content Digest 会在本机读取纳入范围文件的字节，只计算路径、大小与内容 SHA-256。源码正文
不会写入 Studio 制品、返回浏览器或进入 Agent bundle；超过 20,000 个文件或 128 MiB，或遇到不安全
链接时，Bridge 将覆盖标为不完整并阻止 Review、Proposal 与 Apply。

画布不会直接修改 `project-panorama-data`、正式 Target、业务代码或 Presentation；只有用户精确确认
完整 Proposal Hash 后，Bridge 才可调用既有 Apply 事务写入正式 JSON。离线模式中的服务端动作保持
禁用。完整安全、状态和故障恢复合同见
[`docs/architecture-studio-contract.md`](docs/architecture-studio-contract.md)。

## Evidence Inspector

进入 `系统 → 架构来源`。Schema 0.2 在运行 Continuous Observation 后会显示事实记录、观察批次、
证据文本、来源提交、架构 Snapshot 和正式 Reference；Schema 0.1 或尚未首次观察的 V0.2 会显示明确
空状态，不影响下方既有架构基线差异。

直接双击 HTML 时，页面只能证明 `recorded as of`，不会因为内嵌 `gitHead` 存在就声称来源仍是当前。
若用上方 Architecture Studio 命令从受控 Bridge 打开同一 Panorama，Inspector 会读取只含 Hash、
文件计数与覆盖状态的 `sourceFreshness` 比较结果，显示检测时间和漂移原因；源码正文不会返回浏览器。

## Trace Route 与 Gate Projection

从 Control 的 Requirement、System 的 Module，或任意 WorkItem / Acceptance / Gate / Release /
Deployment 入口打开右侧 Drawer。`Trace Route` 只呈现 Schema 已有 ID 字段形成的边，并显示精确 JSON
Pointer；没有直接边时，多跳 Route 会保留中间实体，不把它压缩成想象中的直接关系。

`Trace Gap Candidate` 是 Renderer 基于缺少引用或证据派生的注意候选；Formal Finding 只来自已记录的
Validator Finding Code，两者在 UI 中分区显示。`Gate / Acceptance Projection` 的四个结果都是只读派生：

- `change_ready`：Proposal、活动 Candidate、Session Revision、Formal Validation、基线和 CAS 均当前；
- `verified`：相关 required Gate 均为 `passed`，且 Evidence Reference 存在并为 `available`；
- `accepted`：相关 Acceptance 为 `accepted`、`verificationStatus=passed`，且 Evidence Reference 完整；
- `deployed_observed`：存在 Release / Architecture 精确匹配且包含相关 Module Deployment 的 active Deployment。

条件不足时显示 `blocked` 或 `unknown` 及原因。页面不会生成 `integrated` 布尔值、推进 Gate/Acceptance，
也不会执行 Mission、Agent 调度或 Git Promote。

## Verification Receipt Adapter（Experimental）

外部测试、Trial 或 Eval 工具先生成符合
[`schema/verification-receipt.schema.v0.1.json`](schema/verification-receipt.schema.v0.1.json)
的脱敏 JSON。示例见
[`examples/verification-receipt.v0.1.json`](examples/verification-receipt.v0.1.json)。Receipt 只保存摘要、
外部位置、大小、SHA-256、Isolation 与 Redaction 声明；不得放入日志正文、凭据或项目源码。

命令行预览和 Proposal：

```powershell
python scripts/import_verification_receipt.py project-panorama.v0.2.local.html `
  verification-receipt.json `
  --project-root path/to/project `
  --output .panorama-work/verification-receipt-preview.json `
  --proposal-output .panorama-work/pending-receipt-update.json
```

固定流程为 `Schema → Redaction → Binding → Mapping Preview → Formal Validator → Proposal`。Proposal
仍须使用精确 Proposal Hash、独立 write-once Approval 与既有 `apply_patch.py` 事务；Receipt 不会直接把
Gate 设为 `passed`、Acceptance 设为 `accepted`，也不会生成 Review、Approval、Waiver 或自动选优结论。

通过受保护 Bridge 打开页面时，可在 `系统 → 架构来源 → Verification Receipt Adapter` 选择 JSON，
查看 Preview 后再显式生成 Proposal、精确批准和 Apply。Apply 后，Gate/Acceptance Drawer 显示按
Receipt Hash 去重的只读时间线。`not_enforced / not_executed / unknown` 会原样保留。

完整边界见 [`docs/verification-receipt-contract.md`](docs/verification-receipt-contract.md)，实现与浏览器验收见
[`docs/v0.4-p2-verification-receipt-validation.md`](docs/v0.4-p2-verification-receipt-validation.md)。
V0.4 的本地发布审核记录见 [`docs/v0.4-release-audit.md`](docs/v0.4-release-audit.md)。
V0.5.0 的本地发布候选审核记录见 [`docs/v0.5-release-audit.md`](docs/v0.5-release-audit.md)。
V0.5.1 的内部里程碑审计见
[`docs/v0.5.1-release-candidate-audit.md`](docs/v0.5.1-release-candidate-audit.md)；下一次公开发布的多视图规划见
[`docs/v0.6-multi-view-architecture-panorama-iteration-plan.md`](docs/v0.6-multi-view-architecture-panorama-iteration-plan.md)。

## 自动测试与 CI

```powershell
python -m pytest -q
```

CI 在 Python 3.10 / 3.12 上运行测试、Node URL Sanitizer、校验 Reference JSON、生成并校验
Reference HTML、验证 Renderer 升级保持 Data Hash，以及检查 Presentation Hash。

## Presentation Compatibility Contract

普通数据更新必须：

1. 保持 `project-panorama-data` ID 与两个 Marker；
2. 只替换 `application/json` payload；
3. 不改 CSS、Renderer JavaScript 或 DOM；
4. 保留所有 `extensions` 和未知扩展字段；
5. Apply 前后 `compute_presentation_hash` 一致。

## Design Baseline 与 Schema Findings

- 原始设计基线位于 `docs/design/`；
- V0.1 冻结 Schema 为 `schema/panorama.schema.v0.1.json`；V0.2 Continuous Observation Schema 为 `schema/panorama.schema.v0.2.json`；
- 建模缺口记录在 `docs/schema-findings.md`；
- V0.2 不覆盖 v0.1；迁移默认输出新文件。当前 Presentation 升级 Renderer 为
  `0.4.0`，数据 Schema 仍为 0.1/0.2；Studio Session 使用独立 Schema，Evidence Inspector 与
  Trace/Gate Projection 只派生现有事实；Verification Receipt 使用独立 Schema 与扩展投影，不重构
  Panorama Schema。

V0.2 已实现 Observation Snapshot；原 SF-03、SF-08、SF-09、SF-13 所述完整 Architecture Version Snapshot、Artifact Manifest、结构化 Replacement 和 Next Focus Snapshot 仍未实现。
