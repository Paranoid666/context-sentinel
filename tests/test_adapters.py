import asyncio
import sys
import pytest
from context_sentinel.adapters import capabilities, stage_thread


class FakeClient:
    calls = []
    fail_read = False

    def __init__(self, executable):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read" and self.fail_read:
            raise TimeoutError("ambiguous read")
        return {"thread": {"id": "target-1"}}


def enable(sentinel):
    sentinel.config.app_server_enabled = True
    sentinel.config.codex_executable = sys.executable
    FakeClient.calls = []
    FakeClient.fail_read = False


def test_unsupported_hosts_are_honest(sentinel):
    for host in ("chatgpt", "codex", "codex_app_server"):
        cap = capabilities(host, sentinel)
        assert cap["ui_switch"] == cap["new_thread"] == "unsupported"
    with pytest.raises(ValueError):
        capabilities("imaginary_ui", sentinel)


def test_confirmation_precedes_mutation(sentinel, payload):
    enable(sentinel)
    env = sentinel.checkpoint(payload, "1")
    result = asyncio.run(stage_thread(sentinel, env["checkpoint_id"], client_factory=FakeClient))
    assert result["status"] == "confirmation_required"
    assert FakeClient.calls == []
    assert result["plan"]["model_turn"] is False


def test_stage_is_idempotent_and_does_not_activate(sentinel, payload):
    enable(sentinel)
    env = sentinel.checkpoint(payload, "1")
    result = asyncio.run(stage_thread(sentinel, env["checkpoint_id"], True, FakeClient))
    assert result["status"] == "staged_awaiting_ack"
    assert sentinel.store.active(payload["project_id"]) is None
    again = asyncio.run(stage_thread(sentinel, env["checkpoint_id"], True, FakeClient))
    assert again["duplicate_suppressed"]
    assert [c[0] for c in FakeClient.calls] == ["thread/start", "thread/read"]
    params = FakeClient.calls[0][1]
    assert params["sandbox"] == "read-only"
    assert payload["objective"] not in params["developerInstructions"]


def test_ambiguous_remote_failure_suppresses_retry(sentinel, payload):
    enable(sentinel)
    FakeClient.fail_read = True
    env = sentinel.checkpoint(payload, "1")
    with pytest.raises(TimeoutError):
        asyncio.run(stage_thread(sentinel, env["checkpoint_id"], True, FakeClient))
    again = asyncio.run(stage_thread(sentinel, env["checkpoint_id"], True, FakeClient))
    assert again["status"] == "needs_reconciliation"
    assert again["target_thread"] == "target-1"
    assert len(FakeClient.calls) == 2
    assert sentinel.store.active(payload["project_id"]) is None


def test_crash_left_creating_is_not_retried(sentinel, payload):
    enable(sentinel)
    env = sentinel.checkpoint(payload, "1")
    assert sentinel.store.begin_migration(env["checkpoint_id"])["fresh"]
    result = asyncio.run(stage_thread(sentinel, env["checkpoint_id"], True, FakeClient))
    assert result["status"] == "creating"
    assert result["duplicate_suppressed"]
    assert FakeClient.calls == []
