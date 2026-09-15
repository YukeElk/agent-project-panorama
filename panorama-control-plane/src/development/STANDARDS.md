**P7：规范、配方与按检查绑定的证据**

`catalog` 返回三类配方和两份领域检查器合同，无需接入项目。`matrix --work <ID>` 读取原配置、重新观察输入，返回规则强度、适用理由、依据及逐验收项的声明覆盖。矩阵不执行检查、不保存 Receipt/Assessment、不推进工作。工作台同一入口是 `GET /api/standalone/process/matrix?id=<ID>`，沿用能力认证。

强度保留 required（必备）、conditional（条件必备）、recommended（推荐）、optional（可选）。固定规则包仍是六条基础 required 和四条 conditional；本轮不新增行业规则或认证。未知事实保持 unknown，配方不能豁免实际触发的规则。

```powershell
panorama-process catalog
panorama-process draft --for begin --recipe document-maintenance --input intent.json --output work.json
panorama-process draft --for begin --recipe public-behavior-fix --runner interface --input intent.json --output work.json
panorama-process draft --for begin --recipe retained-artifact-delivery --runner retained --input intent.json --output work.json
panorama-process matrix --work work:returned-id
```

配方只提供可编辑的对象/验收建议。目标、预期结果、实际对象、影响范围仍需回读；多属性检查器不自动全选 claims。公开行为配方默认 impact=interface，文档配方保留 publicBehavior=null。公开接口文档的实际变化仍触发接口规则。review 的 result/method/findings 及 human 来源不由配方生成或改写。

**版本与历史兼容**

| 层 | P7 新格式 | 兼容规则 |
|---|---|---|
| bootstrap | `panorama.development-bootstrap.v2` | 无 formatVersion 的旧请求归一化结果保持 |
| 工作 | `panorama.development-work.v3` | v1/v2 工作继续用原配置、原范围 |
| 工作附加绑定 | `panorama.check-bindings.v1` | subjects/criteria 按 ID 对应 P0 定义 |
| Receipt 扩展 | `panorama.check-binding-receipt.v1` | 键为 `panorama.development.check-binding` |
| 检查器合同 | `panorama.checker-contract.v1` | 项目命令配置引用 checkerContractId |

P0 Receipt v1 从一开始就支持各 check/evidence 的 inputSetIds。本轮使用已有选择性引用语义；新增领域属性、源码身份和依赖闭包语义用 v3 工作及有版本的扩展表达。P0/P1 核心和原规则包保持。扩展绑定 workItemRef 与 checkBindings 摘要，采集、导入及 CLI/API 评估都会检查，旧证据不会被重新解释成满足新领域合同。外部声明依然不获得本地可信凭据。

旧项目收束活动工作后，用 P6 `config-plan` / `config-apply` 显式启用新 bootstrap。恢复旧定义仍产生新修订。历史工作按原版本读取；活动配置改变 runner、合同内容或 scope 定义时，旧 runner 当前环境观察为 unknown。验收映射或依赖闭包变更需要明确的新工作/配置，不改写旧基线、Receipt。

**配置合同与依赖范围**

v2 在原有字段之上增加完整 `checkerContracts` 和 `inputScopes`，每个 runner 必须指定 scopeId、checkerContractId。预检仍默认兼容配置，不自动声称依赖闭包完整。

```json
{
  "id":"handler",
  "completeness":"declared_complete",
  "basis":"已核对检查器递归导入和数据依赖：只读 handler 子目录及固定工具，不访问外部服务。",
  "selectors":[
    {"rootId":"project","include":["src/handler/**"],"exclude":[],"role":"source"},
    {"rootId":"project","include":["tools/handler-check.mjs"],"exclude":[],"role":"tool"}
  ]
}
```

上例是 inputScopes 中的对象。selectors 使用已有 P0 格式，继承根的所有排除项，不能扩大根读取授权。declared_complete 必须有 basis，且具备合同要求的依赖角色；这仍是项目对依赖闭包的明确声明，不是对任意脚本读取行为的沙箱证明。无法核对时用 incomplete，机器输入与非文件源码身份退回全部声明输入，并给出原因。未声明服务、运行时安装库仍未被冻结，宽范围回退也不能证明它们。

从 [目录](../../contracts/checkers/catalog.v1.json) 或 `catalog` 复制完整对象到 checkerContracts。自定义合同需填写 id/version/title、subjectKinds/formats、claims（id/property/evidenceKinds）、notChecked、dependencyRoles/dependencies、failureProtocol、limits 和 examples.positive/negative。代码校验器是 [check-contracts.mjs](check-contracts.mjs)。固定 TAR/GLB 协议只接受目录原合同，不能借同一报告格式改成更强的属性。自定义合同使用 panorama.checker-result.v1 协议。

