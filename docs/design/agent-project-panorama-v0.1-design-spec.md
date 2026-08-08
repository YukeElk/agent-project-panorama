# Agent Project Panorama v0.1 — Design Spec

> 文档状态：Implementation Baseline
> 版本：0.1
> 日期：2026-08-08
> 适用范围：个人使用、AI/Vibe Coding 驱动的 Agent 项目、单项目单 HTML
> 配套文件：`panorama.schema.v0.1.json`、`reference-project.v0.1.json`

---

## 0. 摘要

`Agent Project Panorama` 是一个由 Skill 持续维护的、单项目、单 HTML、Local-first 的工程认知控制面。

它不替代需求文档、架构设计文档、接口契约、验收规范、测试报告或代码仓库；它负责把这些工程资产与项目当前状态组织成一张可理解、可追踪、可评审的地图，使项目负责人在长期 AI 辅助开发中始终能够回答：

1. 项目当前处于哪个阶段，当前主线是什么；
2. 自上次全景评审以来，系统发生了哪些具有工程意义的变化；
3. 当前架构、目标架构和迁移状态分别是什么；
4. 每个模块为什么存在、来自哪里、承担什么职责、成熟到什么程度；
5. AI 对开源基线保留、修改、删除和新增了什么；
6. 当前有哪些待评审设计、阻塞决策、验证缺口、部署偏差和重大风险；
7. 下一步有哪些合理路线，为什么推荐其中一条；
8. 当前版本部署在哪里、依赖哪些数据库和外部服务、访问凭据位于何处；
9. 详细约束和证据应去哪个项目文档查看。

核心产品判断：

> Panorama 的核心不是“展示更多信息”，而是把 AI 产生的大量局部动作压缩成稳定的项目认知模型。

---

# 1. 背景与问题定义

## 1.1 目标场景

用户以 AI/Coding Agent 为主要开发执行端，项目可能从以下任一状态启动：

- 已有明确客户需求与业务场景；
- 只有模糊想法，需要与 AI 讨论并调研开源方案；
- 基于开源 Agent 框架改造；
- 基于论文、参考架构或既有代码继续开发；
- 混合使用开源基线、自定义模块和实验性设计。

项目通常采用：

```text
需求澄清
  → 架构基线
  → MVP 实现
  → 集成验证
  → 真实试用
  → 受控迭代与发布
```

模块内部允许实验、返工和迭代，但项目总体必须持续收敛，避免长期停留在“不断修改、无法落地”的循环中。

## 1.2 当前痛点

Vibe Coding 在项目初期效率很高，但随着时间增长，会产生系统性认知损失：

- AI 基于需求给出最终设计，但用户不知道模块如何拆分、为何这样拆分；
- 采用开源架构后，不清楚保留了什么、改了什么、增加了什么；
- AI 不断建议下一步，用户逐渐只能顺着建议推进，失去独立判断；
- “已完成”往往混淆了设计确认、功能实现、验收通过和实际部署；
- 决策缺乏模块与影响范围，难以判断是局部选择还是架构切换；
- 代码、设计、验收、测试、部署和凭据散落，项目成为黑盒；
- HTML 页面容易堆积大量文本，信息虽多但缺少主线和重点。

## 1.3 产品问题

本产品要解决的不是传统项目管理中的工时、人员、日历和任务看板，而是：

> 如何在 AI 高速设计与开发过程中，持续维护项目负责人对需求、架构、模块、演进、验证、部署和下一步选择的理解权与控制权。

---

# 2. 产品定义

## 2.1 定义

**Agent Project Panorama**：

> 面向 AI/Vibe Coding 项目的单文件工程认知控制面。它以架构为主轴，将需求、模块、开源基线、开发成熟度、验收、门禁、决策、风险、架构版本、部署、资源和外部文档关联起来，并通过 Diff-first、Review-driven 的更新机制维护项目掌控。

## 2.2 使用者

V0.1 默认只有一个主要使用者：项目负责人本人。

因此不优先建设：

- 多人协作；
- 权限体系；
- 审批流引擎；
- 团队资源分配；
- 云端同步；
- 实时评论。

## 2.3 载体

- 一个项目对应一个 HTML；
- MVP、V1、V1.1、V2 等版本在同一 HTML 内管理；
- 不因不同部署环境或服务版本复制多个 Panorama；
- HTML 内可查看 Current / Target / Transition，以及不同 Release 的部署和资源关系；
- HTML 动态渲染结构化 JSON，但普通更新不重写展示层。

---

# 3. 目标与非目标

## 3.1 V0.1 目标

1. 从项目第一天开始存在，并允许信息逐步从 Unknown / Draft 演进到 Confirmed；
2. 以模块为核心理解当前系统；
3. 同时表达当前架构、目标架构和迁移过程；
4. 区分模块的设计成熟度、实现成熟度、验证状态和运行状态；
5. 保留关键架构版本和重要 Delta，不保存所有微小操作；
6. 能表达开源基线及 Retained / Modified / Added / Removed；
7. 将工作项关联到模块、需求和阶段；
8. 展示项目阶段、最新评审更新、关注事项和多方案 Next Focus；
9. 展示 Release、Deployment、Resource、External Service 和 Access；
10. 支持 Embedded / External File / External Store 混合凭据模式；
11. 所有更新先生成 Change Preview，经过用户评审后再写入；
12. 识别重大风险、偏航、验证缺口、部署偏差和评审缺口；
13. HTML 只保留摘要和关系，详细正文通过项目文档链接访问；
14. 页面 UI 可被后续 Vibe Coding 大幅重做，不被 Skill 锁死。

