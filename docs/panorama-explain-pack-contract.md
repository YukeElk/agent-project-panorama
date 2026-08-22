# Panorama Explain Pack Contract v0.1

状态：V0.7 Implemented；Machine Gates Passed；Representative Artifact Human Acceptance Pending

对应 Finding：SF-57

## 1. 定位

`panorama-explain-pack.v0.1` 是 Panorama Core、Model IR、View IR 与 Guided View Set 的只读、可重算解释投影。
它为 Codex 对话内 Visualize Fragment 和离线 Multi-view Renderer Dialog 提供同一组逐步讲解，不是新的事实源，
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

此外必须生成有事实输入时的 Core Story：

- `decision`：Why Now、Options、Recommended/Selected、Status/Review、相关 Requirement/Module/Risk/Reference；
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

### Panorama Dialog

- Multi-view Renderer 只消费已验证 Explain Pack；不在浏览器重新生成 Claim；
- 不增加第四个主导航；每个 Guided View 提供“讲解”按钮；
- Step 切换只更新 Viewer State，并高亮当前 Step 的已绑定 Node/Edge；
- Dialog 打开后移动焦点、Tab 闭环、Esc 关闭并返回触发按钮；
- 无 Explain Pack 时 V0.6 Renderer 行为保持兼容。

## 7. 审批与交付

Compile、Validate、Codex Fragment、Markdown 降级和本地 Renderer Candidate 均属于 Read-only/Automatic
Quality，不需要人工批准，也不增加 Approval Policy Operation。由讲解触发的 Target/Decision/Requirement/
Gate 修改继续走现有 Proposal/Hash Approval。

带 Explain Pack 的 Renderer Bundle、Verified Delivery Receipt 和 Artifact Hash 必须精确绑定 Pack ID 与
Semantic Hash。机器 Browser Evidence 继续保持 `visualReview=pending`；公开代表制品必须由独立人工接受精确
Artifact Hash，且不能把外部接受反写进机器 Receipt。
