# Continuous Observation Contract — V0.2

## 1. 目标

Continuous Observation 让正式 Panorama Current 持续追踪项目事实，避免人工维护遗忘造成
Current 与实际进展割裂。自动化只观察、记录和披露；不修改业务项目，不替用户作决策。

## 2. 权威分类

- `observed`：Git、文件元数据、测试结果或运行观察直接证明的事实。
- `declared`：配置、Manifest 或规范明确声明，但尚未被当前运行证实的信息。
- `inferred`：从多项事实推理出的 Current 披露，必须保存依据和 confidence。
- `unknown`：证据不足。
- `conflict`：可靠来源互相矛盾；必须保留冲突，不得静默选择。

`inferred` 可以进入 Current 披露，但不得被描述为 approved/accepted。只有 Validator 实际返回
的代码才是 Formal Finding；Agent 推理仍只能产生 Risk Candidate 或 Attention Candidate。

## 3. 持久策略授权

首次启用时创建 `continuous-observation-policy.v0.2` 并计算 canonical Policy SHA-256。后续
Observation Batch 必须绑定该 Hash。策略变更要求重新显式启用，不得由 Hook 自行扩大权限。

策略只允许写入：

- Panorama HTML 的 `project-panorama-data`；
- `.panorama-work/` 中的事件、锁、状态与 Last Known Good 制品；
- 用户单独授权时的 Panorama-owned Git State Ref。

策略禁止：

- 修改业务源码、配置、部署、凭据或业务 Git 分支；
- 自动决定 Intent、Target、Decision、Review、Acceptance、Waiver 或 Guidance；
- 自动迁移凭据、执行任意项目命令或默认联网；
- 修改 Schema、DOM、CSS 或 Renderer JavaScript。

## 4. 受保护语义

自动 Apply 必须拒绝以下路径及其子路径：

- `/schemaVersion`、`/intent`、`/requirements`、`/decisions`、`/reviews`、`/guidance`；
- `/architecture/targetVersionId`；
- Module 的 `targetDesign`、`decisionIds`、`reviewIds` 与 `requirementIds`；
- Acceptance 的治理 `status`；
- Resource 的 `access` 与 Credential；
- `/observationPolicy`；
- `/meta/templateVersion` 与 `/meta/createdAt`。

Target 与治理事实保持不变。实际实现偏离它们时，更新 Current 并披露 Drift。

## 5. 触发与补偿

支持 `post-commit`、`post-merge`、`post-checkout` 与 `post-rewrite`。Hook 只写入事件并唤醒
Worker，不解析项目语义。Worker 获取独占锁、合并事件并以最新 HEAD 为准。

每次 Skill 启动或显式 `sync` 都比较 `sourceBinding.gitHead` 与当前 HEAD；不同即执行补偿同步。
因此 Hook 丢失或长时间未运行不会形成永久缺口。

## 6. 更新事务

Worker 必须按以下顺序执行：

1. 验证策略 Hash、Panorama Revision/Data Hash 与 Source Binding；
2. 获取只读 Source Snapshot 和已授权 Operational Evidence；
3. 运行 Standard Assessment；
4. 生成 fact-only Operations 和逐路径 Provenance；
5. 拒绝任何受保护路径；
6. 运行 Schema、跨引用和工程规则校验；
7. 确认 Presentation Hash 不变；
8. 创建备份并原子替换；
9. 提交前复核 Revision、Data Hash 与 Git HEAD。

任何失败都保留 Last Known Good Panorama，并将事件标记为 failed。不得静默修复、降低规则
严重程度或写入部分结果。

## 7. 自动测试执行

Hook 不自动执行通用项目代码。只有策略记录了用户明确授权的准确测试 Manifest/Command 时，
才能沿用 Operational Evidence Contract 的受限执行流程；仍必须披露网络和文件系统隔离状态，
不得返回 stdout/stderr 或声称未读取 Secret。

## 8. Git 规则

实时 Panorama 默认使用 `*.local.html` 并保持业务工作区干净。自动化不得提交业务分支。
Panorama-owned State Ref 是可选能力，默认关闭；启用时只能包含 Panorama 制品和审计状态。
