# Panorama View IR Contract v0.1

状态：Implementation Slice A；当前只实现 `architecture/module`

对应 Finding：SF-34～SF-36、SF-41～SF-43

## 1. 定位

View IR 是一个已验证 Model IR 的有界阅读投影。Architecture、Data Flow、Deployment、Sequence、Lifecycle、
Evolution/Risk 共享同一 Model Identity 和 Evidence，不建立独立事实 Schema。

## 2. Model Binding

每个 View 必须绑定：

- Model ID 和 Model Semantic Hash；
- Project ID、Panorama Data Hash；
- `asOf` 时间；
- View Compiler ID/Version、Type、Profile 和 Scope Filter。

绑定任一不匹配时 View 无效，不允许把旧 View 与新 Model 混用。

## 3. Node、Edge 与 Group

- Node 只通过 `entityRef.type + entityRef.id` 引用 Model Entity；
- Edge 只通过 `relationRef.type + relationRef.id` 引用 Model Relation；
- Edge 端点必须与 Relation 端点投影出的 Node 精确一致；
- Group 只引用 Model Layer，不能用视觉容器制造 ownership/security/deployment boundary；
- Node/Edge Evidence Pin ID 必须属于其绑定的 Entity/Relation；
- Search、Focus、Trace、Reach 和 Deep Link 只能遍历这些现有引用。

Slice A 的 module profile 每次只接受 `current|target|historical` 中一个 Scope。只有端点 Node 都在 View 中且
Relation Scope 匹配时才投影 Edge；不得为保持画面连通而增加代理边。

## 4. Semantic 与 Layout 分离

View Semantic Hash 覆盖删除 `layout` 和 `integrity` 后的完整 View。Layout Hash 只覆盖 `layout`：

- 节点移动、自动/手工布局切换只改变 Layout Hash；
- Label、Entity/Relation Binding、Filter、Evidence 或 Scope 变化必须改变 Semantic Hash；
- Theme、Zoom、Selection、Filter UI 展开状态不进入 View IR。

## 5. Sequence 预留门禁

`sequence` Profile 虽已列入 Schema，但当前没有 Compiler。未来只有 Event/trace、作者 Dynamic View、可验证
控制流或用户批准的 Declared/Target 流程可以形成 Message Order。Module Connection、import graph、文件相邻
和自然语言不能单独形成 `observed_sequence`。

## 6. 当前命令

```powershell
python scripts/compile_panorama_view_ir.py project.model-ir.json `
  --profile module --scope current `
  --output .panorama-work/views/project.current.module.view-ir.json
```

输出已存在时默认停止。当前命令只生成 JSON Candidate，不生成 HTML、不替换 last-good，也不表示人工视觉通过。
