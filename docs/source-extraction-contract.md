# Source Extraction Contract v0.1

状态：WP2 Slice C Implemented；Python/JS/TS/Java/Manifest/Properties Foundation；未进入公开发布

对应 Finding：SF-24、SF-30、SF-39、SF-40

## 1. 目标与边界

Source Extraction 把源码、manifest、构建/部署配置和语言工具输出转换为 typed Source Topology Observation。
Observation 是带 Evidence 的输入，不是正式 Module、Current Architecture 或 Runtime Truth。

Extractor 不读取已分类 Secret 路径、外部私有正文或项目根之外的路径；普通源码正文仅在进程内瞬时解析，
不写入 Observation、Model/View IR、Receipt、Event 或 Renderer。路径、位置、大小、digest 与 import/package
标识属于有界工程元数据。当前不扫描普通源码中的嵌入式 Secret，因此 Receipt 必须记录
`embeddedSecretScan=not_performed`，不得声称源码一定不含 Secret。

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

该门禁已由 Slice A 满足并冻结以下真实消费者合同：

- `source-topology-observation.schema.v0.1.json`；
- `extraction-receipt.schema.v0.1.json`；
- 既有 `transformation-loss-report.schema.v0.1.json`；
- `source_topology.py`、`extract_source_topology.py` 与 Model IR 可选 Source Observation 输入。

## 6. Slice A 支持范围

- Repository Inventory：Git tracked + 非忽略 untracked，或非 Git 有界目录清单；
- Python：标准库 AST 的静态 import、type-only import、相对 import 与 dynamic import candidate；
- JavaScript/TypeScript：有界 comment-aware 静态模式，保留 `import type`、`require` 与 dynamic import；
- Java：有界 comment/string-aware `package` / `import` 模式，精确解析项目内唯一类型和外部符号；
  static import 只定位到可证明的类型前缀；重复 FQCN、wildcard 多目标保持 unresolved；另记录顶层声明、
  type-level annotation 与构造函数参数的行级元数据，但不推断框架行为；
- Manifest：`package.json`、`pyproject.toml` 与 Maven `pom.xml` 声明依赖；Maven 使用标准库 XML parser，
  dependency 坐标保持 declared；`pyproject.toml` 使用 Python 3.11+ 标准库
  `tomllib`，Python 3.10 无 TOML parser 时保留 parse failure/Loss，不静默猜测；
- Java Properties：只持久化 key、line、Evidence Pin 和白名单安全值；`database`、Thymeleaf mode、Actuator
  exposure 可记录安全标量，datasource URL 只记录 `jdbc:<driver>` scheme。密码、用户名、token、任意自定义值
  一律 `omitted_by_policy`；配置声明不等于 active runtime；
- 每个文件、元素和关系绑定 Source Location、文件 SHA-256、Git HEAD 或完整 Content Digest；
- Observation 可选合并进 Model IR，但实体保持 `source_element`，关系保持 derived/declared dependency，
  不进入 module profile，也不生成 Sequence Order。

JS/TS 当前不是完整 parser，Receipt 固定披露 `javascript_typescript_full_parser_not_used`。import/dependency
始终保持 `runtimeObserved=false`、`sequenceOrder=null`。缺失相对目标、动态表达式与解析失败进入 Loss/Unknown，
不得删除以制造“完整”拓扑。

Java 当前也不是完整 parser，Receipt Adapter 固定为 `partial`，Observation 披露 `java_full_parser_not_used` 与
`java_reflection_generated_sources_and_calls_not_resolved`。声明/注解/构造参数只是 bounded syntax metadata；
它不解析方法调用、同包隐式类型引用、反射、注解处理、生成源码或运行状态；Java import 始终只是 derived
dependency candidate。Properties Adapter 只读取声明，不推断 profile 激活、数据库连接或部署暴露。

## 7. Source Element → Module Mapping Proposal

`compile_source_module_mapping.py` 可以把 main-source Java package 编译成待评审候选。它精确绑定 Source
Observation ID/Hash/Content Digest、Source Element 与 Evidence Pin；test、configuration、manifest 和非 Java
元素保持 unmapped。输出固定为 `pending_review`、`suggestedModuleId=null`，不会写正式 Panorama。

package 仅是候选分组信号。正式 Module 必须继续评审职责、接口、状态所有权和部署边界，并经过精确 Proposal
Hash 的正常 Approval/Apply；Mapping Proposal 本身不能绕过该治理流程。

```powershell
python scripts/compile_source_module_mapping.py .panorama-work/source-observation.json `
  --generated-at 2026-08-21T12:05:00Z `
  --output .panorama-work/source-module-mapping-proposal.json
```

## 8. 安全与容量

- 拒绝 project root 或 in-scope 文件的 symlink/junction/reparse point；
- 拒绝绝对路径、`..` 逃逸、超过 32 层的路径；
- 单文件上限 1 MiB、总量上限 128 MiB、文件上限 20,000，超限 fail closed；
- 排除 `.env*`、私钥/keystore 后缀、Secret 目录、依赖/构建/cache、`.panorama-work` 与执行 Skill 的
  `evals/`；
- 不执行项目代码、不安装依赖、不访问网络、不修改项目。

Receipt 的安全陈述必须区分：有支持的输入时 `sourceBodiesReadTransiently=true`，零个受支持输入时为 `false`；
`sourceBodyPersisted=false`、`classifiedSecretPathsRead=false` 与 `embeddedSecretScan=not_performed` 保持固定。
发现 Kotlin/Go/Rust/C/C++/C#/Ruby/PHP/Swift/Scala 等 source-like 文件但没有对应 Adapter 时，
`coverage/status/lossReport` 必须为 `partial` 并披露 `unsupported_source_languages_present`，不得以 0 files
报告 completed。若 Project Root 不是精确 Git checkout，未支持语言正文不进入 Content Digest，
`currentness=unknown` 并披露 `unsupported_source_snapshot_not_content_bound`；固定外部 Source Inventory 只能作为
Proof Lab 旁证，不能被 Extractor 冒充自身 Binding。

## 9. 命令

```powershell
python scripts/extract_source_topology.py path/to/project `
  --project-id PRJ-ID `
  --observed-at 2026-08-21T12:00:00Z `
  --observation-output .panorama-work/source-observation.json `
  --receipt-output .panorama-work/extraction-receipt.json `
  --loss-output .panorama-work/extraction-loss.json

python scripts/compile_panorama_model_ir.py project-panorama.json `
  --source-observation .panorama-work/source-observation.json `
  --output .panorama-work/source-bound.model-ir.json
```

`--observed-at` 是显式 as-of 输入，避免运行时钟破坏可重算性。输出存在时默认拒绝覆盖；`--overwrite`
只覆盖候选制品，不修改正式 Panorama 或业务源码。
