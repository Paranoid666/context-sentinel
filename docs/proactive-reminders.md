# 主动提醒协议（0.3.0）

主动提醒有两个运行入口：AI 在 skill 生效的任务中主动调用 MCP，或宿主把交互事件传给 CLI/MCP。服务不会读取聊天历史、注册桌面回调、抓取 UI 或在闲置时唤醒模型。仅安装技能不能保证每轮都执行；需要模型遵循技能，或宿主提供可靠的事件钩子。

## AI 工作流

1. `monitor_open(project_id, request_key)` 创建/重用一个本地会话。新聊天使用新 key；同一聊天内重试用旧 key。返回 session_id，不是宿主 thread_id。
2. 每轮开始调用 `monitor_observe(observation)`，任务边界或验证失败时也调用。相同 turn_id 只计一轮，相同 event_id 重试不重复处理，改变内容需要新 event_id。
3. `should_notify=true` 时，AI 在聊天中主动显示 `reminder.message`，再调用 monitor_ack。确认的是提醒已展示，不是用户授权迁移或确认恢复。
4. 高风险提醒建议换聊，同时按现有 checkpoint/handoff 协议准备交接；实际换窗口由用户操作。

```json
{
  "session_id": "monitor_open returned ID",
  "event_id": "turn-3-verification-1",
  "turn_id": "turn-3",
  "trigger": "verification_failure",
  "signals": {"failed_verifications": 1, "source": "estimate"}
}
```

trigger 支持 turn_start、task_boundary、verification_failure、manual。signals 是当前观察到的风险信息，不是每次上报都累加的事件数量。服务只累计不同 turn_id 的观察轮数，不推测未上报的历史。

## 阈值与去重

默认观察到 16 轮建议保存，48 轮建议换新聊天。这是连续性提醒，不是“达到某轮数就一定幻觉”。已报告的风险策略或验证失败可更早触发。

SQLite 中保存会话、已处理事件、轮次和提醒 outbox。提醒展示后才确认；未确认提醒在重启后仍可读取，不生成新的重复 ID。同级提醒确认后，需要同时经过 8 个新观察轮次和 600 秒才能再产生。风险升级可跳过冷却。用户请求安静时用 monitor_snooze(session_id, seconds)，范围 0..86400，0 表示恢复；不会创建定时通知。

提醒投递是“确认前可重取”的至少一次协议：如果展示后、确认前进程崩溃，宿主可能再次看到同一提醒。宿主需按 ID 去重；没有声称跨 UI 和 SQLite 的严格 exactly-once。主动提醒本身不修改 active checkpoint、不创建目标线程。

## 宿主事件入口

CLI：monitor-open PROJECT --key CHAT_KEY、observe observation.json、monitor-status SESSION、monitor-ack SESSION REMINDER、monitor-snooze SESSION SECONDS。

`python -m context_sentinel.cli --config ... watch` 从 stdin 接收一行一个 Observation JSON，并输出一行一个结果。它是宿主事件处理器，输入到达时才检查；不是自动读取聊天的守护进程。宿主负责显示/确认提醒，可用其他 CLI/MCP 调用完成 ack。缺少后台通知接口时，能力标记为 unsupported。

配置位于 `[monitor]`：enabled、warn_turns、migrate_turns、cooldown_turns、cooldown_seconds。风险评分与周期提醒共用输出，但 `health.action` 保留原始风险策略结论，`health.recommended_action` 加上了周期/事件提醒策略。