## 3.2 非目标

V0.1 不建设：

- Jira / Trello 式任务看板；
- 人员、工时、预算、采购管理；
- 完整甘特图与日历；
- 在线多人协作；
- 完整代码浏览器；
- Markdown/设计文档编辑器；
- 实时 Git 监听；
- 自动代码理解与全仓扫描；
- 内嵌 AI Chat；
- 复杂量化 KPI；
- 百分制“对齐度”；
- Secret 加密或企业级密钥管理。

---

# 4. 核心设计原则

## P1. 架构是主轴

需求是架构的上游输入；模块是系统功能的具体承载者；开发是模块逐步成熟和架构逐步迁移的过程。

因此：

```text
Requirement
    ↓
Architecture Version
    ↓
Module / Connection
    ↓
Work Item / Acceptance / Gate
    ↓
Release / Deployment / Resource
```

`Development` 不作为与 `Architecture` 平级的独立信息域，避免同一个模块在架构区、开发区重复出现。

## P2. 模块是核心认知单元

每个模块必须尽量回答：

- 为什么存在；
- 解决哪些需求；
- 负责什么；
- 明确不负责什么；
- 来源是开源保留、开源修改、自定义新增还是外部服务；
- 当前设计和目标设计有何不同；
- 当前设计、实现、验证和运行分别处于什么状态；
- 有哪些相关决策、风险、验收、门禁、工作项和文档；
- 对应代码目录在哪里。

## P3. HTML 是地图，不是仓库

Panorama 存放：

- 摘要；
- 状态；
- 关系；
- 关键理由；
- 重要 Delta；
- 文档路径；
- 访问入口；
- 风险提示。

完整设计、接口、ADR、验收标准、测试报告和运行手册留在项目工作区。

## P4. Current / Target / Transition 原生建模

不是简单画两张架构图，而是从数据层表达：

- Current：真实存在或正在运行的设计；
- Target：已经提出或经过评审的目标设计；
- Transition：每个模块、连接、数据流或部署从 Current 迁移到 Target 的状态、原因、工作项和阻塞项。

## P5. “完成”拆为四个状态维度

一个模块能运行，不等于设计成熟，也不等于验收通过，更不等于上线。

必须分别管理：

- Design Maturity；
- Implementation Maturity；
- Verification Status；
- Runtime Status。

## P6. AI 自主推进，关键变化人工控制

AI 可以自行拆分模块和推进局部实现，但必须解释设计理由。

变化按影响分级：

- Local Change：AI 可自行完成；
- Module Change：AI 可推进，但必须在 Panorama 更新时要求评审；
- Architecture / Project Change：必须进入明确的人工评审状态。

## P7. Diff-first，Review-driven

任何 Panorama 更新均先生成可读的语义 Diff；未经用户评审，不写入 HTML。

Diff 表达“项目发生了什么”，而不是“AI 改了哪些文件”。

## P8. 不用抽象评分掩盖问题

不显示“Alignment 92%”等缺少解释力的指标。

直接展示：

- Scope Drift；
- Implementation Drift；
- Architecture Gap；
- Verification Gap；
- Review Gap；
- Baseline Drift；
- Deployment Drift；
- Transition Risk；
- Resource Risk；
- Stage Exit Gap。

## P9. Schema 稳定，Presentation 可变

Skill 只依赖稳定的数据锚点和核心 Schema。

页面布局、视觉风格、SVG、项目特有视图可以重构。普通 Update 禁止重写 Presentation Layer。

## P10. 混合凭据模式

项目可按实际需要选择：

- Embedded；
- External File；
- External Store；
- None。

Skill 提示风险，但不强制用户采用单一存储策略。

---

# 5. 核心信息关系

```text
                         PROJECT INTENT
                               │
                               ▼
                         REQUIREMENTS
                               │
                               ▼
                    ARCHITECTURE VERSION
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
           MODULES                         CONNECTIONS
              │                                 │
     ┌────────┼────────┬────────┐               │
     ▼        ▼        ▼        ▼               │
  DECISION   RISK  ACCEPTANCE  REFERENCE         │
                       │                         │
                       ▼                         │
                     GATE                        │
                       │                         │
              ┌────────┴────────┐                │
              ▼                 ▼                │
          WORK ITEM        TRANSITION ◄──────────┘
              │                 │
              └────────┬────────┘
                       ▼
                    RELEASE
                       │
                       ▼
                   DEPLOYMENT
                       │
                       ▼
                    RESOURCE
                       │
                       ▼
                     ACCESS
```

历史轴：

```text
Architecture v0.1
       ↓ Significant Delta
Architecture v0.2
       ↓ Significant Delta
Architecture v1.0
```

项目控制轴：

```text
Changes
  → Update Batch
  → Review
  → Apply
  → Latest Reviewed Update
  → Next Focus
```

---

# 6. 一级信息架构

V0.1 只有三个一级视图：

```text
CONTROL | SYSTEM | EVOLUTION
```

