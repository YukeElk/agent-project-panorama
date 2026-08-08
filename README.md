# Agent Project Panorama V0.1.2（中文 Renderer）

`Agent Project Panorama` 是一个以架构为主轴、Local-first、单项目单 HTML 的
AI/Vibe Coding 工程认知控制面。它用于恢复和维持对需求、架构、模块、演进、验证、
部署与资源的理解，不是任务看板、文档编辑器或多人项目管理平台。

## V0.1.2 能力

- 无构建、无 CDN、无远程字体、无运行时网络请求的 Single HTML Renderer；
- `控制台 / 系统 / 演进` 三主视图；
- 当前 / 目标 / 迁移、原生 SVG 连接与通用实体详情；
- 同环境多 Deployment、部署视图与完整资源池；
- Module、Decision、Acceptance、Gate、Risk、Connection、Reference 详情；
- Embedded / External File / External Store / None 四种凭据模式；
- JSON Schema、全局 ID、跨实体引用、结构化 Findings 和工程规则校验；
- Approval Hash、Revision/Hash、独占锁、备份、原子写入与最小 JSON Patch Apply；
- 默认 Secret 脱敏、Git Secret Risk 与统一 URL Sanitizer；
- INIT 自动校验、最小项目骨架、确定性 Proposal 工具和 GitHub Actions CI。

页面只读。核心实体编辑、评审批准和文件写回必须通过外部更新流程完成。

## 环境

- Python 3.10+
- `jsonschema`
- `pytest`

```powershell
python -m pip install jsonschema pytest
```

Renderer 不需要 Python；生成后的 HTML 可直接在桌面浏览器打开。

用户可见界面、CLI 帮助、Validator 说明与 Skill 工作流使用简体中文。Schema Key、枚举原值、
ID、Finding Code、JSON Patch Path 和 CLI Flag 保持英文稳定，以兼容现有数据与自动化工具。

## 最小项目初始化

先生成不虚构模块、部署或证据的最小 Schema-valid 数据：

```powershell
python scripts/new_project_data.py `
  --project-id PRJ-MY-PROJECT `
  --name "My Agent Project" `
  --objective "建立项目工程认知" `
  --current-focus "澄清首轮需求" `
  --requirement "首个待澄清需求" `
  --output project.v0.1.json
```

生成 HTML。INIT 默认执行 JSON 前置校验和生成后 HTML 校验；Error 阻止正式输出：

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data project.v0.1.json `
  --output project-panorama.local.html
```

`--allow-invalid` 只用于显式调试，不应生成正式 Panorama。

## Reference Project

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data examples/reference-project.v0.1.json `
  --output examples/reference-project.html
```

直接打开 `examples/reference-project.html`。Reference 的 development 环境同时包含 Active
与 Deploying Deployment，用于验证并行迁移和同环境多版本显示。

## 升级已有 HTML 到中文 Renderer

Renderer 中文化属于 Presentation Layer 升级，不是普通数据 Patch。默认生成新文件：

```powershell
python scripts/upgrade_renderer.py `
  path/to/project-panorama.html `
  --output path/to/project-panorama.zh-CN.html
```

只有明确要替换原文件时才使用 `--in-place`；脚本会先创建时间戳备份。升级前后完整 JSON 与
Data Hash 必须一致，输出的 Presentation Hash 必须与目标模板一致。

## 读取与脱敏

```powershell
python scripts/read_panorama.py examples/reference-project.html
python scripts/read_panorama.py examples/reference-project.html --json
```

`--json` 默认将所有 Embedded Credential 字段替换为 `***REDACTED***`，不会读取
External File 内容，也不会解析 External Store。只有用户明确需要明文时才允许：

```powershell
python scripts/read_panorama.py examples/reference-project.html `
  --json --unsafe-include-secrets
