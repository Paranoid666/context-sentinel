# Context Sentinel 0.2.0

可运行的 Python 3.12+ MVP：V0.1 的 skill、风险策略与持久化，加上 V0.2 的 portable handoff、验证、确认与恢复。它减少上下文遗漏造成的连续性错误；**不能检测所有幻觉，也不能保证新上下文中的结论正确**。

原对话预览没有包含架构正文，本环境没有 `read_thread` 工具。本实现以当前用户请求列出的架构为依据，没有声称读取过原对话中的完整方案。

## 快速运行

从项目目录执行，Windows 可将 `.venv/bin/python` 替换为 `.venv/Scripts/python.exe`：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[test]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe examples/demo.py
.venv/Scripts/python.exe -m context_sentinel.cli --config config/sentinel.toml health examples/signals.json
.venv/Scripts/python.exe -m context_sentinel.cli --config config/sentinel.toml checkpoint examples/handoff.json --key example-1
```

保存命令返回 `checkpoint_id`，随后使用该 ID：

```powershell
context-sentinel --config config/sentinel.toml verify CHECKPOINT_ID
context-sentinel --config config/sentinel.toml handoff CHECKPOINT_ID
context-sentinel --config config/sentinel.toml prepare CHECKPOINT_ID
```

`prepare` 返回 envelope 和 `expected_previous`。接收者复述 objective、constraints、pending、next_action，并生成 `RestoreAck` JSON（见 [协议](docs/protocol.md)）。执行 `restore ack.json --previous PREVIOUS_ID`；首次恢复省略 `--previous`。单纯保存或导入不会激活状态。

演示在临时目录中真正执行风险评估、幂等保存、首次恢复、文件篡改拦截、旧状态保留、修复后恢复及跨存储导入，不会创建外部线程。

## 连接 MCP / 安装 skill

```powershell
context-sentinel-mcp --config config/sentinel.toml
```

这是官方 Python MCP SDK 的 **stdio** 服务；协议输出仅走 stdout。`config/mcp.example.json` 是配置片段，不会自动修改任何宿主配置。将其 command/args 替换为真实绝对路径；把 TOML 的 workspace/data_dir 也设置成绝对路径，避免宿主工作目录差异。依照宿主支持的 MCP 配置方式接入。

`skill/context-sentinel/SKILL.md` 是标准技能入口，可将整个 `skill/context-sentinel` 文件夹复制到用户的 Codex skills 目录，保持自动发现默认行为。本交付没有修改全局技能或 MCP 配置。ChatGPT 是否能调用该服务取决于其当前开放的连接能力；本项目没有实现 remote HTTP MCP 部署，也没有假设 ChatGPT 能加载本地 SKILL.md。无连接时可使用验证后导出的交接文本手动开始新聊天。

服务提供 10 个工具：context_health、host_capabilities、checkpoint_save、checkpoint_import、checkpoint_verify、handoff_export、restore_prepare、restore_commit、checkpoint_repair_export、app_server_probe。线程创建保留为 CLI 操作，不允许模型通过 MCP 自行提交确认。

## 能力边界

| 能力 | ChatGPT adapter | Codex adapter | 可选 Codex App Server adapter |
|---|---|---|---|
| 风险评估 / SQLite+JSON / 手动交接 | 支持（需要工具连接或 CLI） | 支持 | 支持 |
| 自动读取当前聊天、内部 token 遥测 | unsupported | unsupported | unsupported |
| UI 打开窗口 / 焦点切换 | unsupported | unsupported | unsupported |
| 新线程创建 | unsupported | unsupported | 显式启用和确认后，stdio thread/start |
| 自动模型确认 / 完整自动迁移 | unsupported | unsupported | unsupported；仅 staged_awaiting_ack |

Skill 只能在被宿主调用时运行，不能提供后台钩子、定时器或主动监控。signals 由宿主/用户提供，`source=estimate` 标记估算。缺失 token 数据显示为 partial，不会捏造。策略在任务边界、发现矛盾或验证失败时评估；阈值只触发建议，不自动切换聊天。重复报告不要通过新 request_key 不断重复写入同一 checkpoint。

## 可选 App Server

已核实 [官方 App Server 文档](https://developers.openai.com/codex/app-server/) 中的 stdio 换行 JSON-RPC、initialize → initialized、thread/start、thread/read。本机 CLI 生成的 JSON Schema 同时用于核对字段，详见 [接口记录](docs/official-interface.md)。

把配置中的 `app_server_enabled` 改为 true，然后执行：

```powershell
context-sentinel --config config/sentinel.toml probe
context-sentinel --config config/sentinel.toml stage-thread CHECKPOINT_ID
# 上一步输出具体计划。用户已授权该创建动作后才能执行：
context-sentinel --config config/sentinel.toml stage-thread CHECKPOINT_ID --confirm-create
```

线程采用 read-only / untrusted 审批设置。仅生成 handoff 文件定位说明，不把清单正文提升为 developer instructions。不会调用 turn/start、花费模型推理、归档源聊天或切换 UI。`thread/read` 只证明线程 ID 可读，不能证明模型已读取清单或配置已持久化；成功状态仍为 awaiting_ack。恢复确认由接收者另行完成。

App Server 版本差异、授权或尚未物化的空线程可能导致创建后无法读取。此时记录 `needs_reconciliation` 与已知 target ID，保留原状态。远程创建没有已核实的幂等键，超时或进程崩溃不能盲目重试。SQLite migration 记录中的 creating/needs_reconciliation 必须人工核对 CLI 官方接口返回，再决定后续操作；本 MVP 不提供自动清除记录或重建线程操作。

## 可靠性与数据

- SQLite 是权威存储；JSON 使用临时文件、fsync、原子替换。SQLite 与文件系统不伪装成跨资源事务。JSON 写入失败后，同 request_key 重试能修复导出。
- 完整清单严格校验，未知字段/版本拒绝；幂等键的内容冲突拒绝。保存和并发恢复均使用 SQLite 事务。恢复通过 compare-and-swap 避免覆盖另一个接收者已激活的状态。
- 校验 SHA256、JSON/SQLite 一致性和工作区内文件哈希。验证失败发生在激活前，旧 active 指针保持原值，这就是 MVP 的回滚保证；不会回滚外部文件修改或已创建的远端线程。
- JSON 能导入另一 SQLite 存储，但接收者必须配置正确 workspace、独立验证证据并确认。SHA256 防意外损坏，不是数字签名；能同时修改数据库和哈希的攻击者不在其防护范围内。
- facts 的 verified 是记录者声明。系统不自动证明事实、权限或模型理解。空 artifacts 合法，但这意味着没有进行产物文件验证。恢复确认是清单一致性协议，不是语义推理保证。
- 本地存储不加密；不要保存密钥、隐私无关信息或隐藏推理。文件读取到提交之间无法锁住外部写入，执行下一步前仍应核验易变产物。
- 不自动删除 checkpoint、SQLite、原始素材或环境。用户的最近三版成品保留偏好适用于验证后的发布包，不适用于恢复历史数据。

## 文件

`src/context_sentinel/`：models、health、store、service、adapters、server、cli。
`tests/`：策略边界、非法输入、并发幂等、存储损坏、导出失败、恢复回滚、跨存储恢复、宿主确认、远程异常和真实 MCP stdio 通信。
`examples/`：可执行 demo、signals 和完整 handoff 示例。
`docs/`：协议与官方接口核实记录。
`reports/`：本次实际验证结果。`requirements-verified.txt` 记录本次验证环境依赖快照；其他平台按 pyproject.toml 安装，Windows 专属依赖有平台条件。

版本 V0.1 + V0.2 合并为一个 0.2.0 成品发布；没有人为生成三个重复版本。发布 zip 不包含 Python 环境、缓存、真实用户数据或 SQLite 演示数据。
