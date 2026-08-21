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

## SF-28 跨记录流缺少统一 Engineering Event Envelope

- 证据：Observation Batch、Proposal/Approval/Apply、Studio Session/Checkpoint 和 Verification Receipt
  分别保存局部历史，字段、时间精度、来源绑定与结果语义不一致。
- 影响：无法按同一 `asOf` 边界重放项目演进，也无法安全地产生可审计的 RAG/Eval 投影。
- V0.5 决策：新增独立 `panorama-engineering-event.v0.1` Sidecar Schema；不修改 Panorama 0.1/0.2。
  Envelope 固定使用 `recordedAt`、可空 `occurredAt` 和 `timePrecision`；`capturedAt/recordedDate/
  timestampPrecision` 只作为实验字段，不进入正式 v0.1。
- 兼容边界：Raw Capture、Post-hoc Record、Derived Projection 通过事件类型、Authority、Evidence Binding
  和 Adapter Loss Report 区分，不在一个字段中压平。

## SF-29 Proposal Supersession、Causation 与 Artifact Invalidation 缺少稳定关系

- 证据：现有 Change/Review 可绑定 Proposal，但无法统一表达 Proposal 被替代、验证由何事件触发、
  或哪个制品因 Source/Data 漂移失效。
- 影响：时间线只能显示相邻记录，不能证明因果、替代或失效链。
- V0.5 决策：Event Envelope 使用稳定 `eventId`，并通过 `correlationId`、`causationEventIds` 和
  `supersedesEventIds` 表达显式关系；不得从时间邻近、文本相似或 Viewer 操作推断关系。
- 兼容边界：缺少来源关系时保存空数组或 Unknown，不回填虚构关联。

## SF-30 非 Git 项目缺少完整 Source Content Digest Binding

- 证据：cat-sys 等真实样本不是 Git 仓库；Git HEAD 无法表达其 Source 覆盖与内容身份。
- 影响：事件、Proposal、验证与导出可能绑定到无法复核的来源状态。
- V0.5 决策：`sourceBinding.mode` 支持 `git`、`content_digest`、`not_applicable`、`unknown`；
  非 Git 的 `content_digest` 必须同时保存 coverage，只有完整覆盖才可称为 current/matched。
- 兼容边界：mtime、目录名和文件计数不能替代 Source Content Digest；不完整覆盖保持 partial/unknown。

## SF-31 Append-only 与 Retention、合法删除、Redaction 的合同冲突

- 证据：事件链需要篡改检测，但项目内容、隐私数据、撤回授权或意外 Secret 必须能够真正删除。
- 影响：把 append-only 解释为不可删除会造成合规风险；静默删除又会伪造完整链。
- V0.5 决策：默认 Event 只保存工程元数据，Secret 永不允许；合法删除使用版本化 Redaction Manifest，
  删除/脱敏载荷后结束当前 Stream Epoch，并以新 Genesis 事件绑定 Manifest 和前一 Epoch Head。
- 兼容边界：不得重算旧 Hash 冒充原链连续；删除授权、影响 Event ID、处置方式和旧 Head 必须可审计。

## SF-32 Event 与 Panorama State 双写缺少 Outbox/Recovery 合同

- 证据：Panorama Apply、Observation 和 Receipt Import 已有各自事务，但 Event Sidecar 是第二个文件系统边界。
- 影响：崩溃可能形成“状态已更新、事件未完成”或“事件声称成功、状态实际未更新”。
- V0.5 决策：使用 Project-local Transactional Outbox。治理操作采用 `fail_closed`：结果写入后若 Event
  尚未 finalized，不得向调用方报告完整成功，后续治理写入必须进入 recovery-required；只读 Trigger/
  Observation 可使用 `compensatable`，但仍须保留 pending/conflict 状态。
- 恢复边界：只按 Base/Expected/Observed Result Binding 决定 finalize、abandon 或 conflict，不自动 rebase，
  不回滚已经可见但无法安全逆转的项目事实。

## SF-33 Dataset Label Strength、Usage Authorization 与 Leakage Boundary 缺少正式模型

