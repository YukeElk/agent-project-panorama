本项目采用 Panorama 开发过程规范。Structure 保存项目、模块和工作项身份；本地 `panorama-process` 采集输入、检查及过程评估；工作台集成另行提供。

用 `panorama-process help` 查看参数。固定本机安装提供 `panorama.ps1`：在 PowerShell 中用 `& '<安装目录>/panorama.ps1' doctor` 校验安装，`& '<安装目录>/panorama.ps1' process <命令及参数>` 执行开发命令，`& '<安装目录>/panorama.ps1' workbench start --project <项目根>` 启动工作台。若没有可用 PowerShell 入口，用固定 Node 执行安装目录的 `panorama-launch.mjs` 并传相同参数。无须项目包装脚本或开发工作区路径；不自动修改系统 PATH。Node 遵循包的 engines，固定 Structure core 需要 Python 3.11+。

首次接入先运行 `preflight --project <项目根> --data <项目外的数据目录> --python <解释器> --output <bootstrap.json>`，回读 observations、defaults、errors 与 warnings。预检只遍历有界目录元数据和包描述，探测固定 core 的运行时；不执行项目脚本，不创建过程记录。草稿默认排除常见依赖、缓存和构建目录，需要回读并保留实际交付对象的明确观察范围。检测到 package scripts 只返回名称，runner 必须明确可执行文件、参数和证据类型。输出文件为原始 bootstrap，独占创建。使用固定安装入口时可省略 `--python`，入口提供已校验的运行时。

然后 `init --project <项目根> --data <项目外的数据目录> --python <解释器> --input <bootstrap.json>`。bootstrap 登记项目源文件选择范围、项目外输入及允许执行的 runner。JSON 的来源不会自行提供执行授权。init 不执行业务命令；相同输入重试幂等，升级走配置计划。空项目可以 runners 为空：先 begin 工程准备工作并真实评审新说明/检查器，finish 后再登记首个 runner 和新模块。

配置变更用 `config-plan --input <change.json> --output <plan.json>`，change 包含 `reason`，可提供完整 `bootstrap`、`python` 及 `refreshModules: true`。回读 diff、errors、blockers、addedModules 与 historyImpact 后，使用 `config-apply --input <plan.json>`；应用会重新检查预期版本和发现结果。未收束工作或待恢复检查阻止切换。新增模块由固定 Structure 的保留式重扫发现，原工作/模块身份保留。工程准备后的首个业务工作可以引用新模块 ID；目标设计不作为已实现模块登记。

`config-status` 显示活动修订、历史链与待恢复操作。切换中断后普通读取会明确要求恢复，`config-recover` 继续同一次切换；若只有保存的意图而无待恢复标记，重试原 plan 或显式 `--operation <ID>`。不会自动结束工作或重跑检查。回退的 change 为 `{ "reason": "回退理由", "restoreRevisionId": "configuration:..." }`，先计划再应用，产生新修订并保留全部历史。回退保留已发现的 core 模块身份，不删除后来新增的模块。

旧工作一直绑定原修订和原规范。当前配置撤销或改变某个根的读取范围时，历史复查保守地将整个旧根记为 unknown；旧 runner 改动后当前工具身份也保持 unknown。CLI 和工作台显示原修订及活动修订，保存的历史 Assessment 仍是当时结论。不能直接编辑活动 JSON、重写旧 Receipt 或将历史状态当作当前通过。

停用接入时先收束工作，再 `detach --input <request.json>`；request 包含 `reason` 和 config-status 返回的 `expectedRevisionId`。证据、配置和 core 记录保留，只删除仍与工具拥有内容匹配的指引，用户改写的文件保留。重新启用通过新配置计划；保留的自定义文件冲突需要明确处理，工具不覆盖用户内容。

开发开始前用 `begin --input <work.json>`。工作说明包含 goal、expectedOutcome、plannedPaths、criteria、subjects，可指定 moduleIds、kind、impact、publicBehavior。返回的 workItemId 后续用于 `--work`。publicBehavior 不确定时传 null，不能为让规则跳过而写 false。begin 的基线包含 Git 未提交状态或非 Git 文件的实际内容摘要，不假定 HEAD 是实际起点。

准备请求时可用 `draft --for begin --input <intent.json> --output <work.json>`。intent 保留目标、预期结果、路径、对象种类及验收文字；criterion 可用 runnerId 或 reviewKind 复用已登记类型和来源。草稿列出 defaults 来源、errors、warnings 和 suggestions；不完整字段保持 null 并退出 2。输出文件是原始请求，独占创建、不覆盖；回读补齐后可用 `validate --for begin --input <work.json>` 校验。它们只适用于已接入项目，读取声明与工作记录，不运行 core/runner、不扫描或更新账本；valid 不代表当前证据支持完成。已有完整请求不必经过额外准备命令。

