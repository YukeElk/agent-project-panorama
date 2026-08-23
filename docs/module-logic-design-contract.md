# Module Logic Design Contract v0.1

状态：V0.81 已发布；只定义正式 Target/Historical 设计语义

## 1. 定位

`architecture.moduleLogicDesigns[]` 保存经过治理的模块内部设计，不保存 AI 对 Current 源码的临时归纳。正式设计只允许 `target|historical` Scope；Current 阅读固定来自 `module-logic-observation.v0.1`。

每个 Design 归属于一个正式 Module，由稳定 ID、技术名称、可选 authored 中文显示名、摘要、内部节点/边、边界端口及 Requirement/Decision/Risk/Acceptance/Gate/Reference 组成。

## 2. 逻辑节点与边

逻辑节点使用与 Observation 相同的业务实现类型，但它们是 authored Target 语义：输入、预处理、决策、业务处理、LLM/Tool/Agent 调用、状态读写、验证、Loop、Retry、Fallback 和输出。

节点必须说明 Purpose、Rationale 与 Implementation Summary。Loop 必须声明 Exit Condition 并绑定 `loop_back`；条件、重试和回退必须由明确边表达。文件/函数/类不是正式逻辑节点。

节点、边和端口的 ID 在整个 Panorama 中全局唯一。显示名称、坐标和 DOM 顺序不能作为身份。

## 3. 跨边界交互

跨模块端口必须绑定正式 Connection，并校验方向与端点；Resource Port 必须绑定正式 Resource Usage。正式 Core 不接受 `unbound` 端口，未绑定状态只允许存在于 Studio Candidate 并阻断 Formal Proposal。

子画布展示外部引用时只复用正式 Module/Resource 身份，不复制对方内部设计。跨边界编辑若新增或改变通信语义，必须同时产生 Candidate Connection Diff，并在 Proposal 中列出所有受影响模块。

## 4. Current、Target 与 Compare

- Current Observation 始终只读、derived、source-bound；
- Target Design 始终来自正式 Core 或隔离 Candidate；
- 首次编辑可复制 Current Observation 为 Target Candidate，但复制动作不改变 Current 的权威等级；
- Compare 只叠加两个独立且已验证的输入，不把 Current/Target 状态合并到同一对象。

Source 漂移不删除 Target 草稿，但使依赖旧 Current 的校验、讲解与 Proposal stale。

## 5. Studio 与 Hash

Architecture Session 的 `moduleLogicCanvases[]` 保存候选语义与布局：名称、节点类型、端点、条件、端口绑定和关联 ID 进入 Semantic Hash；坐标、相机、选择、折叠和面板状态只进入 Layout/Viewer State。

正式化顺序继续为 Session CAS → Formal Validator → optional Agent Review → Proposal Freeze → 精确 Hash 人工批准 → Apply。页面不得直接写 Core，也不得从画布连线自动生成 Business Flow、Risk、Decision 或验证事实。

## 6. 中文显示名

Layer、Module、Module Logic Design 和 Logic Node 可以保存可选 authored `displayName`。Renderer 使用 `displayName || name` 为主标签，技术名称和 ID 为次级标签。缺少中文名是信息缺口，不能由浏览器即时翻译或自动写回。

## 7. 关联与治理

WorkItem、Risk 和 Decision 可通过 `relatedArchitectureRefs[]` 关联正式 Layer、Module、Connection、Module Logic Design、Logic Node、Logic Edge、Boundary Port 或 Resource。Core 不能引用可失效的 Current Observation Node；当前节点级进度由带 Observation Hash 的 Engineering Event/Explain 投影表达。

WorkItem/Event 只证明工作或事件发生，不自动证明 implemented、verified、approved 或 deployed。所有治理状态继续使用已有正式事实与门禁。
