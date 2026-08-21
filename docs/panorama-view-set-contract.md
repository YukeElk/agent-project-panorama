# Panorama Guided View Set Contract v0.1

状态：Implementation Slice E；Current/Target/Transition/Historical Variant 已实现

对应 Finding：SF-53

## 1. 定位

View Set 是已验证单 scope View IR 的导航组合，不是第四套工程事实。它只拥有章节顺序、稳定章节 ID、
确定性导航标签和默认章节；同一 `profile/scope` 在 v0.1 中至多出现一次。Node、Edge、Group、Layout、Evidence、Information Gap 与工程语义继续由绑定的
View IR / Model IR 独占。

## 2. 绑定与身份

`panorama-view-set.v0.1` 必须精确绑定：

- Model ID、Model Semantic Hash、Project ID、Panorama Data Hash 与 `asOf`；
- 每个章节的 View ID、Profile、单一 Architecture Scope、Semantic Hash 与 Layout Hash；
- 从 0 连续递增的章节顺序，以及集合内唯一的 Chapter ID / View ID；
- `defaultChapterId` 必须属于当前集合。

View Set 必须精确覆盖提供给 Renderer 的 View 集合，最多 24 个章节。任何 Hash、Binding、Order、Chapter ID
或集合覆盖错配都 fail closed。View Set ID、Chapter ID 和 Semantic Hash 均确定性生成。

## 3. Variant 与事实边界

同一 Profile 可以同时存在 `current|target|transition|historical` Variant，例如“模块 · 当前”和“模块 · 目标”。
每个 Variant 仍是独立、可重算的单 scope View IR。View Set 不允许：

- 合并多个 Scope 到一个 View 以制造差异；
- 从标题、目录、自然语言或 UI 状态推断 Conflict/Causality；
- 复制或覆盖 View 拓扑与布局；
- 反向 Apply 到 Panorama Core/Event。

`conflict` 是 Fact Status，不是 Architecture Scope。只有 Model/View 中存在正式 Conflict Fact 时，Renderer
才能展示冲突；V0.1 不生成无事实绑定的“冲突故事”。

## 4. Renderer 与交付

Renderer 0.2 使用 View Set 生成一个可横向滚动、方向键可达的章节栏，并继续使用 View ID 深链。跨章节切换
只在目标 View 含同一 Model Entity 时保持选择。隐藏的 `qa-diagnostics` 只发布当前渲染几何/绑定诊断，不进入
View Set 或工程事实。

Verified Delivery Receipt 记录 View Set ID、Semantic Hash、Default Chapter 和 Chapter Count。机器 Browser
Evidence 与 `visualReview` 分开；自动化不得把后者写成 `accepted`。

## 5. 命令

```powershell
python scripts/compile_panorama_view_set.py project.model-ir.json `
  --view project.current.module.view-ir.json `
  --view project.target.module.view-ir.json `
  --view project.current.dependency.view-ir.json `
  --output project.view-set.json

python scripts/render_panorama_views.py project.model-ir.json `
  --view project.current.module.view-ir.json `
  --view project.target.module.view-ir.json `
  --view project.current.dependency.view-ir.json `
  --view-set project.view-set.json `
  --output project.multi-view.html
```

未显式传入 `--view-set` 时 Renderer 会用同一编译器确定性构建默认 View Set。已存在输出默认停止；覆盖候选
必须显式 `--overwrite`。
