# Operational Evidence Contract

本契约定义 V0.1.4 如何在不读取项目内容、Secret 或外部私有数据的前提下，获取项目自有的 Current Operational Evidence。Discovery、Inspector 与 Runtime Observer 的职责必须分离。

## 目录

1. [Access Classes](#1-access-classes)
2. [Discovery Boundary](#2-discovery-boundary)
3. [Operational Candidate Qualification](#3-operational-candidate-qualification)
4. [Bounded Inspector](#4-bounded-inspector)
5. [Secret and Content Safety](#5-secret-and-content-safety)
6. [Runtime and Verification Observation](#6-runtime-and-verification-observation)
7. [Provenance](#7-provenance)
8. [Failure Semantics](#8-failure-semantics)

## 1. Access Classes

每个 Evidence Candidate 使用以下五类之一：

| Access Class | 含义 | 自动读取策略 |
| --- | --- | --- |
| `PUBLIC_PROJECT_EVIDENCE` | README、ADR、源码、测试、配置、CI 等项目公开工程材料 | 按 Semantic Protocol 选择性读取 |
| `PROJECT_OPERATIONAL_METADATA` | 项目自有 registry、state、audit、generated status、test/lint report 等 | 只能经 bounded Inspector 读取 |
| `PROJECT_CONTENT` | 用户笔记、知识正文、文章、文档正文与业务资料 | 默认禁止读取 |
| `SECRET` | `.env`、私钥、凭据文件或 Secret-risk 路径 | 禁止读取值 |
| `EXTERNAL_PRIVATE_DATA` | project root 之外的私有文件、Vault、数据库正文或凭据源 | 禁止自动访问 |

Access Class 是读取边界，不是 Fact Authority。`PROJECT_OPERATIONAL_METADATA` 也不能自动成为 Current Fact Source。

## 2. Discovery Boundary

`scripts/discover_project_evidence.py` 只输出路径、格式、大小、mtime、Git metadata、marker 与 Hint。它不得读取 Operational Metadata 正文。

Discovery 为每个候选输出：

- `accessClass`；
- `operationalMetadataHint`；
- `machineStateHint`；
- `generatedHint`；
- `secretRisk`；
- `knowledgeBaseHint`。

Knowledge root 内的普通文件优先保持 `PROJECT_CONTENT`。只有同时存在明确 Operational System Boundary、Operational Semantic 与安全格式时，才标记为 `PROJECT_OPERATIONAL_METADATA` Candidate。

Knowledge Root 名称统一通过 canonical matcher 判断：先 lowercase，再把 `_`、`.`、空格与 `-` 规范为分隔符；接受 exact canonical name，或以 `vault`、`wiki`、`knowledge`、`knowledge-base`、`kb` terminal concept 结尾的分隔复合名称。不得使用简单 substring matching，也不得维护另一套 exact-set index 判断。

因此 `personal-vault`、`team_wiki`、`private-knowledge-base` 可以成为 Knowledge Root；`vault-tools`、`wiki-renderer`、`knowledge-graph`、`kb-client`、`myvault` 不能仅凭名称成为 Knowledge Root。

## 3. Operational Candidate Qualification

普通工程目录中的资格判断至少需要两个独立信号：

1. 结构化格式或明确的机器状态文件名；
2. operational path/semantic signal，例如 system、state、registry、audit、reports、runtime、health、metadata、generated、tasks、approvals 或 retrieval。

允许的 Inspector 格式：

- JSON / JSONL；
- YAML；
- TOML；
- 明确 machine-status 的 Markdown/frontmatter。

Knowledge Root（`knowledge`、`vault`、`wiki`、`kb`）默认覆盖普通 operational 名称。`reports`、`tasks`、`status` 等目录名本身不能授权读取。Knowledge Root 内升级为 Operational Candidate 必须同时满足：

1. 明确系统边界：`90-System`、`_system`、`system` 或 `_state`；
2. 边界内存在 registry、state、audit、runtime、tasks、approvals 等 Operational Semantic；
3. 使用允许的安全格式。

Knowledge Root 内的 `.obsidian` 目录只构成内容/应用配置位置，不是 Operational System Boundary；其中 JSON 不得因格式或配置文件名自动升级为 Operational Metadata。本轮不读取 `.obsidian` 内容来推断 Root。

固定边界示例：

```text
knowledge/reports/customer-report.json → PROJECT_CONTENT
vault/tasks/personal-tasks.json        → PROJECT_CONTENT
vault/90-System/tasks/registry.json    → PROJECT_OPERATIONAL_METADATA
```

## 4. Bounded Inspector

使用：

```powershell
python scripts/inspect_operational_evidence.py `
  path/to/project-root `
  --path vault/90-System/tasks/registry.json
```

Inspector 必须：

- 只接受 project-relative path；
- 拒绝符号链接与 root escape；
- 对 structured data 使用完整、有限大小的解析；
- 对 generated Markdown 只读取 bounded prefix；
- 不返回原始文件；
- 只输出状态、计数、ID、时间、关系和有限的结构化事实；
- 输出 `contentHashScope`，区分完整文件与 bounded prefix；
- 记录 `redactions`、`omittedFields`、`warnings` 与 `observedAt`。

Structured 文件超过上限时 fail closed，不解析截断 JSON/YAML/TOML。Generated Markdown 可以从 bounded prefix 提取有限状态行，但必须标记 `truncated=true`。

## 5. Secret and Content Safety

Secret 识别不能只依赖文件名。JSON、YAML、TOML 与 Frontmatter 必须递归识别 password、token、secret、API key、private key、credential、authorization、cookie、session 等键，并将值替换为：

```text
***REDACTED***
```

正文型键（如 body、content、text、article、message、notes、description）默认从 Operational Facts 中省略，并进入 `omittedFields`。

以下情况不得读取正文：

- `PROJECT_CONTENT`；
- `SECRET` 路径；
- `EXTERNAL_PRIVATE_DATA`；
- 非 Operational Candidate；
- root 外路径或符号链接；
- 无法安全解析或超过 structured 上限的文件。

## 6. Runtime and Verification Observation

使用：

```powershell
python scripts/observe_project_runtime.py path/to/project-root `
  --dependency json `
  --process-name worker `
  --scheduler-name nightly-check `
  --check-path reports/current-status.json
```

允许的只读观察：

- Git HEAD、branch、dirty、状态条目数量与状态 Hash；
- project-relative 文件是否存在；
- 顶层 Python dependency 的 import availability（不执行 import）；
- 指定 process name 是否被观察到，不读取 PID 参数或环境；
- 指定 scheduler name 的注册状态，不列出任务命令；
- 项目声明的 test command（默认只校验声明，不执行）。

Safe test command 必须来自 project-relative JSON manifest，并声明：

```json
{
  "safe": true,
  "networkAccess": "none",
  "writesProject": false,
  "installsDependencies": false
}
```

Manifest 中的 `safe=true`、`networkAccess=none`、`writesProject=false` 与 `installsDependencies=false` 只是项目声明，不是技术隔离证明。Observer 验证声明后默认返回：

```text
status = requires_explicit_authorization
provenance = not_observed
```

不得自动执行 Python、Node、native executable 或其他通用项目代码。只有用户明确授权准确命令后才能使用：

```powershell
python scripts/observe_project_runtime.py path/to/project-root `
  --test-manifest observation.json `
  --test-id unit `
  --authorize-test-execution
```

授权执行仍使用 `shell=false`、最小环境、超时、命令过滤和前后项目文件元数据快照；拒绝 shell、package manager、install/build/download/upload 和 URL 参数。它只返回 exit code、状态与耗时；stdout/stderr 直接丢弃，不返回输出内容。结果必须诚实记录：

```text
networkIsolation = not_enforced
filesystemIsolation = not_enforced
projectWriteCheck = post_execution_metadata_check
```

在没有 OS sandbox 或等价技术证据时，不得输出 `networkUsed=false`、`secretValuesRead=false` 等无法证明的事实。

安装依赖、构建容器、模型下载、网络测试、外部 API、来源不明或成本明显的命令不得执行，只能登记 `not_observed / requires explicit authorization`。显式授权只解除“是否执行”门槛，不把命令变成已隔离或已证明安全。

## 7. Provenance

关键 Current 状态使用以下 Provenance：

- `observed_now`；
- `generated_current_state`；
- `current_test`；
- `historical_test`；
- `approved_design`；
- `narrative_reference`；
- `inference`。

Inspector 和 Observer 的结果可以通过 Reference `extensions.evidenceProvenance` 或顶层 `extensions.initMaterialization.evidenceInventory` 进入 Panorama。不得把 `historical_test` 静默改写成 `current_test`。

## 8. Failure Semantics

始终保持：

```text
Not Detected ≠ Absent
Installed ≠ Running
Historical Verification ≠ Current Runtime Availability
```

无法读取、解析器不可用、文件变化、超出边界或观察失败时，返回明确 warning/error；不得猜测事实，不得扩大文件访问，不得安装依赖或调用网络补救。
