**工作台开发过程集成 · P3—P8**

在项目内先按 [P2 指引](../development/README.md)运行 init 和 begin，再使用 `panorama start --project <项目根>`。CLI 与工作台沿用同一已登记 dataRoot。交接页的“开发过程证据”独立于设计候选；模块详情按登记模块和当前源码路径显示关联工作项，无法映射时显示缺口，不按名称猜测。

“刷新工作项”读取记录；“复查当前证据”重新观察已登记文件、非文件对象身份及执行环境，计算但不保存；“保存过程评估”追加本次过程观察 Receipt 与 Assessment。所有业务检查仍由项目内的 `panorama-process check` 执行。首次读取未登记项目不会 init；页面也不恢复采集进程、修改 core 生命周期或设置正式 Acceptance/Gate。

界面分别显示必备/可选验收项、适用规则及理由、证据来源、检查退出结果、缺失覆盖、过期输入/对象和 unknown 原因。保存的历史 Assessment 始终标注“当前有效性未复查”。复查结果是指定时刻的观察；继续编辑项目后应再次复查。Structure 已 done 并不使旧证据永久有效。

**本地接口**

路径前缀为 `/api/standalone`，沿用现有 Host、loopback、Bearer capability 校验，所有 POST 要求当前本地 Origin。请求体上限 1 MiB，严格 JSON 拒绝重复键、无效 UTF-8、过深/非法结构；每份内嵌过程文档再按 P0 上限和 Schema 校验。浏览器粘贴 JSON 通过 `receiptJson`/`documentJson` 原文发送，服务端负责严格解析。

| 方法与路径 | 输入 | 行为 |
|---|---|---|
| GET `process` | 可选 `nodeId` | 登记状态、工作项列表、模块关联、规则包版本与过程存储 revision |
| GET `process/work` | `id` | 工作项上下文、原始基线、Receipt 来源、历史评估、候选时效与待恢复记录 |
| GET `process/receipt` | `id` | 通过 P1 内容寻址存储校验的原 Receipt |
| GET `process/assessment` | `id` | 通过 P1 内容寻址存储校验的历史 Assessment；不复查、不执行 |
| GET `process/matrix` | `id` | 按工作原配置显示规则适用性与声明覆盖；不表示检查通过 |
| POST `process/preview` | `workItemId`，可选 `receipt` 或 `receiptJson` | 当前观察与确定性计算；可预览外部 Receipt；不保存对象和账本 |
| POST `process/assess` | `workItemId, expectedRevision` | 保存本次观察和评估；不执行检查、不调用 core policy/transition |
| POST `process/import` | `workItemId, expectedRevision`，`receipt` 或 `receiptJson` | 验证绑定和原始基线，再经 P2 提交意图与 P1 CAS 保存外部声明 |
| POST `process/handoff` | `workItemId, expectedWorkspaceRevision` | 导出带过程上下文的 handoff v2，候选可为空 |
| POST `process/compatibility` | `document` 或 `documentJson` | 读取 handoff v1/v2、Standard Pack v0.1/v0.2；纯兼容预览 |

过程 revision 与 workspace revision 分别管理，不可互换。旧 revision 返回 409，刷新后重试；同一 Receipt ID/Hash 重复导入不会重复追加。同一工作项使用与 CLI 相同的短锁，Receipt 的提交意图和不可变 CAS 保持原有恢复机制。GET 和预览不恢复 pending 操作；显式保存可对账已落盘 Receipt 意图，不启动或恢复业务进程。评估默认纳入所有已登记检查和最新过程观察，不隐藏相反证据。CLI 已保存的选择理由在历史详情中保留；页面不提供丢弃失败检查的入口。

`currentEvidenceReady` 仅表示本次过程证据满足、候选未过期且没有待恢复记录；不是 CLI 的 `currentProcessReady`（后者还结合 core policy、工作范围与 outcome）。Assessment 内逐项计算使用同一 P1 核心和同一观察实现。工作台的 `policy.decision` 明确为 unknown，完成迁移仍由 CLI 处理。

P4 复审补充证据范围显示：预览/保存返回 `evidenceSelection`，明确本次采用 `all_registered`、实际 Receipt ID 和范围理由；详情的 `completionSelection` 单独保留 CLI finish 的显式选择，后续页面保存不会抹掉它。页面显示两者区别。比较 CLI 与页面时必须使用同一选择范围：CLI 无 selection 的 assess 与页面全量复查一致；resume 可以沿用 finish 的选择，两者因此可能有不同结论。页面继续保留相反证据，不自动继承或新增舍弃失败的选择。

**共用实现与来源边界**

[read-context.mjs](../development/read-context.mjs)只读固定版本 core 身份/工作项 JSON 并核对定义，不启动 Python；[capture.mjs](../development/capture.mjs)重新读取已登记输入；[assessment.mjs](../development/assessment.mjs)由页面和 CLI 共用。服务的过程依赖树不包含 core 执行适配、check-worker 或 `child_process`。源码图的既有 AST 解析是另一条只读解析流程，不导入项目模块。