Requirement、Decision、Risk、Acceptance、Gate、Reference 等作为关联对象和 Drawer 内容，不抢占一级导航。

## 6.1 CONTROL

目标：用户隔一段时间重新打开项目后，在 30 秒内恢复掌控。

### A. 项目头部

仅显示：

- 项目名称；
- 当前阶段；
- 当前主线；
- Current Architecture；
- Target Architecture；
- Current Release；
- Dev / Staging / Production 部署状态；
- 最近更新时间；
- 是否包含 Embedded Sensitive Data。

### B. Latest Reviewed Update

显示最近一次已批准更新批次的工程语义摘要：

- 模块成熟度变化；
- 架构 Delta；
- 新增或关闭的关键决策；
- 新增风险或验证缺口；
- Release / Deployment 变化；
- 当前阶段是否改变；
- 总体主线是否改变。

不显示文件修改流水。

### C. Attention

仅显示需要用户当前关注的事项：

- 待评审架构或模块；
- 阻塞决策；
- High / Critical 风险；
- 验证缺口；
- 实现偏离确认设计；
- 资源与部署不一致；
- 生产敏感凭据风险；
- 当前阶段退出条件缺失。

### D. Next Focus

每次 Update 生成 2–3 个候选路线，最多 4 个：

每个方案包括：

- Why now；
- 详细推荐或不推荐原因；
- Benefits；
- Risks；
- Architecture / Stage / Deployment Impact；
- Prerequisites；
- Expected Outcome；
- 关联实体。

必须标明一个推荐方案，但用户可选择其他方案或要求调整。

### E. Requirement Snapshot

只展示高价值摘要和可筛选列表：

- 业务场景需求；
- 功能需求；
- Confirmed / Clarifying / Deferred；
- 当前 Release 范围；
- 模块承载情况；
- 验证状态。

技术选型不得作为 Requirement。

---

## 6.2 SYSTEM

目标：回答“系统现在到底是什么”。

包含三个二级视角：

```text
Logical Architecture | Runtime & Resources | Architecture Source
```

### A. Logical Architecture

支持切换：

```text
CURRENT | TARGET | TRANSITION
```

展示：

- 分层；
- 模块；
- 连接；
- 数据方向；
- 通信协议；
- 同步 / 异步 / 事件 / 流 / 批处理；
- 数据摘要；
- 认证与可靠性摘要；
- 模块状态；
- 待评审、风险和验证缺口标记。

### B. Module Drawer

模块卡片只显示：

- 名称；
- 来源标签；
- Design / Implementation / Verification / Runtime；
- 关键异常数量。

Drawer 显示：

1. Why it exists；
2. Purpose / Rationale；
3. Responsibilities；
4. Non-responsibilities；
5. Requirement mapping；
6. Current Design；
7. Target Design；
8. Architecture Source；
9. Related Decisions；
10. Risks；
11. Acceptance；
12. Gates；
13. Work Items；
14. References；
15. Code Directory；
16. Deployment / Resources。

超过合理长度的正文必须转为外部 Reference。

### C. Runtime & Resources

表达：

```text
Architecture Module
   → Release
   → Deployment
   → Resource
   → Access
```

必须支持：

- 同一项目多个 Release；
- Dev / Test / Staging / Production；
- 不同服务部署不同 artifactVersion；
- 数据库、向量库、缓存、对象存储、队列；
- 外部 API、MCP Server、管理后台；
- Compute / Container Host；
- Access URL / Host / Port / Database；
- Credential Mode；
- Embedded 凭据遮蔽、显示和复制；
- External File 路径和 Key；
- External Store Provider 和 Reference。

### D. Architecture Source

以开源或参考架构为基线展示：

- Retained；
- Modified；
- Added；
- Removed；
- 修改理由；
- 修改区域；
- Upgrade Impact；
- 当前 Divergence；
- 关联模块和 Architecture Version。

---

## 6.3 EVOLUTION

目标：回答“系统如何走到现在，以及正在向哪里迁移”。

### A. Architecture Timeline

仅保留：

- 关键 Architecture Version；
- Significant Delta；
- 评审状态；
- 变化原因；
- Added / Modified / Removed Module；
- Added / Modified / Removed Connection。

### B. Development Stages

默认模板：

1. 需求澄清；
2. 架构基线；
3. MVP 实现；
4. 集成验证；
5. 试用；
6. 迭代与发布。

允许 AI 根据具体项目删减、重命名或增加阶段，但必须在 Update Preview 中说明原因，避免任意增加冗余阶段。

阶段详情包括：

- Purpose；
- Current Focus；
- Entry Criteria；
- Exit Criteria；
- 涉及模块；
- 阻塞决策；
- 阻塞风险；
- Planned / Actual Date；
- Review。

### C. Stage × Module 状态

当前阶段展开时，以模块为行显示：

- Design Maturity；
- Implementation Maturity；
- Verification Status；
- Runtime Status；
- 当前工作项；
- 待评审；
- 阻塞项。

### D. Release & Deployment History

展示：

- Release 版本；
- 对应 Architecture Version；
- 目标阶段；
- 部署环境；
- 每个模块的 artifactVersion；
- 资源；
- 外部依赖；
- 状态与历史。

---

# 7. 核心数据实体