- 证据：轨迹实验能生成 Event、ATIF、MLflow 和 RAG 样本，但缺少 reward、模型、token、失败归因、许可
  与稳定 Gold Label。
- 影响：把记录存在直接解释为 SFT/RL 资格会产生错误训练标签和时间泄漏。
- V0.5 决策：Event 只保存 `outcome.labelStrength` 与事实来源；`trainingEligibility` 不进入 Event Schema。
  RAG/Eval/Training 资格由带 `asOf`、项目级 split、许可和人工审阅的 Export Policy 动态计算。
- 停止边界：V0.5 Event Core 不执行训练、不托管向量库、不调度 Agent，也不输出训练就绪声明。

## SF-34 Core/Event 到只读 View IR 的 Binding 与可重算合同缺失

- 证据：当前 Renderer 直接消费 Panorama 大对象；验证原型表明多视图需要共享稳定身份和明确 Loss 边界。
- 影响：若 View IR 可写或缺少输入 Hash，它会变成 Core/Event 之外的第三套事实源。
- V0.5 决策：View IR 只由 Core Data Hash、Event Checkpoint/`asOf`、Compiler Version 和 Source Binding
  确定性编译；任何 Viewer/布局状态均不写回 Core、Event 或 View IR。
- 兼容边界：V0.5.0 只冻结合同，不在 Event Core 稳定前重写 Renderer。

## SF-35 关系稳定身份与 Exact-ID Delta 缺少统一合同

- 证据：正式实体和 Studio Candidate 已按类型 + ID 对齐，但关系在不同投影或外部作者模型中没有统一身份。
- 影响：数组重排、布局变化或同名关系可能被误报为架构语义变化。
- V0.5 决策：关系 ID 由关系类型、精确端点 ID、端口/方向和明确 discriminator 的 canonical identity
  生成；Delta 只按稳定 ID 分类 added/removed/changed/unchanged。
- 兼容边界：显示名称、坐标、主题和文本相似度不得用于认定同一关系。

## SF-36 派生制品缺少 Machine Receipt、last-good 与视觉状态合同

- 证据：结构/几何检查能发现溢出，但不能证明人工视觉通过；候选失败也缺少统一 last-good 交付语义。
- 影响：漂亮但错误的制品可能覆盖可用版本，或自动检查被误报为人工验收。
- V0.5 决策：Verified Delivery Receipt 分开记录 semantic、schema、geometry、browser、privacy 和
  `visualReview=pending|accepted|rejected|skipped`；只有候选全部满足相应门禁才原子替换 last-good。
- 兼容边界：自动视觉检查不生成 `accepted`；浏览器不可用时保持 structural-only，不得提升为交互通过。

## SF-37 审批粒度按机械步骤切分，缺少可委托且可验证的 Policy Envelope

- 证据：治理 Apply、Receipt Import、项目测试、Event Head Recovery、Studio 正式化与派生制品交付分别
  使用独立人工确认；其中一部分确认只重复授权确定性机械步骤，并不产生新的治理决策。
- 影响：长期轨迹采集和验证会频繁打断，用户容易把“机器校验通过”“人工查看过”和“批准风险边界”混为
  一谈；若直接跳过现有 Approval，又会伪造授权或让 Agent 自行扩权。
- V0.5 决策：新增独立 `panorama-approval-policy.v0.1` Sidecar Schema，将门禁分为 Decision、Delegated
  Policy、Automatic Quality 和 Read-only 四类。每个 Policy 只允许一个固定 Operation，绑定 Project、
  Producer/Command/Path/Artifact Scope、有效期、使用次数、停止条件、Policy Hash 和人工批准。
- 保留边界：Target/Requirement/Decision/Review/Acceptance/Gate/Waiver/Guidance/Credential/Architecture
  Candidate 的正式语义变化、Policy 自身变化、冲突裁决、风险豁免、外部发布、数据集导出、Hook 安装和
  破坏性删除继续要求逐次精确人工批准。
- 自动边界：Schema 0.2 受信 Receipt Evidence 追加、预批准命令、Event Append、完全确定性的 Head 重建、
  机器门禁通过后的本地 last-good 晋升、Renderer Candidate 和 fact-only Freshness/Observation 可在 Policy
  范围内免逐次审批；每次执行仍须生成绑定 Policy ID/Hash 的 Receipt/Event，并 fail closed。
