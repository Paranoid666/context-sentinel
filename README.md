# Context Sentinel

**给长时间运行的 AI 任务保存一份可检查的交接清单，帮助新聊天接着做。**

聊天变长后，AI 可能遗漏约束、重复犯错，或者忘记哪些结论已经验证。Context Sentinel 根据你或宿主提供的信息评估风险，保存任务进度，再让接收者检查并确认交接内容。

当前版本为 **0.2.0 MVP**，已实现风险评估、任务保存、交接验证和恢复。它不能识别所有幻觉，也不能保证换一个聊天后答案就正确。

## 它怎么工作？

例如，你正在让 AI 开发项目。经过多轮修改，它开始重复修复同一个问题。这时可以：

1. **评估风险**：报告重复错误、验证失败或上下文用量，得到继续工作、保存进度或准备交接的建议。
2. **保存进度**：记录目标、约束、已完成事项、待办、下一步，以及文件和证据。
3. **检查交接**：确认记录完整，相关文件没有发生意外变化。
4. **在新聊天中恢复**：让接收者复述关键内容，验证通过后确认恢复，再继续工作。

如果交接检查失败，原来的有效状态会保留。“恢复”指确认任务记录，后续工作仍由接收者执行。

## 现在能做什么？

| 功能 | 当前状态 |
|---|---|
| 根据提供的信息评估上下文风险 | 已实现；评分是经验规则 |
| 保存任务记录，导出到新环境 | 已实现；SQLite 保存，JSON 导出 |
| 检查交接内容和相关文件是否一致 | 已实现 |
| 验证失败时保留原有效记录 | 已实现 |
| 通过 MCP 向 AI 提供工具 | 已实现；需要配置连接 |
| 提供指导 AI 使用工具的 skill | 已提供；需要单独安装 |
| 通过 Codex App Server 创建交接目标线程 | 可选；已实测，创建后等待接收确认 |
| 自动读取当前聊天或内部 token 用量 | **不支持**（unsupported） |
| 自动打开窗口、切换聊天界面 | **不支持**（unsupported） |
| 后台持续监控、自动完成迁移 | **不支持**（unsupported） |

**下载项目、安装 Python 服务、安装 skill 和连接 MCP 是不同的步骤。** 只下载仓库，或只复制 `SKILL.md`，都不会自动启用完整功能。

## 先运行演示

需要 **Python 3.12 或更新版本**、Git，以及安装依赖时的网络连接。基础演示不需要 OpenAI API Key，也不会调用模型。

### Windows / PowerShell

```powershell
git clone https://github.com/Paranoid666/context-sentinel.git
cd context-sentinel
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe examples/demo.py
```

### macOS / Linux

```bash
git clone https://github.com/Paranoid666/context-sentinel.git
cd context-sentinel
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[test]"
./.venv/bin/python examples/demo.py
```

演示会在临时目录里保存记录、模拟文件被修改、拦截恢复，然后修复文件并再次恢复。以下输出表示流程成功：

| 输出字段 | 含义 | 预期结果 |
|---|---|---|
| `idempotent_retry` | 重复保存同一记录没有产生重复数据 | `true` |
| `verification_failure_preserved_old_state` | 检查失败后，原有效状态被保留 | `true` |
| `recovery_after_artifact_repair` | 文件修复后恢复成功 | `committed` |
| `portable_import_restore` | 记录导入另一存储后恢复成功 | `committed` |

演示结束后会删除临时数据，不会创建外部聊天。

## 在 Codex 中启用：完成三个步骤

本节是安装说明；仓库中的示例不会自动修改你的客户端。

### 第一步：指定文件与数据位置

编辑 [`config/sentinel.toml`](config/sentinel.toml)，把 `workspace` 和 `data_dir` 改为真实的绝对路径。例如：

```toml
workspace = "D:/projects/my-project"
data_dir = "D:/projects/my-project/.context-sentinel"
app_server_enabled = false
codex_executable = "codex"
```

