# Codex Execution Brief — Agent Project Panorama v0.1

## 1. 任务目标

基于以下两个基线文件，完成 `agent-project-panorama` V0.1 的第一轮工程实现：

- `agent-project-panorama-v0.1-design-spec.md`
- `panorama.schema.v0.1.json`
- `reference-project.v0.1.json`

第一轮目标不是做一个完整项目管理系统，而是交付：

1. 可直接本地打开的 Single-HTML Panorama；
2. 三个主视图：CONTROL / SYSTEM / EVOLUTION；
3. 可重复使用的数据读写与校验脚本；
4. Reference Project 的可运行 HTML；
5. 确保普通数据更新不破坏 UI。

---

## 2. 强制原则

### 2.1 不得改变产品定位

它是：

> 以架构为主轴的 AI/Vibe Coding 项目工程认知控制面。

它不是：

- Jira；
- Task Kanban；
- 文档编辑器；
- 多人项目管理平台；
- 代码浏览器。

### 2.2 不得先重写 Schema

将 `panorama.schema.v0.1.json` 视为当前实现基线。

发现 Schema 问题时：

1. 在 `docs/schema-findings.md` 中记录；
2. 说明问题、影响和建议；
3. 除非无法继续实现，否则不要直接修改 Schema；
4. 必须修改时，单独提交最小变更并更新样例和测试。

### 2.3 Presentation 与 Data 分离

模板必须包含：

```html
<!-- PANORAMA_DATA_START -->
<script id="project-panorama-data" type="application/json">
{}
</script>
<!-- PANORAMA_DATA_END -->
```

普通更新脚本只能修改 Marker 内数据。

### 2.4 技术栈

必须使用：

- HTML5；
- CSS；
- Vanilla JavaScript；
- Native SVG；
- Python 标准库为主。

允许为 JSON Schema 校验使用 `jsonschema`。

禁止：

- React；
- Vue；
- Svelte；
- 远程 CDN；
- 远程字体；
- 构建工具依赖；
- 运行时网络请求；
- 遥测。

### 2.5 View-first

HTML 不提供核心数据编辑能力。

允许：

- 切换视图；
- 筛选；
- 搜索；
- Drawer；
- 展开；
- Reveal / Copy Credential；
- 打开文档路径。

禁止：

- 拖拽修改架构；
- 页面内修改 Requirement；
- 页面内直接批准 Review；
- 页面内写回 HTML。

---

## 3. 目标目录

```text
agent-project-panorama/
├── SKILL.md
├── README.md
├── docs/
│   └── schema-findings.md
├── schema/
│   └── panorama.schema.v0.1.json
├── templates/
│   └── panorama.html
├── scripts/
│   ├── panorama_io.py
│   ├── init_panorama.py
│   ├── read_panorama.py
│   ├── validate_panorama.py
│   └── apply_patch.py
├── tests/
│   ├── test_schema.py
│   ├── test_cross_refs.py
│   ├── test_apply_preserves_ui.py
│   └── test_reference_project.py
└── examples/
    ├── reference-project.v0.1.json
    └── reference-project.html
```

---

## 4. Milestone 1 — 数据读写与校验

先实现，不先做 UI。

### 4.1 `panorama_io.py`

提供：

```python
extract_data(html_path) -> dict
replace_data(html_path, data, output_path=None) -> Path
compute_data_hash(data) -> str
compute_presentation_hash(html_text) -> str
create_backup(html_path) -> Path
atomic_write(path, text) -> None
```

要求：

- 严格定位 Marker；
- HTML 内 JSON 转义正确；
- 失败时不破坏原文件；
- UTF-8；
- 可处理中文；
- `replace_data` 前后 Presentation Hash 必须一致。

### 4.2 `init_panorama.py`

参数建议：

```text
--template
--data
--output
```

将 JSON 嵌入模板，生成 Single HTML。

### 4.3 `read_panorama.py`

输出：

- Schema Version；
- Revision；
- Project；
- Current Stage；
- Current / Target Architecture；
- Current Release；
- Latest Update Batch；
- 是否存在 Embedded Secret；
- JSON 数据，可选 `--json`。

### 4.4 `validate_panorama.py`

必须实现两层校验：

#### JSON Schema

使用 `schema/panorama.schema.v0.1.json`。

#### Cross-reference / Rule Validation

至少检查：

