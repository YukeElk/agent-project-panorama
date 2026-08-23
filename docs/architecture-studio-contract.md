# Architecture Studio V0.3/V0.8 合同

## 定位

Architecture Studio 是 `SYSTEM → 逻辑架构` 内的设计模式，不是第四个主视图，也不是通用项目
管理平台。它解决需求拆解之后的模块、层级、职责、状态所有权和数据流设计，并支持多个候选方案
的并行比较。正式 Panorama、业务代码和项目治理状态在画布编辑期间保持不变。

## 两种运行方式

直接打开 Single HTML 时进入离线模式：草稿位于内存或浏览器本地存储，可导入/导出
`panorama-architecture-session.v0.1`。离线页面的 CSP 保持 `connect-src 'none'`，正式校验、
Agent 评审、Proposal、批准和 Apply 不可用，也不得伪装成功。

完整模式由单用户、短寿命、本机 Bridge 提供：

```powershell
python scripts/studio_bridge.py path/to/project-panorama.html `
  --project-root path/to/project `
  --codex-cli "C:\path\to\codex.exe" `
  --open
```

`--codex-cli` 是可选参数；不需要 Agent 评审时省略。Bridge 只探测该参数指定的普通可执行文件，
无效路径会拒绝启动，绝不回退环境变量、PATH 或用户目录。Bridge 绑定 `127.0.0.1` 的随机端口并
输出一次性启动 URL。必须使用该 URL 打开页面；不含项目数据的 `/studio/` Launcher 用 capability
header 获取受保护的 `/studio/document`，fragment 随即从地址栏移除且只保存在页面内存。Bridge
关闭后能力失效。Bridge 是本机开发工具，不是生产 Web 服务，也不提供远程或多人协作。

## 用户流程

1. 在 Panorama 唯一画布中从阅读模式切换到编辑模式；不挂载第二张 Studio 画布。
2. 从正式 Target（不存在时使用 Current）创建隔离会话。
3. 新建、克隆、重命名或归档候选；编辑模块和数据流；最多比较三个候选。
4. 浏览器基础检查持续运行。它只产生 `CLIENT_*` 检查，不是 Formal Finding。
5. Bridge 保存会话到 `.panorama-work/studio/sessions/`。保存使用 Session Revision CAS；冲突时
   保留本地副本，不静默覆盖服务器版本。
6. 正式校验把当前候选保守地投影为完整 Panorama 数据，运行现有 Schema、跨实体引用和风险规则
   Validator。正式实体不会因为画布暂时缺少节点而被删除。
7. 可选 Codex 评审只接收脱敏的 Panorama 架构投影、候选、正式校验结果和有限仓库元数据；不接收
   Credential、External Store 值或业务源码。Agent 只输出 advisory Risk Candidate，不能批准或 Apply。
   Agent 不可用、网络失败或结构化输出不符合合同会作为可见错误保留；它不替代 Formal Validator，
   因而不会伪装成功，也不自动阻断由用户发起的 Proposal。
8. 生成 Proposal 时冻结候选语义，绑定 Session、Candidate Semantic Hash、Base Revision、Data
   Hash、Git HEAD、Source Snapshot 与有界 Source Content Digest。纯布局移动不改变语义 Hash；
   任何语义编辑都会使校验、评审、Proposal 和批准失效。V0.1 没有正式 Source Binding 时，Bridge
   以 Session 创建时的完整覆盖 live baseline 为锚；覆盖不完整或后续内容漂移都会阻断 Proposal。
9. 页面完整显示 64 位 Proposal Hash。用户必须输入身份并精确确认
   `批准 Studio Proposal <EXACT-HASH>`；Bridge 使用 recorder clock 写入独立、write-once Approval。
10. Apply 再次验证 Proposal/Approval/Source Binding、Schema、跨引用、Presentation Hash 和并发状态，
    创建备份后原子替换正式 HTML。失败不得自动 rebase、修复或重批。

Studio Freeze 生成的就是通用 Apply 消费的规范 Proposal；第 9 步的独立 Approval 是该语义变更唯一的
人工批准，不得在 Apply 前再要求一次普通 Proposal Approval。只有 Proposal Operations、Candidate Semantic
Hash、Data/Git/Source/CAS Binding 变化时才要求重新批准。

## 数据与治理边界

- Session 使用独立 Schema `schema/architecture-session.schema.v0.1.json`，不写入
  `project-panorama-data` 或其 `extensions`。
- Candidate 节点使用 session-local `nodeId`；已有正式实体另带 `entityRef`。草稿 ID 不冒充正式
  Module/Connection ID。
- 语义操作与布局操作分开记录。Proposal 只包含候选到正式数据的语义差异。
- 只把 Bridge 保存后返回的完整 `semanticHash / layoutHash` 称为权威 Hash；离线页面显示
  `not_computed_by_bridge`。Session Operations 是可观察的会话记录，不宣称为不可变审计 Journal。
- 候选 Semantic Diff 中正式节点/连线只按 `entityRef.type + entityRef.id` 对齐，草稿只按
  session-local `nodeId/edgeId` 对齐；显示名称不是身份。坐标、缩放、折叠、视觉顺序和操作时间
  不进入 Semantic Diff。
- semantic edit、candidate switch、Panorama/Data/Git/Source drift、CAS Conflict、coverage
  incomplete 与 formal source uninitialized 等 stale 原因可同时派生显示，但 V0.4 不将其写入
  Panorama Schema 或冒充正式 Finding。
- 页面不能生成 Approval 时间、Formal Finding Code、verified/accepted/deployed/waived 等治理事实。
- Agent review 的 `ready` 只表示建议已完成，不是用户批准。
- Apply 只修改正式 JSON payload；Presentation Layer 与 Renderer Hash 保持不变。

## 本机安全边界

- Bridge 只绑定 IPv4 loopback `127.0.0.1` 随机端口，并严格校验 Host、Origin、capability 与 CSRF。
- 不启用 CORS，不接受客户端文件路径；API 请求体有大小上限，敏感响应禁止缓存。
- Bridge 仅在 HTTP 响应中生成带 nonce 且 `connect-src 'self'` 的 Renderer 变体，不写回 canonical
  HTML；直接打开的文件仍为 `connect-src 'none'`。
- Codex 通过固定 CLI 参数在系统临时根的独立目录和只读 sandbox 中执行；Bridge 传入的项目内
  `work_root` 不会被采用，运行目录只复制脱敏输入 bundle 与输出 Schema，不复制业务源码。输出受
  JSON Schema 约束。只读 sandbox 不是操作系统 allowlist：当前不能宣称提供进程树、网络或宿主
  文件系统可达性隔离，绝对路径在宿主权限允许时仍可能可达；页面、输入 privacyBoundary 与输出
  adapter 元数据必须披露这些限制。
- `.panorama-work/studio/` 只保存会话、Proposal、Approval 与审计制品，并应保持 Git ignored。
- Bridge 对工作目录和所有制品读写逐级拒绝 symlink、junction/reparse point 与路径逃逸；Source
  Content Digest 对纳入范围的路径、大小和内容做 SHA-256，超过覆盖上限时停止正式化，不把部分
  哈希伪装成完整基线。Bridge 在本机读取文件字节仅用于该摘要；源码正文不写入制品、不返回浏览器、
  也不进入 Agent bundle。覆盖上限为 20,000 个文件与 128 MiB；跳过不安全链接同样会阻断 Review、
  Proposal 与 Apply。

## 故障与恢复

- Bridge 不可用：保留离线编辑和 JSON 导出。
- Session CAS 冲突：停止自动保存，允许导出本地副本或重新载入服务器版本。
- Git/Data/Source 漂移：保留草稿并标记 stale，禁止 Proposal/批准/Apply；不自动 rebase。
- Codex 不可用或未登录：正式 Validator 仍可运行，Agent 评审显示 `not_configured`。
- Apply 失败：正式 HTML 保持原状态；若事务已开始，保留备份用于审计与恢复。

## V0.8 候选层级画布

Candidate 新增 `architectureLayers` 语义投影。用户可在 Studio 中新增、重命名、排序、设置父层级并安全删除层级/
功能区；节点继续绑定 Layer，数据流继续绑定显式端点。Layer ID、名称、顺序、父子关系、Kind 和 Summary 进入
Candidate Semantic Hash 与 Proposal Diff；坐标、折叠和 `layoutHeight` 只进入 Layout。

Client Validator 必须拒绝重复 Layer ID、未知父层级、父子循环和节点悬空 Layer 引用。删除仍含模块或子层的 Layer
必须在 UI 中阻断；Formal Validator 再次校验完整 Panorama。候选物化可以更新正式 `architecture.layers`，但只有
Proposal 的精确 Hash Approval 与 Apply 才能修改项目 Core。

## V0.8 父子语义画布与 Module Logic Candidate

Studio 是唯一 Canvas Runtime 的 `edit` 模式，不是与查看/讲解并列的工具。进入编辑模式时保留当前
`architecture|module_logic(moduleId)` 场景、Camera 和 Selection，但把数据源从正式 Current/
Target 投影切换为隔离 Candidate。

- 架构主画布编辑 Layer Container、Module Card 和 Candidate Connection；
- Layer/Module 的 authored `displayName` 是可编辑中文主显示名，稳定 `name` 是只读技术名；清空
  `displayName` 只触发兼容降级与 Information Gap，不得覆盖技术名或由浏览器自动翻译；
- 模块子画布编辑 Logic Node/Edge、Loop/Condition、Boundary Port 和内部数据流；
- 其他模块/资源在子画布中只是只读外部引用卡。新建跨模块交互必须同时生成 Candidate
  Connection Diff 并披露受影响模块；
- `moduleLogicCanvases[]` 属于 Candidate。名称、节点类型、边端点、条件、Loop 退出条件、端口绑定和关联
  ID 进入 Semantic Hash；内部节点/端口坐标进入 Layout Hash；Camera、Selection、Explain/Playback 不进入两者；
- Current Observation 始终只读。复制为 Target Candidate 只是创建起点，不提升事实状态；
- `unbound_candidate` 只允许草稿中存在，Formal Validation/Proposal 必须拒绝未绑定边界端口。
