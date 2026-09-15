**现有格式与开发过程合同的映射 · P0**

以下映射是适配器实施约束，不是已运行的迁移。上游接口以 [upstream.lock.json](upstream.lock.json) 固定的源码为准；现有格式保持各自原始权威。

| 来源 | 新合同中的位置 | 处理规则 |
|---|---|---|
| Structure 显式 project_id / checkout_id | `binding.projectId/checkoutId` | 精确绑定；模块、路径及同名项目不替代项目身份 |
| Structure Module.id、责任、owns/may_touch/forbidden | 项目 `moduleBindings` 及 work context 的 `moduleIds/plannedPaths` | 引用原模块；Panorama 源码节点另做可失效映射，不把目录自动提升成模块 |
| Structure WorkItem.id | `workItemRef.id` | 保留 ID，工作项继续由原接口管理 |
| WorkItem title、scope、modules、acceptance | `contextSnapshot` 与规范化 definitionDigest | title 对应目标，scope/modules 保留；缺少的预期结果及验收项稳定 ID须显式补齐。kind/impact 作为适用性事实来源，不猜测其语义 |
| 工作项 acceptance 字符串列表 | `criteria[].id/version/requirement` | 保存独立稳定映射，文字变更递增版本；不按数组下标维持身份，重复文字有歧义时不自动合并 |
| core done / Harness completed | `upstreamCompletionClaim` | 仅保留上游历史声明，不转成满意、正式接受或部署 |
| Harness checks: id/task_id/name/code/status/fingerprint/timestamp | Receipt 的 check/execution 线索及历史引用 | 必须另外取得输入清单、运行配置、对象和来源。缺失数据无法产出完整新 Receipt时，先保留为外部声明 |
| Harness events、pause/resume 与 owner | 执行接续资料的来源引用 | 逻辑 session 变更不能自动解释为模型上下文重启；P2 自身执行记录与上游工作项分开 |
| Harness inspect raw_shell_enabled | enforcement 的原始声明 | 不能据此填写 observed host_interception；本机事实不足保持 declared/unknown |
| 现有 Source Model project.id | `binding.panoramaProjectId` | 与 Structure ID显式映射，不要求两个系统的字符串相等 |
| Source Snapshot id/contentDigest | 可选 `sourceSnapshotId` | 用于架构观察关联；不能代替 Verification Input Set |
| 开始开发前的本地内容采集 | `contextSnapshot.baseline` | 保存实际开始时点及声明范围；没有原始采集时为 unknown。检查前快照、Git HEAD、设计基线均不能自动代填 |
| 现有 handoff.v1 candidate.id/version、baseline.id | `candidateBinding` | 绑定完整三元组；没有候选的普通开发保留 null |
| handoff requirements/acceptanceSuggestions | 目标、约束与验收定义的参考来源 | suggestions 不是已批准或已实施的验收标准；使用前形成明确版本的 criteria |
| 现有 import-result 的 source/summary/references | 外部声明及引用 | 不凭自由文本生成 checks、Hash、人工评审或 processReady |
| 标准包 v0.1 id/version/source/requirement/severity | v0.2 同名字段 | 字段含义保留，新增 strength/when/evidenceRequirements 需要明确补齐 |
| v0.1 appliesTo | 包级范围线索 | 不冒充逐规则条件；转换时不自动声称完成适用性判断 |
| v0.1 evaluator（路径、Git、Pointer、manual） | 兼容声明式检查器映射 | 没有已实现映射时保留 unsupported；manual 仍为 unknown，不能升级成自动通过 |
| 旧正式 Verification Receipt | 单独的旧 Receipt/正式制品导入流程 | 不与新的 process-receipt 格式混用，不绕过其正式绑定和证据追加规则 |

**新增字段的权威来源**

项目配置中的规则选择、逻辑输入根和 runner 引用属于项目声明。runner 的精确命令及本机路径由本地配置管理；共享规范不能携带任意可执行内容。将模块和工作项重复抄入另一个可编辑状态库不属于本映射。

规范化工作定义由过程适配器根据原工作项及已明确的验收扩展构造；它是带来源的冻结投影。`definitionDigest` 排除上游状态、执行耗时和结果。P2 必须记录原始读取来源，避免把规范化摘要误说成 core 原始记录摘要。

execution 的历史结果由实际采集或外部生产者提供；输入清单和对象身份由该次采集绑定；Assessment 由明确版本的评估器计算。人工评审必须有真实来源，仅靠 Coding Agent 自报不能制造 human 记录。

**兼容和升级策略**

v0.1 Standard Pack、现有 workspace、handoff 和文本结果继续按原格式读取。缺少新格式必须字段时不得填充看似有效的默认 Hash、规则强度、source snapshot 或验收结果。兼容导入可以保留说明并提示缺口；它不必伪装成一个完整的 process Receipt。

旧包的 severity 不推导 strength；特别不能把 critical 当成 required，或把 low 当成 optional。未知的 when 和证据要求应显式配置，不能默认 always 或不适用。

新 Schema 与旧文件并存。P0 没有改写旧 Standard Pack Schema、旧执行产品合同、workspace 格式或现有 import-result 实现。新运行时和工作台导入只在后续工作包实施，并保留历史版本。

固定上游只锁定已评审接口；P0 未安装、初始化、迁移或修改任何目标项目的 `.structure`。P2 引入实际适配时应按固定内容取得 core，保留许可证、检查摘要，并让版本漂移成为明确错误。

**存储与恢复交接给 P1/P2 的要求**

项目内配置可随版本控制保存；本机路径、日志及过程运行状态留在项目外的 Panorama 数据目录。CLI 与工作台必须共用真实路径/dataRoot 定位，不共享或绕开现有 workspace 的长持有锁。

不可变对象、索引、幂等键、短事务锁与预期 revision 由同一过程存储模块负责。core 与过程库之间的 pending/committed 操作用稳定 operation ID 对账，不能假设两套存储天然原子提交。真实进程中断与工作项尚未完成是两个独立事实。

这些是后续实现约束；P0 的 JSON 样本和测试脚本没有运行上述写入协议。
