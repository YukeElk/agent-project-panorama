# Fixed Semantic Evaluation Prompts

## A — Bare Skill

主要 Benchmark 必须逐字使用：

```text
为这个项目初始化 Managed Project Panorama。
先只输出 INIT Preview，不要写入正式 HTML。
```

不得追加 Evidence Hierarchy、Current/Target、Legacy Strategy 或模块拆分提示。

## B — Minimal Intent

```text
为这个项目初始化 Managed Project Panorama。
重点帮助我掌握当前架构、开发状态、风险和下一步。
如果已有旧全景或项目导读，不要覆盖。
先只输出 INIT Preview。
```

## C — Oracle Prompt

Oracle Prompt 只作为人工比较项登记。它可以由人工详细指定 Evidence Hierarchy、Current/Target 和 Legacy Strategy，但完整内容不得放入生产 Skill，也不保存在本仓库的生产可加载路径中。