- `workspace`：AI 正在处理的项目目录，用来核对交接中的文件。
- `data_dir`：记录保存目录，存放 SQLite 数据库和 JSON 交接文件。

以上路径请替换；保留配置原有的 `[policy]` 部分即可使用默认风险阈值。绝对路径可以避免客户端启动目录不同而把记录保存到错误位置。

### 第二步：连接 MCP 服务

MCP 让 Codex 能调用保存、验证和恢复工具。安装了 Codex CLI 后，可以执行下面的命令；将两个路径替换为实际安装位置：

```powershell
codex mcp add context-sentinel -- "D:\projects\context-sentinel\.venv\Scripts\python.exe" -m context_sentinel.server --config "D:\projects\context-sentinel\config\sentinel.toml"
codex mcp list
```

macOS / Linux 使用 `.venv/bin/python` 的绝对路径。通过客户端设置界面接入时，选择 **STDIO**，填写同样的程序路径和参数。入口以客户端实际界面为准，详见 [Codex 官方 MCP 文档](https://developers.openai.com/codex/mcp/)。

`codex mcp list` 用来检查注册情况。重新加载客户端后，实际调用 `host_capabilities` 或 `context_health` 成功，才说明服务可用。`config/mcp.example.json` 供支持该格式的其他宿主参考，不是 Codex 的配置文件。

### 第三步：安装 skill

将整个 [`skill/context-sentinel`](skill/context-sentinel) 文件夹复制到客户端支持的技能目录。当前 [Codex 官方技能文档](https://developers.openai.com/codex/skills/) 使用以下位置：

| 使用范围 | 安装后应存在的文件 |
|---|---|
| 当前用户，适用于多个项目 | `~/.agents/skills/context-sentinel/SKILL.md` |
| 仅当前项目 | `<项目目录>/.agents/skills/context-sentinel/SKILL.md` |

Windows 中的 `~` 通常是用户目录，例如 `C:\Users\你的用户名`。如果客户端版本使用其他技能目录，以该版本实际支持的位置为准。

重新加载客户端，在技能选择器中查找 `context-sentinel`，或尝试显式调用 `$context-sentinel`。skill 告诉 AI 何时保存、如何交接；MCP 服务负责实际执行。**两者都就绪后，完整流程才可使用。**

可以用下面的请求检查接入：

> 使用 Context Sentinel，先报告当前宿主支持哪些能力，再根据我提供的风险信号评估是否需要保存进度。不要假设你能读取聊天历史或自动切换窗口。

## ChatGPT 怎么使用？

是否能直接调用服务，取决于你的客户端当前开放的连接能力。本项目提供本地 STDIO MCP 服务，没有提供远程 HTTP 部署，也没有假设所有 ChatGPT 客户端都能加载本地 skill。

如果客户端无法连接，可以先用命令行保存并验证任务，再导出交接文本，手动粘贴到新聊天中。接收者仍应核实记录里的证据、文件与权限。

## 手动保存与交接

下面从仓库根目录执行。macOS / Linux 将 `.\.venv\Scripts\python.exe` 换成 `./.venv/bin/python`。

**评估风险：** [`examples/signals.json`](examples/signals.json) 是风险信号示例。

```powershell
.\.venv\Scripts\python.exe -m context_sentinel.cli --config config/sentinel.toml health examples/signals.json
```

**保存进度：** [`examples/handoff.json`](examples/handoff.json) 是完整记录示例，实际使用时应填写自己的任务信息。

```powershell
.\.venv\Scripts\python.exe -m context_sentinel.cli --config config/sentinel.toml checkpoint examples/handoff.json --key example-1
```

返回的 `checkpoint_id` 是这份记录的唯一编号。重试同一内容时使用相同 `--key`；内容变更后使用新 key。

**验证并导出：** 将 `CHECKPOINT_ID` 替换为返回的编号。

```powershell
.\.venv\Scripts\python.exe -m context_sentinel.cli --config config/sentinel.toml verify CHECKPOINT_ID
.\.venv\Scripts\python.exe -m context_sentinel.cli --config config/sentinel.toml handoff CHECKPOINT_ID
```

`verify` 检查记录和文件；`handoff` 输出可交给新聊天的文本。保存和导出不会自动切换当前聊天。

### 接收者如何确认恢复？

恢复分为“准备”和“确认提交”，避免未经检查就覆盖原有效状态：

1. 在新存储中导入交接 JSON：`import envelope.json`。仍使用同一存储时，无需导入。
2. 运行 `prepare CHECKPOINT_ID`，取得待确认记录和 `expected_previous`。
3. 核实证据和文件，复述目标、约束、待办与下一步，生成确认文件 `ack.json`。
4. 运行 `restore ack.json --previous PREVIOUS_ID`；首次恢复没有旧记录时，省略 `--previous`。

以上是 `context_sentinel.cli` 的子命令，使用与前面相同的 Python 路径和 `--config` 参数。确认文件格式见 [交接与恢复协议](docs/protocol.md)。检查失败时停止恢复，保留原有效记录。

## 可选：创建 Codex 交接目标线程

默认关闭，需要已安装的 Codex CLI 和可用的 App Server。该功能已在本机实测通过，但**创建线程不等于完成迁移**：目标模型还需要读取并确认交接。

将 `config/sentinel.toml` 中的 `app_server_enabled` 改为 `true`，然后执行：

```powershell
# 检查能否连接本机 App Server
.\.venv\Scripts\python.exe -m context_sentinel.cli --config config/sentinel.toml probe

# 先查看将要创建的线程计划
.\.venv\Scripts\python.exe -m context_sentinel.cli --config config/sentinel.toml stage-thread CHECKPOINT_ID

# 用户已授权创建后，再执行这一条
.\.venv\Scripts\python.exe -m context_sentinel.cli --config config/sentinel.toml stage-thread CHECKPOINT_ID --confirm-create
```

成功返回 `staged_awaiting_ack`，表示“目标线程已暂存，等待接收确认”。流程使用只读设置，不启动模型、不切换界面、不归档源聊天。

如果返回 `needs_reconciliation` 或遗留 `creating` 状态，先核对是否已经创建线程，不要盲目重复创建。接口核实记录见 [`docs/official-interface.md`](docs/official-interface.md)。

## 验证结果与数据说明

**37 项测试通过**，包括真实 MCP 通信、并发保存、损坏拦截、恢复确认、跨存储交接与异常处理。可执行：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

本次验证环境为 Windows / Python 3.12。真实 App Server 测试完成了握手、目标线程创建和读回核对，没有执行模型回合。详细结果见 [验证报告](reports/verification.md)。其他平台与 ChatGPT 实际接入尚未实测。

- **风险评分是建议。** 信号由用户或宿主提供；缺失 token 信息会标为未知，不会伪造用量。
- **文件校验不能证明事实正确。** SHA256 用于检查内容是否变化；记录中的“已验证事实”仍需要可靠证据。
- **恢复失败保留的是原有效记录。** 不会撤销外部文件修改，也不会自动删除已创建的远端线程。
- **记录保存在本机，未加密。** 不要写入密钥、无关隐私信息或隐藏推理。不要把恢复历史数据当缓存删除。

## 项目文件导航

| 位置 | 用途 |
|---|---|
| `skill/context-sentinel/` | 安装给 AI 使用的技能说明 |
| `src/context_sentinel/` | Python 服务、风险策略、存储与宿主适配器 |
| `config/` | 服务配置与 MCP 配置示例 |
| `examples/` | 可执行演示和输入示例 |
| `tests/` | 自动化测试 |
| `docs/` | 交接协议与官方接口记录 |
| `reports/` | 实际测试和演示结果 |
| `requirements-verified.txt` | 本次验证环境的依赖版本快照 |

普通安装使用 `pyproject.toml` 中的依赖声明即可。
