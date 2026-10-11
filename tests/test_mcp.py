import asyncio
import json
import os
import sys
from datetime import timedelta
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_mcp_roundtrip(tmp_path, payload):
    config = tmp_path / "config.toml"
    config.write_text("data_dir = " + json.dumps(str(tmp_path / "state")) + "\nworkspace = " + json.dumps(str(tmp_path)) + "\n", encoding="utf-8")

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "context_sentinel.server", "--config", str(config)], env=dict(os.environ))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "Context Sentinel"
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                assert {"context_health", "checkpoint_save", "checkpoint_import", "restore_prepare", "restore_commit"} <= names
                assert "stage_thread" not in names
                opened = await session.call_tool("monitor_open", {"project_id": "mcp-monitor", "request_key": "chat-1"})
                sid = json.loads(opened.content[0].text)["session_id"]
                observed = await session.call_tool("monitor_observe", {"observation": {"session_id": sid, "event_id": "e-1", "turn_id": "t-1", "signals": {"failed_verifications": 3}}})
                decision = json.loads(observed.content[0].text)
                assert decision["should_notify"] and decision["reminder"]["severity"] == "migration"
                acknowledged = await session.call_tool("monitor_ack", {"session_id": sid, "reminder_id": decision["reminder"]["id"]})
                assert json.loads(acknowledged.content[0].text)["status"] == "acknowledged"
                replay = await session.call_tool("monitor_observe", {"observation": {"session_id": sid, "event_id": "e-1", "turn_id": "t-1", "signals": {"failed_verifications": 3}}})
                assert not json.loads(replay.content[0].text)["should_notify"]
                result = await session.call_tool("context_health", {"signals": {"failed_verifications": 3}})
                data = json.loads(result.content[0].text)
                assert data["action"] == "prepare_handoff"
                saved = await session.call_tool("checkpoint_save", {"payload": payload, "request_key": "mcp-test"})
                assert not saved.isError
                envelope = json.loads(saved.content[0].text)
                check = await session.call_tool("checkpoint_verify", {"checkpoint_id": envelope["checkpoint_id"]})
                assert json.loads(check.content[0].text)["valid"]
                ack = {"checkpoint_id": envelope["checkpoint_id"], "payload_sha256": envelope["payload_sha256"],
                       **{f: payload[f] for f in ("objective", "constraints", "pending", "next_action")}}
                commit = await session.call_tool("restore_commit", {"ack": ack, "expected_previous": None})
                assert json.loads(commit.content[0].text)["status"] == "committed"
                invalid = await session.call_tool("checkpoint_save", {"payload": {}, "request_key": "bad"})
                assert invalid.isError
    asyncio.run(run())
