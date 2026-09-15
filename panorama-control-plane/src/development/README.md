**项目内开发工具 · P2—P7**

这套入口在工作台关闭时独立工作：Structure 管理项目/模块/工作项，本地采集器记录真实检查，[P1 核心](../process/README.md)计算当前证据是否支持要求。它不依赖 Pi、MCP、模型 API 或后台监督 agent。

从 Panorama 源码包运行 `pnpm process help`，或 `node src/development/cli.mjs help`。包提供 `panorama-process` bin；只有该包已经安装到命令搜索路径时才能直接使用这个命令。下例省略 Node/源码路径前缀。Node 版本遵循 [package.json](../../package.json)；固定 core 使用 Python 3.11+。Python 绝对路径在首次 init 由 `--python` 或 `PANORAMA_PYTHON` 提供，后续保存在项目外的本机配置中。

```powershell
panorama-process init --project D:/projects/demo --data D:/panorama-data/demo --python C:/Python312/python.exe --input bootstrap.json
panorama-process begin --project D:/projects/demo --input work.json
# 编辑项目文件，后续使用返回的 workItemId
panorama-process scan --project D:/projects/demo --work work:returned-id
panorama-process check --project D:/projects/demo --work work:returned-id --runner unit
panorama-process finish --project D:/projects/demo --work work:returned-id --input outcome.json
panorama-process resume --project D:/projects/demo --work work:returned-id
```

命令输出 JSON。退出 0 表示相应操作正常；2 表示有效结果仍需处理，例如真实检查失败、缺少证据、范围外修改或只读旧任务；1 表示输入、绑定、依赖或存储操作错误。`check` 输出同时保留实际 execution 结果及业务子进程 exitCode，CLI 自己的退出码不能替代它。P4 增加本机 `runDirectory`，可直接读取其中的 `stdout.log`、`stderr.log` 和 `result.json`，无需为了找失败输出再次执行检查；未启动或被提前中断时日志文件可能尚未产生。此路径仅由本地 CLI 返回，不写入公开 Receipt 或工作台接口。进程退出 0 也不代表整个任务符合要求，需查看 `assess` / `finish`。

P7 的规则矩阵、三类任务配方、领域检查器合同及显式版本启用方式见 [规范与检查范围](STANDARDS.md)。下文原格式示例继续兼容。

**准备项目配置**

`bootstrap.json` 是本地注册请求，含明确的文件选择范围与可执行命令。它应来自用户授权的开发任务或已审查项目配置。读取配置不执行业务命令；只有 `check` 执行被选定的 runner。

```json
{
  "profile": "solo-light",
  "roots": [
    {"id":"project","kind":"project","description":"源码、测试、配置与依赖锁","include":["src/**","test/**","package.json","pnpm-lock.yaml"],"exclude":[]},
    {"id":"golden","kind":"external","description":"外部评估样本","path":"D:/evaluation-data","include":["golden.json"],"role":"fixture"}
  ],
  "publicPaths": ["src/api/**","docs/api/**"],
  "runners": [
    {"id":"unit","command":"C:/node/node.exe","args":["--test","test/unit.test.mjs"],"evidenceKinds":["behavior_test"],"timeoutMs":120000,"maxLogBytes":65536,"envKeys":[]}
  ]
}
```

默认只有项目根，include 为 `**`；实际项目应收窄到源码、检查脚本、配置、依赖锁文件及需要的输入。默认排除 `.git`、`.structure`、`.panorama`、常见虚拟环境/依赖目录和 `.env` 文件；其他秘密文件、链接或扫描限额会产生缺口，不能静默跳过后认为范围完整。项目根固定逻辑 ID `project`，项目外根须明确提供绝对路径。`external` 是运行输入；`artifact` 表示产物目录，可位于项目中或项目外。选择范围同时构成读取 allow-list。

注册 runner 使用可执行文件的绝对路径与独立参数数组，`shell:false`，不通过系统默认 shell 拼接字符串。Windows `.cmd/.bat` 不直接执行，需要明确注册解释器及其参数。PATH 等最小运行变量和声明的 envKeys 才会传给命令；其值不写入 Receipt，环境摘要使用本机密钥的 HMAC。工具可执行文件、可识别的脚本/配置参数文件及声明的依赖摘要参与检查前后和当前比较。脚本参数文件必须可从注册根读取。内联脚本包含在 runner 定义摘要中。

初始化会读取/建立 core 身份，生成 `.panorama/process.json`、固定基础规则包、`.panorama/DEVELOPMENT.md`，在现有 AGENTS 后附加一个有标记的短指引，并把独立技能放到 `.agents/skills/panorama-development-process/`。相同配置重复 init 不重复增加指引或重置状态。冲突、旧身份未迁移、不同 core profile、长路径或缺少 Python 会明确失败，不自动覆盖已有治理配置。

