# Panorama Model IR Contract v0.1

状态：Implementation Slice A

对应 Finding：SF-34、SF-35、SF-39、SF-40

## 1. 定位

`panorama-model-ir.v0.1` 是 Core/Event/Source 输入的只读、可重算归一化模型，不是新的事实源。它只保存
渲染和多视图编译所需的稳定实体、关系、来源与时间边界；删除 Model IR 不会丢失正式工程事实。

## 2. 输入门禁

Slice A 只接受通过 Panorama Formal Validator 的 Schema 0.1/0.2 JSON 或 Single HTML。当前只读取：

- `project`、`meta` 和 Schema Version；
- `architecture.layers/modules/connections`；
- exact-path `factProvenance`；
- recorded `sourceBinding`。

不得从页面文案、DOM、文件相邻、名称相似、目录结构或 LLM 输出补实体和关系。Source/Event 输入在相应
Adapter 完成前固定为未提供或 recorded metadata，不能冒充运行观察。

## 3. 身份与关系

- 正式 Module/Connection 使用 authored ID；不得用显示名称、数组位置或坐标重新编号；
- derived ID 只用于 Model、Evidence、View Node/Edge/Group 等投影对象，由 canonical input 生成；
- Relation 必须保留精确端点、方向、Scope、protocol、mode 和 data summary；
- import/dependency/data flow/runtime call/sequence message 是不同 Relation Kind，不允许扁平化；
- Slice A 只生成 `communication` Relation，不从 Connection 推断 Sequence Order。

Layer 对 Current、Target、Historical 分别绑定。Target 使用 `targetLayerId`，缺失时才回退到正式 `layerId`；
同时投影多个 Architecture Scope 会造成 Layer 归属歧义，因此 module profile v0.1 每次只允许一个 Scope。

## 4. Evidence 与事实状态

每个 Layer/Entity/Relation 至少有一个 Evidence Pin。Slice A 使用：

```text
kind=json_pointer
ref=/architecture/...
revision=<Panorama Data Hash>
digest=<bound object canonical hash>
accessClass=PROJECT_OPERATIONAL_METADATA
freshness=recorded_as_of
```

只有 exact JSON Pointer 的 Fact Provenance 才能覆盖默认 `declared/unknown-confidence`。`inferred` 投影为
`factStatus=derived`，不升级为 observed。离线编译不比较实时 Git；即使记录了 Git Head，也必须保留
`source_currentness_not_verified`。

## 5. Hash 与验证

Model Semantic Hash 覆盖删除顶层 `integrity` 后的完整 Model，包括 Project/Source/Event/`asOf` Binding、
Compiler、Entity、Relation、Evidence 和 Information Gap。相同输入字节与 Compiler Version 必须得到相同
Model ID 和 Semantic Hash。

Validator 必须拒绝：

- Schema invalid、Semantic Hash mismatch；
- 重复 Layer/Entity/Relation ID；
- 未知 Layer 或 Relation Endpoint；
- Evidence Pin 缺失；
- Project/Source/Event Binding 不完整。

## 6. 当前命令

```powershell
python scripts/compile_panorama_model_ir.py project-panorama.json `
  --output .panorama-work/views/project.model-ir.json
```

输出已存在时默认停止；`--overwrite` 只覆盖候选制品，不修改正式 Panorama。