- 单次批准：Studio Freeze 产生的规范 Proposal 与 Apply 使用同一 Approval；不得再要求第二个普通 Proposal
  Approval。Schema 0.1 Receipt 因缺少正式 Provenance/Observation Batch，仍保留人工 Proposal 路径。
- 实现边界：本轮只冻结 `approval-policy-contract.md`、Policy/Execution Receipt/Revocation Schema、示例和契约测试；
  在 Policy Runtime 与各 Adapter 实现前，现有命令行行为保持不变，不以提示词绕过 Approval。

## V0.5 Design Closure 结论

- 2026-08-20 用户批准先完成设计闭合，再开始 V0.5 迭代。
- SF-28～SF-37 以 Sidecar-first、Panorama Schema 0.1/0.2 不变为前提进入实现。
- 唯一正式 Event v0.1 合同使用 `recordedAt / occurredAt / timePrecision`；实验 Schema v0.2 仅作验证输入。
- 首个实现切片限定为 Runtime Preflight、Event Schema、Event Store、Recorder、Validator 与受控测试；
  不包括现有事务 Adapter、Renderer 改版、C4 全量导入、Dataset Export 或后训练。
- Outbox、Retention/Redaction、Transformation Loss 先冻结机器合同；只有基础 Event Store 通过原子、并发、
  幂等、篡改和恢复测试后，才允许将其接入 Proposal/Apply、Observation、Receipt、Studio 或 INIT。
- Approval Policy 只授权预定义低风险 Operation；Policy 激活本身仍是治理决策。只有 Policy Hash、Expiry、
  Use Ledger、Execution Receipt、保护路径和 fail-closed 测试全部通过后，才允许取消对应的逐次人工确认。

## SF-38 Operation Effect 与 Policy Core 审计之间缺少 Adapter Transaction

- 证据：Approval Policy Core 能在副作用前分配 Use，并在成功后写 Audit Event、Execution Receipt 与 Ledger；
  但真实 Operation 的效果位于独立文件系统边界。进程在效果已发生、Audit/Receipt/Ledger 尚未全部完成时，
  Core Pending 本身不能保存 Before、Expected、Observed Result，也不能证明重试是否应再次执行效果。
- 影响：若 Operation Adapter 只依赖 Pending Use，post-effect/pre-receipt 崩溃可能造成重复副作用、错误 no-op、
  自动 rebase 或永久无法解释的审计缺口。SF-32 约束 Panorama/Event 双写，SF-37 约束授权 Envelope，均不能
  替代每种 Operation 的精确效果事务。
- V0.5.1 决策：为首个 `event_head.recover` Adapter 增加独立
  `panorama-event-head-recovery-transaction.v0.1`，绑定 Policy ID/Hash、Use、Input Hash、固定 Store、Before
  Head、Expected Tail、Observed/Final Head、Output Hash、Receipt/Event 与 `stateHash`。状态仅允许
  `prepared/allocated/effect_observed/finalized/failed/conflict`。
- 恢复边界：只接受原 Before、原 Expected Tail、精确 durable Receipt 或已完成 Core 四类可证明状态；任意
  第三状态、Binding/Hash 篡改或歧义均 fail closed，不重做 Event、不推断 Epoch、不自动 rebase。
- 后续门禁：第二个 Operation Adapter 必须证明此事务模式可复用或登记其特有的 Effect Contract；不得直接
  以 Policy Pending 充当通用 Saga/Agent Runtime。

## SF-39 Source Topology Observation 缺少 typed edge、解析状态与 Extractor Receipt

- 证据：现有 Panorama Module/Connection 是正式架构模型，`factProvenance` 能绑定观察来源，但没有保存
  import/dependency/call/data-flow/config relation 的种类、resolved/unresolved/non-followable、工具版本和 Loss。
- 影响：直接从源码生成架构时，import、package、service、runtime call 与 data flow 容易被压成同一种 Edge，
  动态加载和缺失解析也可能被错误显示为不存在。
