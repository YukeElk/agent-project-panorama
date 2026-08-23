# Source Extraction Contract v0.2

状态：V0.85 Release Candidate；v0.1 输入兼容；多语言/多技术栈 Adapter Registry 已实现；尚未获外部发布授权

对应 Finding：SF-24、SF-30、SF-39、SF-40、SF-46

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

## 5. Schema 版本与兼容

只有 Repository Inventory 与至少一个 Language/Config Adapter 端到端原型通过后，才冻结
`source-topology-observation.v0.1` 和 `extraction-receipt.v0.1`。Schema 必须由真实输出与负向测试证明，不先创建
无人消费的字段集合。

该门禁已由 Slice A 满足并冻结以下真实消费者合同：

- `source-topology-observation.schema.v0.1.json`；
- `extraction-receipt.schema.v0.1.json`；
- 既有 `transformation-loss-report.schema.v0.1.json`；
- `source_topology.py`、`extract_source_topology.py` 与 Model IR 可选 Source Observation 输入。

V0.85 不修改已冻结的 v0.1，而是新增：

- `source-topology-observation.schema.v0.2.json`；
- `source-module-mapping-proposal.schema.v0.2.json`；
- `stack_adapters.py` 单一版本化 Registry；
- `sync_source_observation.py` 项目本地幂等同步入口。

Extractor 默认输出 v0.2；Validator 和 Model IR 同时接受 v0.1/v0.2。v0.2 的 `adapterRegistry`、
`supportMatrix`、`stackProfile` 与 `monorepoBoundaries` 都进入 Observation semantic hash。Registry 版本、
Evidence Pin 或 Stack Signal 被篡改时必须 fail closed，不能静默降级到旧合同。

## 6. V0.1 基线支持范围

- Repository Inventory：Git tracked + 非忽略 untracked，或非 Git 有界目录清单；
- Python：标准库 AST 的静态 import、type-only import、相对 import 与 dynamic import candidate；
- JavaScript/TypeScript：有界 comment-aware 静态模式，保留 `import type`、`require` 与 dynamic import；
- Java：有界 comment/string-aware `package` / `import` 模式，精确解析项目内唯一类型和外部符号；
  static import 只定位到可证明的类型前缀；重复 FQCN、wildcard 多目标保持 unresolved；另记录顶层声明、
  type-level annotation 与构造函数参数的行级元数据，但不推断框架行为；
- Manifest：`package.json`、`pyproject.toml` 与 Maven `pom.xml` 声明依赖；Maven 使用标准库 XML parser，
  dependency 坐标保持 declared；`pyproject.toml` 优先使用 Python 3.11+ 标准库
  `tomllib`，Python 3.10 使用已声明依赖 `tomli`；两者都不可用时保留 parse failure/Loss，不静默猜测；
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

### 6.1 V0.85 多语言支持等级

支持等级描述证据能力，不等于“理解整个程序”：

- L0：只识别文件/清单存在；
- L1：有界解析 package/namespace/import/dependency 并生成带 Source Location 的静态候选；
- L2：能在正式 Module 边界内生成受约束的内部逻辑 Observation；
- L3：有受信运行/Event/Trace 证据，可陈述实际顺序或运行关系。

V0.85 正式 L1：Python、JavaScript、TypeScript、Java、Kotlin、Go、C#。V0.85 Preview L1：Rust、PHP、Ruby、
Swift、Scala、C、C++。Preview 表示已具备有界抽取和测试样例，但尚未达到正式样本集的 recall/precision 门禁；
页面、Receipt 和 Support Matrix 必须显示 preview，不能称为正式全语义支持。所有静态关系仍固定
`runtimeObserved=false`、`sequenceOrder=null`。

Manifest/构建边界覆盖 `package.json`、`pyproject.toml`、`pom.xml`、Gradle、`go.mod`、`.csproj`、
`Cargo.toml`、`composer.json`、`Gemfile`、`Package.swift` 与 CMake。技术栈 Profile 分为 framework、data、
AI、interface 和 delivery 信号；例如 React/Next.js、Spring、ASP.NET Core、Gin、FastAPI、EF/SQLAlchemy、
OpenAI/LangChain、gRPC/GraphQL/OpenAPI、Docker/GitHub Actions/Terraform。每个信号必须绑定 Evidence Pin，
并明确 `declared` 或 `derived`，不能由文件名相邻关系推导架构事实。

