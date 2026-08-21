# Source Extraction Contract v0.1

状态：Behavioral Design Closed；WP2 Machine Schema/Implementation Pending

对应 Finding：SF-24、SF-30、SF-39、SF-40

## 1. 目标与边界

Source Extraction 把源码、manifest、构建/部署配置和语言工具输出转换为 typed Source Topology Observation。
Observation 是带 Evidence 的输入，不是正式 Module、Current Architecture 或 Runtime Truth。

Extractor 不读取 Secret、外部私有正文或项目根之外的路径；不把源码正文写入 Observation、Model/View IR、
Receipt、Event 或 Renderer。路径、位置、大小与 digest 属于有界工程元数据。

## 2. Adapter 分层

1. Repository Inventory：入口点、workspace/package、构建、部署和配置候选；
2. Language Adapter：typed import/dependency/call candidate；
3. Configuration Adapter：container、route、queue、database、collector pipeline 等 declared relation；
4. Author Model Adapter：Structurizr/LikeC4 等作者模型，并生成 Transformation Loss Report；
5. Runtime/Event Adapter：受信 trace/Event/Receipt，提供 observed relation 和 order。

每个 Adapter 必须记录 ID/Version、Project/Source Binding、输入/输出 Hash、解析状态、Access Class、Loss、
Information Gap 与 currentness。未解析动态加载、反射、生成代码和外部依赖必须保留 unresolved/unknown。

## 3. 不可推导规则

- file/directory ≠ Module；package ≠ service；import ≠ runtime call；depends_on ≠ observed dependency；
- 文件相邻、命名相似和共同目录不能产生 Relation；
- source topology grouping 只能是 `derived candidate`，不能自动写入正式 Current/Target；
- static call candidate 不能自动成为 observed Sequence；
- LLM 只可解释或分组已有 observation，不能新增无 Evidence Node/Edge。

## 4. Currentness 与 Evidence

- Git 项目绑定精确 revision；非 Git 项目绑定完整 Source Content Digest 与 coverage；
- partial coverage 不能称为 current；mtime/文件数不能替代 digest；
- Source Location 只有在 revision/digest 当前时可激活；旧位置保持可读但标记 stale/historical；
- 每个输出 Node/Relation 都必须至少绑定一个 Source Location、配置 Pointer、作者模型 ID、Receipt 或 Event。

## 5. WP2 Schema 冻结门禁

只有 Repository Inventory 与至少一个 Language/Config Adapter 端到端原型通过后，才冻结
`source-topology-observation.v0.1` 和 `extraction-receipt.v0.1`。Schema 必须由真实输出与负向测试证明，不先创建
无人消费的字段集合。