- V0.6 决策：Source Extraction 先输出独立 typed Observation 与 Extraction Receipt，再由 Model Compiler
  归一化；每条输出绑定工具版本、Source Revision/Digest、位置、解析状态、Access Class 和 Loss Report。
- 实现门禁：只有 Repository Inventory 与至少一个 Language/Config Adapter 原型通过正负向测试后才冻结
  Source Observation/Receipt Schema；不得先创建无消费者的空壳机器合同。

## SF-40 Source Grouping 与正式 Architecture Adoption 缺少权威边界

- 证据：源码目录、package、workspace 和 import cluster 可以帮助理解结构，但不自动具备正式 Module 的职责、
  状态所有权、部署边界或 Target 评审事实。
- 影响：若 Extractor/LLM 直接创建 Current Module，会把启发式分组冒充治理事实，并绕过 Architecture Proposal。
- V0.6 决策：源码分组只能是 `derived candidate`；已有正式 Module 映射继续使用 authored ID。采纳新分组为
  Current/Target/Decision 必须经过精确 Proposal Hash 的人工批准。
- Slice A：Core Model Compiler 只投影正式 `architecture.modules/connections/layers`，不读取目录或源码补边。

## SF-41 Sequence Message Order 与运行因果缺少来源合同

- 证据：Connection、import、dependency 和时间相邻事件都不能证明一次请求中消息的实际顺序、同步/异步、
  return、retry 或 failure path。
- 影响：直接把静态拓扑转为时序图会制造运行因果，漂亮的箭头可能被误认为 observed trace。
- V0.6 决策：Sequence 只接受有序 Engineering Event/runtime trace、作者 Dynamic View、可验证控制流
  `derived_possible_sequence` 或用户批准的 Declared/Target 流程。每条 Message 必须绑定顺序来源。
- Slice A：Model Connection 的 `semantics.order` 固定为 null；Schema 虽预留 Sequence Profile，但没有 Compiler，
  不得描述为已实现。

## SF-42 Cross-view Scope、Layer 与 Partial Graph Closure 缺少确定性规则

- 证据：同一 Module 可同时属于 Current/Target，并可能在 Target 使用不同 Layer；Relation 只有端点和 Scope
  同时满足时才应进入某个 View。
- 影响：复用单一 `layerId` 或为保持图连通而补代理边，会混淆 Current/Target，产生悬空 Edge 或错误 Boundary。
- V0.6 决策：Model IR 分别保存 current/target/historical Layer Binding；module View v0.1 每次只投影一个
  Architecture Scope。Edge 仅在两个端点 Node 均存在且 Relation Scope 匹配时进入 View。
- Hash 边界：Scope/Label/Binding/Evidence 进入 View Semantic Hash；坐标和 Viewer State 不进入。

## SF-43 Multi-view Candidate 缺少跨语义、几何、交互与性能的发布证明

- 证据：现有 Formal Validator 验证 Panorama Core，SF-36 只冻结了通用派生交付边界；多视图还需要验证
  cross-view ID、Evidence Pin、Sequence Order、Geometry、Deep Link、viewport、可访问性和容量分层。
- 影响：单个 Schema-valid JSON 或一张漂亮截图不能证明多图一致、可交互、可读或适合公开发布。
- V0.6 决策：View Validator、Renderer Validator、Browser Evidence、Machine Receipt 和人工视觉状态分层；
  失败候选不替换 last-good。代表性公开制品必须绑定精确 Artifact Hash 的人工 `accepted`。
- 实现门禁：Renderer 前至少准备 Architecture/Data Flow/Sequence 三种 Schema-valid Fixture；Delivery Schema
  只有 Candidate/last-good/Receipt/Transaction 故障测试通过后才冻结。

## V0.6 WP0 Core Design Closure 结论

- 2026-08-21 用户批准开始 V0.6 迭代；V0.5.1 以本地内部基线提交 `b0b2e47` 保留，不单独发布。
- SF-34～SF-36 与 SF-39～SF-43 共同冻结 Truth → Model → View → Layout/Viewer State 边界。
- 本轮只冻结并实现已有消费者的 `panorama-model-ir.v0.1` 和 `panorama-view-ir.v0.1` Machine Schema；
  Source/Renderer/Delivery 行为合同已闭合，对应 Schema 随真实工作包实现冻结。
