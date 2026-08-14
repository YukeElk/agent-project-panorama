# Schema Findings — V0.1 第一轮

本文件记录实现期间发现的 Schema 建模缺口。按照本轮约束，
`schema/panorama.schema.v0.1.json` 保持冻结，以下内容均未触发 Schema 重构。

## SF-01 `intent` 可被引用但没有稳定 ID

- 证据：`entityRef.type` 允许 `intent` 且要求 `id`；顶层 `intent` 对象没有 `id` 字段。
- 当前处理：Validator 将项目 ID 作为唯一顶层 Intent 的引用别名；Reference Project 中的
  `intent` 引用因此可以解析。
- 影响：该约定没有被 Schema 明示，其他生产者可能选择不同 ID，造成互操作分歧。
- 建议：后续版本为 `intent` 增加稳定 `id`，或在 Schema/规范中正式声明项目 ID 别名。

## SF-02 `data_flow` 缺少一等实体

- 证据：`transition.subjectType` 允许 `data_flow`，但顶层不存在 Data Flow 集合或对应定义。
- 当前处理：Validator 对已建模 subject type 做类型化解析；`data_flow` 只能做全局 ID
  存在性检查，无法验证其实体类型。
- 影响：无法可靠表达或校验数据流迁移的所有权、生命周期与引用。
- 建议：增加一等 Data Flow 实体，或移除该枚举并明确由 `connection` 表达数据流。

## SF-03 Architecture Version 只有 Delta，没有完整成员快照

- 证据：`architectureVersion` 保存 `baselineIds` 和 `delta`，没有每个版本的完整
  Module/Connection membership。
- 当前处理：Current / Target 以当前 `architectureScope` 和目标 Delta 推导；历史时间线
  展示版本元数据，不尝试伪造任意历史版本的完整拓扑。
- 影响：当版本链出现多段增删、重加或缺失中间版本时，无法仅靠单个版本对象稳定重放拓扑。
- 建议：增加规范化版本成员快照，或定义从根版本开始按顺序重放 Delta 的严格规则。

## SF-04 Architecture Source 的 Baseline 归属存在歧义

- 证据：`module.source.baselineId` 可为 `null`，同时 `changeType` 允许
  `retained / modified / added / removed`；项目又可同时引用多个 Baseline。
- 当前处理：Renderer 将有 `baselineId` 的模块归入对应 Baseline，并把无绑定的
  `added / removed` 作为项目级变更显示。
- 影响：多 Baseline 项目中，未绑定的新增或移除无法确定相对哪个 Baseline。
- 建议：要求基线相关 change type 提供 `baselineId`，或引入明确的项目级来源分组。

## SF-05 Resource–Deployment 关系存在多份可漂移副本

- 证据：同一关系可同时出现在 `resource.usedByDeploymentIds`、
  `deployment.resourceIds` 和 `moduleDeployment.resourceIds`。
- 当前处理：Validator 校验所有 ID 存在，并对明显不一致给出规则告警；不擅自选择一份
  数据覆盖其他副本。
- 影响：生产者可能更新单侧关系，使 Runtime/Resource 视图产生不一致解释。
- 建议：指定唯一事实源，并将其他方向定义为派生字段；或增加严格的双向一致性约束。

## SF-06 External File 凭据路径与 Reference 缺少强关联

- 证据：资源凭据可直接保存 External File path；`reference.kind=credential_file` 也可保存
  文件路径，但两者之间没有必需引用。
- 当前处理：Renderer 仅显示路径与 Key；Validator 分别检查路径，并产生
  `Missing Reference Path` 等告警。
- 影响：两处路径可能独立更新并漂移，无法证明某资源使用的是哪条 Reference。
- 建议：让 External File 凭据强制引用一个 `credential_file` Reference，路径只保留一份。

## SF-07 Delta 中的 removed ID 仍依赖全局实体保留

- 证据：`removedModuleIds` / `removedConnectionIds` 使用普通 ID 列表；跨引用校验要成立，
  被移除实体仍必须保留在全局集合中。
