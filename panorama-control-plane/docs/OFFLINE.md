# Panorama 1.0 离线试用

每台服务器解压一份全景，多个项目分别接入。包内含已构建工作台、项目内 CLI、开发过程技能、规范合同、固定 Structure core 和所需 JavaScript 库。安装和使用均不需要 npm、pnpm、pip 或下载依赖。

Node.js 和 Python **不包含在发布包中**，须由服务器预先提供。业务项目自己的测试工具、依赖、模型服务也由项目环境提供。

## 环境与选择

- Node.js：22.19.0 或更高的 22.x，或者 24.0.0 及以上；建议使用仍受维护的 LTS 版本。
- Python：3.11+，包含标准库。全景本身不需要额外的 Python 包。
- Linux 下载 `panorama-1.0.0-linux.tar.gz`；Windows 下载 `panorama-1.0.0-windows.zip`。
- 全景程序由 JavaScript、Python 和静态页面构成，不含 CPU 原生二进制。x64/ARM64 使用各自系统对应的 Node/Python；具体实测平台以 Release 验证结果为准。
- 包应解压到普通目录，保留完整结构。Windows 项目根建议使用短路径，例如 `D:\projects\demo`。

先将压缩包及 `SHA256SUMS.txt` 拷贝到断网服务器，核对 SHA-256 后解压。安装脚本只校验包和运行时，在解压目录登记本机路径并生成启动入口；不改变 PATH，不安装系统服务，不修改业务项目。

## Linux

```sh
tar -xzf panorama-1.0.0-linux.tar.gz
cd panorama-1.0.0
sh ./install.sh --node /opt/node/bin/node --python /usr/bin/python3
./panorama doctor
```

如果 Node/Python 已在 PATH 中，可直接运行 `sh ./install.sh`。Python 符号链接会解析为实际解释器路径。下面将安装目录记为 `/opt/panorama-1.0.0`；替换为实际位置。

## Windows PowerShell

解压 zip 后进入 `panorama-1.0.0`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Node 'C:\node\node.exe' -Python 'C:\Python313\python.exe'
powershell -NoProfile -ExecutionPolicy Bypass -File .\panorama.ps1 doctor
```

`ExecutionPolicy Bypass` 仅用于该进程，不更改机器执行策略。如果环境禁止 PowerShell 脚本，可直接执行：

```powershell
& 'C:\node\node.exe' .\offline.mjs install --python 'C:\Python313\python.exe'
& 'C:\node\node.exe' .\offline.mjs doctor
```

## 接入已有项目

在该项目的 Coding Agent 任务中给出安装路径，并让它完成以下步骤：

1. 执行 `process preflight`，读取实际目录、现有规范及检查入口，生成 bootstrap 草稿。
2. 将读取范围收窄到本次开发需要的源码、测试、配置和样本；显式登记真实检查器，不凭脚本名称假定已经通过。
3. 执行 `process init`。已有 AGENTS 内容保留，追加带标记的全景指引，登记项目技能。
4. 后续每项工作使用 begin → 修改 → scan → check/必要的 review → finish；新任务用 resume 回读。

Linux 示例（先创建项目外的数据和请求父目录）：

```sh
/opt/panorama-1.0.0/panorama process preflight --project /srv/projects/demo --data /srv/panorama-data/demo --output /srv/requests/demo-bootstrap.json
# 回读并补齐 bootstrap，再初始化
/opt/panorama-1.0.0/panorama process init --project /srv/projects/demo --data /srv/panorama-data/demo --input /srv/requests/demo-bootstrap.json
```

Windows 使用相同参数，将入口替换为 `& 'D:\tools\panorama-1.0.0\panorama.ps1'`。固定入口自动提供登记的 Python，无需每次添加 `--python`。

接入基线取实际文件状态，包括未提交修改；已有项目过去未采集的开发历史不会被补造成已验证记录。

可复制给 Coding Agent：

> 本项目使用 Panorama 1.0，安装入口为【实际路径】。先阅读发布包 README 和 app/skills/panorama-development-process/references/project-guide.md，执行 preflight 并根据实际源码、检查器补齐接入配置，然后 init。保留已有项目规则，从真实文件状态建立基线；后续开发使用 begin、scan、check、必要的 review、finish，并在接续时 resume。检查结果必须来自实际执行。

## 接入空项目

先创建项目目录，使用同样的 preflight/init 流程；bootstrap 的 runners 可以为空。先记录并完成“工程准备”工作，产出需求说明、目录、依赖说明及首个检查器。真实评审后结束准备工作，再使用 config-plan/config-apply 登记首个 runner 和新模块（`refreshModules: true`），开始功能研发。空目录不会被显示为已经实现的架构。

## 工作台与远程访问

```sh
/opt/panorama-1.0.0/panorama workbench start --project /srv/projects/demo --data /srv/panorama-data/demo --port 43110
```

在本机浏览器打开启动输出的完整地址。服务器上只监听 `127.0.0.1`。从自己的电脑访问远程服务器时，保持本地和远程端口相同：

```sh
ssh -N -L 43110:127.0.0.1:43110 user@server
```

随后打开 `http://127.0.0.1:43110/#cap=...`（使用本次启动实际输出的凭证）。多个项目使用不同端口和数据目录。工作台关闭后，项目内 CLI 仍可正常记录开发过程。

全景离线可解析源码、编辑候选、导出和导入 Agent 交接、记录检查和展示演进。自然语言模型分析需另有可访问的模型服务；未配置时使用外部 Agent 交接或手工设计。

## 多机、升级与故障

- 分发原始压缩包；`installation.local.json` 和安装后生成的启动入口只属于本机。
- 每个项目单独 init，数据目录必须在项目外。同一项目的 CLI 和工作台使用同一数据目录。
- 不把某个项目的 `.structure`、`.panorama` 或过程数据作为新项目模板。已有项目整体迁移到另一台服务器属于独立迁移工作，1.0 不自动重绑定历史记录。
- 升级解压到新目录后重新安装，保留旧安装和项目记录。运行时更换也使用新解压目录；既有项目的 Python 配置变更走 config-plan/config-apply。
- doctor 验证程序清单、文件摘要及实际运行时；它不代表某个项目的业务验收已经通过。
- CLI 返回 0 表示操作正常，2 表示有效结果仍有待处理项，1 表示输入、安装或操作错误。检查失败和证据过期须按输出处理。

详细接口位于包内 `app/src/development/README.md`、`CONFIGURATION.md`、`STANDARDS.md`；原始第三方许可证及 `app/THIRD-PARTY-NOTICES.md` 随包保留。
