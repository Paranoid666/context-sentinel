# Checkpoint / handoff / restore protocol v1

Handoff 的必填字段以 `src/context_sentinel/models.py` 为准，示例见 `examples/handoff.json`。数据应精简到能继续当前任务，保留证据指向和未解决问题，避免用模糊摘要替代约束。

## Envelope

```json
{
  "checkpoint_id": "32 lowercase hex characters",
  "created_at": "timezone-aware ISO8601 UTC timestamp",
  "payload_sha256": "64 lowercase hex characters",
  "payload": {"schema_version": 1, "...": "all required Handoff fields"}
}
```

SHA256 使用 UTF-8 JSON，sort_keys=True、separators=(",", ":")、ensure_ascii=False。校验哈希之前验证 Handoff 类型/版本。artifact 路径相对配置 workspace；不接受绝对路径、路径穿越或解析后逃出 workspace 的符号链接。

## State transitions

1. checkpoint_save：按 project_id + request_key 幂等保存。相同 key 的不同内容报错。
2. checkpoint_verify / restore_prepare：验证清单与 artifacts，不改变 active。未导出的 JSON 可从 SQLite 重建；已有 JSON 不一致则失败，只有显式 repair-export 能重建。
3. 接收上下文复述清单并生成以下 RestoreAck；可运行 CLI restore 或 MCP restore_commit。

```json
{
  "checkpoint_id": "id from prepare",
  "payload_sha256": "hash from prepare",
  "objective": "exact recorded objective",
  "constraints": ["exact constraints in order"],
  "pending": ["exact pending tasks in order"],
  "next_action": "exact next action"
}
```

4. restore_commit：再次校验磁盘 artifacts、hash 和复述字段；检查 expected_previous 后，在同一 SQLite 事务中变更 active 并写入事件。重复激活同一 checkpoint 幂等。并发激活另一 checkpoint 后，旧 expected_previous 被拒绝。
5. 若失败：active 不变。保留候选清单供调查，不自动删除、不自动改 hash 来接受变化。

## Portable handoff

复制 envelope JSON 和对应文件给接收环境。运行 `context-sentinel import envelope.json` 导入，随后 prepare/ack/commit。导入不是授权，不自动激活；本 MVP 不会打包原始文件或下载远端证据。

## Remote staging

Local plan → user-authorized CLI confirmation → migration row creating → thread/start → created_unverified → thread/read → staged_awaiting_ack。

任何 remote ambiguity → needs_reconciliation。creating（包括崩溃遗留）或已有记录均禁止自动重复创建。已创建目标线程可能保留；这是安全保留策略，不声称删除远端资源。原线程与 active 均不受 staging 影响。目标接收者完成恢复协议后，可继续用户已授权的工作；MVP 没有自动模型 turn、自动 UI 切换或后台循环。
