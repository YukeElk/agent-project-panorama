# Semantic Evaluation Harness

此目录只用于离线评测 V0.1.3 的陌生项目理解能力，不属于生产 Skill Prompt。正常 INIT 不得读取本目录、case invariants、评测 notes 或人工 Oracle。

## 评测目标

- 使用 `prompts.md` 的 Bare Skill Prompt 作为主要 Benchmark；
- 记录 `rubric.yaml` 的五个维度与 Critical Fail；
- 用 `result-template.md` 记录证据、修正量和 Prompt Independence；
- case 目录只保存 invariants，不保存完整标准 Preview；
- 不把 case 名称、文件名、模块名或技术栈写入生产 `SKILL.md`、Semantic Protocol 或 discovery script。

## 推荐人工流程

1. 准备全新、未被当前 Agent 读取过的项目 clone。
2. 只加载发布后的 Skill，不提供 evidence hierarchy 或架构答案。
3. 发送 Bare Skill Prompt。
4. 在任何写入前保存 INIT Preview。
5. 人工依据原始项目证据和隔离 invariants 评分。
6. 记录 external instructions、human corrections、unsupported claims 和 missed critical facts。

自动测试只检查 Harness 结构与生产/评测隔离，不执行具体 case，也不验证 Oracle 答案。
