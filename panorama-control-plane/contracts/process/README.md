**开发过程规范：P0 合同包 · 0.1.0**

本目录交付项目配置、规范、执行证据及评估结果的数据合同，以及可以独立校验的样本。P0 不包含运行规则的产品引擎、项目开发 CLI、Skill 安装或工作台界面。

后续 [P1 本地核心](../../src/process/README.md) 已实现计算与存储，[P2 项目内工具](../../src/development/README.md) 已实现 Structure 适配、真实执行采集和恢复，[P3 工作台](../../src/standalone/PROCESS-EVIDENCE.md) 已展示逐项证据并接入结构化交接，共用本目录的冻结合同；下列 P0 样本仍保留其原始合成来源和期望值。

从 [CONTRACT.md](CONTRACT.md) 阅读字段、状态和约束；[MAPPINGS.md](MAPPINGS.md) 说明 Structure 与现有全景格式如何对应；[VALIDATION.md](VALIDATION.md) 记录本阶段验证及下一阶段边界。

| 制品 | 内容 |
|---|---|
| [schema-manifest.json](schema-manifest.json) | 四类入口、共享定义、包版本、限制和哈希算法 |
| [项目配置 Schema](schemas/project.schema.json) | `panorama.process-project.v1` |
| [规则包 Schema](schemas/standard-pack.schema.json) | `standard-pack.v0.2` |
| [执行证据 Schema](schemas/receipt.schema.json) | `panorama.process-receipt.v1` |
| [评估 Schema](schemas/assessment.schema.json) | `panorama.process-assessment.v1` |
| [共享定义](schemas/common.schema.json) | 身份、输入、产物、证据、状态及声明式条件 |
| [基础规则包](rules/development-process.v0.1.json) | 六条基础必备、四条条件必备 |
| [样本目录](examples/catalog.json) | 两项目、七组正向/缺口场景及可读的合成输入 |
| [固定上游清单](upstream.lock.json) | 已验证提交、归档摘要、116 文件树摘要与 10 个接口文件摘要 |
| [上游许可证](UPSTREAM-LICENSE.txt) | 固定 core 原许可证，按原字节保留 |

**样本使用方式**

所有样本使用 `purpose: contract_example`、虚构的 `fixture:` 身份和明确的合成来源。`assessment.expected.json` 是预先编写的期望结果，未经过 P1 评估器计算。样本中的 `passed`、时间和生产者只用于说明数据结构，不是一次实际业务执行或人工验收。

| 样本 | 表达的结果 |
|---|---|
| `cat-valid` | 附件与行为证据在声明范围内足够 |
| `cat-external-stale` | 历史执行通过、上游 completed，但外部输入已经变化 |
| `cat-review-missing` | 已声明完成，但视觉评审未执行，保持证据不足 |
| `hogwarts-valid` | 源码行为与留存对象分别有证据，并展示可选设计候选绑定 |
| `hogwarts-retained-uncovered` | 检查的是另一个新建对象，无法支持留存对象验收 |
| `hogwarts-docs-only` | 只有文档回读，无业务测试进程；条件项不适用 |
| `hogwarts-impact-unknown` | 未知的接口影响保留未知，整体不能满足 |

`object.bin` 是可计算摘要的普通合成字节，不是有效 GLB 或生产附件。其目的在于检验对象身份和覆盖关系能否表达。真实 GLB、TAR、HTTP、浏览器或生产环境的业务判据由后续项目检查器验证。

每组 `baseline-inputs/` 保存合成的开发前内容，`inputs/` 保存检查/当前观察内容。两者分别用于验证开始基线和检查输入，避免将检查时点误作开发起点。

**运行合同检查**

从 `panorama-control-plane` 根目录运行。需要项目既有 Node 环境，以及装有 [test/process/requirements.txt](../../test/process/requirements.txt) 的 Python 3.10+。依赖仅用于测试，不进入工作台运行时；可在独立虚拟环境安装：

```powershell
python -m venv <项目外测试环境目录>
& '<测试环境目录>/Scripts/python.exe' -m pip install -r test/process/requirements.txt
$env:PANORAMA_CONTRACT_PYTHON = '<测试环境目录>/Scripts/python.exe'
pnpm test:process-contracts
```

Unix 使用相应环境的 `bin/python`。校验过程不联网解析 Schema，不执行业务代码。`rfc3339-validator` 是必需测试依赖；缺少日期校验支持时检查直接报错，不降级忽略 `date-time`。

`pnpm test` 仍运行现有 standalone 回归；`pnpm test:all` 已纳入合同检查，因此需要上述测试依赖。原有源码解析回归还需要可用的 `PANORAMA_PYTHON`，它可以与合同测试解释器分别配置。
