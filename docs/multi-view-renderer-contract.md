# Multi-View Renderer Contract v0.1

状态：V0.6/V0.7 已实现能力作为兼容基线；V0.81 发布 V0.8 WP4–WP8 唯一读/编画布、父子导航、相机、Target Module Logic Studio、知识镜片、中文扫描层与编辑修正

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

## 6. V0.7 兼容基线

V0.7 的 Renderer 0.3.1 只在输入包含已完整验证的 `panorama-explain-pack.v0.1` 与对应 Panorama Core 时，把 Guided
Viewer 作为原生 `<dialog>` 嵌入正式 Panorama Renderer 0.4.0。该形态仅作为历史兼容基线；V0.8 集成交付必须使用
第 7 节的同页画布。控制台、系统、演进、Evidence Inspector 与
Architecture Studio 不得被平行只读页面替换。View/Decision/Risk/Transition/Evidence/Verification Story 都来自该包；当前步骤只高亮其正式
`focusNodeIds/focusEdgeIds`，不得在浏览器内重编译事实或从文案搜索同名对象。切换 Story 时必须切到包中精确绑定的
Chapter/View；Step、Dialog 开关与临时高亮均为 Viewer State，不进入包或 Bundle 的语义 Hash。

节点以单击为主要选择路径，Enter/Space 只保留为可访问键盘等价项。中文标签、Purpose、关系、属性、证据与缺口
必须进入主扫描层；原始枚举/JSON 只能放在次级详情。章节切换、空 View 或无效 hash 不能残留上一 View 的 Drawer。

无 Explain Pack 时 Renderer 保持 V0.6 兼容并生成 `panorama-multi-view-bundle.v0.1`；带包时生成
`panorama-multi-view-bundle.v0.2`，Bundle 身份语义包含 Explain Pack ID/Hash。两个模式都保持离线 CSP、无外部脚本、
键盘原生控件和现有 Search/Trace/Drawer 行为。

## 7. V0.8 唯一画布、父子导航与有限动效

Renderer 任一时刻只能挂载一个 Canvas World。`read|edit` 是两种模式；Explain、Progress、Risk/Decision、
Evidence 和 Playback 是 Knowledge Lens；Module/Dataflow/Runtime/Sequence/Lifecycle/Evolution-Risk 是
Structural Lens。模式/镜片切换不得重建另一套节点 DOM、改变几何或丢失 Camera/Selection。

主画布双击 Module 进入绑定 `rootModuleId` 的子画布；单击仅选择，Enter/Space 是键盘等价。
子画布只展示当前模块内部逻辑与 Boundary Port，外部 Module/Resource 仅显示紧凑引用卡；面包屑/
Back 恢复父画布离开前相机。Canvas Viewport 必须 `overflow:hidden`，支持空白拖动、Space+拖动、中键/触控平移、
指针中心缩放和 Fit All/Selection，正常桌面不依赖画布滚动条。

六类结构镜片均保留；空 Scene 显示绑定范围与信息缺口，并清空不属于当前 Scene 的详情、Story 和
播放上下文。中文 `displayLabel` 作为主扫描层，技术名称/ID 作为次级身份；不得在浏览器即时翻译或写回。

动效只消费 Explain Pack 中精确绑定的 `playbackScenarios`，并沿原 Scene Node/Edge Path 高亮。默认静止、
用户启动、有限完成，提供暂停、重播、速度和 Reduced Motion；页面隐藏时暂停，打印保留静态语义。
播放状态不进入任何语义 Hash。

### 7.1 WP4 已实现边界

- Integrated Bundle 以 `views[]` 承载顶层结构镜片，以 `childViews[]` 承载 `module_logic` 子画布；
  Child 必须绑定同 Bundle 中的父架构 View，且不得进入 Guided View Set 顶层章节。
- 阅读面只保留一个 `CanvasSceneAdapter + CanvasStore + CameraController + NavigationStack` Runtime；
  旧 `全景查看 / 全景讲解` 已合并为 `阅读模式`，Explain/Detail/Playback 位于同一画布右侧知识镜片。
- 主画布 Module 单击选择，双击或键盘 Enter/Space 下钻；子画布 Back 返回并按 View ID 恢复父相机。
- 相机按 View 保存 `x/y/scale`，支持空白/中键/Space 平移、滚轮指针中心缩放、Zoom、Fit All 和
  Fit Selection；这些字段只存在 Viewer State，不进入 Bundle/View Hash。
- WP5 已把 Studio 迁入统一 Runtime，并恢复正式化治理链。
- WP6 已把模块整体、内部节点、边界交互、项目进度、风险/决策和显式业务流程动效接入同一场景；
  空场景必须清空旧选择/讲解上下文。
- WP7 使用 authored `displayName` 作为中文主标签，技术名、ID、事实状态作为副标签；旧数据缺失时显示 Gap。
- WP8 的 1440、1600、1920、2048、839、430、360 宽度矩阵、Reduced Motion、打印与真实项目局部归纳
  已通过。机器通过不替代精确候选的人工视觉接受。
