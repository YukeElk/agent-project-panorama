---
name: agent-project-panorama
description: "操作 Agent Project Panorama V0.1/V0.1.1 Single HTML，执行 INIT、INSPECT、PROPOSE UPDATE、APPLY UPDATE、VALIDATE 和 Renderer 升级。Use when Codex needs to create, inspect, validate, preview, migrate, or apply a controlled architecture-centric project panorama update with secret redaction, approval-hash binding, revision guards, and Presentation Layer preservation."
---

# Agent 项目全景

将本文件所在目录作为 Skill 根目录，并从该目录运行脚本。把一个 Panorama 视为单项目、以架构为主轴的工程认知界面；不要扩展成任务看板或通用项目管理平台。支持 Schema `0.1`、Data Template `0.1.0/0.1.1` 与中文 Renderer `0.1.2`。

## 不变量

- 保持 HTML 对用户只读；数据写入必须经过 `scripts/apply_patch.py`。
- 普通更新只能修改 `project-panorama-data` 内的 JSON payload。
- 保留两个数据 Marker、DOM、CSS、Renderer JavaScript、未知字段与所有 `extensions`。
- Apply 前要求用户评审并批准准确的 Proposal Hash；不得制造或推断批准。
- Schema 重构前先记录 Schema Finding；普通更新不得修改冻结的 Schema v0.1。
- 不确定信息标记为 unknown/draft/pending，或向用户索取证据；不得虚构 ID、路径、评审、验收、部署或凭据。
- 除非用户明确批准迁移，否则保持现有凭据模式。
- 摘要不得输出 Embedded 明文；除非用户明确要求对应敏感值，否则不得使用 `--unsafe-include-secrets`。
- 中文仅用于显示层与用户消息；Schema Key、枚举原值、ID、Finding Code、JSON Patch Path 和 CLI Flag 保持英文。

## INIT

接受模糊想法、明确需求、已有项目或 OSS 改造；只采集已有证据。

没有 Panorama JSON 时，生成最小骨架：

```powershell
python scripts/new_project_data.py `
  --project-id PRJ-ID `
  --name "项目名称" `
  --objective "项目目标" `
  --current-focus "澄清需求" `
  --requirement "初始草稿需求" `
  --output project.v0.1.json
```

骨架只创建一个当前澄清阶段、草稿架构、合法的 Unknown/Draft 状态和两个下一步选项；不得虚构模块、部署、评审、文档或运行事实。

只通过带校验的初始化脚本生成 HTML：

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data project.v0.1.json `
  --output project-panorama.local.html
```

初始化脚本在生成前后都执行校验。`--allow-invalid` 仅用于显式调试，不得用于正式交付。

## INSPECT

1. 先执行 VALIDATE，再信任实体关系。
2. 读取项目定位摘要：

   ```powershell
   python scripts/read_panorama.py path/to/project-panorama.html
   ```

3. 只有确有需要时才读取完整数据：

   ```powershell
   python scripts/read_panorama.py path/to/project-panorama.html --json
   ```

   输出默认脱敏。不要读取 External File 内容，也不要解析 External Store 值。
4. 汇报当前阶段、主线、当前→目标架构、活跃/部署中运行状态、最近更新、High/Critical 关注事项、验证缺口和当前下一步焦点。

## PROPOSE UPDATE

不得修改目标 HTML。

1. 使用最小 `add`、`replace`、`remove` JSON Pointer 操作表达变更。
2. 保留 ID 与 extensions。下一步焦点提供 2–3 个可行选项，说明为什么现在做、收益、风险、影响、前置条件、证据、预期结果和一个推荐项。
3. 生成确定性 Proposal：

   ```powershell
   python scripts/propose_update.py `
     path/to/project-panorama.html `
     candidate-update.json `
     --output pending-update.json
   ```

4. 使用其事实生成 Revision/Hash、Operations、Affected Entities、Validation、结构化 Findings、审计草稿、Attention 与 Proposal Hash；不得解析终端字符串。
5. Preview 与 UpdateBatch Attention 必须包含全部 High/Critical Finding；可以解释，但不得隐藏。
6. 向用户展示语义上的 What/Why/Impact、Change Level、Review Requirement、不确定项、Next Focus 与准确更新包，然后停止等待批准。

用户批准时只填写 `approval.status`、`approvedBy`、`approvedAt`，并保留准确的 `proposalHash`。任何 Base、Operation、Change、Review、UpdateBatch 或 Guidance 变化都要求重新 Proposal 与批准。

## APPLY UPDATE

只有用户明确批准准确 Proposal Hash 时才能执行：

```powershell
python scripts/apply_patch.py `
  path/to/project-panorama.html `
  pending-update.json
```

让脚本强制执行 Approval/Hash 绑定、Review/UpdateBatch 关系、Base Revision/Data Hash、独占锁、备份、校验、Presentation Hash 不变、提交时并发检测和原子替换。

失败时不得静默修复、rebase 或重新批准；重新执行 INSPECT 与 PROPOSE UPDATE。成功后执行 VALIDATE 与 INSPECT，并汇报新 Revision、语义结果、剩余 High/Critical、备份和 HTML 路径。

V0.1.1 将历史 Next Focus 选项保存在 inactive 兼容记录中，避免旧 UpdateBatch 失效。不要把它描述为最终模型；正式快照属于 V0.2 Migration Proposal。

## UPGRADE RENDERER

Renderer 中文化是 Presentation Layer 升级，不是普通数据更新。升级已有 HTML 时执行：

```powershell
python scripts/upgrade_renderer.py `
  path/to/project-panorama.html `
  --output path/to/project-panorama.zh-CN.html
```

只有用户明确选择原文件时才使用 `--in-place`；该模式必须先生成时间戳备份。升级前后必须证明 Data Hash 和完整 JSON 相同，并让输出 Presentation Hash 与目标模板一致。不得顺便翻译用户项目数据、实体名称或技术标识。

## VALIDATE

```powershell
python scripts/validate_panorama.py path/to/project-panorama.html
python scripts/validate_panorama.py path/to/project-panorama.html --json
```

退出码 `0` 表示没有 Schema/跨引用 Error，`1` 表示工程数据无效，`2` 表示输入或运行失败。Warning 仍是可操作工作，优先处理 `high` / `critical`。

汇总 Architecture Gap、Scope Drift、Implementation Drift、Review Gap、Verification Gap、Baseline Drift、Deployment Drift、Transition Risk、Resource Risk、Stage Entry/Exit Gap、Missing Reference Path、Embedded Secret 与 Git Secret Risk。Finding Code 保持英文稳定，说明文字使用中文。未经批准不得自动修复 Finding。

## 安全与 Git

- Embedded 本地凭据优先放在 `*.local.html`，并建议宿主仓库忽略该模式。
- High `GIT_SECRET_RISK` 是分享/提交决策，不是删除或迁移数据的授权。
- Production 优先使用 External File 或 External Store，同时尊重用户当前凭据模式。
- 动态链接经过协议白名单；被拒绝 URL 保持可读但不可点击。

## 停止边界

不要新增主视图、后端、React/Vue、Kanban、人员/预算/日历管理、云协作、自动 Git 提交、自动凭据迁移或通用 Agent 平台。除非用户单独批准，不得执行 V0.2 Schema Migration。
