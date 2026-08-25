# Architecture Layering and Containment Contract v0.1

状态：V0.8.5.1 Released

本合同定义 Panorama 如何把项目事实组织为“架构层级/容器 → 功能模块 → 模块内部逻辑”。它不规定固定层级名称，
也不允许根据目录、框架名、组件后缀或常见架构印象直接归层。

## 1. 主视角

一个 Architecture Scope 的主画布只能声明一个 Primary Viewpoint：

- `logical_capability`：以系统边界、能力和责任归属组织，是个人项目全景主画布的默认值；
- `domain`：以业务领域组织；
- `runtime`：以运行责任和进程边界组织；
- `deployment`：以部署单元和环境组织；
- `integration`：以系统间集成边界组织。

Runtime、Deployment、Source Topology、Sequence 等其他角度应作为 View 投影，不得与主视角的同级 Layer
混成一棵无法解释的树。确需不同主视角时生成独立 View，不通过给同一 Module 随意换层实现。

Schema 固定 Layer/Module 字段结构，不固定 Layer 的名称、数量和深度。`kind` 说明当前 Layer 的角色，
`parentLayerId` 表示语义包含，不是视觉分组或所有权推断。

## 2. Layer、Module 与 Module Logic 判定

### Layer / Container

满足以下任一条件时优先建为 Layer/Container：

- 表示系统边界或由多个独立责任单元组成的能力边界；
- 需要包含多个可独立部署、拥有接口或独立演进的 Module；
- 需要保留父子范围，避免子模块被误解为脱离所属系统；
- 只是组织视角，不直接拥有可验证的实现、运行和发布状态。

### Module

Module 必须能够独立回答职责、接口、状态所有权、部署角色、需求覆盖和演进边界。名称、目录、package、
workspace 或技术标签都不能单独证明 Module 边界。

### Module Logic

候选功能如果主要服务一个 Module，且没有独立接口/状态/部署/演进边界，应进入该 Module 的内部逻辑子画布。
内部预处理、协议适配、模型调用、Loop、工具调用和结果整理不应仅因为源码目录独立就升级为并列 Module。

证据不足时先合并为较大责任单元，或放入 `LAYER-UNASSIGNED` 并记录候选归属；不得使用名称惯例强制分类。

## 3. 归层判定顺序

对每个正式 Module 按以下顺序判断：

1. 确认当前 Architecture Scope 与 Primary Viewpoint；
2. 确认 Module 是否真的是独立责任单元，还是父 Module 的内部逻辑；
3. 识别所属系统/能力容器，先保留包含关系；
4. 使用责任、接口、状态所有权、部署、安全、数据所有权、独立演进和明确设计决定作为证据；
5. 记录为什么选择该 Layer，以及为什么没有选择最接近的替代 Layer；
6. 无法消除歧义时进入 `LAYER-UNASSIGNED`，保持 `unknown/low`，等待评审。

“接入组件通常属于交互层”“执行组件通常属于智能体层”只能成为待验证假设，不能成为正式归层证据。

## 4. 兼容扩展

V0.85 maintenance 不改变冻结 Core Schema，使用现有 `extensions` 保存可验证的层级语义。

### Architecture Profile

`architecture.extensions.layeringProfile`：

```json
{
  "formatVersion": "panorama-layering-profile.v0.1",
  "primaryViewpoint": "logical_capability",
  "secondaryViewpoints": ["runtime", "deployment", "source"],
  "assignmentPolicy": "single_primary_evidence_bound",
  "ambiguityPolicy": "unassigned",
  "containerPolicy": "layer_or_module_logic",
  "status": "draft"
}
```

`status=reviewed` 表示所有在用 Layer 和 Module Scope 都已完成本合同要求的语义说明；未完成时只能保持
`draft`，不得借 reviewed 绕过缺口。

### Layer Semantics

`layer.extensions.layerSemantics`：

```json
{
  "formatVersion": "panorama-layer-semantics.v0.1",
  "boundaryType": "capability",
  "rationale": "该容器表示一个由多个独立责任单元构成的系统能力边界。",
  "inScope": ["该边界内共同演进的责任"],
  "outOfScope": ["独立外部平台"],
  "evidenceReferenceIds": ["REF-ARCH"],
  "confidence": "medium"
}
```

`boundaryType` 允许 `system|capability|responsibility|technical|runtime|integration|unassigned`。

### Module Layer Assignment

`module.extensions.layerAssignments`：

```json
{
  "formatVersion": "panorama-layer-assignments.v0.1",
  "current": {
    "layerId": "LAYER-ID",
    "basis": "responsibility",
    "rationale": "主要职责和状态所有权位于该能力边界内。",
    "evidenceReferenceIds": ["REF-CODE", "REF-ADR"],
    "confidence": "medium",
    "alternativeLayerIds": []
  }
}
```

Assignment Key 使用 `current|target|historical`，必须与同 Scope 的 `layerId/targetLayerId` 一致。`basis` 允许：

```text
responsibility | capability | interface | state_ownership | deployment |
security | data_ownership | independent_evolution | explicit_design_decision | unknown
```

`evidenceReferenceIds` 只能引用正式 Reference。归入 `LAYER-UNASSIGNED` 时必须使用 `basis=unknown`，
`confidence=unknown|low`，并在 rationale/alternativeLayerIds 中保留待确认范围。

## 5. Studio 行为

- 新建 Module 必须进入用户当前选中的 Layer/Container；
- 没有有效选择时只允许进入现有 `LAYER-UNASSIGNED`，否则停止并要求先选择层级；
- 禁止按 Agent/Data/Service 类型、Layer 数组位置或名称自动猜层级；
- 拖动跨层、下拉换层会同时成为 Semantic Edit，并使原归层理由、证据和置信度失效；
- 用户补充的新归层理由进入 Candidate Semantic Hash；坐标仍只进入 Layout Hash；
- Formal Validator 必须拒绝 Assignment 与正式 Layer Binding 不一致，缺少理由/证据保留可见 Gap。

## 6. 投影与呈现

Core `parentLayerId/kind/layerSemantics` 必须进入 Model IR；Module View 必须携带被使用 Layer 的全部祖先，
并在 View Group 中保留 `parentGroupId`。空的父容器不能因为没有直接 Module 而被丢弃。

Renderer 可以折叠或简化容器，但必须让用户看见 Module 所属的完整父子路径。视觉邻近、坐标和连线穿越
不能创建或改变包含关系。

## 7. Validator 边界

确定性 Validator 检查 Profile/Assignment 结构、Scope/Layer 一致性、Reference、父层引用和 reviewed 完整性。
它不根据名称判断“应该属于哪里”。职责含义和候选边界仍由 Agent 结合项目证据解释，用户负责最终评审。