- 全局 ID 唯一；
- 引用存在；
- Current Stage 存在；
- 只有一个 Stage 为 `current`；
- Current / Target Architecture 存在；
- Module Layer 存在；
- Connection From / To 存在；
- Deployment 的 Release / Architecture / Resource / Module 存在；
- Decision Option 引用有效；
- Guidance Option 引用有效；
- Reference relatedEntities 有效。

至少输出以下 Warning：

- Architecture Gap；
- Scope Drift；
- Review Gap；
- Verification Gap；
- Baseline Drift；
- Deployment Drift；
- Transition Risk；
- Resource Risk；
- Stage Exit Gap；
- Missing Reference Path；
- Embedded Secret in Production；
- Embedded Secret Present。

输出格式：

```text
ERROR ...
WARNING ...
INFO ...
```

退出码：

- 0：无 Error；
- 1：Schema / Cross-reference Error；
- 2：文件读取或 Marker 错误。

Warning 不阻止生成。

---

## 5. Milestone 2 — HTML Renderer

## 5.1 全局布局

三个一级导航：

```text
CONTROL | SYSTEM | EVOLUTION
```

桌面优先，建议目标：

```text
1440 × 900
```

同时保证 1024px 宽度可用。

不要用大段 Hero 文案。

## 5.2 CONTROL

必须实现：

### Header

- Project Name；
- Current Stage；
- Current Focus；
- Current Architecture；
- Target Architecture；
- Current Release；
- Dev / Staging / Prod 状态；
- Updated At；
- Sensitive Data 标记。

### Latest Reviewed Update

从 `meta.latestUpdateBatchId` 获取。

显示：

- `summaryItems`；
- Stage Before / After；
- Change Level。

### Attention

显示最新 Update Batch 的 `attentionItems`，最多默认 5 条，可展开全部。

### Next Focus

显示：

- 2–3 个方案；
- 推荐标记；
- Why Now；
- Recommendation Reason；
- Benefits；
- Risks；
- Impact；
- Prerequisites；
- Expected Outcome。

默认折叠长内容，避免首屏失焦。

### Requirement Snapshot

按：

- Type；
- Definition Status；
- Fulfillment Status；
- Target Release；
- Module Coverage；

筛选和查看。

## 5.3 SYSTEM

二级视图：

```text
Logical Architecture | Runtime & Resources | Architecture Source
```

### Logical Architecture

视图切换：

```text
CURRENT | TARGET | TRANSITION
```

要求：

- 按 Layer 排列 Module；
- 使用 SVG 显示 Connection；
- CURRENT 过滤 current / both；
- TARGET 过滤 target / both；
- TRANSITION 高亮 Transition State；
- 连接显示方向；
- Hover 高亮上下游；
- 点击 Module 打开 Drawer；
- 支持按 Layer、Source、Status 筛选。

不要求可拖拽布局。

### Module Card

只显示：

- Name；
- Source Tag；
- Design；
- Implementation；
- Verification；
- Runtime；
- Pending Review / Risk / Gap 标记。

### Module Drawer

分组展示：

- Why；
- Current / Target Design；
- Responsibilities / Non-responsibilities；
- Requirements；
- Source / Baseline；
- Decisions；
- Risks；
- Acceptance；
- Gates；
- Work Items；
- References；
- Code Path；
- Deployment / Resources。

### Runtime & Resources

按 Deployment 展示：

```text
Release
  → Deployment
  → Module artifactVersion
  → Resource
  → Access
```

要求：

- Dev / Test / Staging / Production 切换；
- Module artifactVersion；
- Endpoint；
- Database / External API；
- Credential Mode；
- Embedded 字段默认遮蔽；
- Reveal 按钮；
- Copy 按钮；
- External File 显示 Path + Keys；
- External Store 显示 Provider + Reference；
- 页面顶部提示“遮蔽不等于加密”。

### Architecture Source

按 Baseline 展示：

- Retained；
- Modified；
- Added；
- Removed；
- Divergence；
- Upgrade Strategy；
- Module Change Areas；
- Upgrade Impact。

## 5.4 EVOLUTION

必须实现：

### Architecture Timeline

按 `createdAt` 展示：

- Version；
- Status；
- Summary；
- Rationale；
- Added / Modified / Removed；
- Review Status。

### Stage Roadmap

按 order 展示：

