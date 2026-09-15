**开发过程合同 · 0.1.0 · P0**

本文件冻结 P1/P2 实现所需的数据含义。Schema 和规则文件是本包的结构化输入；本文件规定 JSON Schema 难以表达的跨记录约束。格式合法、引用完整和真实验收分别判断。P0 的测试仅确认本包结构及样本内部一致性，不提供可导入生产结果的运行时验证器。

**身份与数据权威**

`binding.authority` 指定工作项原始系统，`projectId`、`checkoutId` 沿用它的稳定身份；`panoramaProjectId` 可为空。禁止把路径摘要、模块 ID 或候选 ID 当成上游项目 ID。单纯复制路径或同名项目不建立身份相等关系。

`workItemRef.id` 引用上游工作项。`contextSnapshot` 是本次读取到的目标、范围和验收定义及结果说明的冻结快照，不是可独立编辑的任务副本。工作项状态不在 context 中维护。

`workItemRef.definitionDigest` 是规范化定义的摘要，字段严格为 `goal`、`expectedOutcome`、`moduleIds`、`plannedPaths`、`criteria`。它不包含实际变化、执行结果、中断说明或上游状态。此摘要不是上游 JSON 文件摘要；适配器必须明确该区别。

`contextSnapshot.baseline` 单独保存开始开发前的基线：稳定 ID、采集时间、声明范围内的选择器和文件清单。`observed` 需要至少一个输入集；无法取得开始基线时使用 `unknown` 并写明原因，BASE-02 保持证据不足。该基线属于执行上下文，不参与工作定义摘要。它不能用修改完成后的检查输入、Git HEAD 或可选设计候选基线代填；Git HEAD 也不能覆盖开始时已有的未提交修改。`changedPaths` 表示项目逻辑根内、相对于此基线观察到的变化；选择范围不足以比较时必须披露未知。

验收项 `id` 跨执行保持稳定；要求、范围或指定证据发生变化时增加 `version`，并更新定义摘要。数组位置不能作为稳定身份。`candidateBinding` 为可选关联；存在时必须同时包含候选 ID、版本和源码基线 ID，不得只按候选名称重绑。

Receipt 是一次执行产生的证据包，使用独立的 `executionId` 和 `receiptId`。Assessment 是对明确的工作定义、规范版本、证据与当前观察的评估。Receipt 和 Assessment 不替代工作项生命周期。

**规则定义与裁剪**

`strength` 与 `severity` 独立：

| 要求强度 | 含义 |
|---|---|
| `required` | 基础必备，`when` 固定为 `always: true`，适用性固定为 applicable |
| `conditional` | 条件必备；条件为真时必须满足，条件未知或冲突时不能跳过 |
| `recommended` | 推荐；保留结果与缺口，不单独阻止整体满足 |
| `optional` | 可选；保留本次适用性及是否执行，不单独阻止整体满足 |

本包包含 BASE-01～BASE-06，以及 COND-ARTIFACT、COND-EXTERNAL、COND-INTERFACE、COND-REVIEW。`evaluator.id/version` 是受实现识别的声明式检查器标识，不能携带程序、argv 或 URL 回调。未识别 ID 必须作为不支持/证据不足处理。

六个基础 evaluator 的责任依次为：身份目标完整性、范围与基线完整性、规则计算记录完整性、执行/输入/对象绑定、逐项验收引用与匹配、结果及接续信息完整性。基础记录本身不能替代具体的行为或视觉证据；BASE-05 必须展开检查所有必需验收项。

四个条件 evaluator 的责任为：留存对象的实质检查覆盖、声明外部输入的版本绑定、公开行为与相应文档检查、指定来源的人工评审。接口规则要求行为证据和说明回读两部分。默认人工/视觉规则要求 `human` 来源；Codex 文档回读可以满足文档要求，不能冒充该人工评审。

多个 `evidenceRequirements` 按 AND 处理；每项分别满足 `minCount`。同一证据只有确实符合每项条件时才可复用。首版的 `currentRequired` 固定为 true，历史证据仍能浏览，但不能直接证明当前满足。

`subjects` 的解释为：`process` 指该工作项的过程记录；`acceptance_subjects` 指验收项明确引用的对象；其余分别选择留存产物、外部输入、接口行为或评审对象。对象 ID、对象角色和证据类型必须同时匹配，不能以同名文件、相同 checker 名或一段文本替代。