合同面向 source_behavior、retained_artifact、verification_input；新合同暂不支持 fresh_build 的两阶段报告。旧工作原 fresh_build 路径保留，不用留存对象配方代替构建证明。TAR 验证清单、成员和字节完整性；GLB 验证原始有限坐标、实际导入后的网格/尺寸及关联来源存在性。两者都不证明视觉质量、业务语义或生产验收；GLB 不打开关联 .blend，不支持稀疏/压缩/外部 POSITION 编码。

**工作绑定**

v2 配置的新工作必须含 checkBindings，subjects/criteria 与原工作定义一一对应：

```json
{
  "formatVersion":"panorama.check-bindings.v1",
  "subjects":[{"subjectId":"handler","format":"handler.behavior.v1","scopeId":"handler"}],
  "criteria":[{"criterionId":"AC-01","claims":["handler.expected-input-output"]}]
}
```

把它放入完整 begin 请求的 checkBindings。使用 draft 时，可在 subject 写 format/scopeId，在 criterion 写 claims，草稿会移入版本化绑定。缺少格式保持 null，不自动全选属性。评审项可为空 claims；机器检查必须至少有一个合同支持的 claim，且格式、对象 kind、证据类型匹配。源码行为对象与 runner 必须引用同一个 scope；实际留存文件须在范围内并以 `{subject:ID}` 参数绑定。

**输入、对象、环境与独立项目扫描**

原 roots 一直宽范围扫描，用于原基线、changedPaths、公开行为条件、范围外变更及过程记录。各 scope 的项目选择器也在 begin 时进入原始基线；所有选择器保留在每份 Receipt。机器 check/evidence 仅引用自己的 scope 输入，incomplete 回退宽输入。

非文件源码对象身份由工作绑定及其 scope 内容计算，当前身份独立重算；文件对象身份仍是实际字节散列。环境绑定所选 scope 的 dependency/tool/config 文件、Node/runner 二进制、可识别脚本/配置参数、最小环境及 envKeys 的 HMAC，并加入保守的全项目依赖清单。执行前后和当前观察采用相同算法。

全项目依赖清单每次从宽扫描重建，包含所有 dependency/tool/config 角色文件，以及已声明目录下的 package.json、package-lock.json、npm-shrinkwrap.json、pnpm-lock.yaml、yarn.lock、bun.lock/bun.lockb、pyproject.toml、poetry.lock、uv.lock、Pipfile/.lock、requirements*.txt、Cargo.toml/lock、go.mod/sum、composer.json/lock、Gemfile/.lock、*.csproj、packages.lock.json。增删改都会失效；其他模块的锁文件也可能触发保守重跑。其余相关配置、导入文件及模型 manifest 引用须显式纳入 scope，不能通过排除真实依赖降低重跑次数。

使用能覆盖未来新增依赖的选择器，不只列当前存在的文件。P7 每次采集所有登记 scope，最多 32 个 scope、100 个总输入集合；不在工作中途改选择器。相同文件在同一集合中角色冲突会产生缺口。明确无关文件变化可以保留某项业务证据；范围外变更仍使 CLI policy=deny，不能据此认为工作可完成。API preview 只评估证据，CLI finish 还核对 Structure policy 和未完成事项。

**结果报告、退出与资源**

自定义 runner 参数必须含 `{executionId}`，可用 `{subjectDigest:ID}` 获得对象摘要以关联报告。摘要不替代实际断言。成功 stdout 为一个 JSON 行（其他日志不要另起 JSON 对象行）：

```json
{"formatVersion":"panorama.checker-result.v1","executionId":"execution:actual-id","contractId":"handler.check","contractVersion":"1","status":"passed","subjects":[{"id":"handler","identityDigest":"64位实际摘要","format":"handler.behavior.v1"}],"claims":["handler.expected-input-output"]}
```

所选对象、claims 必须全覆盖；执行 ID、版本、格式和身份核对一致。错对象、错执行、缺少/重复报告、未登记属性、日志截断不能支持通过。失败应返回非零，不要在 catch/finally 中无条件输出成功。TAR 协议要求唯一的 status/original_files/original_bytes 成功报告，结合真实 argv/输入/退出码绑定；GLB 协议要求唯一 RETAINED_SUPPORT_RESULT、对象散列、原始坐标计数、非空网格及关联来源摘要。它们能识别遗漏/吞掉失败报告，不能认证恶意本地 writer。

真实进程状态保留在本机 result.json、CLI recovery 和 Receipt 扩展 rawExecution。退出 0 但报告无效时机器证据为 errored、验收不足、CLI 退出 2；非零/超时/中断不会因出现成功报告而提升。单对象最多 64 MiB，超时不超过合同上限；stdout/stderr 各自受 maxLogBytes 限制（至多 1 MiB）。超对象限额不执行，截断报告不支持成功。不提供系统级 CPU/RAM/网络隔离或完整 Python/Blender 插件安装树冻结。
