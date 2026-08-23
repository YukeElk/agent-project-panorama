# Panorama Business Flow Contract V0.8

## 目的

`businessFlows` 是项目全景中的正式、可选业务流程事实，用于驱动逐步讲解和有限动效演示。它不替代 `architecture.connections`：Connection 描述静态可通信关系，Business Flow 描述一个经过明确编排的业务路径。

## 事实边界

- 不得从画布坐标、节点邻近、静态依赖拓扑或名称猜测流程顺序。
- 每个 Step 必须显式绑定 `moduleId`；除第一个 Step 外还必须显式绑定入站 `connectionId`。
- Validator 必须校验 Step 顺序连续、连接方向与前后模块一致、Current/Target 范围一致。
- `observed` 只表示有精确 provenance 的已观察事实；声明的设计流程必须使用 `declared`。
- Transition、失败和补偿流程必须显式建模，不得用正常路径的动画暗示已经发生。

## Core 结构

Business Flow 包含稳定 ID、名称、摘要、架构范围、事实状态、状态、默认标志、触发条件、结果、风险/决策/参考资料和 2–64 个有序 Step。

Step 类型为：

- `trigger`
- `processing`
- `data_transfer`
- `decision`
- `result`
- `failure`
- `compensation`

一个架构范围最多有一个 `isDefault=true` 的 Business Flow。

## Explain Pack 投影

Explain Pack 将每个正式 Business Flow 投影为 `playbackScenarios[]`：

- Scenario 精确绑定 Business Flow Core Ref 与 View Binding。
- Step 精确绑定源 Step、Module、Connection、View Node、View Edge 和相关 Core Ref。
- View 未投影相应 Node/Edge 时必须输出 `informationGaps`，不得补造锚点。
- Scenario/Step ID 由绑定语义确定性生成，项目修订或流程事实变化后重新编译。

## Viewer 运行规则

- 默认静止，不自动播放。
- 只能由用户点击“播放”启动；播放到最后一步后停止。
- 提供播放、暂停、重新开始、动效开关和速度控制。
- 页面隐藏时暂停；`prefers-reduced-motion` 下默认静止；打印保持静态语义。
- 播放进度、速度、当前 Step 和动效开关属于 Viewer State，不进入 Core、Explain Pack 或 Proposal Semantic Hash。

## Studio 关系

Studio 可以编辑模块、连接及候选层级；Business Flow 的正式写入仍必须进入隔离 Candidate、Formal Validation、Proposal、精确批准和 Apply。V0.8 不允许仅凭 Studio 连线自动生成“真实业务流程”。