**条件表达式与适用性**

首版表达式只允许 `always`、`fact/equals`、`paths`、`all`、`any`、`not`。事实值比较采用类型严格相等；缺少事实、值为 null 或不可判定时为 unknown，不能按 false 处理。事实应来自明确声明或观察，保存 `basisRefs`；相互矛盾的来源保留 conflict。

组合规则：任何参与的 conflict 均保留 conflict；其余情况下，all 中明确 false 优先于 unknown，any 中明确 true 优先于 unknown；not 保留 unknown，只反转明确布尔结果。没有足够文件清单覆盖时，paths 没匹配不能自动证明不适用。

路径使用正斜杠的根内相对路径。选择模式只定义 `*`（单段）、`**`（多段）、`?`（单字符）；不支持执行式匹配或隐式扩展。模式对保存的逻辑路径按大小写敏感匹配；实际文件身份另按本机规范化规则解析。排除项独立保存，不混入否定式命令。

字段中的路径不允许绝对路径、盘符、反斜杠、点段、控制字符和空段。Schema 无法证明磁盘上的链接安全，真实路径、符号链接和 reparse 检查属于 P1/P2 运行时要求。外部路径仅由本机显式注册的逻辑根解析；Receipt 不能新增读取授权。

**输入快照、产物与有效性**

每个 `inputSet` 保存选择规则、解析到的文件清单和检查前后两个快照。文件记录同时包含 root、路径、角色、状态、字节数和摘要。`missing/unreadable` 的内容摘要及大小必须为空。

这里的 before/after 是执行检查前后，和任务开始基线分别采集。恢复原工作项时保持原开始基线；重选输入范围、补录当前状态不能追认此前未观察到的历史。基线中各输入集的 `snapshot.digest` 与检查快照使用同一摘要算法。

文件清单包括选择集合本身，因此新增、删除、排除规则或角色变化都能改变身份。清单使用 rootId、path 的稳定顺序，重复定位的文件记录非法。P0 的小型样本不等于完整真实项目依赖闭包。

`subjects` 指实际需要验收的对象。`retained_artifact` 与 `fresh_build` 是不同角色；`producedByExecutionId` 可为空，导入旧产物时不虚构产生它的执行。`identityDigest` 为已知对象身份，未知时为空。文件未变化只证明身份，不证明其内容正确。

Assessment 同时保存 `currentInputObservations` 和 `currentSubjectObservations`，支持复查“当时检查什么、现在观察到什么”。可读/已观察时需要摘要，unknown 时摘要为空。缺少应该比较的输入/对象观察，必须保持未知，不能宣布 current。

| Freshness | 含义与限制 |
|---|---|
| `current` | 对已经声明并成功比较的输入与对象仍有效；changedIds、unknowns 均为空 |
| `stale` | 至少一个已声明输入/对象发生变化；changedIds 非空 |
| `unknown` | 缺少足够当前比较依据；unknowns 必须给原因 |

该状态的范围固定为 `declared_inputs_and_subjects`，不能解释成整个项目或生产环境未变化。未声明依赖不会被本合同自动发现。环境信息仅绑定声明的工具、依赖摘要和覆盖边界；未知环境信息如影响某条规则，P1 必须让该条规则的判断保留不足。

执行前后输入变化时，保留真实退出码，再把相关证据标为过期或不确定。输入恢复为同一摘要可以恢复“内容相同”的判断，但不能据此断言两个时点之间从未发生变化。

**执行、证据和评估状态**

machine 检查的 passed 必须携带退出码 0；failed 必须携带非零退出码。errored/interrupted 描述执行或采集故障，不能仅凭退出码推出成功。review/record 没有子进程退出码，必须使用 null。not_run 的起止时间、退出码为空，也不能携带已执行证据。

已经执行的起止时间必须是有效日期且结束时间不早于开始时间。时间含义只在生产者声明的时钟范围内成立，不能自动用于跨主机因果排序。Schema 检查日期格式，运行时还应检查时间关系。

证据要求有来源、checkId、对象、输入集、验收项以及受限摘要，可附内容寻址对象引用。Receipt、check 与 evidence 的引用必须双向一致。原始日志/源码/环境变量值不进入 Receipt；`objectDigest` 仅为受本机对象仓库管理的引用，不是任意文件打开指令。`redaction.producerDeclared` 只是生产者声明，运行时仍需独立扫描内容。

