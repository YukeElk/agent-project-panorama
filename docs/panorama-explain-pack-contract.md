# Panorama Explain Pack Contract v0.1

状态：V0.7 投影继续兼容；V0.81 已发布 V0.8 父子语义画布的 Explain/Progress/Risk-Decision/Playback 镜片

对应 Finding：SF-57

## 1. 定位

`panorama-explain-pack.v0.1` 是 Panorama Core、Model IR、View IR 与 Guided View Set 的只读、可重算解释投影。
它为 Codex 对话内 Visualize Fragment 和离线 Multi-view Renderer 同页画布提供同一组逐步讲解，不是新的事实源，
不拥有拓扑、布局、Decision、Review、Gate 或 Acceptance 状态。

Explain Pack 不能读取页面文案、DOM、截图、源码正文或模型自由文本来补事实。删除 Explain Pack 不会丢失
工程事实；任何消费者都不得从讲解文本反向 Apply 到 Core、Model、View 或 Event。

## 2. 输入与绑定

编译器只接受：

- 通过 Formal Validator 的 Panorama Schema 0.1/0.2 JSON 或 Single HTML；
- 通过 `validate_model_ir` 的 `panorama-model-ir.v0.1`；
- 通过 `validate_view_ir` 的 1–24 个单 Scope View IR；
- 精确覆盖同一 View 集合且通过 `validate_view_set` 的 Guided View Set。

顶层绑定必须包含 Project ID、Panorama Schema Version、Revision、Data Hash、Model ID/Semantic Hash、View Set
ID/Semantic Hash、`asOf` 与 Compiler。每个 Story 绑定一个现有 Chapter/View；每个 Step 绑定该 View 的
Semantic/Layout Hash。任一输入、Hash、集合覆盖或 Project Binding 不匹配都 fail closed。

## 3. Story 与 Step

V0.7 必须为六个现有 Profile 生成 View Story：

- `module`：模块边界、分层、责任和已存在通信路径；
- `dependency_dataflow`：依赖/数据方向、静态候选边界和 custody/contract（若有）；
- `deployment_runtime`：Release、Deployment、Module、Resource 链和 Declared/Observed 区分；
- `sequence`：Event Action 的已记录顺序，不提升为 Runtime Call；
- `lifecycle`：Adapter 明确 before/after 的状态迁移；
- `evolution_risk`：Event Hash Chain、Outcome/Risk Emphasis 和 Information Gap，不制造因果或 blast radius。

每个非空 View Story 必须覆盖：整图规模/范围/边界、关键关系及方向、全部可见节点的逐项职责与事实状态、证据/时效、
未知项。节点较多时可以在不超过 16 Step 的前提下分组，但每个节点必须有独立 Claim 和 Node Anchor；概览不能替代
节点详解。空 View 必须说明是未投影到绑定事实，不得表达为项目中不存在该类对象。

此外必须生成有事实输入时的 Core Story：

- `decision`：为什么当前需要决策、Options 的收益/代价/影响/风险/可逆性/成本、Recommended/Selected、Status/Review、
  相关 Requirement/Module/Risk/Reference，以及不同选择可能导致的已记录结果；
- `risk`：风险描述/成因、Severity/Status、Impact、Mitigation、关联对象与后续追踪；缺失成因、影响或应对措施时必须
  生成 `information_gap`，不得推测；
- `transition`：Current→Target/Transition 的正式关系与 blocker；
- `evidence_freshness`：Evidence Pin、Freshness、Conflict/Unknown；
- `verification_trace`：Requirement→Module→Decision→Acceptance/Gate 的现有 ID 关系。

每个 Step 至少包含一个 `claimBlock`。Claim 类型只允许：

- `bound_fact`：绑定 View Node/Edge、Model Entity/Relation 或 Core Entity；
- `information_gap`：原样引用现有 Information Gap 或缺失状态；
- `navigation`：仅解释如何阅读，不表达工程事实。

`bound_fact` 必须有至少一个 Anchor 或 Evidence/Core Reference；`information_gap` 必须有 gap 文本；
`navigation` 不得携带状态、风险、因果、部署或验证结论。Step 的 Focus 只引用绑定 View 中的 Node/Edge；
不得用名称相似度补引用。

## 4. Core Reference 与安全

Decision、Requirement、Risk、Acceptance、Gate、Transition、Review 和 Reference 使用只读 `coreRef`：

```text
type=<stable core entity type>
id=<authored ID>
jsonPointer=<exact array path>
digest=<canonical object hash>
```

Core Reference 只保存讲解所需的有界字段和精确引用；Resource Access/Credential、Embedded Secret、源码正文、
Prompt、模型回答正文和隐藏推理禁止进入 Explain Pack。编译后执行与 Engineering Event 相同的敏感键扫描。

## 5. Hash、容量与 Viewer State

Explain Semantic Hash 覆盖删除顶层 `integrity` 后的完整 Pack。Pack ID、Story ID、Step ID 均由 canonical input
确定性生成。V0.1 最多 24 个 View Story、64 个 Core Story、每 Story 16 Step、每 Step 64 Anchor，总 JSON
不超过 1 MiB。

