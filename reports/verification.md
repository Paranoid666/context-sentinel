# 实际验证结果

日期：2026-10-11，Asia/Hong_Kong。环境：Windows、Python 3.12.10、MCP Python SDK 1.30.0、Pydantic 2.14.0、pytest 9.1.1；依赖版本见 requirements-verified.txt。

## 已验证

- **37 项 pytest 全部通过**，含真实 MCP stdio 子进程 initialize、list_tools、risk、save、verify、restore_commit 和错误输入；报告见 pytest.xml。
- 策略阈值、缺失遥测、负数/NaN/错误类型拒绝、完整清单/版本拒绝。
- 并发幂等（同 Store 与独立 Store 实例）、幂等键内容冲突、导出写盘失败后重试修复、JSON 与 SQLite 损坏拒绝、缺失导出重建。
- 磁盘文件哈希变化和越界路径拒绝；确认内容/哈希错误不激活；并发 compare-and-swap 只允许一个接收者成功；重复提交幂等。
- 可移植 JSON 导入新存储、独立验证后恢复；demo.json 记录真实演示结果。
- 标准 SKILL.md 通过 skill-creator quick_validate。通过 pip editable 安装并使用实际入口；wheel 构建后在独立 target 目录安装，确认导入来自 wheel 并验证 CLI 与风险评估（wheel-smoke.json）。

## 官方 App Server 真实集成

官方页面已实际取回；本机 Codex CLI 0.162.0-alpha.17.2 生成 JSON Schema 核对字段。真实执行了 initialize / initialized / thread/start / thread/read：

- 新建演示线程：`01a12a3b-0e7b-70b0-9dea-5f26ae59b2de`。
- 结果：**staged_awaiting_ack**。读回 thread.id 一致。
- 重复调用被抑制，没有重复创建。
- 原 active checkpoint 仍为 null；没有修改源聊天。
- 没有调用 turn/start、启动模型、切换 UI、发送外部消息或更改全局配置。
- 接收模型尚未读取并确认交接；**没有声称完整自动迁移已完成**。

完整响应见 app-server.json。演示交接文件及迁移记录保留在当前 workspace 的 work/app-server-state；它们是线程引用的数据，不能作为缓存清理。项目复制到其他机器后，应在新机器重新生成 checkpoint 与计划；本次真实线程 ID 和绝对文件路径只是测试证据。

## 模拟覆盖与未验证范围

FakeClient 验证了确认前不创建、远端读取超时、崩溃留下 creating 记录后的重复抑制。真实集成没有故意制造远端超时、断电或损坏 Codex 状态。

未验证/未实现：ChatGPT 实际连接部署、remote HTTP MCP、当前聊天读取、后台调度/遥测订阅、UI 自动控制、自动模型确认、接收上下文实际继续执行。跨平台设计使用标准 Python API，但此次仅在 Windows / Python 3.12 实测。SQLite+JSON 校验不提供加密、签名或事实真伪验证。

故障时旧 active 不变；外部文件和已创建远端线程不自动撤销。恢复后不自动执行任务，权限和事实仍需接收者核实。