`draft/validate --for check|review|finish` 还需要 `--work <ID>`，check 同时需要 `--runner <ID>`。check/review 仅在唯一兼容验收项时自动选择，多项时先明确 criterionIds。review-intent 的 result、summary、method、findings 必须来自实际回读；finish-intent 可直接含 summary、incomplete、resumeNotes，缺失值不会自动补成已完成。已有外部请求可以直接 validate，正式命令仍会重新校验当前状态。

修改后 `scan --work <ID>` 观察范围；`check --work <ID> --runner <已注册ID>` 收集真实执行。只有被选定的 runner 会运行。runner 使用独立 executable/args，Windows 的 cmd/bat 必须由明确的解释器参数调用，不能拼接未经审查的 shell 字符串。源文件、测试脚本、配置、依赖锁文件和项目外输入必须在声明范围中；扫描只覆盖该范围。

P7 的 `catalog` 提供三类配方和 TAR/GLB 合同。`draft --for begin --recipe document-maintenance|public-behavior-fix|retained-artifact-delivery --input <intent.json>` 可复用建议；机器配方可显式选 `--runner <ID>`。回读实际格式、对象路径和验收文字，配方不生成评审通过。`matrix --work <ID>` 只读重新观察规则强度、适用依据及逐验收项声明覆盖。

细化需在完整 bootstrap 显式设置 `formatVersion: panorama.development-bootstrap.v2`、checkerContracts、inputScopes，各 runner 引用 checkerContractId/scopeId。合同声明属性、未检查内容、依赖、失败协议、资源边界和反例。scope 要覆盖实际导入、配置、工具、输入对象及来源引用；核对闭包后才能填 completeness=declared_complete 及 basis，否则用 incomplete 退回宽输入，未登记服务仍未冻结。

新绑定工作为 v3：checkBindings 使用 panorama.check-bindings.v1，subjects 按 subjectId 指定 format/scopeId，criteria 按 criterionId 指定 claims。draft 可从 subject 的 format/scopeId、criterion 的 claims 组装。源码对象与 runner 引用同一 scope；完整性合同不能支持视觉/语义质量 claim。命令需符合结果协议，退出 0 但缺报告仍是 errored；真实退出码另行保存。完整配置/报告示例在 Panorama 包 `src/development/STANDARDS.md`。

独立项目扫描仍发现范围外及新增文件；只有业务输入、源码对象身份和环境依赖按 scope 绑定。相关源文件、检查器、匹配文件增删、依赖锁和已登记环境变化失效。无关改动保留某项业务证据时也不能越过范围规则。历史合同或 scope 被活动配置改变后旧 runner 当前观察为 unknown，Receipt 与基线不改写。

`review --work <ID> --input <review.json>` 记录 coding agent 已完成的文档或视觉核对；填写 kind、result、summary、method、findings，必要时填 subjectIds、criterionIds 和 limitations。它没有 human 来源权限。`import` 接收符合合同的 Receipt 并保持外部声明，不自动认证生产者。

结束时 `finish --work <ID> --input <outcome.json>`，其中 outcome 是 `{ "summary": "本次结果", "incomplete": [], "resumeNotes": ["接续信息"] }`。命令保存逐项评估，只有当前证据与范围满足要求、没有未完成事项时才推进 core 工作项 done。可以在 assess/finish 输入中指定 selection：明确 receiptIds 和 reason；不会删除历史失败。

中断或新上下文使用 `resume --work <ID>`。不记得 ID 时运行 `resume` 列出工作项。它会读取实际 core 状态、保留初始基线、重新比较输入并对账已结束的采集；仍运行的进程会显示 PID。已中断的命令不会自动重启。原始退出状态丢失时保留 interrupted/not_run，不能补写 passed。

加 `--format summary` 可阅读紧凑 JSON：目标、原基线、验收定义、逐项判断、过期/冲突/未知、实际业务退出码和日志目录保持可见；需要完整数据时使用默认 JSON。nextActions 只是可执行参数建议。`--timings on` 可观察加载、散列、core、执行、评估与存储开销，workerTiming 单列业务命令耗时；父子和并行区间会重叠，不能简单相加。

`.panorama/process.json` 与 `.panorama/rules/` 是可版本控制的声明；本机活动配置、工作和日志沿用 dataRoot/process/v1 位置，配置修订采用 v2 格式，显式启用 P7 检查绑定的工作采用 v3 格式。不可变修订和切换意图在 dataRoot/process/v2/configuration。原始 stdout/stderr 仅存本机运行目录，Receipt 只含摘要和哈希引用。两个本地 CLI 共享数据目录定位器；同一 checkout 多个已登记数据根会拒绝继续。命令结果为 JSON：0 表示操作正常，2 表示有效结果仍需处理，1 表示输入或操作错误。

已有 AGENTS 规则和用户授权持续适用。本指引不要求重复申请权限。过程评估不代替行业验收、正式审批或部署记录，也不声称限制 Codex 原生文件写入。完整输入样例与运行限制见 Panorama 源码包 `src/development/README.md`。