- WP1 Slice A 只支持 Formal Panorama Core → Model IR → single-scope Module Architecture View IR；源码抽取、
  多图 Renderer、Sequence、Event Capture、last-good、Export 和后训练仍不得外推为已实现。

## SF-44 Source Extraction 安全 Receipt 容易把“未读 Secret 路径”误写成“未读任何 Secret 值”

- 证据：WP2 真实原型需要瞬时读取普通源码正文才能运行 AST/静态 import parser；普通源码中可能嵌入未分类
  Secret，而本轮没有执行源码 Secret scanner。
- 影响：若 Receipt 使用 `secretValuesRead=false`，会把路径策略误报为内容级隐私证明，并与实际 parser 行为
  冲突。
- V0.6 决策：安全 Receipt 拆分为 `sourceBodiesReadTransiently=true`、`sourceBodyPersisted=false`、
  `classifiedSecretPathsRead=false`、`embeddedSecretScan=not_performed`。只有分类为 Secret 的路径保证不读取；
  不对普通源码内容做“无 Secret”声明。
- 保留边界：Source Body、Prompt、模型输出和 Secret 值均不得进入 Observation/Receipt/Loss/Model/View。

## SF-45 JS/TS 有界静态模式不能冒充完整语言解析器

- 证据：WP2 首个无依赖实现可识别 comment-aware `import/export from`、`import type`、`require` 与 dynamic
  import，但不具备完整 AST、路径别名、生成代码、条件导出和 bundler resolution 语义。
- 影响：若 Adapter 只输出关系而不披露解析器能力，缺失关系可能被误解为不存在，低置信关系也可能被提升为
  精确源码事实。
- V0.6 决策：JS/TS Adapter 固定为 `partial`，关系 Authority 为 `inferred`、Confidence 为 `medium`，并在
  Information Gap 与 Loss Report 中记录 `javascript_typescript_full_parser_not_used`。后续完整 parser 必须使用
  新 Adapter Version 与独立对照测试，不能静默替换语义。

## V0.6 WP2 Slice A 结论

- Repository Inventory、Python AST、JS/TS 有界静态 import、package/pyproject manifest、Source Observation、
  Extraction Receipt 与 Loss Report 已形成端到端消费者后冻结机器 Schema；
- Source Observation 可合并进 Model IR，但始终使用 `source_element` 与 dependency candidate，不改变正式
  Module Architecture View；
- Source Extraction 仍不是多图 Renderer、完整 JS/TS parser、runtime trace、Sequence 或正式架构采纳。

## SF-46 View Group 不能继续等同于 Architecture Layer

- 证据：Dependency/Data Flow 需要按 source kind 辅助阅读，Deployment/Runtime 需要按 environment 辅助阅读；
  两者都不是正式 Architecture Layer。
- 影响：若继续复用 `layerId`，视觉分组会伪造架构分层；若完全不表达分组，Renderer 只能按标签猜容器语义。
- V0.6 决策：View Group 显式携带 `groupType + groupRef`；`layer` 组必须精确绑定 Model Layer，
  `environment/source_kind` 组的 `layerId` 必须为 null。Group 只属于阅读投影，不建立 ownership、security 或
  deployment boundary 事实。

## SF-47 Declared Deployment 不能冒充 Observed Runtime

- 证据：Panorama Core 的 Release/Deployment/Resource 是正式声明记录，但 deployment status、artifact version、
  resource association 本身不能证明进程、网络、调用或健康状态已被运行观察。
- 影响：若 Deployment View 直接命名为 Runtime Truth，会把配置与部署记录提升为实时事实，也会掩盖观察缺失。
- V0.6 决策：Model IR 投影 Release、Deployment、Resource 与三类 deployment relation，同时保留 Fact
  Provenance；默认 `declared`，只有 exact provenance 为 observed 才允许 `runtimeObserved=true`。View 只显示
  已有关系，并在任一关系未被运行观察时增加 `deployment_runtime_observation_not_verified`。