- 当前处理：V0.1 按“保留历史实体、Scope 决定可见性”解释，Reference Project 符合该约定。
- 影响：若生产者物理删除已移除实体，Delta 将成为悬空引用；Schema 未明确历史保留策略。
- 建议：在规范中明确实体墓碑/历史保留语义，或为 Delta 保存不可变历史快照。

## SF-08 Release 没有 Module artifactVersion 目标映射

- 证据：`release.moduleIds` 只声明 Module 集合；`artifactVersion` 只存在于
  `deployment.moduleDeployments`。
- 当前处理：Validator 可检查 Release/Deployment Module 集合、Release ID、Architecture
  Version 和 Active Deployment，但不能判断某个 artifactVersion 是否符合 Release 目标。
- 影响：设计规范 R7 的“Module artifactVersion 与目标 Release 不一致”无法确定性实现。
- 建议：为 Release 增加按 Module ID 索引的期望 artifactVersion 或不可变 Artifact 清单。

## SF-09 Transition 缺少结构化 Replacement 与兼容窗口

- 证据：Transition 只有一个 `subjectType/subjectId`、版本范围、状态、说明和日期；没有
  old/new subject 配对、Compatibility Mode 或最大共存期限。
- 当前处理：Validator 检查 Current/Target 差异无 Transition 与 Blocked Transition；不通过
  名称或自由文本猜测替换关系。
- 影响：R8 的“旧新长期并存”以及“Target 已部署但旧模块未退役且无兼容说明”不能可靠判断。
- 建议：增加 `replacesSubjectRef`、结构化 Compatibility 策略及 coexistence deadline。

## SF-10 缺少 Actual-vs-Declared 运行依赖快照

- 证据：Current Design 保存技术、接口和职责摘要，Deployment 保存 Module/Resource；没有
  扫描得到的实际依赖、实际接口或实际职责快照。
- 当前处理：R3 只对 Active Deployment 超出 Release Scope、运行非 Current Module 等
  可证明的不一致告警。
- 影响：“实现引入未声明关键依赖”或“职责/接口改变但 Current Design 未更新”无法由现有
  数据单独证明。
- 建议：增加可引用的 Actual Dependency/Interface Snapshot，并记录采集时间和证据来源。

## SF-11 Out-of-Scope 只有自由文本

- 证据：`intent.outOfScope` 是字符串列表，Work Item/Requirement 没有结构化 Scope Tag 或
  Out-of-Scope 引用。
- 当前处理：R2 可检查孤立 Work Item 和 Active Work Item 指向 Future Stage；不做自由文本
  相似度猜测。
- 影响：“当前工作明显属于 Out of Scope”无法稳定自动判定。
- 建议：为 Scope 声明稳定 ID，并允许 Work Item/Requirement 显式引用。

## SF-12 Baseline changedAreas 没有时间序列

- 证据：Module Source 只有当前 `changedAreas`，没有按 Architecture Version 或 Update Batch
  保存的历史面积。
- 当前处理：R6 检查 OSS Modified 缺理由/changedAreas、High Divergence 和缺 Upgrade
  Strategy；不推断“修改区域持续增加”。
- 影响：无法基于单份快照判断修改区域增长趋势。
- 建议：记录每次架构版本的 Baseline Diff 指标或可追溯 changedAreas 快照。

## SF-13 Next Focus 历史引用依赖可变 Guidance

- 证据：`updateBatch.nextFocusOptionIds` 引用顶层 `guidance.options`；顶层 Guidance 表示当前建议，
  但历史 Update Batch 需要不可变地解释当次 A/B/C 选择。
- 当前处理：V0.1.1 Apply 保留历史 Guidance Option 的兼容副本，并在
  `guidance.extensions.historicalOptions` 中标记 `historical / inactive`；Renderer 只展示当前
  `guidance.options`。Validator 同时检查所有历史 `nextFocusOptionIds`，禁止产生悬空引用。
- 影响：兼容副本避免连续更新破坏旧 Update Batch，但历史快照仍不属于 Update Batch 本身，
  数据所有权不够清晰。
