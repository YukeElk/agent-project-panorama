# Multistack Source Adapter Contract v0.1

状态：V0.82 Foundation Contract

## 1. Registry

语言、Manifest 和技术栈识别必须来自版本化的确定性 Registry。Registry 条目至少包含：

- stable adapter ID/version；
- language/manifest/config identity；
- suffix 或 exact filename；
- parser class 与 capability level；
- explicit information gaps；
- source/body safety policy。

不允许 Renderer、LLM 或调用方各自维护另一份“支持语言”列表。Schema、Extractor、Receipt、Support Matrix 和测试必须由同一 Registry 对账。

## 2. Parser Class

- `python_ast`：Python 标准库 AST；
- `bounded_lexer`：comment/string-aware 的静态 dependency 语法；
- `structured_manifest`：JSON/TOML/XML/YAML/line-oriented manifest；
- `declaration_scanner`：框架、接口和基础设施声明信号；
- `not_supported`：只纳入 Inventory/Loss，不读取正文。

bounded parser 不得描述为 compiler frontend、type checker、call graph 或 runtime tracer。

## 3. 通用输出

每个 project-local Source Element 必须保存 language、path、digest、parse status、adapter binding 和 Evidence Pin。
Language Adapter 只可产生：

- package/namespace/module declaration metadata；
- static import/include/use/require relation；
- dynamic or unresolved dependency candidate；
- framework/route/resource/infra declaration signal；
- explicit Information Gap/Loss。

关系保持 `runtimeObserved=false`、`sequenceOrder=null`。框架信号不自动产生正式 Connection、Deployment、Resource、Requirement、Risk 或 Decision。

## 4. 技术栈投影

`stackProfile` 是 Source Observation 的只读扩展，按以下类别记录：

- runtime/language；
- frontend/backend/framework；
- data/messaging/ai；
- interface/build/deployment/ci。

每条 Stack Signal 必须有 signal ID、category、technology、source kind、confidence、fact status、Evidence Pin IDs 和 adapter ID。
仅 Manifest 明确声明时使用 declared；来自语法或配置匹配时使用 derived；不得写 observed runtime。

## 5. Module Candidate

允许的分组基础包括：

- Java/Kotlin package；
- Go package directory；
- C# namespace/project；
- Python import package；
- JS/TS workspace/package；
- Rust crate、PHP namespace/package、Ruby gem/application、Swift module、Scala package、C/C++ build target。

分组仍只是 L2 candidate。编译器必须精确覆盖 project-local elements，保存 Evidence，并固定为 `pending_review`、`suggestedModuleId=null`。
正式 Module 继续要求职责、接口、状态所有权和部署边界评审及 Hash-bound Proposal/Apply。

## 6. Currentness 与 Monorepo

Git checkout 绑定精确 HEAD；非 Git 项目完整 Digest 必须覆盖所有已支持与仅识别的 source-like 文件。
Monorepo 的 workspace/build root 可以成为分组信号，但 directory ≠ workspace ≠ service ≠ Module。
任一 Adapter/Registry/Source Binding 变化都使下游 Mapping、Module Logic、Explain Pack 和 Delivery stale。

## 7. 安全与失败关闭

- 不执行项目代码、构建工具或 package manager；
- 不安装依赖、不访问网络、不读取 project root 外内容；
- Secret path、link/reparse、容量和路径深度沿用 Source Extraction Contract；
- 普通源码中的 embedded secret scan 状态必须如实记录；
- parser crash 只使对应文件/Adapter partial，不得吞掉 Loss；
- Registry 未知语言必须进入 unsupported inventory，不得报告 complete。

## 8. 支持声明

Support Matrix 的状态只允许：`supported`、`partial`、`preview`、`not_applicable`、`unsupported`。
正式 `supported` 必须绑定验证项目、expected facts、actual facts、recall、forbidden claims、determinism 和测试证据。
没有验证绑定的框架检测只能称 preview，即使 Parser 已能产生节点和关系。