- 安全边界：Resource 只投影类型、环境、状态、provider/location；不复制 Access、Credential 或 Endpoint 正文。

## V0.6 WP3 Slice A/B 结论

- Dependency/Data Flow Compiler 只消费 `dependency|data_flow`，支持精确 root、深度与节点预算，超限 fail closed；
- Deployment/Runtime Compiler 只消费正式 deployment relation，按 single scope 和 deployment environment 过滤，
  保留 shared resource 的真实端点闭包；
- 跨视图 Node/Edge ID 继续由 Model Entity/Relation ID 派生，坐标仍不进入 Semantic Hash；
- Sequence、Lifecycle、Evolution/Risk 和 Renderer 仍未完成，不得从静态关系推断顺序或运行因果。

## SF-48 Engineering Event 顺序不能自动提升为 Runtime Message 或 Lifecycle Transition

- 证据：Engineering Event 已证明 Stream Sequence、Actor、Subject、Correlation 与 Outcome，但通用 Event 并未
  声明网络参与者、同步/异步协议、return/retry，也通常没有 lifecycle 的 before/after state。
- 影响：把每条 Event 直接画成运行调用会制造系统因果；把 succeeded/failed 当状态终点会虚构前态和恢复边。
- V0.6 决策：Event Checkpoint 只接受完整有效且 Head=current 的 Event Chain。Sequence 可将 Actor → Subject
  投影为 `engineering_event_action`，顺序只来自 `stream.sequence`，并明确不是 runtime call。Lifecycle 仅消费
  经受约束 Adapter 显式提供的 before/after state；缺失时输出 Information Gap，不猜 transition。
- 身份边界：Actor 和未进入正式 Model 的 Subject 使用 Event-derived Entity；若 Subject ID 精确匹配正式 Entity，
  则复用正式 Entity。Checkpoint 绑定 Project、Stream、Epoch、Tail ID/Hash、Sequence 与 canonical hash。

## SF-49 Renderer Machine Pass 不能冒充独立人工视觉 Acceptance 或 Verified Delivery

- 证据：WP4 首次真实浏览器截图在 Schema、JavaScript 和交互可运行时仍暴露直线穿节点、Dense Label 干扰、
  Fit 非真实、长文本横向溢出和 Empty 遮罩误显；这些问题不能由 View IR Schema 捕获。
- 影响：若把 DOM/Geometry/截图自动检查直接写成 `visualReview=accepted`，会混淆机器状态与人工判断；若把
  Candidate 直接当 last-good，失败渲染可能覆盖仍可用制品。
- V0.6 决策：Renderer 输出固定为 `visualReview=pending`；Geometry、Browser、Agent Visual Review 和独立人工
  Visual Acceptance 分栏记录。只有 WP6 Candidate/last-good 原子事务、Machine Receipt、故障测试和精确
  Artifact Hash 人工 Acceptance 全部通过后，才允许 Promote/公开发布。
- 实现结果：WP4 Candidate 使用分层布局、正交路由、真实 Fit、长文本约束、六图共享交互与独立 Geometry
  Validator；四档 Desktop 和两档窄屏机器矩阵通过，但独立人工 Acceptance 继续保持 pending。

## V0.6 WP4 Machine Candidate 结论

- 同一 Model 绑定六个 View 后生成 hash-bound Single HTML，不依赖框架、CDN、远程字体或默认网络；
- Search/Focus、Scope/Freshness/Risk/Evidence Filter、上下游 Trace、稳定选择、Entity/Relation/Event/Trace
  Deep Link、Evidence Drawer、Pan/Zoom/Fit、Theme 和键盘路径已进入真实浏览器验证；
- `385 passed, 2 skipped`、20 Schema、Skill Validator、URL Sanitizer、JS Syntax、Geometry 与 Browser Console
  门禁通过；
- 层级聚合/展开、Proof Lab 和独立人工视觉 Acceptance 仍属于后续工作包。

## SF-50 last-good 晋升必须把效果事实与 Policy Receipt 分离