正式字段定义以 `panorama.schema.v0.1.json` 为准。本节描述语义约束。

## 7.1 Project / Intent

`Project` 保存项目身份和当前阶段。

`Intent` 保存：

- Objective；
- Core Scenarios；
- Current Focus；
- In Scope；
- Out of Scope；
- Constraints；
- Success Definition。

Intent 是判断 Scope Drift 和 Next Focus 的最高层依据。

## 7.2 Requirement

仅包含：

- 抽象业务场景需求；
- 功能性需求。

Requirement 同时具有：

- Definition Status；
- Fulfillment Status。

技术选型、模块拆分、框架选择属于 Architecture / Decision，不属于 Requirement。

## 7.3 Architecture Baseline

用于描述：

- 开源项目；
- 论文；
- 参考架构；
- 内部基线。

保存版本、Commit、采用理由、当前 Divergence 和 Upgrade Strategy。

## 7.4 Architecture Version

只在关键评审节点创建。

保存：

- Summary；
- Rationale；
- Baseline；
- Module Delta；
- Connection Delta；
- Review。

不保存所有微小快照。

## 7.5 Module

模块是核心实体。

必须包含：

- Purpose；
- Rationale；
- Requirement IDs；
- Architecture Scope；
- Layer；
- Source；
- Current Design；
- Optional Target Design；
- Four-dimensional Status；
- Code Directory；
- Decision / Risk / Acceptance / Gate / Work Item / Review / Reference 映射。

允许 Target-only 和 Current-only 模块。

## 7.6 Connection

用于表达模块间：

- From / To；
- Current / Target / Both；
- Active / Planned / Deprecating / Removed；
- Protocol；
- Communication Mode；
- Data Summary；
- Contract Reference；
- Auth Summary；
- Reliability Summary；
- Rationale。

数据流和通信要求不另建重复实体，默认由 Connection 承载。

## 7.7 Transition

表达从 Current 到 Target 的迁移。

可作用于：

- Module；
- Connection；
- Data Flow；
- Baseline；
- Deployment。

保存状态、原因、工作项、决策、风险和目标日期。

## 7.8 Work Item

Work Item 是二级实体，不建设完整任务系统。

每个 Work Item 至少关联以下之一：

- Stage；
- Module；
- Requirement。

必须回答：

- 解决哪个模块的什么问题；
- 为什么现在做；
- 预期改变什么状态；
- 实际产生什么影响。

## 7.9 Acceptance Criterion

Acceptance 定义“什么必须成立”。

可作用于：

- Project；
- Stage；
- Module；
- Requirement；
- Release；
- Deployment。

必须记录：

- Defined By；
- Review；
- Expected Evidence；
- Verification Status。

评审规则：

- Human Defined → AI Review；
- AI Defined → Human Review；
- Joint Defined → 按影响级别完成相应评审。

## 7.10 Gate

Gate 定义“执行了什么检查”。

Acceptance 与 Gate 不合并：

- Acceptance：目标条件；
- Gate：验证机制或门禁执行。

Gate 可包括：

- Architecture Review；
- Build；
- Lint；
- Type Check；
- Unit Test；
- Integration Test；
- Performance；
- Security；
- Agent Eval；
- Deployment；
- Manual Review。

必须关联证据路径或明确显示 Evidence Missing。

## 7.11 Decision

只记录会影响未来理解、替换、部署或返工成本的重要决定。

必须包含：

- Question；
- Trigger；
- Context；
- Impact Level；
- 关联模块、需求、阶段、Release；
- 多个 Option；
- Benefits / Drawbacks / Impacts / Risks；
- Reversibility；
- Estimated Cost；
- Recommendation；
- Selection；
- Review。

## 7.12 Risk

不使用单一风险分数，只使用清晰类别和严重度。

Risk 可来源于：

- Human；
- AI；
- Rule；
- Mixed。

## 7.13 Release / Deployment / Resource

### Release

表达要交付的版本及其 Architecture Version。

### Deployment

表达 Release 在某个环境的真实部署，并允许各模块拥有不同 artifactVersion。

### Resource

表达：

- Compute；
- Container Host；
- Database；
- Cache；
- …7 tokens truncated…Store；
- Queue；
- External API；
- MCP Server；
- Admin Console；
- User Account；
- Network；
- Other。

## 7.14 Reference

统一管理外部材料路径，不管理其正文。

类型包括：

- Design Spec；
- Acceptance Spec；
- Interface Contract；
- ADR；
- Test Report；
- Runbook；
- OSS Reference；
- Credential File；
- Source Directory；
- Deployment Config；
- Research；
- Other。

## 7.15 Review / Change / Update Batch

### Change

单条具有工程意义的变化。

### Review

对 Architecture、Module Design、Acceptance、Release、Deployment 或 Panorama Update 的明确评审。

### Update Batch

一次从旧 Revision 到新 Revision 的已评审更新，包含：

- Period；
- Change IDs；
- Summary Items；
- Attention Items；
- Next Focus；
- Stage Before / After；
- Change Level。

CONTROL 的“自上次全景评审以来”由最新 Applied Update Batch 渲染。

---

# 8. 状态模型

## 8.1 Design Maturity

```text
unknown
draft
experimental
review_ready
confirmed
rework
deprecated
```

