# Project Standards Assessment Contract — V0.2

## 1. 边界

Standard Pack 是项目规范的结构化输入，不是项目事实本身。评估器只判断证据是否满足规则，
不会修改源码、配置、部署、Target、Decision、Review、Acceptance 或 Waiver。

## 2. 输入

首轮正式输入为通过 `standard-pack.schema.v0.1.json` 校验的 JSON/YAML。Markdown 可以作为
Source Reference，但必须先规范化为结构化 Pack；Hook 不在无人值守环境中猜测自然语言规则。

每个 Pack 必须包含稳定 ID、版本、来源、适用范围、规则、严重程度、证据要求和声明式
Evaluator。Pack 不得携带 Shell、Python、Node、URL 回调或其他可执行内容。

## 3. 声明式 Evaluator

V0.2 支持：

- `path_exists` / `path_absent`；
- `path_glob_exists`；
- `git_tracked`；
- `panorama_pointer_exists`；
- `panorama_pointer_equals`；
- `manual`，始终输出 `unknown`。

路径必须是项目根内的相对路径，不跟随越界符号链接。评估器不得读取 Secret 或输出项目文件
正文。

## 4. 结果

规则结果为 `pass`、`fail`、`unknown`、`not_applicable`、`waived` 或 `conflict`，并绑定：

- Standard Pack ID、版本和 Hash；
- Git Commit 与 Panorama Data Hash；
- Evidence 摘要；
- 受影响实体；
- 评估时间。

确定性 Evaluator 的失败是 Standard Assessment Finding，但不伪造 Panorama Validator Code。
由 Agent 推理出的未来影响只能保存为 Risk Candidate，必须包含依据、触发条件、影响、缓解方式
和定性 confidence，不输出无证据的精确概率。

## 5. 自动复评

Commit、Pack Hash 或 Source Snapshot 改变时自动复评。实现可以按 changed paths 跳过明确不适用
的规则，但最终 Assessment 必须记录完整规则计数。证据缺失使用 `unknown`，不得默认判定 pass 或
absent。多个规范冲突时保存 `conflict`，不得静默选择优先级。