技能源码在 [skills/panorama-development-process](../../skills/panorama-development-process/SKILL.md)。项目技能位置依据当前 [Codex 技能文档](https://learn.chatgpt.com/docs/build-skills)；AGENTS 的发现还受根目录、子目录覆盖与大小限制影响，见 [AGENTS 文档](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。P2 验证了生成文件和指引内容，不把它描述成一次无上下文模型的自然使用试验。非 Git 项目建议从项目根启动 Codex；已有 AGENTS.override.md 时应核对实际生效指引。本轮不安装或修改全局技能。

**定义本次任务**

```json
{
  "goal":"修复输入处理",
  "expectedOutcome":"明确输入下的行为符合约定",
  "plannedPaths":["src/handler.mjs","test/unit.test.mjs"],
  "kind":"bugfix",
  "impact":"internal",
  "publicBehavior":false,
  "criteria":[
    {"id":"AC-01","version":1,"requirement":"约定输入得到正确结果","required":true,"subjectIds":["handler"],"allowedEvidenceKinds":["behavior_test"],"acceptedActorKinds":["system"]}
  ],
  "subjects":[{"id":"handler","kind":"source_behavior","locator":null}]
}
```

`moduleIds` 省略时使用已登记模块；应按实际影响收窄。`publicBehavior:null` 保留未知。扫描命中 publicPaths，或声明 interface/boundary/architecture 影响，会触发公开行为要求；通常还需对说明文档作对应回读。P2 自动接入固定基础包，其他规则包仍可用 P1 API 计算，配置版本通过 P6 的 config-plan/config-apply 管理，不能改写历史配置制造通过。

`begin --work <已有ID>` 只关联与目标、范围、模块和验收文字一致的 core 工作项。原始基线在 core 创建之前持久化；Git 未提交文件和非 Git 项目均按实际文件摘要处理。开始时无法完整观察的基线保持 unknown，后续不能换成较晚的快照。工作定义变化或 candidate 版本不匹配会拒绝旧绑定，需要明确的新任务/版本。

`check --input scope.json` 可显式提供 subjectIds 和 criterionIds；省略时选择与 runner 的 evidenceKinds 相符的验收项。机器 runner 只能产生 behavior_test、interface_test 或 artifact_integrity，不能赋予自己人工评审类型。旧 v1/v2 工作的所有输入集合均参与检查；P7 显式启用的 v3 工作使用按检查绑定，见 [规范与范围](STANDARDS.md)。

**P5：准备请求与阅读结果**

已有完整请求可继续直接调用原命令。需要复用元数据或检查草稿时，使用 `draft` / `validate`；它们不是每次开发都必须增加的步骤。项目已经接入、`unit` runner 已登记时，可以把下面的 `intent.json` 转成完整 begin 请求：

```json
{
  "goal":"修复内部输入处理",
  "expectedOutcome":"约定输入得到正确结果",
  "plannedPaths":["src/handler.mjs","test/unit.test.mjs"],
  "kind":"bugfix",
  "publicBehavior":false,
  "subjects":[{"kind":"source_behavior"}],
  "criteria":[{"requirement":"约定输入得到正确结果","runnerId":"unit"}]
}
```

```powershell
panorama-process draft --for begin --project D:/projects/demo --input intent.json --output work.json --format summary
# 回读请求、默认值来源和问题；有待补字段时编辑 work.json。
panorama-process validate --for begin --project D:/projects/demo --input work.json
panorama-process begin --project D:/projects/demo --input work.json --format summary
```

草稿复用模块、局部 ID、初始 version、required=true 及所选 runner 的证据类型；单对象可复用 subjectIds。显式值保留，模块仍需按实际影响收窄。文档或视觉评审可用 `reviewKind` 替代 `runnerId`，映射到 coding_agent 来源。目标、要求、对象种类、路径和检查覆盖仍由 Agent 明确；缺少公开影响声明时保持 null，不从“文档任务”推断 false。

`draft` 返回 request、defaults 来源、errors、warnings 和 suggestions。`--output` 独占创建**原始请求 JSON**，不覆盖已有文件；即使草稿不完整也可落盘供编辑。不要把整个输出信封交给 begin。缺字段为 null 且退出 2，完整草稿退出 0。结构或关联校验失败的 `validate` 也退出 2；JSON 解析、CLI 参数或读取失败为 1。

后续请求支持：

```powershell
panorama-process draft --for check --project D:/projects/demo --work work:returned-id --runner unit --output scope.json
panorama-process validate --for check --project D:/projects/demo --work work:returned-id --runner unit --input scope.json
panorama-process draft --for review --project D:/projects/demo --work work:returned-id --input review-intent.json --output review.json
panorama-process draft --for finish --project D:/projects/demo --work work:returned-id --input finish-intent.json --output outcome.json
```

check/review 只有一个兼容验收项时自动绑定；有多项时列候选并保留 null，需要明确 criterionIds，subjectIds 可从已选项导出。旧式 check 省略范围仍保留原行为，validate 会提示隐式多项选择。review 草稿必须填入实际 kind、result、summary、method、findings，不生成通过结论。finish-intent 可直接提供 summary、incomplete、resumeNotes，草稿将其放入 outcome；缺少的未完成事项不会自动变成空列表。

这两个准备入口只读取已登记配置和工作记录；除明确指定的请求输出文件，不建立存储、运行 core/runner、恢复进程、扫描输入、评估证据或创建完成记录。`valid:true` 只说明当前请求结构与声明关联通过校验；之后的 begin/check/review/finish 仍执行原有校验、观察和锁定，不代表当前证据有效或工作可完成。首次接入及配置版本维护见 [P6 配置管理](CONFIGURATION.md)。

所有操作可加 `--format summary`，默认仍是完整 JSON。摘要保留原工作 ID、目标、基线身份、验收定义及逐项判断、规则缺口、证据选择、失败/过期/未知、业务退出码与本机日志目录。`operationExitCode` 是 CLI 结果；`execution.exitCode` 是业务退出码。没有评估时 processEvidenceReady 为 null。nextActions 给出可执行参数以定位原工作或校验草稿；这些建议不会自动运行。完整记录仍可通过默认 JSON 和本机数据读取。

`--timings on` 开启本机分段计时，默认 off。父进程包括 location、load、input/subject/environment_observation、tool_hash、core、executor_wait、assessment、storage 和 lock_wait；check 的 execution/recovery 可含 workerTiming，business 才是采集子进程直接执行命令的区间。inclusiveMs 包含子区间，exclusiveMs 扣除已计子区间，但并行区间仍可能重叠；worker 时间已在父进程等待中，不能重复相加。assessment 可能含 P1 自身存储；startupUnattributedMs 包含启用计时前的运行时和模块加载，JSON 序列化与进程退出在测量终点之后。计时是定位开销的墙钟观测，不是 CPU 分析、完整环境冻结或效率保证，且不进入 P0 Receipt 合同。

**留存产物、构建及评审**

留存产物使用 `kind:"retained_artifact"` 与实际 locator；对应 root 必须参加输入采集。runner 参数中通过 `{subject:model}` 绑定文件，例如 `["test/check-glb.mjs","{subject:model}"]`。这确保命令针对登记对象调用；它仍需要项目检查器真正检验 GLB 内容。摘要相同不能代替语义检查。执行期间对象改变或执行后对象损坏时旧证明不可用。

新构建使用 `kind:"fresh_build"`，注册一个 `artifact` 根并设置 `observeOnly:true`，同时从源码输入范围排除输出目录。新输出开始前必须不存在，结束后得到其内容身份和 producedByExecutionId。已经存在的对象不能重贴 fresh 标签，改用留存检查或新的输出路径。生成对象本身不能证明其业务/视觉验收合格，应配置相应检查器和验收项。动态端点无独立版本观察时保持未知。

文档回读可以不执行业务检查：实际阅读后调用 `review --input review.json`：

```json
{
  "kind":"document_review","result":"passed",
  "subjectIds":["handler"],"criterionIds":["AC-DOC"],
  "summary":"说明与变更一致",
  "method":"对照接口行为和文档中的输入输出约定",
  "findings":["明确说明新增输入限制"],
  "limitations":[]
}
```

review 的验收项需要允许 document_review 和 coding_agent。结果是 coding agent 在本地通道提交的声明，不认证评审内容本身，也不获得 human 权限。要求人工作视觉判断时，agent 的回读或外部 JSON 的 human 自报都无法替代它；P2 没有接入人工身份认证源。没有执行回读就不提交 passed。

**结束与恢复**

`outcome.json`：

```json
{"outcome":{"summary":"完成的改动与检查结果","incomplete":[],"resumeNotes":["后续需要了解的说明"]}}
```

`assess` 重新观察当前输入、对象及 runner 环境，保存逐规则/逐验收项 Assessment。`finish` 还要求原始范围完整、core policy 允许、没有活动检查和未完成事项，才推进 core verifying/done，并再次观察。过程合格、core done、正式 Acceptance/Gate 和部署是不同的结果；P2 不写后两者。

默认使用全部本地/导入检查及最新过程扫描。有相反的当前有效证据会返回 conflict；重跑不会删除失败。确有取舍时在 assess/finish 输入中增加 `selection:{"receiptIds":["receipt:..."],"reason":"明确取舍依据"}`，保存选择及理由；resume 会沿用 finish 的选择并再次核对时效。审计者仍可读取所有历史 Receipt。

`resume` 不执行 runner。它读取实际工作项、基线、当前差异、未完成信息、评估缺口与正在运行的 PID。CLI 被终止时，独立采集进程通过 IPC 断开终止其业务进程树并保存结果；采集进程也被终止、退出码无法取得时，恢复记录 interrupted/not_run。若仍能观察到运行的进程，则报告 running 并阻止重复检查，不假称已恢复执行。

core 创建、Receipt 提交、core 完成均有先保存的操作意图。恢复按固定 workItemId、定义摘要和上游实际状态对账；只完成一侧不会补出成功。core 已 done 但输入随后变化时，过程仍可 blocked；固定 core 的 done 是终态，后续修改应建立后续工作项，保留原历史。

**存储与边界**

[P6 接入与配置修订](CONFIGURATION.md)增加 preflight、config-plan/apply/status/recover、detach，以及固定本机安装和 doctor。新活动配置与工作使用明确的 v2 格式，修订归档和切换意图位于 process/v2/configuration，旧 v1 配置及工作继续可读。工作绑定原配置；当前撤销的输入范围和工具保守显示 unknown。空项目可先完成工程准备，收束后通过固定 core 的保留式重扫登记首个 runner/模块。配置变更必须在工作收束边界执行，不能直接改活动 JSON。

CLI 与 `panorama start` 共用 [local-project.mjs](../local-project.mjs)。首次启动在本机 Panorama 目录记录 checkout 与 dataRoot，后续可从子目录定位；显式 --data 也必须一致。首次并发注册会保留同一目的地，已登记的其他数据根和历史默认根发生冲突时失败。该机制只覆盖已登记目录及历史默认目录，不能枚举整个机器上未登记的数据拷贝；移动数据需要明确迁移，此版不自动移动。`PANORAMA_STATE_HOME` 可设置宿主登记目录，测试使用独立目录。

运行数据保存在 dataRoot/process/v1，P1 对象和索引继续负责不可变 Receipt/Assessment。local.json 保存本机配置，development/works 保存操作状态与原始基线，development/runs 保存独立采集请求、状态、实际结果和 stdout/stderr，development/pending 保留提交意图。过程写入使用工作和配置短事务锁，业务命令运行时释放锁；已持久化的 pending run 与开放工作阻止配置切换，同时允许查看运行状态。同一 checkout 一次只允许一项登记检查，其他工作项的待恢复执行也会阻止启动。原 workspace.json 和 `.panorama.lock` 保持独立。

日志每流默认最多 64 KiB，可配置 0–1 MiB；超出后继续排空管道但只保存截断说明。Receipt 仅保存固定摘要和日志哈希/大小，绝不复制原始日志或环境变量值。原始日志只在本机，分享时应单独审查。它们不是 P1 对象仓库中的正式 evidence object。单份公开文档和本机记录均有 1 MiB 上限；超限会拒绝而非截断后通过。

Windows core 项目根按 staging 目录、最长模块哈希文件名及原子写临时后缀一起计算保守长度上限，避免上游目录交换导致半初始化；需要较短项目位置的提示会在执行 core init 前返回，并列出上限。该限制不是对所有启用长路径系统的能力判断。当前验证覆盖本地 NTFS 进程终止，不宣称提供任意断电、网络盘或敌对文件系统替换的事务保证。

本机有写权限的协作者仍属于信任边界。环境仅覆盖登记的工具、脚本/配置、输入和变量；未登记的网络服务、安装依赖内容、动态加载及瞬时修改无法保证冻结。采集器证明自己直接启动的命令及退出结果，不推断不透明脚本内部未上报的子检查结果；runner 应传播失败且有明确覆盖合同。[P3 工作台](../standalone/PROCESS-EVIDENCE.md)已共用只读上下文、输入观察与评估，不导入执行器。[P4 试点](../../../outputs/panorama-process-p4-20260914/REPORT.md)已完成两项目共六项连续任务和无原对话上下文接续，接入、留存对象、证据选择与成本记录见[使用手册](../../docs/process-pilot-playbook.md)。

固定 [core manifest](../../vendor/structure-core/vendor-manifest.json) 含 23 个文件，源自提交 `9cab2b1345cca68177708fc30f2cac6bbe5792b5`；[MIT 许可证](../../vendor/structure-core/LICENSE) 原样保留。适配器启动时验证清单、固定摘要及可执行树，不修改上游源码；只开放 init、work、context、policy、export 与身份读取，不伪造 human-confirmed Gate。可用 `node scripts/vendor-structure.mjs <已解压原始归档目录>` 复核全部 116 个上游文件并重建相同 vendor 内容。

验证入口：`pnpm test:development`、`pnpm test:process`、`pnpm test:process-contracts`、`pnpm test:standalone`、`pnpm build`。CLI 集成测试用 `PANORAMA_TEST_PYTHON` 指定 Python；测试中的业务数据是刻意构造的隔离夹具，命令执行、退出与终止都是真实发生。