说明：

- `experimental` 允许先实现功能再优化设计；
- `review_ready` 表示 AI 已形成方案，等待人工设计评审；
- `confirmed` 表示设计已经被接受；
- `rework` 表示已确认设计需要重新处理。

## 8.2 Implementation Maturity

```text
not_started
prototype
functional
stable
deprecated
```

## 8.3 Verification Status

```text
not_defined
pending
partial
passed
failed
waived
```

## 8.4 Runtime Status

```text
not_deployed
development
test
staging
production
retired
```

## 8.5 Transition State

```text
planned
ready
in_progress
migrating
blocked
completed
cancelled
```

## 8.6 Stage Status

```text
not_started
current
blocked
completed
skipped
```

---

# 9. 变化分级与评审规则

## 9.1 Local Change

典型情况：

- 模块内部实现；
- 局部重构；
- 补充测试；
- 文档链接；
- 非关键依赖升级；
- Gate 执行结果；
- Resource 描述修正。

处理：

- AI 可自主完成；
- 更新 Panorama 时记录；
- 无需单独 Architecture Review；
- 若引入敏感信息或造成运行影响，可升级等级。

## 9.2 Module Change

典型情况：

- 模块职责改变；
- Current / Target Design 改变；
- 接口改变；
- 技术组件替换；
- 状态所有权改变但未影响全局；
- 模块内增加重要子组件；
- 模块部署方式改变。

处理：

- AI 可继续不依赖该评审的工作；
- Update Preview 必须明确；
- 进入 Module Design Review；
- 用户批准后才能标为 Confirmed。

## 9.3 Architecture Change

典型情况：

- 新增或删除核心模块；
- Layer 结构改变；
- 主要 Connection / Data Flow 改变；
- 全局状态所有权改变；
- 开源基线替换；
- 从 Single-Agent 切换到 Multi-Agent；
- 核心部署拓扑改变；
- Current / Target Architecture 指针改变。

处理：

- 必须 Architecture Review；
- 可以生成目标架构和迁移计划；
- 未评审前不得把 Target 视为 Current；
- AI 可继续处理不依赖该决策的工作。

## 9.4 Project Change

典型情况：

- Intent 改变；
- V1 范围改变；
- Current Stage 改变；
- Release 策略改变；
- 核心业务场景改变。

处理：

- 必须用户评审；
- 更新 Requirement、Stage 和 Next Focus；
- 明确是否造成架构重新设计。

---

# 10. Update Protocol

## 10.1 触发

用户可以说：

- “创建项目全景”；
- “读取当前全景”；
- “根据这些开发进展更新全景”；
- “需求变更了，更新全景”；
- “引入了新技术方案，更新架构”；
- “部署了新版本，更新资源和环境”。

## 10.2 Read

Skill 必须先：

1. 找到 HTML；
2. 读取 `project-panorama-data`；
3. 校验 Schema；
4. 读取当前 Revision；
5. 识别 Current Stage / Architecture / Release；
6. 读取最新 Update Batch；
7. 检查已有 Pending Review。

## 10.3 Analyze

将用户提供的新信息归类为：

- Intent；
- Requirement；
- Architecture Version；
- Module / Connection / Transition；
- Work Item；
- Acceptance / Gate；
- Decision / Risk；
- Release / Deployment / Resource；
- Reference；
- Change。

必须区分：

- 用户明确事实；
- AI 推断；
- 项目文档证据；
- 未知信息。

未知信息使用 Unknown / Draft / Pending，不得伪造确定结论。

## 10.4 Change Preview

写入前必须输出固定结构：

```text
PANORAMA UPDATE PREVIEW

Current Stage
...

Mainline
Changed / Unchanged

ARCHITECTURE
+ Added
~ Modified
- Removed

MODULE STATE
...

VERIFICATION
...

RELEASE / DEPLOYMENT / RESOURCE
...

DECISIONS / RISKS
...

CHANGE LEVEL
Local / Module / Architecture / Deployment / Project

REVIEW REQUIRED
...

ATTENTION
...

NEXT FOCUS
A / B / C
```

Preview 必须说明：

- What；
- Why；
- Impact；
- Review Requirement；
- Remaining Uncertainty。

## 10.5 Review

用户可：

- Approve；
- Approve with adjustment；
- Reject；
- Defer；
- 选择 Next Focus；
- 要求补充影响分析。

## 10.6 Apply

经批准后：

1. 生成备份 HTML；
2. 校验 Base Revision / Base Hash；
3. 只修改 JSON Data Anchor；
4. 更新 Revision；
5. 写入 Change；
6. 写入 Review；
7. 写入 Update Batch；
8. 更新 Guidance；
9. 运行 JSON Schema；
10. 运行 Cross-reference Validation；
11. 运行 Drift / Risk Rules；
12. 写回 HTML；
13. 复核 Presentation Layer 未发生非授权变化。

## 10.7 Pending Update Package

推荐使用机器可读的临时更新文件：

```json
{
  "baseRevision": 3,
  "baseDataHash": "...",
  "operations": [],
  "changeRecords": [],
  "reviewDraft": {},
  "updateBatchDraft": {},
  "guidanceDraft": {}
}
```

V0.1 可采用 JSON Patch 或等价 Patch 结构。

