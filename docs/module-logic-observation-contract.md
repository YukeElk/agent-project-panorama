# Module Logic Observation Contract v0.1

状态：V0.81 已发布；Current Module Logic 仍保持只读、证据绑定边界

## 1. 定位

`module-logic-observation.v0.1` 是 Codex 对一个正式 Module 的当前源码进行有界阅读后形成的只读 Sidecar。它回答模块内部的业务处理逻辑，但不是 Panorama Core、正式 Current Architecture、Target Design、运行 Trace 或用户批准的 Decision。

Observation 可被删除和重算。它不修改业务源码，也不写入 `project-panorama-data`。任何消费者都必须保留 `factStatus=derived`、`authority=inferred`、Evidence、Source Binding、Coverage、Loss 和 Information Gap。

## 2. 可见颗粒度

允许的内部节点类型为：

- `input`、`preprocess`、`decision`、`processing`；
- `llm_call`、`tool_call`、`agent_call`；
- `state_read`、`state_write`、`validation`；
- `loop`、`retry`、`fallback`、`output`。

函数、类、文件和行号只能作为 Evidence Location，不能直接成为画布逻辑节点。一个逻辑节点可以由多处源码共同证明，但每个节点、边、端口、外部引用、Unresolved 项和 Transformation Loss 至少绑定一个 Evidence Pin。

## 3. 不可推导规则

- import、类型引用和文件相邻不能证明执行顺序、Runtime Call、Loop、Retry 或 Fallback；
- LLM/Agent/Tool 调用只有在源码、配置、作者模型、Receipt 或 Event 中有精确 Evidence 时才能出现；
- 没有退出条件的 Loop 必须拒绝，不能用“可能循环”等模糊文字绕过；
- 未解析动态调用、反射、生成代码、框架注入和不支持语言必须进入 Information Gap/Loss；
- AI 不能创建正式 Module、Connection、Risk、Decision、Acceptance、Gate、Review 或 Approval；
- 不能把 Observation 的 `derived` 改写成 `observed/declared/approved/verified/deployed`。

## 4. Source、Project 与 Module Binding

Observation 必须绑定：

- Project ID、Panorama Schema、Revision 和完整 Data Hash；
- 正式 Module ID 与该 Module 对象的 canonical digest；
- Git HEAD 或完整 Source Content Digest、Coverage 与 Currentness；
- 显式 `asOf`、Producer ID/Version 和合同版本。

Git/Source、Panorama Revision/Data 或 Module Digest 任一变化时，旧 Observation 对新输入验证失败或显示 stale。Partial Source 不能声明 current；存在未支持语言或 Information Gap 时不能声明完整覆盖。

## 5. 边界端口与外部引用

子画布只显示当前模块内部逻辑。其他 Module/Resource 只能作为 `externalReferences[]` 紧凑卡片，不允许携带其内部节点。

每个 Boundary Port 必须绑定一个内部节点和一个外部引用：

- `connection`：绑定正式 Connection；方向必须与 Current Module、外部 Module 和 Connection 方向一致；
- `resource_usage`：绑定正式 Resource，且 Resource 必须在 Core 中声明当前 Module 为使用者。

不得按名称、坐标或自由文本补 Connection/Resource Usage。无法绑定时只保留 Information Gap，不生成伪端口。

## 6. Evidence 与隐私

Source Location 只保存安全的项目相对 POSIX 路径、行号、文件 digest 和 Freshness。禁止绝对路径、路径逃逸、Secret 路径、源码正文、Prompt、模型回答、隐藏推理、Token 或 Credential。

普通源码正文只允许在 Codex/Analyzer 进程中瞬时读取，不进入 Observation、Loss、Event、Renderer 或 Explain Pack。Observation 总大小上限 1 MiB。

## 7. 身份与 Hash

`observationId` 由删除 `observationId/integrity` 后的 canonical 内容确定性生成；`semanticHash` 覆盖删除顶层 `integrity` 后的完整 Observation。Viewer State、布局和相机不属于 Observation。

Schema-valid 不等于 Evidence-valid。Validator 还必须检查 ID 唯一性、端点、Loop、Evidence 引用、Source/Project/Module Binding、Boundary Port 和隐私边界；即使攻击者重算 Hash，语义错配仍必须失败。

## 8. 动态更新

每次需要阅读模块内部逻辑时，先比较 Source Binding。`match` 才能称为当前归纳；`stale/incomplete/unknown` 必须可见。重新归纳会生成新的 Observation ID/Hash；旧 Target Candidate 保留，但其 Validation、Review、Explain Preview 和 Proposal 变为 stale，不自动 rebase。

## 9. 审批边界

Prepare、Materialize、Validate 和 Reconcile 都是只读/Automatic Quality，不需要新增人工审批。把归纳结果采纳为正式 Target Design 必须进入 Architecture Studio Candidate，并继续执行 Proposal、精确 Hash Approval 与 Apply。
