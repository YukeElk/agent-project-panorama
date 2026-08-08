# Agent Project Panorama V0.1

`Agent Project Panorama` 是一个以架构为主轴、Local-first、单项目单 HTML
的 AI/Vibe Coding 工程认知控制面。它用于恢复和维持对需求、架构、模块、
演进、验证、部署与资源的理解，不是任务看板、文档编辑器或多人项目管理平台。

## V0.1 内容

- 无构建、无 CDN、无远程字体、无运行时网络请求的 Single HTML Renderer；
- `CONTROL / SYSTEM / EVOLUTION` 三主视图；
- Current / Target / Transition 与 Native SVG Connection；
- Module Drawer、Architecture Source、Stage × Module；
- Release / Deployment / Module artifactVersion / Resource / Access；
- Embedded / External File / External Store / None 四种凭据模式；
- JSON Schema、全局 ID、跨实体引用及工程规则校验；
- Revision/Hash 保护、备份、原子写入及最小 JSON Patch Apply；
- Reference Project 和自动测试。

页面只读。核心实体编辑、评审批准和文件写回都必须通过外部更新流程完成。

## 环境

- Python 3.10+
- `jsonschema`（Schema 校验）
- `pytest`（运行测试）

```powershell
python -m pip install jsonschema pytest
```

Renderer 本身不需要 Python；生成后的 HTML 可直接在桌面浏览器打开。

## 生成 Reference HTML

在本目录执行：

```powershell
python scripts/init_panorama.py `
  --template templates/panorama.html `
  --data examples/reference-project.v0.1.json `
  --output examples/reference-project.html
```

然后直接打开：

```text
examples/reference-project.html
```

## 读取与校验

```powershell
python scripts/read_panorama.py examples/reference-project.html
python scripts/read_panorama.py examples/reference-project.html --json
python scripts/validate_panorama.py examples/reference-project.html
python scripts/validate_panorama.py examples/reference-project.v0.1.json
```

Validator 输出格式：

```text
ERROR ...
WARNING ...
INFO ...
```

退出码：

- `0`：无 Schema/Cross-reference Error（允许 Warning）；
- `1`：Schema、跨引用或数据一致性 Error；
- `2`：文件、JSON、Marker 或运行依赖错误。

## Patch Apply

更新包使用 Base Revision 和逻辑数据 Hash 防止覆盖新状态。支持 `add`、
`replace`、`remove`，路径采用 JSON Pointer。

```json
{
  "baseRevision": 3,
  "baseDataHash": "<scripts.panorama_io.compute_data_hash 的结果>",
  "operations": [
    {
      "op": "replace",
      "path": "/intent/currentFocus",
      "value": "新的已评审主线"
    }
  ],
  "changeRecords": [],
  "reviewDraft": {},
  "updateBatchDraft": {},
  "guidanceDraft": {}
}
```

执行：

```powershell
python scripts/apply_patch.py `
  examples/reference-project.html `
  path/to/approved-update-package.json
```

Apply 顺序：

1. 获取同目录独占更新锁并读取 HTML 数据锚点；
2. 校验 Base Revision / Data Hash；
3. 创建同目录时间戳备份；
4. 在内存应用 Patch 并更新 Revision / 时间；
5. 追加可选 Change / Review / Update Batch 并更新 Guidance；
6. 执行 Schema、跨引用和规则校验；
7. 生成暂存 HTML 并确认 Presentation Hash 不变；
8. 提交前重新比较目标文件，拒绝覆盖并发新状态；
9. 原子替换原 HTML 并校验最终字节。

任何失败都保留原 HTML；若已经进入 Apply 阶段，备份仍保留用于审计和恢复。

## 自动测试

```powershell
python -m pytest -q
```

覆盖：

- Schema Draft 2020-12 与 Reference Data；
- 全局 ID、Module/Connection/Deployment/Decision/Guidance 引用破坏；
- Stable Marker、中文与 `</script>` 安全序列化；
- Presentation Hash 保持；
- Reference Current / Target / Transition 语义；
- 四种凭据显示数据；
- JSON Patch add / replace / remove；
- Revision/Hash 冲突、备份、失败不覆盖、Schema 回滚；
- 并发写入窗口保护、无效 UTF-8 与畸形 Schema 输入；
- 已实现 Module Review、Acceptance Evidence、Release Module 部署缺失；
- Change / Review / Update Batch / Guidance 追加。

## Presentation Compatibility Contract

普通数据更新必须满足：

1. 保持 `project-panorama-data` ID；
2. 保持 `PANORAMA_DATA_START / END` Marker；
3. 只替换 `<script type="application/json">` 的 JSON payload；
4. 不改 CSS、Renderer JavaScript 或 DOM；
5. 保留 `extensions` 和未知扩展字段；
6. Apply 前后 `compute_presentation_hash` 一致。

## 凭据说明

Embedded 凭据默认遮蔽，但遮蔽不是加密，查看 HTML 源码仍可读取值。
External File 只显示路径和 Key，External Store 只显示 Provider、Reference 和
Key。Validator 会对 Embedded Secret、Production Embedded Secret 和失效路径告警，
但不会擅自迁移或删除用户选择的凭据模式。

## Schema Findings

实现期间发现但未修改的 Schema 问题记录在
[`docs/schema-findings.md`](docs/schema-findings.md)。本轮以
`schema/panorama.schema.v0.1.json` 为冻结基线。

## 本轮边界

`SKILL.md` 严格在 Renderer、Scripts、测试、Reference HTML、Patch Apply 和 README
完成后编写，支持 INIT / INSPECT / PROPOSE UPDATE / APPLY UPDATE / VALIDATE，并要求
Preview 先于 Apply。页面内编辑、拖拽架构、直接批准 Review、自动 Git 扫描、云端协作、
Task Kanban、企业级 Secret 加密和完整 Impact Graph 均不在 V0.1 第一轮范围内。