## 7. Source Element → Module Mapping Proposal

`compile_source_module_mapping.py` 可以按受支持语言的稳定语义边界生成待评审候选：Java/Kotlin/Scala package、
Go package directory、C# namespace/project、Python package、JS/TS workspace、Rust crate、PHP namespace、
Ruby app、Swift module 和 C/C++ build target。它精确绑定 Source Observation ID/Hash/Content Digest、Source
Element 与 Evidence Pin；配置、manifest 和无法证明分组边界的元素保持 unmapped。输出固定为
`pending_review`、`suggestedModuleId=null`，不会写正式 Panorama。

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
发现 Dart、Elixir、F#/Lua/Objective-C/R/Solidity 等 source-like 文件但没有对应 Adapter 时，
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

python scripts/sync_source_observation.py path/to/project `
  --project-id PRJ-ID `
  --observed-at 2026-08-24T12:00:00Z
```

`--observed-at` 是显式 as-of 输入，避免运行时钟破坏可重算性。输出存在时默认拒绝覆盖；`--overwrite`
只覆盖候选制品，不修改正式 Panorama 或业务源码。

Sync 只写项目自己的 `.panorama-work/source/`：不可变 Observation/Receipt/Loss 与 `latest.*` 指针制品。相同
Source Binding + Registry Hash 返回 `noop`；内容或 Registry 变化才发布新观察。输出路径逃逸、Latest 被篡改、
Observation 校验失败或锁冲突均 fail closed。该命令不修改 Panorama Core，不自动批准 Mapping Proposal。

## 10. V0.8 Module Logic 有界归纳链

Module Logic 不直接把 Source Topology 中的文件/import 变成内部流程。它使用三段只读流程：

1. `prepare_module_logic_analysis.py` 验证 Panorama 和 Source Observation，根据正式 Module `codePath`
   生成最多 256 文件 / 16 MiB 的元数据 allow-list，不持久化源码正文；
2. Codex 只能瞬时读取 Manifest 列出的文件，输出受约束 Candidate；不执行项目代码、不安装依赖、
   不使用网络、不读取 Manifest 外路径；
3. `materialize_module_logic_observation.py` 核对文件/行/Evidence Digest、Manifest/Panorama/Module/Source
   Binding、Coverage、Loss、敏感字段和 Hash；`reconcile_module_logic_observation.py` 后续比较实时文件摘要
   与最新 Source Observation，只返回 `match|stale|incomplete|unknown`。

```powershell
python scripts/prepare_module_logic_analysis.py project-panorama.json `
  .panorama-work/source-observation.json `
  --module-id MOD-ID --prepared-at 2026-08-23T08:00:00Z `
  --output .panorama-work/module-logic/MOD-ID.analysis-manifest.json

python scripts/materialize_module_logic_observation.py project-panorama.json `
  .panorama-work/module-logic/MOD-ID.analysis-manifest.json `
  .panorama-work/module-logic/MOD-ID.candidate.json `
  --generated-at 2026-08-23T08:05:00Z `
  --output .panorama-work/module-logic/MOD-ID.observation.json

python scripts/reconcile_module_logic_observation.py project-panorama.json `
  .panorama-work/module-logic/MOD-ID.analysis-manifest.json `
  .panorama-work/module-logic/MOD-ID.observation.json `
  .panorama-work/source-observation.latest.json `
  --project-root path/to/project --checked-at 2026-08-23T09:00:00Z
```

Manifest 被截断、Source 不完整、Candidate 未声明读取全部 allow-list 或存在 Unresolved/Loss/Gap 时，
Observation 必须降为 `partial/recorded_as_of`。任何 Evidence 越出 Manifest、Digest 错配、Prompt/源码正文持久化、
Panorama/Module/Source 绑定变化都 fail closed。