若 Base Revision / Hash 不匹配，必须重新读取 Panorama 并生成 Preview，禁止覆盖新状态。

---

# 11. 自动检测规则

规则输出明确文本，不输出抽象分数。

## R1. Architecture Gap

条件：

```text
Confirmed / In-scope Requirement
且没有任何 Module 承载
```

## R2. Scope Drift

条件之一：

```text
新增 Module 无 Requirement 且无明确 Rationale
```

或：

```text
Work Item 与 Current Stage、Module、Requirement 均无有效关系
```

或：

```text
当前工作明显属于 Out of Scope / Future Release
```

## R3. Implementation Drift

条件：

- 实际实现引入 Current Design 未声明的关键依赖；
- 模块责任或接口已经改变，但 Current Design 未更新；
- 部署运行的组件与确认架构不一致。

## R4. Review Gap

条件：

- Module / Architecture / Project Change 已实现或迁移，但相应 Review 不存在或 Pending。

## R5. Verification Gap

条件：

```text
Implementation = functional / stable
但 Acceptance 未接受
或 Gate 未配置 / 未运行
或 Evidence 缺失
```

## R6. Baseline Drift

条件：

- OSS Modified 无理由；
- 核心运行时修改区域持续增加；
- Divergence 为 High；
- Upgrade Strategy 缺失。

## R7. Deployment Drift

条件：

- Deployment 运行的 Release 与声明不一致；
- Module artifactVersion 与目标 Release 不一致；
- Active Release 无 Deployment；
- Current Architecture 与 Active Deployment 的 architectureVersion 不一致。

## R8. Transition Risk

条件：

- Current / Target 不同，但无 Transition；
- 旧路径与新路径长期并存；
- Transition Blocked；
- Target Module 已部署，但旧模块未退役且无兼容说明。

## R9. Resource Risk

条件：

- Resource Missing；
- Access / Credential Reference 失效；
- Production Embedded Secret；
- 外部服务缺少用途或依赖模块；
- 数据库或服务环境不明确。

## R10. Stage Exit Gap

条件：

- Stage 被标记 Completed，但 Exit Criteria 未通过；
- 准备进入下一阶段，但核心模块 Verification 仍存在阻塞缺口。

## R11. Architecture Replacement Warning

条件：

- 被替换模块拥有核心状态；
- 高耦合连接数量大；
- 多个 Requirement 直接依赖；
- Replacement 需要改变主要数据流。

提示应明确：

- Module-only Replacement；
- Module + Adjacent Interfaces；
- Architecture Change；
- Project Redesign。

V0.1 只做基础判断，完整 Blast Radius 进入后续版本。

---

# 12. Next Focus 推导规则

推荐优先级：

```text
Critical / Blocking Risk
  ↓
Pending Architecture Review
  ↓
Blocking Decision
  ↓
Current Stage Exit Criterion
  ↓
Core Module Verification Gap
  ↓
Target Architecture Transition
  ↓
Deployment Drift
  ↓
New Feature / New Module
```

每个候选方案必须说明：

1. Why now；
2. 与 Current Stage 的关系；
3. 与 Project Intent 的关系；
4. 对模块和架构的影响；
5. 优点；
6. 风险；
7. 前置条件；
8. 完成后的可验证结果。

推荐逻辑应优先：

- 收敛当前阶段；
- 降低同时存在的实验变量；
- 获取证据后再引入复杂组件；
- 先完成可试用版本，再扩展 Future Scope。

---

# 13. 资源与混合凭据模型

## 13.1 支持模式

### Embedded

适用于：

- 本地开发用户；
- 测试管理员；
- Sandbox Token；
- 临时开发数据库账号。

要求：

- `sensitive = true`；
- 默认遮蔽；
- 用户交互后显示；
- 可复制；
- 页面显著标记 `Contains Sensitive Data`。

### External File

HTML 保存：

- 文件路径；
- Key 名称；
- 用途。

不得自动把外部 Secret 复制进入 HTML。

### External Store

HTML 保存：

- Provider；
- Secret Reference；
- Key 名称。

### None

无凭据或无需记录。

## 13.2 风险说明

密码遮蔽只是显示控制，不是加密。

直接查看 HTML 源码仍可读取 Embedded Secret。

Skill 必须提示：

- HTML 可能被 Git 跟踪；
- HTML 可能被分享；
- Production Embedded Secret 风险较高；
- External File 路径可能失效。

但 Skill 不擅自迁移或删除用户选择的凭据。

---

# 14. Single HTML 技术设计

## 14.1 文件结构

最终项目中只有一个 Panorama 文件：

```text
project-panorama.html
```

内部包含：

```text
HTML / CSS / JavaScript / SVG Renderer
+
Embedded Panorama JSON
```

## 14.2 稳定数据锚点

必须存在：

```html
<!-- PANORAMA_DATA_START -->
<script id="project-panorama-data" type="application/json">
{ ... }
</script>
<!-- PANORAMA_DATA_END -->
```

普通 Update 只修改 Marker 中的数据。

## 14.3 技术约束

- HTML5；
- CSS；
- Vanilla JavaScript；
- Native SVG；
- 不依赖 React / Vue；
- 不依赖远程 CDN；
- 不需要构建工具；
- 不发起遥测；
- 不把敏感信息发送到网络；
- 本地直接打开可用；
- 适配桌面浏览器；
- 页面可打印，但打印不是 V0.1 主目标。

