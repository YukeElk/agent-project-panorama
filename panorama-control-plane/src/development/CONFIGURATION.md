# 接入与配置修订（P6）

配置管理面向一个本机 checkout/dataRoot。过程 Receipt 和规则继续使用 P0/P1 原合同；本页定义新的本机配置、修订、切换计划及工作关联。

## 首次接入与空项目

`preflight --project PATH --data PATH --python PATH [--input bootstrap.json] --output bootstrap.json` 有界读取目录元数据和根 package.json（最多 64 KiB），返回草稿、默认来源、排除项、脚本名称、工具检查及能力缺口。最多观察 1,500 项、目录深度 4；链接不跟随，敏感名称、常见依赖/缓存/构建和工具状态目录跳过。仅探测固定 core 的 Python 版本，不执行项目脚本或 runner，不初始化项目或主机登记。

传入的 bootstrap 不会被替换成默认草稿。预检检查根、可执行文件、cwd，以及与已有采集器一致识别的脚本/配置文件参数是否存在并处于声明范围；无法理解的参数语义仍需开发者核对。预检不证明依赖安装完整、业务检查覆盖充分或运行成功。输出为原始 bootstrap，不包含 Python 字段；init 时仍传 `--python` 或使用固定安装入口。

新项目可以没有 runner。先用 init → begin 登记工程准备；真实建立说明和脚本，按已明确的验收项 review/check → finish。该工作收束后，计划新增 runner 并设置 `refreshModules: true`，由固定 core 的 `init(force, dry_run)` 预览新模块，再以同一支持接口保留式重扫。新业务工作引用返回的 moduleIds。发现的模块知识仍是草稿，不代表目标设计已经实现。

## 计划、切换与回退

change 请求只接受 `reason`（必填），以及可选 `bootstrap`、`python`、`restoreRevisionId`、`refreshModules`、`enabled`。bootstrap 是完整定义；不提供时复用当前定义。restoreRevisionId 与 bootstrap/python 互斥。profile 与 features 变更暂不支持。基础规则包的语义升级不属于此入口；P7 可以通过完整 bootstrap v2 显式登记领域合同和输入 scope，见 [规范与检查范围](STANDARDS.md)。

```json
{"reason":"登记工程准备后的第一个检查器","bootstrap":{"roots":[{"id":"project","kind":"project","description":"实际源码与检查脚本","include":["**"],"exclude":[".agents/**","**/.coverage","**/.pytest_cache/**"]}],"runners":[{"id":"test","command":"C:/runtime/node.exe","args":["src/check.mjs"],"evidenceKinds":["behavior_test"]}]},"refreshModules":true}
```

1. `config-plan --input change.json --output plan.json` 返回 valid、errors、checks、blockers、diff、addedModules、historyImpact。输出文件为完整计划，独占创建。
2. 回读并执行 `config-apply --input plan.json`。应用重新验证可用路径和完整候选，以 expectedRevisionId、expectedConfigurationDigest、expectedCoreManifestDigest 和 planHash 防止应用过期或被改写的计划。
3. `config-status` 查看活动修订、历史链及待恢复操作。相同计划重试复用原操作；已提交后再重试不会把后来的活动版本回退。
4. 回退使用 `{"reason":"恢复原检查定义","restoreRevisionId":"configuration:..."}` 重新计划并应用。回退产生新修订，并保留后来已发现的 core 模块身份；不删除模块或任何证据。

配置切换与开发命令共用短配置租约。所有未收束的本地工作、core 的非终态工作、运行或待恢复检查、Receipt 提交意图均阻止切换。检查开始时持有租约并保存 pending run，执行期间释放短锁；此时持久化运行意图和开放工作阻止配置切换，同时允许查看运行状态。检查结果对账时重新取得配置租约并验证版本。配置维护不替用户结束工作、取消业务或重跑命令。

## 本机格式与读取语义

| 记录 | 格式及位置 | 不变量 |
|---|---|---|
| 活动配置 | `panorama.development-local.v2`，沿用 `process/v1/local.json` | phase=committed；包含 revisionId、sequence≥1、enabled、guidanceOwnership；内容必须与归档的 local 完全一致 |
| 修订 | `panorama.configuration-revision.v1`，`process/v2/configuration/revisions/<sha256(id)>.json` | id、parentId、reason、origin、recordedAt、完整 local、recordHash；独占创建；内容和父引用不可重写 |
| 计划 | `panorama.configuration-plan.v1`，调用者指定位置 | 绑定项目/数据根及预期版本；保存 request、完整 target、指引差异及 planHash；不包含 environmentKey |
| 切换记录 | `panorama.configuration-change.v1`，`process/v2/configuration/operations/<sha256(id)>.json` | 保存原配置、下一配置、计划、core 原树清单与阶段；内部恢复状态可更新 |
| 待恢复指针 | `process/v2/configuration/pending.json` | 指向同一次 operationId/planHash；存在时正常读取拒绝混合状态 |
| 兼容配置下的新工作 | `panorama.development-work.v2`，沿用 `process/v1/development/works/` | configurationRevisionId + 原 configurationDigest；基线、验收和 workItemRef 继续保持 |

