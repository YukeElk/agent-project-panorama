**开发过程核心 · P1 · 1.0.0**

本模块实现 [P0 合同](../../contracts/process/CONTRACT.md)的确定性计算、输入复查和不可变存储。它可以被本地工具独立调用，运行时不依赖 Python、模型、Pi、MCP 或工作台服务。开发任务的开始、执行命令采集、Structure 身份读取及恢复指引现由独立的 [P2 开发工具](../development/README.md)实现。

**运行和入口**

```powershell
pnpm install
pnpm test:process
pnpm assess:process --input request.json --output assessment.json
```

评估入口只读取指定 JSON 请求，计算并输出 Assessment；输出文件已存在时拒绝覆盖。退出码 `0` 表示本次过程要求得到支持，`2` 表示有效的 blocked/unknown/conflict 评估，`1` 表示输入或操作错误。输入格式见下方 `assessProcess`；请求内包含完整项目配置、规则包、Receipt 与当前观察，没有业务命令字段。

`contract_example` 请求和结果始终保留合成样本标记。文件入口不会把请求文件里的 `verifiedEvidence` 当作真实模式的可信凭据：真实模式携带该字段时拒绝，需要后续受信采集端接入。没有可信来源时，仍可计算哪些要求缺少证据。

`pnpm test:process-contracts` 保留 P0 的独立 Python Schema 校验，需要其测试依赖；P1 运行及 `test:process` 使用 Node。`test:all` 包含两组过程测试。Schema 使用固定版本 Ajv 的 [Draft 2020-12 实现](https://ajv.js.org/json-schema.html#draft-2020-12-breaking)及[完整日期格式检查](https://ajv.js.org/guide/formats.html)，仅编译随项目提供的 Schema，不加载外部 Schema 或回调。

**公开 API**

通过 [index.mjs](index.mjs) 导入：

| API | 职责 |
|---|---|
| `parseProcessJson` / `processValue` | 有界、无重复键、有限数值和纯数据输入；拒绝 getter、函数、循环引用及非法字节 |
| `validateDocument` | 四类公开格式的 Schema 校验；不改写默认值，不转换类型 |
| `validateConfiguration` / `validateReceipt` | 校验规范摘要、项目/checkout、工作定义、可选候选、基线、输入清单、来源和双向引用 |
| `validateAssessment` | 检查引用、版本、逐项完整性与聚合；外部结论仍是声明，不认证其判断过程 |
| `evaluateApplicability` | 计算 when 表达式，返回适用性、理由和 basisRefs |
| `createInputRegistry` | 注册本机读取范围，采集文件摘要和复查当前对象 |
| `assessProcess` | 计算逐验收项与逐规则结论，生成带完整摘要的 Assessment |
| `openProcessStore` | 在外部数据目录中保存不可变 Receipt、评估及可重放的评估输入 |

示例调用：

```js
import { assessProcess } from './src/process/index.mjs';

const assessment = assessProcess({
  project, packs: [rulePack], receipts: [receipt],
  mode: 'record',
  workItemRef: currentWorkItemRef,
  candidateBinding: currentCandidateBinding, // 普通任务传 null
  assessedAt: new Date().toISOString(),
  facts, paths,
  currentInputObservations,
  currentSubjectObservations,
  environmentObservations,
  verifiedEvidence,
  verifiedObjectDigests,
});
```

`project/packs/receipts` 遵循 P0 Schema。`workItemRef` 必须来自当前工作定义，存在候选时提供其当前完整版本绑定；历史记录可以保存在存储中，但不能在评估时静默改绑到新定义或新候选。

`facts` 是 `{key,value,status,basisRefs}` 数组，字段同项目 `features`。`paths` 是 `{rootId,paths,complete,basisRefs}` 数组，表示已经观察的变化及覆盖范围。未知事实不能当 false；不同事实来源冲突时不自动覆盖。结构上已声明外部输入、必需留存产物或 human-only 验收，会补充相应的 true 事实，与相反声明形成 conflict。该观察只说明记录结构，不证明业务运行已经发生。

两种 current observations 使用 P0 Assessment 的字段。遗漏的输入或对象补为 unknown；不沿用旧 Assessment 的 current 状态。`environmentObservations` 为 `{executionId,identityDigest,state,observedAt}` 数组，state 为 observed/unknown；真实机器检查缺少环境摘要或当前比较时保持不足。非文件对象如过程记录、在线端点，需要对应观察者提供身份；文件读取器不会抄用 Receipt 中的摘要来证明其仍有效。

`verifiedEvidence` 是受信调用方提供的 `{receiptId,receiptHash,evidenceId,actorId,actorKind}` 数组，必须精确绑定原证据及生产者。它是调用边界的可信输入，不能直接从外部 Receipt、用户上传 JSON 或其 extensions 复制。P1 会检查绑定，但尚未接入真实执行采集、人工身份系统或签名认证；P2/后续宿主适配负责提供独立核验结果。Receipt 自报 observed、local_capture、human 或 completed 都不会建立这份信任。合成模式仅供合同演算，无法导入真实模式存储。

`verifiedObjectDigests` 引用已核对内容的证据对象。缺失对象不会参与满足判定；存储入口还会重新读取并核对对象摘要。本阶段对象仓库保存 JSON 制品，不是任意日志或二进制上传接口。

**计算规则**

十个内置 evaluator 均只接受版本 `1`；其他 ID/版本返回不足。证据必须同时匹配类型、来源和具体对象，每个必需对象分别满足数量要求。证据继承其 check 声明的全部输入依赖，不能通过缩小 evidence.inputSetIds 绕开失效。

检查前后内容变化、当前选择规则变化、新增/删除匹配文件及对象身份变化会使相关证据过期。缺少比较或无法读取则保留未知。历史退出码始终保留；只有当前有效的失败能判为 unsatisfied，当前成功和失败并存时为 conflict。新的有效证据可以支持要求，但不能自动覆盖另一份仍有效的相反结论；评估调用方需明确本次使用的 Receipt 集合。

开始基线独立于检查输入。同一工作项的后续 Receipt 必须保持原基线；基线未知、范围无法对照或 changedPaths 不一致时 BASE-02 不满足。BASE-05 展开检查所有必需验收项；可选规则不能取消它们。未知接口影响可以使条件规则 unknown，同时保留其他基础记录已经完整的判断。

聚合优先级为 conflict → blocked → unknown → satisfied。只有必备/已触发条件必备及 required 验收项参与阻断；推荐/可选结果仍展示。Assessment 不写入正式 Acceptance、Gate、部署状态或上游工作项状态。

**文件读取范围**

```js
const registry = await createInputRegistry(project, {
  project: { path: projectRoot, allow: ['src/**', 'package.json'] },
  evaluationData: { path: explicitlyRegisteredRoot, allow: ['golden.json'] },
});
const snapshot = await registry.capture(selectors);
const observations = await registry.observe(receipt, assessedAt);
```

本机根及 allow-list 由调用方显式注册，不从 Receipt 取得绝对路径。P0 项目声明中的根可以暂不映射，读取时会报告 unknown。允许范围支持完整模式相等、明确目录的 `/**` 子树、`**` 全根，以及匹配单个字面路径；无法证明模式在授权范围内时保持不完整。

默认最多 50,000 个匹配文件、100,000 个目录项、32 层目录、单文件 64 MiB、单次 capture 512 MiB；调用方可以收紧这些限额。超过限额、权限不足、秘密文件、目录链接/Windows junction、读取期间变化均报告缺口。扫描使用两次清单比较和文件句柄前后身份检查，路径按逻辑名称大小写敏感匹配；不会执行所读文件。

Schema 文档仍限 1 MiB/20 层；较大的输入清单可能先达到文档限制，此时导入拒绝，不能截断后通过。JSON 评估请求总上限 16 MiB/24 层，每份内嵌公开文档仍单独限额。目录和内容读取保护面向本机协作过程，不宣称提供抵御有权限同时替换整个文件系统的宿主沙箱。

**不可变存储与恢复**

```js
const store = await openProcessStore({ projectRoot, dataRoot, project, packs: [rulePack] });
const head = await store.list();
await store.importReceipt({ receipt, expectedRevision: head.revision, operationId });
// store.assess({ receiptIds, evaluation, expectedRevision, operationId })
// store.getReceipt(id), store.getAssessment(id), store.replayAssessment(id)
```

数据位于 `<dataRoot>/process/v1/`，沿用现有项目真实路径和 dataRoot 越界检查，使用自己的短事务锁。工作台原来的 workspace.json 和整实例锁不参与过程写入。

对象先写入短临时文件并 flush，再以内容摘要发布为不可变文件；索引最后原子替换。依靠本地文件系统的硬链接发布和原子 rename，Windows 当前验证环境为 NTFS。进程中断产生的孤立对象/临时文件不会出现在记录列表，也不能作为已提交记录读取。幂等 operation ID 允许重复调用；同操作不同输入、同 Receipt ID 不同内容、过期 revision 或活动写入竞争得到明确错误。未知锁状态不被强行清除，确认原进程已结束后才能恢复。

每条评估保存当时的完整输入并可重放。重放用于验证历史计算，不会重新读取磁盘来冒充当前评估；更新当前结论需要重新观察并生成新 Assessment。Windows 无目录 fsync，当前验证覆盖进程终止恢复，不声称覆盖所有断电场景。索引和单对象各有 1 MiB 限额、记录和操作最多各 10,000 条；达到容量时拒绝写入，归档/压缩为后续能力。

P1 计算模块不进行 Structure/core 的双存储写入。[P2 开发工具](../development/README.md)负责固定 core、共享 dataRoot 定位、真实采集及跨存储操作恢复；[P3 工作台](../standalone/PROCESS-EVIDENCE.md)通过只读观察和同一评估核心展示逐项证据，不启动业务检查。实际子进程验证与自然业务试点分开统计。P0 manifest 中的 stage/未实现运行器标记保留为当时合同快照，当前核心能力以本文件和 `EVALUATOR_VERSION` 为准。