## 14.4 交互

V0.1 允许：

- 切换三个主视图；
- Current / Target / Transition；
- 点击模块打开 Drawer；
- 筛选层、状态、来源和环境；
- 查看连接详情；
- 查看 Release / Deployment / Resource；
- 展开和遮蔽 Embedded Credentials；
- 复制路径、地址和凭据字段；
- 打开相对路径或 URL；
- 搜索 ID / 名称。

V0.1 不允许：

- 在页面中编辑核心实体；
- 拖拽架构；
- 直接改状态；
- 直接批准评审；
- 直接写回文件。

编辑通过 Vibe Coding / Skill 完成。

## 14.5 信息密度约束

- 一个卡片只回答一个判断；
- 卡片正文原则上不超过 3–5 行；
- Module Purpose / Rationale 使用摘要；
- 完整文字进入 Drawer 或外部文档；
- 首页不展示全部 Decision / Risk；
- 不显示无决策价值的计数；
- 不用大段项目说明占据首屏；
- Attention 默认最多显示 5 条；
- Next Focus 默认 2–3 个方案；
- Latest Update 默认显示 3–6 条工程语义摘要。

---

# 15. Skill 能力设计

## 15.1 INIT

适用：

- 模糊想法；
- 明确需求；
- 已有项目；
- 开源改造项目。

输出：

- 最小 Project / Intent；
- 初始 Requirement；
- 默认 Stage；
- Draft Architecture 或 Baseline；
- Unknown / Draft 状态；
- 初始 References；
- Next Focus；
- 单 HTML。

不得为了填满页面而虚构模块和文档。

## 15.2 INSPECT

读取并解释：

- Current Stage；
- Current / Target Architecture；
- Latest Update；
- Attention；
- Core Module State；
- Deployment / Resources；
- Next Focus。

## 15.3 PROPOSE UPDATE

根据对话、用户描述或用户提供的工程材料生成：

- Semantic Diff；
- Change Level；
- Review Requirement；
- Risk / Drift Findings；
- Next Focus Options；
- Pending Update Package。

停止等待评审。

## 15.4 APPLY UPDATE

只对已评审变更执行：

- Backup；
- Patch；
- Revision；
- Change / Review / Update Batch；
- Validation；
- Write-back。

## 15.5 VALIDATE

输出：

- Schema Errors；
- Broken References；
- Duplicate IDs；
- Drift / Risk Warnings；
- Missing Documents；
- Credential Warnings；
- Presentation Anchor Integrity。

---

# 16. 建议目录结构

```text
agent-project-panorama/
├── SKILL.md
├── README.md
├── schema/
│   └── panorama.schema.v0.1.json
├── templates/
│   └── panorama.html
├── scripts/
│   ├── panorama_io.py
│   ├── init_panorama.py
│   ├── read_panorama.py
│   ├── validate_panorama.py
│   ├── propose_patch.py
│   └── apply_patch.py
├── tests/
│   ├── test_schema.py
│   ├── test_cross_refs.py
│   ├── test_apply_preserves_ui.py
│   └── test_reference_project.py
└── examples/
    ├── reference-project.v0.1.json
    └── reference-project.html
```

## 16.1 `panorama_io.py`

负责：

- 提取 JSON；
- 计算数据 Hash；
- 替换 Marker；
- 计算 Presentation Layer Hash；
- 创建备份；
- 原子写入。

## 16.2 `validate_panorama.py`

负责：

- JSON Schema；
- Cross-reference；
- Unique ID；
- Current Stage；
- Current / Target Architecture；
- Review Gap；
- Verification Gap；
- Deployment / Resource；
- Credential Warning；
- Reference Path Warning。

## 16.3 `apply_patch.py`

要求：

- Base Revision / Hash 一致；
- Patch 可应用；
- 数据校验通过；
- Presentation Hash 不变；
- 失败时不覆盖原文件。

---

# 17. V0.1 开发范围

## 必做

1. Core Schema；
2. Reference Data；
3. Single HTML Template；
4. CONTROL；
5. SYSTEM / Logical；
6. Current / Target / Transition；
7. Module Drawer；
8. Runtime / Resource / Access；
9. Architecture Source；
10. EVOLUTION；
11. Stage × Module；
12. Release / Deployment；
13. Embedded Credential Mask / Reveal；
14. Relative Reference Link；
15. Read / Validate / Apply Scripts；
16. Diff-first Update Package；
17. Presentation Layer Preservation；
18. Basic Rule Engine；
19. Reference Project；
20. Skill Instructions。

## 不做

- 页面编辑器；
- 自动 Git 扫描；
- 自动从代码重建架构；
- 云端协作；
- Task Kanban；
- 复杂可视化布局编辑；
- 企业 Secret 加密；
- AI Chat；
- 完整 Impact Graph。

---

# 18. V0.1 验收标准

## 18.1 创建

- 可从模糊想法创建最小 Panorama；
- 可从已有项目材料创建；
- 不确定内容显示 Unknown / Draft；
- 生成文件可本地直接打开。

## 18.2 架构

