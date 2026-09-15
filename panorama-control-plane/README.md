# Panorama 1.0.1

本地源码理解、设计评审与开发过程记录工作台，包含 P0—P8 的过程规范、项目内 CLI、检查证据和模块演进。

多服务器试用请下载 Release 中的 Linux / Windows 包，阅读[离线接入说明](docs/OFFLINE.md)。包中已包含页面和 JavaScript 库，服务器只需自备 Node.js 22.19+（22.x）或 24+ 与 Python 3.11+。无需 npm/pnpm/pip 安装。

## 远程工作台

1.0.1 默认监听 `0.0.0.0`，可通过服务器 IP 直接访问。运行 `panorama workbench start --project /srv/projects/demo --data /srv/panorama-data/demo --port 43110` 后，使用 `http://服务器IP:43110/#cap=本次启动凭证`。端口使用 `--port` 配置，监听网卡使用 `--host` 配置；无需 SSH 转发。HTTP 页面支持设计编辑和复制交接说明。

## 接口

- [项目内开发工具](src/development/README.md)
- [接入与配置修订](src/development/CONFIGURATION.md)
- [规范与检查范围](src/development/STANDARDS.md)
- [工作台过程证据与演进](src/standalone/PROCESS-EVIDENCE.md)
- [项目开发技能](skills/panorama-development-process/SKILL.md)

## 从源码开发

在本目录中运行 `pnpm install --frozen-lockfile`、`pnpm build`。`pnpm test:all` 检查过程工具、工作台和离线分发；测试所用 Python 绝对路径通过 `PANORAMA_TEST_PYTHON` 与 `PANORAMA_PYTHON` 指定。P0 的独立合同测试另需 Python jsonschema，仅用于开发验证。

`pnpm build:offline --output <项目外的新绝对目录>` 生成可迁移分发目录；该构建步骤在已有开发依赖的机器上执行，目标服务器不进行依赖安装。分发包只包含独立工作台和项目内过程工具的运行闭包，未启用旧 Control Plane 执行产品。

全景记录可检查的工程事实，不替代业务验收。原始输入、检查失败和过期原因保留；1.0 版本号不等于已完成长期真实项目试用或任意行业认证。
