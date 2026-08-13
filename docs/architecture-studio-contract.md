# Architecture Studio V0.3 合同

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

1. 在 Panorama 中进入 `系统 → 逻辑架构 → 架构设计 Studio`。
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

## 数据与治理边界

- Session 使用独立 Schema `schema/architecture-session.schema.v0.1.json`，不写入
  `project-panorama-data` 或其 `extensions`。
- Candidate 节点使用 session-local `nodeId`；已有正式实体另带 `entityRef`。草稿 ID 不冒充正式
  Module/Connection ID。
- 语义操作与布局操作分开记录。Proposal 只包含候选到正式数据的语义差异。
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
