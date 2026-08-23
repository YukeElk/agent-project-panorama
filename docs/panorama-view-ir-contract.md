# Panorama View IR Contract v0.1

状态：V0.6 顶层 Views 与 V0.81 发布的 `module_logic` 父子 Model/View 编译链、统一 Canvas Runtime 已完成

对应 Finding：SF-34～SF-36、SF-41～SF-43、SF-46～SF-48

## 1. 定位

View IR 是一个已验证 Model IR 的有界阅读投影。Architecture、Data Flow、Deployment、Sequence、Lifecycle、
Evolution/Risk 共享同一 Model Identity 和 Evidence，不建立独立事实 Schema。

## 2. Model Binding

每个 View 必须绑定：

- Model ID 和 Model Semantic Hash；
- Project ID、Panorama Data Hash；
- `asOf` 时间；
- View Compiler ID/Version、Type、Profile 和 Scope Filter。

绑定任一不匹配时 View 无效，不允许把旧 View 与新 Model 混用。

## 3. Node、Edge 与 Group

- Node 只通过 `entityRef.type + entityRef.id` 引用 Model Entity；
- Edge 只通过 `relationRef.type + relationRef.id` 引用 Model Relation；
- Edge 端点必须与 Relation 端点投影出的 Node 精确一致；
- `layer` Group 必须引用 Model Layer；`environment/source_kind` Group 只表达阅读分组且 `layerId=null`，
  不能用视觉容器制造 ownership/security/deployment boundary；
- Node/Edge Evidence Pin ID 必须属于其绑定的 Entity/Relation；
- Search、Focus、Trace、Reach 和 Deep Link 只能遍历这些现有引用。

六个已实现 profile 每次只接受 `current|target|transition|historical` 中一个 Scope。只有端点 Node 都在 View 中且
Relation Scope 匹配时才投影 Edge；不得为保持画面连通而增加代理边。

- `module` 只投影正式 Module 与 `communication`；
- `dependency_dataflow` 只投影 `dependency|data_flow`，可用 exact root、`maxDepth`、`maxNodes` 有界聚焦；
- `deployment_runtime` 只投影 `deployment`，environment 过滤基于 Deployment 关系，shared resource 端点仍保留；
- `sequence` 只投影有序 Engineering Event Action，不提升为 Runtime Call；
- `lifecycle` 只投影 Adapter 显式 before/after state transition；
- `evolution_risk` 只投影 Event Hash Chain，失败强调不是 Formal Finding 或 blast radius；
- View Validator 除 Schema/Hash 外，还必须验证 profile/compiler、Node/Entity 字段、Edge/Relation 字段、端点、
  relation kind 与 scope；攻击者重算 Semantic Hash 也不能绕过绑定。

## 4. Semantic 与 Layout 分离

View Semantic Hash 覆盖删除 `layout` 和 `integrity` 后的完整 View。Layout Hash 只覆盖 `layout`：

- 节点移动、自动/手工布局切换只改变 Layout Hash；
- Label、Entity/Relation Binding、Filter、Evidence 或 Scope 变化必须改变 Semantic Hash；
- Theme、Zoom、Selection、Filter UI 展开状态不进入 View IR。

## 5. Event View 门禁

Sequence、Lifecycle 与 Evolution/Risk 已接入受验证 Event Checkpoint。Module Connection、import graph、文件相邻
和自然语言不能形成 Event Sequence；Outcome 不能单独形成 Lifecycle；Risk emphasis 不能成为 Formal Finding。

## 6. 当前命令

```powershell
python scripts/compile_panorama_view_ir.py project.model-ir.json `
  --profile module --scope current `
  --output .panorama-work/views/project.current.module.view-ir.json

python scripts/compile_panorama_view_ir.py project.source-bound.model-ir.json `
  --profile dependency_dataflow --max-nodes 100 `
  --output .panorama-work/views/project.dependency.view-ir.json

python scripts/compile_panorama_view_ir.py project.model-ir.json `
  --profile deployment_runtime --scope transition --environment development `
  --output .panorama-work/views/project.transition.deployment.view-ir.json

python scripts/compile_panorama_view_ir.py project.event.model-ir.json `
  --profile sequence --correlation-id CORR-ID `
  --output .panorama-work/views/project.sequence.view-ir.json

python scripts/compile_panorama_model_ir.py project.panorama.json `
  --module-logic-observation project.orchestrator.module-logic.json `
  --output .panorama-work/views/project.module-logic.model-ir.json

python scripts/compile_panorama_view_ir.py project.module-logic.model-ir.json `
  --profile module_logic --scope current `
  --root-module MOD-ORCHESTRATOR `
  --parent-view .panorama-work/views/project.current.module.view-ir.json `
  --output .panorama-work/views/project.orchestrator.current.module-logic.view-ir.json
```

输出已存在时默认停止。当前命令仍只生成 JSON Candidate，不生成 HTML、不替换 last-good，也不表示人工视觉通过。

多个 single-scope View 的顺序与导航组合见
[Panorama Guided View Set Contract](panorama-view-set-contract.md)。View Set 不能复制或改写本合同定义的
Node/Edge/Group/Layout/Evidence。

## 7. V0.8 Module Logic 子画布投影

`module_logic` 是嵌套子画布 Profile，不得作为第七个顶层 Tab。它必须同时绑定：

- `rootModuleId`：当前展开的正式 Module；
- `parentViewBinding`：父架构 View ID 和 Semantic Hash；
- `moduleLogicBinding`：二选一绑定当前 `module-logic-observation` 或正式 Target Design，禁止混合两者。

子画布内部 Node/Edge 只来自该 Binding。Boundary Port 通过 `port_binding|resource_usage` 关系连到复用
正式身份的外部 Module/Resource 紧凑 Node；外部 Node 不得携带对方的内部逻辑实体。旧 Observation、
父 View Hash 或 Module Digest 不匹配时 fail closed，不按名称猜测对齐。

Model IR 的 `moduleLogicBindings[]` 是子画布唯一索引。Current 接受两类已校验 Observation：

- `complete/current`：完整当前归纳；
- `partial/recorded_as_of`：局部、按时点记录的只读归纳，Binding 必须显式携带 `coverage`、
  `sourceCoverage`、`currentness`，View 必须保留 Information Gap。

`stale|unknown` Observation 失败关闭。Target/Historical 只接受正式 `architecture.moduleLogicDesigns[]`。
同一 Module/Scope 多个绑定、父 View Scope 不一致、Root Module 未在父 View 中或外部端点不是正式
Module/Resource 时，编译器必须停止。

`module_logic` 不是 Guided View Set 顶层章节。Renderer 通过它的 `parentViewBinding` 和
`extensions.navigation.enterGesture=double_click_module` 建立主子导航；该字段只是确定性交互契约，
不代表 WP4 Canvas Runtime 已实现。