- Completed；
- Current；
- Blocked；
- Not Started；
- Skipped。

点击 Stage 显示：

- Purpose；
- Current Focus；
- Entry / Exit Acceptance；
- Module 状态表；
- Blockers。

### Release / Deployment History

显示：

- Release；
- Architecture Version；
- Stage；
- Status；
- Deployment；
- Module artifactVersion；
- Resource。

---

## 6. 信息密度要求

必须遵守：

- 首屏不出现大段设计正文；
- 卡片正文不超过 3–5 行；
- Attention 默认最多 5 条；
- Latest Update 默认 3–6 条；
- Next Focus 默认只展开推荐方案；
- 详细信息进入 Drawer；
- 文档正文不复制进 HTML；
- 不新增无决策价值的 KPI；
- 不显示 Alignment 百分比。

---

## 7. Milestone 3 — Patch Apply

实现 `apply_patch.py`。

输入建议：

```json
{
  "baseRevision": 3,
  "baseDataHash": "...",
  "operations": [],
  "changeRecords": [],
  "reviewDraft": {},
  "updateBatchDraft": {},
  "guidanceDraft": {}
}
```

要求：

1. 读取 HTML；
2. 校验 Revision / Hash；
3. 创建 Backup；
4. 应用 Patch；
5. Revision + 1；
6. 更新 `meta.updatedAt`；
7. 追加 Change / Review / Update Batch；
8. 更新 Guidance；
9. Schema Validation；
10. Cross-reference Validation；
11. Presentation Hash 不变；
12. 原子写入；
13. 失败时保留原文件。

可以自行实现最小 JSON Patch，不强制完整 RFC 6902；必须支持：

- add；
- replace；
- remove。

---

## 8. Milestone 4 — Skill

在 Renderer 和 Scripts 通过后再完成 `SKILL.md`。

Skill 必须支持：

```text
INIT
INSPECT
PROPOSE UPDATE
APPLY UPDATE
VALIDATE
```

关键约束：

- Update 必须先 Preview；
- 未经用户评审不 Apply；
- 不确定信息不脑补；
- 普通更新不修改 Presentation；
- 语义总结，不输出文件操作流水；
- Next Focus 生成多方案并解释；
- 重大 Change 必须分级；
- 保留 Extensions；
- 不擅自迁移凭据模式。

---

## 9. 测试要求

## 9.1 Schema

- Schema 本身可被 Draft 2020-12 Validator 接受；
- Reference JSON 通过 Schema。

## 9.2 Cross Reference

- 破坏 Module ID 后测试失败；
- 破坏 Connection Endpoint 后测试失败；
- 破坏 Deployment Resource 后测试失败；
- Decision Option 错误时测试失败。

## 9.3 Presentation Preservation

测试：

1. 计算原 Template Presentation Hash；
2. 更新 JSON；
3. 再计算；
4. 必须相同。

## 9.4 Credential

- Embedded 默认遮蔽；
- Reveal 后可见；
- Copy 可用；
- External File 不显示实际 Secret；
- Production Embedded 产生 Warning。

## 9.5 Renderer Smoke Test

对 Reference Project：

- 三个主视图可切换；
- Current / Target / Transition 正确；
- Module Drawer 可打开；
- Latest Update 正确；
- Next Focus 推荐正确；
- Dev Deployment 和资源正确；
- Embedded Admin Account 默认遮蔽；
- Target Metadata Store 可见但 Current 不显示；
- Direct Vector Access 在 Current 可见、Target 不显示、Transition 标记 deprecating。

---

## 10. 首轮交付顺序

严格按以下顺序执行：

```text
1. 创建目录
2. 复制 Schema / Reference Data
3. 实现 IO
4. 实现 Validator
5. 完成测试
6. 实现 HTML Template
7. 生成 Reference HTML
8. 进行 Smoke Test
9. 实现 Patch Apply
10. 编写 README
11. 编写 SKILL.md
12. 输出验收报告
```

---

## 11. 最终输出

请在完成后提供：

1. 文件树；
2. 运行命令；
3. 测试结果；
4. Reference HTML 路径；
5. 已实现的 Drift Rules；
6. 未实现项；
7. Schema Findings；
8. UI 截图或可视检查结论；
9. 是否满足 V0.1 Acceptance；
10. 下一轮建议。

不要自行扩大范围。