- 建议：V0.2 将当次 Next Focus Snapshot（Options 与 Recommended/Selected）保存到对应
  Update Batch，顶层 Guidance 只代表当前建议，并提供 v0.1 → v0.2 Migration。

## SF-16 事实观察更新与治理变更共用 Review/UpdateBatch

- 证据：Schema v0.1 的正式更新只能通过带人工批准语义的 Review、Change 与 UpdateBatch
  表达；持续 Git、运行态、验证与规范观察并不是项目决策，也不存在真实的逐次批准人。
- 影响：如果自动追踪复用人工 Apply，会伪造 Review/Approval；如果坚持逐次批准，Current
  又会因长期未维护而与实际项目割裂。
- 决策：V0.2 新增 `observationPolicy`、`sourceBinding` 与 `observationBatches`，使用持久策略
  授权事实更新；人工 Proposal/Review/Apply 保留给治理变更。

## SF-17 实体字段缺少 Fact Authority 与 Evidence Provenance

- 证据：v0.1 只能在零散 `extensions` 中保存来源，无法统一区分 observed、declared、
  inferred、unknown 与 conflict。
- 影响：自动重建 Current 时可能把代码推断误写为已确认架构，或无法解释某个状态来自哪个
  Commit、规范或运行观察。
- 决策：V0.2 新增 `factProvenance`；每个自动变更路径必须记录 authority、confidence、
  evidence、Commit 与时间。推断可披露但不得伪装成批准事实。

## SF-18 Current Architecture 缺少 Git Commit 绑定的可重建快照

- 证据：v0.1 `architecture.currentVersionId` 指向人工维护的 Architecture Version，不能证明
  当前数据覆盖哪个 Git HEAD，也不能在 Hook 遗漏后检测和补偿。
- 影响：长期开发后 Current 与源码实际状态可能无声漂移。
- 决策：V0.2 新增 `currentArchitectureSnapshots`，绑定 Commit、Source Snapshot、模块路径
  观察与未映射变更；`sourceBinding` 明确最后已物化的 HEAD。启动和每次 Hook 均执行补偿检查。

## SF-19 缺少持续规范评估与结果快照

- 证据：v0.1 可以登记规范 Reference，但没有 Standard Pack、Rule、Assessment Result 或
  Pack Hash 的稳定结构。
- 影响：无法证明某次风险披露使用了哪个版本的项目规范，也无法增量复评。
- 决策：规范定义使用独立 `standard-pack.schema.v0.1.json`；V0.2 Panorama 保存
  `standardAssessments` 快照及 Pack Hash，不把规范正文复制进 HTML。

## SF-20 缺少 Architecture Session 与 Candidate 模型

- 证据：Schema v0.1/v0.2 只能保存正式 Architecture Version、Module、Connection、Decision、
  Review 与 Change，不能表达一个尚未批准、可能包含多个候选的架构设计会话。
- 影响：若直接把画布草稿写入正式实体，会把不完整设计伪装成 Target 或污染治理历史。
- V0.3 处理：使用独立 `panorama-architecture-session.v0.1` Schema 与本机持久化合同定义
  Candidate、Assumption、Unknown、Operation 和 Project/Source Binding；不写入
  `project-panorama-data`。正式化仍由 Proposal/Approval/Apply 完成。

## SF-21 正式 Module/Connection 约束不适合不完整画布草稿

- 证据：正式 Module 与 Connection 要求稳定 ID、职责、状态、来源、接口和跨实体引用；设计过程中
  节点和数据流可能暂时缺少这些信息。
- 影响：若对每一步画布编辑强制套用正式 Schema，正常的中间设计状态会被错误拒绝；若放宽正式
  Schema，又会降低正式全景质量。
- V0.3 处理：草稿使用 session-local `nodeId/edgeId` 和浏览器基础检查；不得将这些结果称为
  Formal Finding。正式提案前保守转换为完整实体、保留未编辑正式字段且不因画布缺失而删除实体，
  再运行正式 Validator。