- 证据：单独写 Candidate Receipt 不能证明 last-good 已替换；Policy Pending Use 也不能证明文件副作用发生。
  进程可能在原子替换后、Audit Event/Execution Receipt 前中断。
- 影响：若以 Pending 或重跑 Renderer 作为恢复手段，可能重复覆盖、消费不同输入，或把第三方修改误判为本次
  效果；若先写成功 Receipt，则可能产生“审计成功但 last-good 未变化”。
- V0.6 决策：新增独立 Promotion Transaction，输入绑定 Policy Hash、Delivery Receipt/Candidate、last-good
  Before/Expected；状态限定为 prepared → allocated → effect_observed → finalized，每态带 `stateHash`。恢复只
  接受 Before 或 Expected；第三状态保留现场并 fail closed。成功必须绑定 Engineering Event、Execution Receipt
  与 Ledger，Core durable Receipt 只能按精确 Use 恢复。
- 实现结果：候选生成不触碰 last-good；相同字节 no-op 不消耗 Use；post-effect 中断只补审计；候选/事务篡改、
  第三状态和并发 Writer 均有负向测试。人工视觉继续是独立事实，机器证据固定为 pending。

## V0.6 WP6 Machine Slice 结论

- 冻结 Browser Evidence、Verified Delivery Receipt、Promotion Transaction 三个独立 v0.1 Schema；
- self-host 六视图候选完成四档 Desktop 与两档窄屏 hash-bound 证据，机器门禁通过，人工视觉 pending；
- `verified_delivery.promote` 成为第二个真实 Approval Policy Operation Adapter，沿用单 Operation、Use-before-
  effect、Event/Receipt/Ledger 与确定性 resume 边界；
- 该结论只允许内部 Candidate/last-good，不代表 Proof Lab、人工视觉、安装、Tag、Push 或公开 Release 完成。

## SF-51 Source Resolver 的路径规范化是事实准确性门禁

- 证据：Panorama × Archify 受控同项目实验首次发现，JS/TS `../../` 与 `../../../` 内部 import 在匹配
  Inventory 前只转换了分隔符、没有消解 `..`，使 5/5 可解析内部依赖被错误标成 unresolved。
- 影响：Dependency View 会创建伪 Unknown Target，降低实体闭包与事实召回；单元测试中的 `./helper` 不能覆盖
  多包仓常见的父目录相对导入。
- V0.6 修复：使用 project-relative POSIX lexical normalization；规范化后越出根的路径仍拒绝，合法父目录相对
  import 精确匹配已读取 Inventory。受控项目由 6 files / 11 elements / 5 unresolved 变为 6 files /
  6 elements / 0 unresolved；新增父目录相对 import 回归测试。

## SF-52 零个受支持输入不能报告 Source Extraction completed

- 证据：固定 Spring PetClinic 切片包含 63 个文件，其中 49 个 Java 源文件；当时 V0.6 Extractor 没有 Java Adapter，旧逻辑返回
  0 files / 0 elements / 0 relations 且 status=completed。
- 影响：空图会被误读为“项目没有架构关系”而不是“工具不支持该语言”，直接违反 Loss/Unknown 边界，并让
  公开项目门禁产生假阳性。
- V0.6 修复：识别常见 source-like 未支持扩展；存在时 Source coverage、Receipt、Loss Report 固定为 partial，
  披露 `unsupported_source_languages_present` 与聚合 Loss，不读取正文。零个受支持输入时
  `sourceBodiesReadTransiently=false`；Extraction Receipt Schema 允许该布尔值如实表达。非 Git 输入无法用
  included-content digest 绑定未读取正文，故 `currentness=unknown` 并披露
  `unsupported_source_snapshot_not_content_bound`。
- 后续结果：有界 Java `package/import` Adapter 已读取全部 49 个 Java 文件并用完整 Content Digest 绑定，
  不再产生 unsupported-language 假空图；但它仍不是语义架构生成器，完整发布影响见 SF-54。

## SF-53 固定六 Profile Tab 不能表达同 Profile 的 Current/Target/Conflict Guided Views

