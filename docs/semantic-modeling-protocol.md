# Project Panorama Semantic Modeling Protocol

本协议定义 V0.1.3 在陌生、已有或复杂项目上执行 INIT 时的强制语义流程。目标是建立可审阅的工程认知模型，不是自动宣布某个文件为真相，也不是替用户修改业务项目。

## 目录

1. [适用范围与停止边界](#1-适用范围与停止边界)
2. [强制 INIT 流程](#2-强制-init-流程)
3. [Project Evidence Discovery](#3-project-evidence-discovery)
4. [Fact Class 与 Authority](#4-fact-class-与-authority)
5. [Source Freshness Audit](#5-source-freshness-audit)
6. [Conflict Detection](#6-conflict-detection)
7. [Truthfulness Rules](#7-truthfulness-rules)
8. [Legacy Panorama Strategy](#8-legacy-panorama-strategy)
9. [Requirement Extraction](#9-requirement-extraction)
10. [Module Decomposition](#10-module-decomposition)
11. [Layer Decomposition and Containment](#11-layer-decomposition-and-containment)
12. [Current / Target / Transition](#12-current--target--transition)
13. [Module 四状态](#13-module-四状态)
14. [Architecture Version Reconstruction](#14-architecture-version-reconstruction)
15. [Risk Candidate 与 Formal Finding](#15-risk-candidate-与-formal-finding)
16. [Next Focus](#16-next-focus)
17. [INIT Preview Gate](#17-init-preview-gate)
18. [Script 与 Agent 的边界](#18-script-与-agent-的边界)

## 1. 适用范围与停止边界

对已有项目、Legacy 项目或复杂项目，执行本协议全部步骤。对只有模糊想法的新建极简项目，可以使用最小骨架流程，但仍不得虚构实现、运行、验证或批准事实。

本协议禁止：

- 修改业务项目以便让 Panorama 更完整；
- 读取 `.env`、私钥、Credential Manager 或正式知识库中的 Secret value；
- 调用网络来补全项目事实；
- 覆盖旧总览或 Legacy Panorama；
- 在 Preview 获得用户批准前生成正式 Managed HTML；
- 读取 `evals/` 中的 case invariants 或其他 Oracle 来回答生产 INIT；
- 将自身推理包装成 Validator Finding Code。

## 2. 强制 INIT 流程

```text
DISCOVER
→ CLASSIFY EVIDENCE
→ ASSESS FRESHNESS
→ DETECT CONFLICTS
→ MODEL PROJECT
→ INFER CURRENT / TARGET / TRANSITION
→ ASSESS GAPS / RISKS
→ GENERATE INIT PREVIEW
→ HUMAN REVIEW
→ INIT
```

不得从“发现几个文件”直接跳到 JSON 或 HTML。发现阶段只建立候选证据地图；Authority、抽象与状态判断由 Agent 在后续步骤完成。

## 3. Project Evidence Discovery

先运行只读工具：

```powershell
python scripts/discover_project_evidence.py path/to/project-root
```

工具输出文件候选、格式、更新时间、生成痕迹、Secret 风险、Knowledge Base 提示、Managed/Legacy Panorama 提示与 Git 元数据。除非路径被识别为知识库，marker detection 可读取 bounded HTML prefix；其他 Inventory 保持 metadata-only。它不读取 Secret value 或正式知识库正文，不越出 project root，也不判定哪个来源一定是真相。

Agent 必须主动覆盖以下证据类别：

### A. Project Intent

- project instructions；
- product requirements；
- research baseline；
- project brief；
- README；
- explicit user decisions。

### B. Machine-readable / Structured State

- JSON、YAML、TOML；
- manifest、registry、state、lock metadata；
- generated status；
- CI configuration。

### C. Current Implementation

- source roots 与 package manifests；
- services、module directories、executable scripts；
- current config；
- container/deployment declarations。

### D. Verification

- tests、test reports、benchmarks；
- acceptance specifications；
- gates、CI、lint configuration。

### E. Decisions / Design

- ADR、decision register；
- accepted proposal；
- architecture document；
- approved review。

### F. Runtime / Resource

- deployment config 与 runtime observation；
- service endpoint declaration；
- external service reference；
- resource manifest。

### G. Narrative Documentation

- README、guide、overview；
- operator manual；
- hand-written status page 或 dashboard。

Inventory 只是导航。根据候选再有选择地读取最相关、非敏感来源，避免无差别加载整个仓库。

## 4. Fact Class 与 Authority

Authority 必须按事实类别判断，禁止使用一个全局 Tier 排序覆盖所有问题。

至少使用以下 Fact Class：

```text
INTENT
REQUIREMENT
CURRENT_IMPLEMENTATION
CURRENT_RUNTIME
TARGET_DESIGN
HISTORICAL_RATIONALE
VERIFICATION
DECISION
RESOURCE
REFERENCE
```

### INTENT / REQUIREMENT

优先顺序：Explicit User Decision → Reviewed Requirement → Accepted Project Brief → Narrative Docs → Inference。

### CURRENT_IMPLEMENTATION

优先顺序：Actual Code / Config → Current Repository Structure → Generated Current State → Docs → Historical Design。

### CURRENT_RUNTIME

优先顺序：Current Runtime Observation → Current Deployment State → Current Config Presence → Generated Runtime State → Historical Verification → Accepted Design → Narrative Docs。

### TARGET_DESIGN

优先顺序：Approved Decision / ADR → Reviewed Target Architecture → Accepted Requirement → Draft Proposal → AI Inference。

Draft Proposal 或 AI Inference 只能形成 draft/proposed/pending Target Candidate，不能形成 confirmed Target。

### HISTORICAL_RATIONALE

优先顺序：ADR → Review Record → Decision Record → Commit/Change Evidence → Narrative Summary。

### VERIFICATION

优先顺序：Current Test Result → Current Gate Evidence → Accepted Historical Evidence → Narrative Claim。

### DECISION / RESOURCE / REFERENCE

Decision 以明确状态和 Review 为准；Resource 必须区分配置意图、检测结果与运行观察；Reference 记录来源与适用事实，不把存在本身当作权威证明。

## 5. Source Freshness Audit

每个用于关键结论的来源必须标注：

```text
current
likely_current
stale
historical
unknown
```

判断至少综合：

- 文件更新时间；
- embedded generated timestamp；
- Git commit 或版本引用；
- 与当前 repository structure 的一致性；
- generator 是否仍是当前版本；
- 内容是否引用旧阶段；
- current machine/runtime evidence 是否已反证。

mtime 只是信号，不是裁决。较新 Narrative 也不能自动覆盖结构化状态；较旧 ADR 仍可能是 Historical Rationale 的权威来源。

Freshness Audit 至少记录：Source、Fact Class、Freshness、Signals、Authority、Used For、Caveat。

## 6. Conflict Detection

主动检查：

- Machine-readable State vs Narrative；
- Runtime Observation vs Historical Test；
- Approved Decision vs Old Design；
- Generated Snapshot vs Current Git；
- Current Implementation vs Accepted Architecture；
- Target vs Current。

冲突必须保留为：

```text
CONFLICT
Source A:
Source B:
Fact Class:
Likely Explanation:
Current Handling:
Needs Human Review:
```

允许解释来源可能处于不同时间、环境或范围，但不得静默选择“更像真的”一方。冲突未解决时，使用 unknown、pending、candidate 或并列陈述。

## 7. Truthfulness Rules

1. Historical Verification ≠ Current Runtime Availability。
2. Planned ≠ Implemented ≠ Deployed ≠ Active。
3. Accepted Design ≠ Current Architecture。
4. Installed ≠ Running。
5. Not Detected ≠ Absent；没有充分反证时使用 unknown、not_detected 或 not_observed。
6. Active Contract / Configuration Intent ≠ Active Runtime。
7. Test Passed Historically ≠ Current Test Baseline Passed。
8. Narrative Documentation cannot silently override fresher structured evidence。
9. Preserve Conflict → Explain Scope / Time Difference。

任何状态都必须有证据范围：时间、环境、对象和观察方式。一个局部测试通过不能把整个项目标为 stable；一个配置文件存在不能把服务标为 active。

## 8. Legacy Panorama Strategy

对承担项目总览作用的 HTML、dashboard、overview 或 project guide 进行标记检测。

### Managed Panorama

同时存在 Managed Marker 和 `project-panorama-data` 时，停止 INIT，转入 INSPECT / VALIDATE。不得把 Managed Panorama 当普通 Narrative 重新建模。

### Legacy / Narrative Panorama

没有 Managed Marker，但内容或命名明显承担总览作用时，默认：

```text
COEXIST
Existing Overview → System / Project Guide
Managed Panorama → Engineering Cognition / Control
```

不得覆盖 Legacy 文件。只有用户明确批准 Adopt Existing Presentation，才提出迁移方案；迁移仍需独立 Preview 与 Review。

## 9. Requirement Extraction

Requirement 优先来自 business scenario、user outcome、functional capability、constraint 与 quality requirement。

不要把技术选型、文件结构、测试工具或框架名称直接变成 Requirement。它们可以是 Module design、implementation evidence、Decision 或 Reference。

初始模型通常保留约 5–15 个核心 Requirement，以认知密度为目标而非机械配额。每项应说明来源、定义状态、覆盖证据与 Unknown。

## 10. Module Decomposition

Module 是可独立解释职责、设计、实现、验证和运行状态的系统责任单元，不等同于目录、文件或任务。

综合以下证据拆分：

- responsibility；
- interfaces；
- state ownership；
- deployment boundary；
- technical cohesion；
- independent evolution；
- requirement coverage。

初始模型通常保持 6–15 个中等粒度 Module，但不机械限制。每个 Module 必须回答：

```text
Why does it exist?
What does it own?
What does it NOT own?
Which requirements does it satisfy?
Where is its implementation evidence?
```

证据不足时合并为较大责任单元并标记待拆分，不要把每个目录强行升级为 Module。

## 11. Layer Decomposition and Containment

完整执行 [Architecture Layering and Containment Contract](architecture-layering-contract.md)。Layer 名称、数量和层深
不是固定分类；必须先声明当前 Scope 的 Primary Viewpoint，再判断系统/能力容器、独立 Module 与 Module Logic。

- 个人项目全景的主画布默认使用 `logical_capability`，Runtime、Deployment、Source 和 Sequence 使用其他 View；
- `parentLayerId` 表示语义包含，不能由坐标或视觉邻近推导；
- Module 只能有一个 Primary Layer，不代表它只有一个观察角度；其他角度由 View 投影表达；
- 归层必须记录责任、接口、状态所有权、部署、安全、数据所有权、独立演进或明确设计决定等依据；
- 常见组件名称、目录和技术标签不能代替证据；
- 无法判断时进入 `LAYER-UNASSIGNED`，保留替代归属和 Unknown，不强制分类。

INIT Preview 的 Current/Target Architecture 必须说明 Primary Viewpoint、父子容器、每个 Module 的归层理由和
关键替代方案。正式 Core 使用合同定义的兼容 `extensions` 保存 Profile、Layer Semantics 和 Module Assignment。

## 12. Current / Target / Transition

### Current

只能来自 actual implementation、actual config、actual runtime 或 accepted-and-implemented structure。Roadmap、proposal 与未落地 ADR 不进入 Current。

### Target

confirmed Target 至少需要 approved decision、accepted ADR、reviewed architecture 或 explicit user-approved target 之一。Idea、proposal、future possibility 必须保持 draft/proposed/pending。

### Transition

只有 Current 和 Target 存在可证实差异时建立。至少说明：from、to、why、status、blocker、decision、risk、acceptance。没有证据时不虚构日期、进度或完成度。

## 13. Module 四状态

对每个 Module 独立判断：

```text
Design
Implementation
Verification
Runtime
```

Implementation=functional 不推导 Design=confirmed、Verification=passed 或 Runtime=active。Current tests、historical tests、deployment declaration 与 runtime observation 分别作为不同证据记录。

## 14. Architecture Version Reconstruction

已有长期项目应尝试提取少量关键 Architecture Versions，而不是只创建 Current / Target 两个标签。版本代表 architecture-changing decisions，不代表每次 commit。

优先识别 initial baseline、关键架构决定、主要组件引入/移除、runtime model change 与 deployment topology change。通常 2–5 个关键版本足够。

若无法可靠重建，明确写 `historical architecture incomplete`，列出已知锚点与未知区间，不补写推测历史。

## 15. Risk Candidate 与 Formal Finding

Semantic Reasoning 发现的 documentation stale、resource unclear、historical/runtime mismatch 等，只能称 Risk Candidate 或 Attention Candidate。

只有当前 Validator 真正执行并返回代码时，才能称 Formal Finding。不得为推理结果伪造 `VERIFICATION_GAP`、`DEPLOYMENT_DRIFT` 或其他 Finding Code。

Preview 中分栏输出：

- Formal Findings：附 Validator execution evidence；
- Risk Candidates：附来源、推理和需要确认的事实。

## 16. Next Focus

Next Focus 基于 Current Stage、Blocking Decision、Exit Criteria、Formal Findings、Risk Candidates、Current→Target Transition 与 Verification Gap，不等于 AI 想做什么。

优先级：Blocking Issue / Decision → Stage Exit → Architecture Review → Core Verification → Transition → New Capability。

提供 2–3 个合理方案，每项包含 Why now、Benefits、Risks、Impact、Prerequisites、Expected outcome，并推荐一个。允许主线、并行工程保障和长期证据流并存，不强制所有 Focus 串行。

## 17. INIT Preview Gate

已有、Legacy 或复杂项目必须执行：

```text
DISCOVER → SOURCE AUDIT → MODEL → INIT PREVIEW → STOP
```

Preview 固定输出：

1. Project Intent
2. Current Stage
3. Current Architecture
4. Target Architecture
5. Architecture Versions
6. Current → Target Transitions
7. Modules
8. Requirements
9. Decisions
10. Acceptance / Gates
11. Runtime / Resources
12. Source Freshness
13. Conflicts
14. Formal Findings
15. Risk Candidates
16. Unknown / Needs Human Review
17. Next Focus

输出 Preview 后停止，不写 JSON、不写 HTML。只有用户明确批准该 INIT Preview，才生成 Schema-valid JSON、运行 INIT、VALIDATE 与 INSPECT。

## 18. Script 与 Agent 的边界

### Script 负责

- 文件发现与格式识别；
- project-root 边界与 Secret 路径保护；
- Git metadata、mtime、marker detection、hash；
- structured diff、Schema validation、cross reference；
- deterministic rules。

### Agent 负责

- source authority judgment；
- requirement abstraction；
- module decomposition；
- architecture reconstruction；
- target inference；
- risk interpretation；
- next focus reasoning。

Python heuristics 不得自动“理解架构”。确定性工具输出 Candidate，Agent 输出带证据和不确定性的语义模型，用户负责最终 Review。