```

该命令会向 stderr 输出明文风险警告。

## 校验

```powershell
python scripts/validate_panorama.py examples/reference-project.html
python scripts/validate_panorama.py examples/reference-project.html --json
```

文本输出保持 `ERROR / WARNING / INFO`；`--json` 输出包含 `level`、`severity`、`code`、
`message`、`path` 的结构化 Findings。退出码：

- `0`：无 Schema/Cross-reference Error；
- `1`：Schema、跨引用或数据一致性 Error；
- `2`：文件、JSON、Marker 或运行依赖错误。

Warning 不阻止生成，但 High/Critical Findings 在 Proposal 阶段不得被隐藏，并默认进入
Update Batch Attention。

## Proposal → Review → Apply

准备候选操作文件：

```json
{
  "operations": [
    {"op": "replace", "path": "/intent/currentFocus", "value": "新的主线"}
  ],
  "summary": "更新当前主线",
  "changeLevel": "local"
}
```

确定性生成事实差异、Validation Findings、Attention、审计草稿和 Proposal Hash：

```powershell
python scripts/propose_update.py `
  examples/reference-project.html `
  candidate-update.json `
  --output pending-update.json
```

`pending-update.json` 初始为 `approval.status = pending`，不能 Apply。用户审阅完整 Proposal
后，只填写审批记录：

```json
{
  "status": "approved",
  "approvedBy": "user",
  "approvedAt": "2026-08-08T12:00:00Z",
  "proposalHash": "<保持 propose_update 生成的值>"
}
```

不得在批准后继续修改 `baseRevision`、`baseDataHash`、`operations`、`changeRecords`、
`reviewDraft`、`updateBatchDraft` 或 `guidanceDraft`。任一字段变化都会使 Approval Hash 失效，
必须重新 Proposal 和 Review。

Apply：

```powershell
python scripts/apply_patch.py `
  examples/reference-project.html `
  pending-update.json
```

Apply 强制检查 Approval Hash、Review/UpdateBatch 关系、Base Revision/Data Hash、Schema、
跨引用、Presentation Hash 和提交前并发状态。失败不覆盖原 HTML；进入 Apply 后创建的备份
保留用于审计和恢复。历史 Next Focus 会在 v0.1.1 兼容层中保留为 inactive historical
record，当前 UI 只显示当前 Guidance。

## Security

- Embedded 允许用于本地开发，但遮蔽不是加密，HTML 源码仍可读取值；
- 推荐使用 `project-panorama.local.html`，仓库 `.gitignore` 已包含 `*.local.html`；
- Embedded 数据位于 Git tracked/staged 文件时，Validator 输出 High `GIT_SECRET_RISK`；
- Production Credential 优先使用 External File / External Store；不自动迁移用户数据；
- Renderer 的所有动态链接统一经过 `safeHref()`；允许 HTTP、HTTPS、file 和相对路径，
  拒绝 JavaScript、VBScript、data 和其他未批准 Scheme。

## 运行时与实体详情

`系统 → 运行时与资源` 包含：

- 部署视图：环境 → 部署列表 → 发布 → 模块 artifactVersion → 资源 → 访问；
- 资源池：按环境 / 类型 / 状态过滤所有项目资源，包括未绑定当前 Deployment 的资源；
- 资源访问与凭据模式；Embedded 默认遮蔽；
- 通用右侧实体详情：Module、Decision、Acceptance、Gate、Risk、Connection、Reference。

控制台环境状态聚合同环境全部 Deployment，例如“1 个运行中 / 1 个部署中”。

## 兼容性

- Data Schema：`0.1`
- Renderer：`0.1.2`（zh-CN）
- 支持 Template：`0.1.0`、`0.1.1`
- Skill 支持 Schema：`0.1`

不支持的 Schema/Template 会显示或返回明确错误，不静默渲染。

## 自动测试与 CI

```powershell
python -m pytest -q
```

CI 在 Python 3.10 / 3.12 上运行测试、Node URL Sanitizer、校验 Reference JSON、生成并校验
Reference HTML、验证 Renderer 升级保持 Data Hash，以及检查 Presentation Hash。

## Presentation Compatibility Contract

普通数据更新必须：

1. 保持 `project-panorama-data` ID 与两个 Marker；
2. 只替换 `application/json` payload；
3. 不改 CSS、Renderer JavaScript 或 DOM；
4. 保留所有 `extensions` 和未知扩展字段；
5. Apply 前后 `compute_presentation_hash` 一致。

## Design Baseline 与 Schema Findings

- 原始设计基线位于 `docs/design/`；
- 当前冻结 Schema 为 `schema/panorama.schema.v0.1.json`；
- 建模缺口记录在 `docs/schema-findings.md`；
- V0.1.1 没有覆盖或重构 v0.1 Schema。

V0.2 的历史快照、Artifact Manifest、结构化 Replacement 和正式 Next Focus Snapshot 仅作为
后续 Migration Proposal，本轮未执行。