- 证据：受控项目的正式 Model 同时含 Current 与 Accepted Target；V0.6 Candidate 的 Module Slot 只接受一个
  current-scope View，导致 NATS Target 在同一交付 Artifact 中没有可见入口。Archify 用三个 Guided Views 在
  同一证据图上切换当前路径、已接受目标与证据冲突。
- 影响：即使 Model 保留事实，Renderer 仍可能通过 View Set 选择隐藏关键 Target/Conflict；六种图类型不等于
  完整项目全景，也不能用空 Evolution View 替代未提供的 Event History。
- V0.6 设计调整：新增 View Set / Guided View Contract，允许同一 profile 的命名 scope variant 与 Story；
  Renderer 顶层选择 Guided View，再显示 profile/scope/evidence。所有 variant 继续精确绑定同一 Model 和
  Evidence，不复制或编辑事实。
- 实现结果：Renderer 0.2 已在同一 Artifact 提供 Current/Target Module，Deep Link、跨 Variant 选择和六档
  viewport 通过；Conflict 仍必须来自正式 Fact Status，不能由 View Set 文案制造。

## SF-54 Java import coverage 不等于源码架构语义覆盖

- 证据：有界 Adapter 在固定 PetClinic 切片读取 49 个 Java 文件，形成 221 个 Source Element 与 468 条静态
  import，其中 24 条精确解析到项目内文件；但 10 条 Oracle 还包含 Controller Annotation、直接 Repository
  注入、Thymeleaf/数据库配置、冲突、运行未知与测试未知，均不由 import 关系单独证明。
- 影响：把“语言已支持”宣传成“源码架构已生成”会再次形成假阳性；外部符号和文件依赖也不能自动成为
  Panorama Module、Layer、Runtime Call 或 Sequence。
- V0.6 决策：Java Adapter 固定为 partial parser，披露反射/生成源码/调用/资源配置缺口；公开代表视图必须使用
  exact root/depth/node budget，并显示 Information Gap。Oracle Fact Recall 在没有 Normalized Claim Adapter 前
  记录为 `not_scored`，不得用文件/关系数量替代。
- 实现结果：V0.6 Slice C 增加行级 Java declaration/type annotation/constructor metadata、Maven dependency 与
  脱敏 Properties Adapter。固定 PetClinic 得到 0.80 supported fact recall、1.00 forbidden-claim avoidance；
  另生成 5 个 hash-bound `pending_review` package candidate，正式 Module 新增 0。`PUBLIC_SOURCE_SEMANTIC_COVERAGE_AND_MODULE_MAPPING`
  机器门禁关闭，但 Thymeleaf template resolution 和 Service comment conflict 仍为已披露限制。

## SF-55 Configuration value 与 package grouping 都必须保持候选边界

- 证据：Properties 同时可能包含架构声明和 credential；Java package 提供责任聚类信号，但 PetClinic 的 owner
  package 同时含 Controller、Entity、Repository，不能直接等同一个正式 Module。
- 影响：原样持久化配置会泄密；自动把 package 写成 Module 会伪造职责、接口、状态与部署边界。
- V0.6 决策：Properties 只记录 key/line/Evidence Pin 和白名单安全值，datasource URL 只保留 scheme，其他值
  `omitted_by_policy`。Mapping Artifact 固定 `pending_review`、`suggestedModuleId=null`，绑定 Observation Hash、
  Source Element 与 Evidence Pin；正式采纳继续走精确 Hash 的 Proposal/Approval/Apply。

## SF-56 小型图通过不代表多列依赖图不会穿节点

- 证据：Controlled 5 nodes / 5 edges 与 PetClinic 小图均通过；Backstage 9 nodes / 10 edges 首轮出现 3 条
  `GEOM_EDGE_THROUGH_NODE`。固定 midpoint orthogonal route 会穿过中间列节点。
- 影响：只用小图做视觉门禁会把复杂依赖图的可读性回归带入发布；增加项目数量但不增加拓扑形态也不足。
- V0.6 修复：路由先尝试紧凑路径并对全部非端点矩形做 obstacle check；命中后使用确定性、有界的底部 lane。
  Python Geometry Validator 与浏览器 Renderer 共享同一策略。Backstage 修复后 3→0，三项目六档矩阵均为 0。
