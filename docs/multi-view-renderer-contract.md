# Multi-View Renderer Contract v0.1

状态：Renderer 0.3 / Guided View Set / Explain Pack Dialog Implemented；V0.7 Representative Artifacts Pending Human Acceptance

对应 Finding：SF-34～SF-36、SF-42、SF-43

## 1. 产品边界

Renderer 消费 validated Model/View IR 与 Guided View Set，不读取页面文案或源码重新推导拓扑。保留 `控制台 / 系统 / 演进` 三主视图；
Architecture、Data Flow、Deployment、Sequence 等作为 `系统` 内 Guided View，Lifecycle/Evolution/Risk 位于
`演进`，不增加第四个主导航。

## 2. 交互真实性

- Search/Focus/Cross-highlight 只恢复已存在的 Entity/Relation/Event；
- Trace/Route/Reach 只遍历 View IR Edge，不推断 blast radius、风险、可合并性或部署影响；
- 同一对象跨 View 使用相同 Model ID，View Node ID 只代表投影实例；
- Deep Link 必须包含 Project/Model/View/Object Binding，stale 时显示原因而不是静默跳到同名对象；
- Zoom、Theme、Selection、展开和临时 Filter 属于 Viewer State，不写回 Core/Model/View IR。
- 同一 Profile 可由 View Set 并列 current/target/transition/historical Variant；章节只引用既有 View，不拥有
  Topology/Layout，不从 Story 文案推断事实。

## 3. 图类型规则

- Architecture：6–12 个首屏主节点，真实 Layer/Boundary，主路径清晰；
- Data Flow：区分 dependency、call 与 data custody，标签保留 contract/classification；
- Deployment：Declared Config 与 Observed Runtime 分层，未观察不能显示 active；
- Sequence：Message 自带顺序，async/return/retry 只来自 Evidence；
- Lifecycle：recoverable failure 必须有真实 Transition 回边；
- Evolution/Risk：Delta、Event、Finding/Freshness 为覆盖层，不制造因果边。

## 4. 几何与可访问性

自动质量门禁至少拒绝 edge-through-node、节点/标签硬重叠、模糊共享走廊、端点方向错误、越界和不可读标签。
节点移动先改变 Layout Hash；不得通过删除有语义的 Label 或缩小正文掩盖 Geometry Error。

代表性制品检查 1440×900、1600×1000、1920×1080、2048×1320；840px 以下使用分段面板。主要流程必须
键盘可达、焦点可见，Drawer/Dialog 焦点闭环。自动检查保持 `visualReview=pending|skipped`，不能生成
`accepted`。

## 5. 实现门禁

Renderer 实现前必须先有至少 Architecture、Data Flow、Sequence 三种 Schema-valid View IR Fixture；每次修复
只针对 Validator 的稳定 code/subject/evidence。候选失败不得修改正式 Panorama 或 last-good HTML。

## 6. V0.7 逐步讲解

Renderer 0.3 只在输入包含已完整验证的 `panorama-explain-pack.v0.1` 与对应 Panorama Core 时显示原生
`<dialog>`。View/Decision/Transition/Evidence/Verification Story 都来自该包；当前步骤只高亮其正式
`focusNodeIds/focusEdgeIds`，不得在浏览器内重编译事实或从文案搜索同名对象。切换 Story 时必须切到包中精确绑定的
Chapter/View；Step、Dialog 开关与临时高亮均为 Viewer State，不进入包或 Bundle 的语义 Hash。

无 Explain Pack 时 Renderer 保持 V0.6 兼容并生成 `panorama-multi-view-bundle.v0.1`；带包时生成
`panorama-multi-view-bundle.v0.2`，Bundle 身份语义包含 Explain Pack ID/Hash。两个模式都保持离线 CSP、无外部脚本、
键盘原生控件和现有 Search/Trace/Drawer 行为。