当前步骤、Dialog 开关、动画进度、选择、Zoom、Theme 与 Codex Follow-up 状态属于 Viewer State，不进入
Explain Pack 或任何 Semantic Hash。

## 6. 两个呈现表面

### Codex Visualize Fragment

- 输出为对话专属目录中的 HTML Fragment，不进入仓库或业务项目；
- 不使用 `fetch`、XHR、WebSocket、远程脚本或插件缓存路径；
- 只显示一个当前 Step 和一个主视觉，提供上一步/下一步；
- `window.openai.sendFollowUpMessage` 只能由用户显式点击触发，并只发送脱敏 ID、Hash 与所选问题；
- 不支持 Visualize 时降级为同一 Explain Pack 的 Markdown/Mermaid。

### Panorama Same-page Explanation

- 带 Explain Pack 的 Renderer 必须嵌入正式 Panorama，保留控制台、系统、演进和 Evidence Inspector；
- 讲解是当前 Canvas Scene 的 Knowledge Lens，不是第三种模式、独立 Guided 画布或替代主页面；
- Guided 子表面只消费已验证 Explain Pack；不在浏览器重新生成 Claim；
- 不增加第四个主导航；`read|edit` 共用唯一 Canvas Runtime，不使用 iframe、data URL 或讲解 Modal；
- 讲解 Rail 只推进绑定当前 Scene 的 Story/Step，并在原 Node/Edge 上高亮，不重建一套几何；
- 节点单击直接打开详情；Enter/Space 仅为键盘等价操作，界面不得要求回车；
- Step 切换只更新 Viewer State，并高亮当前 Step 的已绑定 Node/Edge；
- 讲解 Rail 的原生控件保持键盘可达、焦点可见；节点详情不要求额外确认或回车；
- 章节切换、空视图与无效 Deep Link 必须清空不属于当前 View 的旧 Node/Edge Drawer；
- 无 Explain Pack 时 V0.6 Renderer 行为保持兼容。

## 7. 动态更新与失效

“动态”指项目事实演进后确定性重编译，不指离线页面轮询项目或由模型实时补事实。Panorama Revision/Data Hash、
Model Semantic Hash/`asOf`、View Set Hash、任一 View Semantic/Layout Hash、Core Object Digest 或 Event 投影变化时，
必须按上游到下游顺序重新编译 Explain Pack 与所有呈现制品。旧包对新输入验证必须失败；浏览器不得继续显示旧包
并标成当前。`generatedAt`、Project Binding 和 Explain Pack ID/Hash 共同暴露当前编译批次。

## 8. 审批与交付

Compile、Validate、Codex Fragment、Markdown 降级和本地 Renderer Candidate 均属于 Read-only/Automatic
Quality，不需要人工批准，也不增加 Approval Policy Operation。由讲解触发的 Target/Decision/Requirement/
Gate 修改继续走现有 Proposal/Hash Approval。

带 Explain Pack 的 Renderer Bundle、Verified Delivery Receipt 和 Artifact Hash 必须精确绑定 Pack ID 与
Semantic Hash。机器 Browser Evidence 继续保持 `visualReview=pending`；公开代表制品必须由独立人工接受精确
Artifact Hash，且不能把外部接受反写进机器 Receipt。

## 9. V0.8 Playback Scenario

`playbackScenarios` 是正式 `businessFlows` 的只读投影。Scenario 绑定 Business Flow Core Ref、Chapter/View 与
Current/Target 范围；每个 Playback Step 精确绑定源 Flow Step、Module、Connection、View Node/Edge 和相关 Core Ref。
未投影锚点只能形成 Information Gap。

Scenario 不拥有流程事实，也不能从 View Edge 自动生成。Scenario/Playback Step ID 由绑定语义确定性生成；Core
Revision、Business Flow、View 或 Core Digest 变化后旧包必须失效并重新编译。速度、进度、Live/Still 与当前 Step
属于 Viewer State，不进入 Explain Pack Semantic Hash。

## 10. V0.8 Module Logic 与进度讲解

Explain Pack 可为通过验证的 `module_logic` 子画布生成 `module_logic_overview`、`module_logic_node`、
`boundary_interaction` 和 `progress` Story。它们必须绑定子 View 的 `rootModuleId`、父 View Hash 和 Current
Observation Hash 或 Formal Target Design Digest。

- 整图：模块目标、范围、输入/输出、主处理路径、关键约束与信息边界；
- 节点：设计需求、设计原因、功能、系统角色、内部处理、外部交互、技术选型和 Evidence/Gap；
- 边界：内部端口、正式 Connection/Resource Binding、数据、协议和外部模块/资源的精确身份；
- 进度：只投影可精确解析到 Layer/Module/Logic Node 的 WorkItem/Event，并分开 `claimed/in_progress/
  implemented/verified/approved/deployed`；事件存在不等于验证、批准或部署。

Core 不引用可过期 Current Observation Node。当前节点进度只能作为带 Observation Hash 的 Event/Explain
投影；Observation stale 后必须清除该高亮与讲解上下文。
