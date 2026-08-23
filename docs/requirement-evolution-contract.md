# Requirement Evolution & Decision Projection Contract

状态：V0.8 P0 Renderer 已实现；P1 Core Schema 待实现

版本：0.1-draft
日期：2026-08-23

## 1. 目的

把需求从“控制台中的静态快照”调整为可追踪的演进对象，同时保持三个主视图的职责边界：

- **控制台**：当前阶段的决策驾驶舱，只展示阶段退出、需求健康度、迁移、风险和决策摘要；
- **演进**：需求、阶段、模块、架构版本、迁移、发布和证据的完整记录；
- **系统画布**：需求在架构层级、功能模块和模块内部逻辑中的实现位置。

Trace、JSON Pointer、Trace Gap Candidate 和 Formal Finding 是技术诊断，不是默认的人类主扫描层。

## 2. P0 Renderer 投影

P0 不修改 Core Schema。只允许通过正式 ID 字段生成以下投影：

| 投影 | 允许的数据链 | 显示要求 |
| --- | --- | --- |
| Requirement → WorkItem | `workItems[].requirementIds` | 可显示为实际开发工作 |
| Requirement → Stage | Requirement → WorkItem → `stageId`；或 Requirement → target Release → `stageId` | 必须标记“推导” |
| Requirement → Module | `requirements[].moduleIds` | 直接关系 |
| Requirement → Layer | Requirement → Module → `layerId/targetLayerId` | 必须标记“推导” |
| Requirement → Transition | Requirement → WorkItem → `transitions[].workItemIds` | 必须标记“推导” |
| Requirement → Architecture Version | 目标 Release 的 `architectureVersionId`；或 Transition 的 `fromVersionId/toVersionId` | 必须说明来源；不等于完整架构快照 |
| Requirement → Release | `requirements[].targetReleaseIds` | 直接关系 |
| Requirement → Acceptance | `requirements[].acceptanceCriteriaIds` 或现有 Scope Route | 直接或正式 Trace |
| Requirement → Deployment | Release / Architecture / Module Deployment 的严格匹配 | 只读状态投影 |

禁止用名称相似、画布邻近、DOM 顺序、模块同层或自由文本猜测关系。链无法闭合时显示 Information Gap。

## 3. P1 Requirement Realization 数据合同

未来 Schema 应增加独立、稳定身份的 `requirementRealizations[]`，而不是继续向 Requirement 堆叠状态字段。建议最小结构：

```json
{
  "id": "RR-REQ-004-ARCH-V04",
  "requirementId": "REQ-004",
  "stageId": "STG-MVP",
  "architectureVersionId": "ARCH-V04",
  "transitionIds": ["TRANS-WORKSPACE-METADATA"],
  "workItemIds": ["WI-WORKSPACE-METADATA"],
  "layerIds": ["LAYER-DATA"],
  "moduleIds": ["MOD-WORKSPACE", "MOD-METADATA"],
  "moduleLogicRefs": [],
  "releaseIds": ["REL-V10"],
  "acceptanceCriteriaIds": ["ACC-WS-METADATA"],
  "decisionIds": ["DEC-WORKSPACE-METADATA"],
  "riskIds": ["RISK-WORKSPACE-CONC"],
  "state": "in_progress",
  "validFrom": "2026-08-08T10:00:00+08:00",
  "validTo": null,
  "referenceIds": [],
  "extensions": {}
}
```

约束：

1. `requirementId`、`stageId` 和 `architectureVersionId` 是演进主坐标；
2. 关联模块内部逻辑时只能引用正式 Target/Historical Logic ID，不得引用 Current Observation Node；
3. 每次状态变化记录 Engineering Event，不覆写历史 realization；
4. `state` 不能替代 Acceptance/Gate/Deployment 的真实字段；
5. 缺少证据时保持 `unknown/partial`，不得从工作项完成自动声明需求已验证或已交付。

## 4. P1 可重建架构版本快照

当前 `architecture.versions[].delta` 只能说明变化，不能重建某个版本的完整架构。未来每个正式 Architecture Version
必须满足以下二选一：

- 绑定一个不可变、内容寻址的完整 Architecture Snapshot；或
- 绑定一个已验证基线和一条无缺口、顺序确定的 Delta Chain，可确定性物化完整 Snapshot。

Snapshot 至少覆盖 Layer、Module、Connection、Module Logic Design、Resource Usage 以及各对象的语义 Hash。
Renderer 在此能力完成前只能显示“版本与迁移链”，不得称为“该版本完整架构”。

## 5. 人类界面要求

- 需求主详情先回答：需求是什么、在哪个阶段、落在哪些层级/模块、由哪些工作推进、改变哪个架构版本、如何验收和部署；
- 原始 Trace 与字段路径默认折叠为技术诊断；
- Gate / Acceptance / Deployment 使用“验证 / 验收 / 部署”的中文交付证据链；
- 控制台不得再次展示完整需求库存；演进页不得删除完整需求记录；
- 任一推导关系都必须有明确“推导”标识和可审计 ID 链。

## 6. 门禁

- P0：Renderer 单元测试、JavaScript 语法、模板与派生参考 HTML 同步、全量回归；
- P1 Schema：先关闭 Schema Finding，再补 Schema、Validator、Migration、Renderer 和负向测试；
- 完整架构快照：必须包含确定性物化与 Hash 校验负向测试；
- 浏览器机器证据和人工视觉接受继续绑定精确 Artifact Hash，不能由源码测试替代。