`upstreamCompletionClaim` 保留来自上游或 Agent 的完成说明，不提供额外通过权重。`enforcement` 分别表达指引、接口拒绝、事后扫描、宿主拦截。缺少证据的宿主保证不得从 declared/unknown 提升为 observed。

Assessment 的每条规则必须保存适用性、理由、检查与证据引用、freshness 和 satisfaction。not_applicable 的 satisfaction/freshness 为空，不制造一次“未执行但通过”的检查；unknown 适用性对应 insufficient；conflict 对应 conflict。

| Satisfaction | 含义 |
|---|---|
| `satisfied` | 指定类型、对象、来源和版本范围内的证据足够支持要求 |
| `unsatisfied` | 有效证据明确显示未满足 |
| `insufficient` | 未执行、类型不符、对象不符、过期、未支持的检查器或缺少依据 |
| `conflict` | 来源或规则存在未解决冲突 |

非空文字不证明满足。每个 required 验收项都要单独判断，不能通过把关联规则改为 optional 来取消验收要求。人工接受、正式 Gate 与部署状态不由此表自动写入。

整体聚合顺序为：阻断性的 conflict → 已适用必需项未满足/不足所产生的 blocked → 必需条件未明所产生的 unknown → satisfied。只有最后一种状态 `processReady=true`。推荐/可选规则的失败与冲突保留展示，不单独阻断；显式 required 的验收项仍独立参与聚合。基础必备项不能被标成不适用。

每条规则、每个验收项必须且只能有一条对应版本的评估行；规则统计须覆盖整个已绑定规则包，包括不适用与未知。规则升级重新生成评估，不能覆盖旧结果或修改旧 Receipt。

**哈希、版本与导入约束**

摘要算法固定为 `panorama-sorted-json-v1`：复用本仓库 `src/domain/canonical.mjs` 的 canonicalJson，递归按 JavaScript Object.keys().sort() 排序对象键，保留数组顺序，再用 JSON.stringify 的 UTF-8 字节计算 SHA-256。它不是另一种规范化标准的别名；不做字符串 trim 或 Unicode 规范化。

| 字段 | 参与摘要的内容 |
|---|---|
| `rulePacks[].digest` | 完整规则包 JSON |
| `selectionDigest` | selectors 数组，顺序属于定义 |
| `before/after.digest` | `{selectionDigest, files}` |
| `contextSnapshot.baseline.inputSets[].snapshot.digest` | 同样使用 `{selectionDigest, files}`，文件来自开始基线 |
| `workItemRef.definitionDigest` | 前文列出的五个规范化定义字段 |
| `receiptHash` | 完整 Receipt，删除 receiptHash 自身 |
| `assessmentHash` | 完整 Assessment，删除 assessmentHash 自身 |
| 文件、工具及对象摘要 | 原始字节 |

`extensions` 保留未知字段、参与完整摘要，但不能改变身份、适用性、执行器或状态含义。声明式消费者不得执行其中内容。输入必须是有限数值的合法 JSON；运行时还需拒绝重复键、超出安全整数范围的整数、过大或过深的文档。包限制为 1 MiB/20 层，单输入集最多 50,000 条文件；任一限制不足以完整表达时应报告不完整，不静默截断后给出通过。

同 ID、同 Hash 的 Receipt 可幂等复用；同 ID、不同 Hash 必须保留为冲突或显式新版本，不覆盖。跨项目/checkout 错绑拒绝，跨候选版本只能作为历史关联。旧文本导入不能猜填不存在的摘要和评审。

`purpose=contract_example` 和 contract_fixture/contract_example 生产者表示样本。真实导入路径必须拒绝将其作为实际执行证据。仅修改一个 purpose 字段无法使样本通过本 Schema 的类型一致性约束；生产者身份本身仍需运行时判定，Schema 不是防伪系统。

**分阶段保证**

P0 提供结构和语义合同、预期样本、Schema 校验与样本引用检查。P1 实现真实适用性计算、完整引用/摘要/覆盖校验、当前比较与评估。P2 实现本地采集、上游身份访问和中断恢复。P3 才把结果接入工作台。P0 的通过不能用于宣称后续能力已经运行。