只有本地采集账本中的 Receipt ID、Hash、origin 匹配才能提供 P1 的可信来源凭据。HTTP 无法登记任意根、runner、verifiedEvidence 或人工身份。上传 Receipt 中的 `local_capture`、`observed`、`human` 和 extensions 不构成凭据。原始业务日志、本机命令路径、环境变量值和 environmentKey 不返回过程接口；公开 Receipt 保留摘要与哈希引用。本机有写权限的协作者仍是信任边界；这不是签名认证或宿主沙箱。

**交接与旧格式**

新导出格式为 `panorama.handoff.v2`。普通开发交接的 `process` 包含 binding、workItemRef/定义摘要、原始 context/baseline、可选 candidateBinding、rulePacks、rules、criterionIds、expectedReceiptFormat、Receipt 引用及历史 Assessment 引用。预期返回格式继续是冻结的 `panorama.process-receipt.v1`。设计交接未关联工作项时 `process:null`，不补造过程证据。

候选版本、基线或当前源码快照变化会使旧绑定 stale。工作项和 Receipt 保留原绑定；导出过期工作项时不把新候选目标替换进旧交接，必须先处理版本差异。`readHandoff` 保留 v1 读取，v1 或缺少 process 的 v2 显示 legacy/unknown；有效 v2 也只是外部上下文，不能自动成为通过证明。

旧 `import-result` 自由文本入口继续保存外部声明。Standard Pack v0.1 保留原文及版本，列出 strength/when/evidenceRequirements 等缺口；不从 severity 推导强度，不把 manual 变成通过。v0.2 可以验证格式，但此版本没有新增规则包登记或配置迁移功能。旧正式 HTML Verification Receipt/Acceptance/Gate 流程保持独立。

**P8：模块演进与当前解释**

交接页可按登记模块筛选工作；当前源码节点按登记模块与源码路径关联。目标节点只显示工作显式绑定的候选级关联，不推断该节点已经实现或属于某个职责。普通未绑定候选的工作可以正常追踪。`GET process` 增加可选 `candidateId` 过滤，保留 `nodeId` 过滤；两者一起使用时取交集。

`process/work` 增加 `evolution`（`panorama.process-evolution.v1`）：工作开始基线、全部已登记 Receipt 的输入清单差异与检查/评审、最近评估、完成操作引用的评估和完成状态。时间来自原始记录，没有独立完成时间时单列，不生成虚构时间或因果。更早的其他 Assessment 保留在 P1 原存储中，此视图不声称列出完整评估审计链。界面先显示最近 12 条，可继续展开；文件差异每次显示 20 条。

每项记录保留原 ID、摘要与 JSON Pointer，可复制引用、下载原 Receipt 或查看历史评估原文。日志只显示执行 ID、stream、SHA-256、保留/总字节与截断状态，不读取原始日志，也不声明本次已校验日志文件。配置中的命令路径和环境值不进入演进投影。

`process/preview` 和 `process/assess` 增加 `evolution` 当前观察解释。最终结论仍用全部登记检查和当前扫描计算；单份 Receipt 的解释复用同一 P1 评估器、同一次观察及该份对应的来源凭据。外部 Receipt 含多检查时解释覆盖该份全部检查，不拆造新 Receipt。文件变更使用 rootId/path、前后摘要，并区分执行期间与检查之后。输入无法读取、被撤销、范围变更或两次观察不一致时显示 unknown，不推断批量删除。环境聚合摘要只能证明身份变化，不能自动定位唯一变化因素。

设计内容只在候选版本与工作绑定一致时显示，源码快照变化后保留原设计并标 stale。候选改版/缺失时不代入新版内容。设计问题、当前工作区的选择理由和身份映射保留显式引用，但不是正式 Requirement/Decision；自由文本不生成正式需求。源码结构是最近显式刷新/启动时的解析结果，目标和静态导入均不证明业务运行。

工作交接 v2 增加只读演进投影、checkBindings 和 completionSelection；始终标为历史上下文，不把浏览器临时复查结果写成持久通过。没有新增生命周期权威或业务执行入口。

**验证**

`pnpm test:process-ui` 覆盖 P3/P8 HTTP 认证、严格原文解析、CLI/页面逐项一致、来源伪造、输入过期、原始 Receipt 保留、待恢复检查、候选改版与重启、旧格式兼容、单份 Receipt 解释、相反证据保留、外部扩展字段及依赖树执行隔离。实际两项目与空项目、浏览器交互证据见 [P8 报告](../../../outputs/panorama-process-p8-20260915/REPORT.md)。测试使用隔离项目，后续自然任务仍由 P9 验证。