v1 活动配置和工作不做静默迁移。首次明确配置升级时归档为 `configuration:legacy-<configurationDigest>`；v1 工作继续按此引用解释，不回写 revisionId。相同配置的 init 重试只核验，不自动升级指引或格式。需要更新项目指引时也应使用明确的配置计划。

configurationDigest 沿用 P2 的 binding/bootstrap/project/packs 语义，因此两个修订可能有同一摘要（例如回退或指引维护）；revisionId 区分其发生次序和启停状态。摘要没有被重新定义来表示新的含义。修订快照和切换记录可能含本机路径及私有环境 HMAC key，只保存在本机；CLI/status/API 不返回该 key。

CLI 和工作台根据工作选择原修订。历史 Assessment 显示为历史结果；复查仍按原规范解释，但只在当前继续授权且原样一致的输入根下读取。根路径、选择器或 observeOnly 变化时，整个旧根保守报告 unknown；已改变或撤销的 runner 不读取旧工具，其当前环境身份 unknown。原 Receipt 字节、基线和规则引用保持不变。P7 保留根读取授权的保守边界，并在其内细化业务检查；合同或 scope 变更也使对应旧 runner 当前观察为 unknown。

## 中断、冲突和退出接入

应用先保存恢复意图，再发布 pending 标记；随后调用固定 core、更新工具拥有的指引、保存不可变修订、更新声明，最后提交活动 local 并清理 pending。`config-recover` 接续原意图，不创造另一个修订。若只保存了意图尚无 pending，可重试原 plan 或 `config-recover --operation ID`；这段间隙新建的工作会阻止恢复发布 pending，避免锁住它们。

目录重扫如在上游交换 `.structure` 时被终止，只允许从本项目中唯一、与保存的原树摘要完全一致的 `.structure-backup-<uuid>` 恢复，再走受支持接口。其他备份、后续发现变化、声明或指引被人工改动时明确拒绝；需要恢复已知原文件并继续操作。P6 不提供盲目覆盖、丢弃待恢复操作、跨 checkout 搬迁或断电事务保证。单记录 1 MiB、历史和工作枚举有界；大型 core 清单超限时拒绝应用。

`detach --input request.json` 要求 reason 和 expectedRevisionId，且工作已收束。它创建 enabled=false 的新修订，移除仍匹配工具拥有摘要的四份指引文件和 AGENTS 中的准确指针块，保留其他用户内容、改写的指引、全部证据与 core 身份。重新启用通过新计划；用户改写文件发生冲突时仍保留并要求明确处理。

## 固定本机安装

先在源码包完成依赖安装和 `pnpm build`，再执行：

```powershell
node scripts/install-local.mjs --destination D:/Tools/Panorama/p6 --python C:/Runtime/Python/python.exe
& D:/Tools/Panorama/p6/panorama.ps1 doctor
& D:/Tools/Panorama/p6/panorama.ps1 process preflight --project D:/Work/new-agent --data D:/PanoramaData/new-agent --output D:/Work/bootstrap.json
& D:/Tools/Panorama/p6/panorama.ps1 workbench start --project D:/Work/new-agent
```

destination 必须不存在且位于源码包外。安装复制已构建程序、合同、固定 core、技能、说明和当前 node_modules（含构建依赖），不运行下载或安装脚本。依赖链接只允许指向快照内，排除源 .bin 和缓存入口。每份文件与链接、Node/Python 的版本、路径、二进制摘要写入 installation.json；installationId 由内容生成。入口启动前校验清单及运行时，再通过参数数组启动 CLI/工作台；缺失/变更时拒绝，不回退到开发目录。

Node/Python 仍是固定路径的外部运行时，安装不封装其完整标准库或业务依赖。doctor 只核验安装文件与这两个二进制；项目工具的当前可用性通过 preflight/config-plan 和每次采集确认。固定目录不要移动，尤其 Windows junction 使用本安装目录；升级需安装到新目录，不覆盖旧版本。没有修改系统 PATH、PowerShell profile 或业务项目状态。若 PowerShell 执行策略不允许运行脚本，使用清单中的 Node 直接运行 `panorama-launch.mjs`，不修改机器策略。