- 分层、模块、连接可渲染；
- Current / Target / Transition 可切换；
- Module Current / Target Design 可对照；
- OSS Retained / Modified / Added / Removed 可展示；
- Architecture Version + Delta 可追踪。

## 18.3 状态

- 四维模块状态可展示；
- Current Stage 清晰；
- Work Item 能映射到 Module / Requirement / Stage；
- Acceptance 与 Gate 区分；
- Review Pending 明确。

## 18.4 运行态

- Release 对应 Architecture Version；
- Deployment 显示模块 artifactVersion；
- Resource Pool 可查看；
- External Service 可查看；
- Embedded / External File / External Store 可混用；
- Embedded Secret 默认遮蔽。

## 18.5 更新

- Update 必须先 Preview；
- 未批准不 Apply；
- Base Revision 冲突时拒绝覆盖；
- Apply 前自动备份；
- 普通更新不修改 Presentation Layer；
- Revision、Change、Review、Update Batch 正确增长。

## 18.6 控制

至少识别：

- Architecture Gap；
- Scope Drift；
- Implementation Drift；
- Review Gap；
- Verification Gap；
- Baseline Drift；
- Deployment Drift；
- Transition Risk；
- Resource Risk；
- Stage Exit Gap。

## 18.7 体验

针对参考项目：

- 用户 30 秒内能判断当前阶段、最新变化和下一步；
- 60 秒内能理解一个核心模块为何存在及成熟状态；
- 90 秒内能判断 Current 与 Target 的迁移进度；
- 90 秒内能定位 Dev 部署、数据库、外部 API 和测试账号；
- 首屏没有长篇设计正文。

---

# 19. 实施顺序

## Milestone 1 — Model Freeze

- Review Design Spec；
- Review JSON Schema；
- Validate Reference Data；
- 修正实体关系。

## Milestone 2 — Renderer

- 建立 Single HTML；
- 实现三主视图；
- 实现 Current / Target / Transition；
- 实现 Module Drawer；
- 实现 Runtime / Resources；
- 实现 Source View。

## Milestone 3 — IO & Validation

- Extract / Replace；
- Backup / Atomic Write；
- Schema Validation；
- Cross-reference；
- Rule Engine；
- Presentation Hash。

## Milestone 4 — Diff-first Update

- Pending Update Package；
- Preview Renderer；
- Apply Patch；
- Revision Conflict；
- Update Batch。

## Milestone 5 — Skill

- 编写 `SKILL.md`；
- 固化 INIT / INSPECT / PROPOSE / APPLY / VALIDATE；
- 使用参考项目测试；
- 使用一个真实 Agent 项目测试。

## Milestone 6 — V0.1 Review

重点验证：

1. 信息是否仍然过多；
2. 架构是否真的是主轴；
3. Latest Update 是否能恢复项目认知；
4. Next Focus 是否能帮助独立决策；
5. Skill 是否限制 UI 重构；
6. 更新成本是否可接受；
7. 是否存在 Schema 过度设计。

---

# 20. 已知限制

1. V0.1 主要基于对话和用户提供的信息维护，若用户未提供真实工程变化，Panorama 可能滞后；
2. `maskedByDefault` 不等于加密；
3. 浏览器无法可靠检查所有本地相对路径，路径存在性应由验证脚本检查；
4. 自动架构布局只需可读，不追求复杂图编辑；
5. 架构历史只保存关键版本和 Delta，不能完整恢复每一次局部中间态；
6. AI 对 Change Level 和 Drift 的判断仍可能有误，因此评审动作是必要控制点；
7. V0.1 不从代码自动证明实现与设计一致，只能基于用户描述、文档和测试证据判断。

---

# Appendix A — ID 约定

建议：

```text
PRJ-*
REQ-*
BASE-*
ARCH-V*
LAYER-*
MOD-*
CONN-*
TRANS-*
STG-*
WI-*
REL-*
DEP-*
RES-*
DEC-*
OPT-*
RISK-*
ACC-*
GATE-*
REF-*
REV-*
CHG-*
UPD-*
FOCUS-*
```

ID 创建后不得因名称变化而修改。

---

# Appendix B — 默认 Update Preview

```text
PANORAMA UPDATE PREVIEW

BASE
Revision:
Current Stage:
Current Architecture:
Target Architecture:
Current Release:

MAINLINE
Changed / Unchanged
Reason:

ARCHITECTURE
+ Added:
~ Modified:
- Removed:

MODULE STATE
...

VERIFICATION / GATES
...

RELEASE / DEPLOYMENT / RESOURCE
...

DECISIONS / RISKS
...

DRIFT / GAPS
...

CHANGE LEVEL
...

REVIEW REQUIRED
...

NEXT FOCUS

A. ...
Why now:
Benefits:
Risks:
Impact:
Prerequisites:
Expected outcome:

B. ...

C. ...

AWAITING REVIEW
```

---

# Appendix C — Presentation Compatibility Contract

普通 Update 必须遵守：

1. `project-panorama-data` ID 不变；
2. Data Marker 不变；
3. JSON 必须符合当前 Schema；
4. 不重写 CSS / JS / DOM；
5. 不删除项目自定义 UI；
6. 不删除 `extensions`；
7. 遇到未知扩展字段时保留；
8. 只有用户明确提出“重新设计/重构 Panorama UI”时才允许修改 Presentation Layer。
