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

## 结论

- 本轮未修改或重构 Schema。
- 上述 Finding 均有显式兼容策略，不阻塞 Reference Project V0.1 的读取、校验和渲染。
- SF-01、SF-03、SF-08、SF-09、SF-13 建议作为下一版 Schema 设计的优先议题。
