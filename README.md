# Agent Project Panorama V0.4（Evidence Inspector P0A）

`Agent Project Panorama` 是一个以架构为主轴、Local-first、单项目单 HTML 的
AI/Vibe Coding 工程认知控制面。它用于恢复和维持对需求、架构、模块、演进、验证、
部署与资源的理解，不是任务看板、文档编辑器或多人项目管理平台。

## V0.4 P0A 能力

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

正式全景数据对页面直接写入保持只读。核心实体编辑先进入隔离的 Architecture Studio
会话；正式评审、精确 Hash 批准和文件写回仍由受控 Bridge 与既有更新事务完成。

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
- Renderer：`0.4.0`（zh-CN，含 Architecture Studio 与 Evidence Inspector）
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
- 将语义操作与纯布局操作分开记录。
- 新建、克隆、重命名、归档和最多三个候选的指标对比；
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
  `0.4.0`，数据 Schema 仍为 0.1/0.2；Studio Session 使用独立 Schema，Evidence Inspector 只派生
  现有事实，不重构 Panorama Schema。

V0.2 已实现 Observation Snapshot；原 SF-03、SF-08、SF-09、SF-13 所述完整 Architecture Version Snapshot、Artifact Manifest、结构化 Replacement 和 Next Focus Snapshot 仍未实现。