## SF-22 Layout 与 Architecture Semantics 缺少独立持久化边界

- 证据：现有 Schema 不保存画布坐标、缩放或折叠状态，也没有声明哪些编辑影响架构语义 Hash。
- 影响：若把布局变化混入正式语义，移动节点也会使 Approval 失效；若完全忽略操作记录，则难以
  审计真实架构变更。
- V0.3 处理：独立 Session 合同将 `semanticOperations` 与 `layoutOperations` 分离，只有前者标记
  `affectsSemanticHash=true`；Semantic Hash 排除坐标，Proposal 只绑定语义投影。本轮不修改
  Panorama Schema。

## SF-23 Architecture Proposal 缺少完整 Source Snapshot Binding

- 证据：当前人工 Proposal 绑定 Panorama Revision/Data Hash，但架构设计期间 Git HEAD 可能继续变化；
  正式 Proposal 尚未保存完整的设计会话基线与 Source Snapshot。
- 影响：长时间编辑后，候选可能仍基于旧实现事实，旧复审结果不应继续有效。
- V0.3 处理：Bridge 冻结 Proposal 时把 Session ID/Revision、Candidate Semantic Hash、Panorama
  Data Hash、Git HEAD 与 Source Snapshot Hash 放入受 Proposal Hash 覆盖的 Studio Binding；
  独立 Approval 再次绑定该状态。发现变化时标记 `stale` 并阻止后续动作，不自动 rebase。
- V0.3 加固：原 Source Snapshot 是有来源的元数据快照，不能单独证明同长度且恢复 mtime 的文件
  内容未变化。Bridge 因此另计算有界的路径/大小/内容 SHA-256 `Source Content Digest`，并将完整
  覆盖状态和摘要绑定到 Session live baseline 与 Proposal。当前独立 Session Schema 通过
  `additionalProperties` 前向兼容承载 `liveBaseline/formalSourceBindingStatus`；若未来把它们升级为
  稳定跨工具合同，应先新增 Schema Finding 并显式版本化，而不是静默改 Panorama Schema。
- 剩余边界：generic 非 Studio Proposal 仍只强制 Revision/Data Hash；V0.3 不借机改变其兼容合同。

## V0.1 结论

- 本轮未修改或重构 Schema。
- 上述 Finding 均有显式兼容策略，不阻塞 Reference Project V0.1 的读取、校验和渲染。
- SF-01、SF-03、SF-08、SF-09、SF-13 建议作为下一版 Schema 设计的优先议题。

## V0.2 Migration 结论

- SF-16～SF-19 已获得用户明确批准，允许建立 V0.2 Migration。
- V0.2 只取得“观察、记录和披露”授权；不得修改业务项目、自动作出治理决策、伪造批准、
  验收或豁免。
- V0.1 继续只读兼容；迁移必须输出新文件，默认不覆盖原 Panorama。

## SF-24 Fact Provenance 缺少显式 Fact Class 与结构化 Evidence Source

- 证据：V0.2 `factProvenance` 保存 JSON Path、Authority、枚举 Confidence、Evidence 字符串、Commit、
  时间和 Observation Batch，但没有显式 Fact Class，也没有可验证的 Evidence Source 实体引用和来源限制。
- 影响：Evidence Inspector 可以按 Path/注册表派生实体类别，但不能把派生类别伪装成来源事实；Evidence
  字符串也不能默认当作可点击或可验证实体。现有 Confidence 不能换算为百分比分数。
- V0.4 首版处理：只读显示正式 Path/Authority/枚举 Confidence；派生类别明确标记 Derived，Evidence
  默认纯文本。需要稳定 Fact Class、Source Type、Access Class 或结构化 Evidence Binding 时，再设计
  版本化字段并单独批准 Schema 变更。

## SF-25 Observation Conflict 缺少可审计的两侧结构

- 证据：`observationBatches[].conflicts` 当前只是字符串列表，没有 left/right provenance、各自 Authority、
  Confidence、时间、范围、状态或人工处置记录。
