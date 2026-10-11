# Verified official interface notes

核实日期：2026-10-11（Asia/Hong_Kong）。

官方来源：https://developers.openai.com/codex/app-server/ ，通过 HTTPS 实际取回页面正文。在线搜索连接器在本次环境返回错误，改用直接请求官方页面。未用搜索摘要代替正文。

本机：`codex-cli 0.162.0-alpha.17.2`；`codex app-server generate-json-schema --out ...` 实际生成当前版本 JSON Schema。

已读取并核对：

- initialize 的 clientInfo{name,title,version}，完成响应后发送 initialized notification。
- stdio 上每行一个 JSON-RPC 消息，request 包含 id、method、params；通知没有 id。按官方示例不添加未出现的 framing 或伪造 UI API。
- ThreadStartParams：cwd、sandbox、approvalPolicy、developerInstructions 可用。代码传 sandbox=read-only、approvalPolicy=untrusted，未使用危险权限或 experimental 字段。
- ThreadReadParams：threadId、includeTurns；读回结果仅核对 thread.id。
- 文档存在 turn/start，但本项目没有调用它。没有承诺无需接收确认即可恢复模型工作。

Schema 生成与握手不证明账户可以成功执行模型 turn。本次未调用模型 API、不读取认证密钥、不更改 Codex 全局设置。stdio adapter 与运行中的桌面 UI 不是同一个控制入口；官方页面没有建立本项目能够切换当前窗口的依据，该能力明确 unsupported。

真实 probe / staging 的最终结果见 `reports/verification.md`。测试中 FakeClient 的覆盖与本机真实集成结果分开标注。