- 影响：Renderer 无法可靠展示“冲突两侧证据”，也无法证明某个 Conflict 已被人工确认、保留或解决。
- V0.4 首版处理：展示 Batch 级 Conflict Summary，并关联同 Batch 的 provenance；缺少正式绑定时显示
  `Needs Human Review — structured sides unavailable`。不得从自由文本猜测双方或静默裁决。
- 后续门禁：若需要富结构 Conflict，必须定义独立 ID、side bindings、resolution status、review binding
  与时间语义，并在 Schema Proposal 中保持旧字符串兼容。

## SF-26 Session 缺少持久 stale reasons 与不可变 Journal/Checkpoint

- 证据：Architecture Session 只有 `status=stale`、双操作数组和双 Hash；没有标准化 stale reason 列表、
  原因发生时间、失效制品引用或不可变事件链。当前 Undo/Redo 会恢复 Session 快照。
- 影响：页面可以解释当前运行中检测到的漂移，但刷新后未必保留完整原因；现有操作数组也不能称为
  append-only Audit Journal，更不能宣称恢复 Agent、测试环境或工具副作用。
- V0.4 首版处理：从 Session/Bridge/Proposal/CAS 状态派生并同时显示多个 stale 原因；操作区域称为
  `Session Operations`。不把原因写入正式 Panorama，不声称不可变审计或外部状态恢复。
- 后续门禁：持久 reason、artifact invalidation、event chain 或 Candidate Checkpoint 需要独立版本化 Session
  Schema、Hash 边界、保留策略和迁移方案。

## SF-27 缺少 Verification Receipt 与多次验证历史模型

- 证据：现有 Gate 主要保存当前状态、`lastRunAt/resultSummary/evidenceReferenceIds`；Reference 可保存测试
  报告，但没有版本化 Receipt、运行绑定、隔离事实、redaction manifest、Finding mapping 或多次结果历史。
- 影响：无法仅靠当前 Gate 状态重建 append-only Verification Timeline，也不能安全接收外部 Trial/Eval
  结果；若直接信任 Receipt 更新 Gate，会制造 verification/acceptance 治理事实。
- V0.4 处理：先使用独立 `panorama-verification-receipt.v0.1` Schema 和脱敏导入 Preview；默认只映射为
  候选 `Reference(test_report)`、Evidence 与 provenance，再经过 Formal Validation 和 Proposal。
- 停止边界：Receipt 不执行测试、不调度 Agent、不自动选优、不直接把 Gate 设为 passed，也不形成
  Acceptance、Review、Approval 或 Waiver。
- V0.4 P2 实现：独立 Receipt Schema 与 Reference extension 保存脱敏收据；Schema 0.2 复用现有
  Fact Provenance/Observation Batch 记录导入来源，Schema 0.1 仅保留 Reference/Evidence mapping。没有新增
  Panorama Schema 字段；若要正式持久多次运行、Finding mapping 或处置状态，仍须另行批准 SF-27 重构。

## V0.4 Design Approval 结论

- 2026-08-13 用户批准 Evidence Inspector、Studio Operations/Semantic Diff、Trace Projection 与
  Verification Receipt Adapter 的分阶段设计方向。
- 批准范围是 Design Brief 与按 P0→P1→P2 顺序进入实现；不是对 SF-24～SF-27 Schema 重构的批准。
- P0 首版只使用现有数据派生 Presentation；实时 Freshness 只能由 Bridge 观察，离线 HTML 不得声称
  当前 Git/Source 仍匹配。
- 不新增主视图，不引入通用 Run/Mission/Agent/Trial 平台，不自动执行测试或推进治理状态。
- P1 已在不修改 Schema 的前提下从现有 ID 字段派生 Trace Route 与四类治理投影；缺少引用、证据或
  实时绑定时保持 Candidate/unknown/blocked。若未来需要持久 Trace Edge、用户处置 Gap 或可写投影状态，
  必须另立 Schema Finding，不能复用本次 Presentation 授权。
