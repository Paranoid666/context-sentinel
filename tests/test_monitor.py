import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pydantic import ValidationError
import pytest

from context_sentinel.models import MonitorPolicy
from context_sentinel.service import Sentinel
from context_sentinel.store import IntegrityError


def setup(sentinel, **policy):
    sentinel.config.monitor = MonitorPolicy(warn_turns=3, migrate_turns=10, **policy)
    now = [1000.0]
    sentinel.monitor.clock = lambda: now[0]
    sid = sentinel.monitor.open("project", "chat-1")["session_id"]
    return sid, now


def observe(sentinel, sid, turn, **extras):
    return sentinel.monitor.observe({"session_id": sid, "event_id": f"e-{turn}", "turn_id": f"t-{turn}", **extras})


def test_periodic_reminder_before_user_requests_check(sentinel):
    sid, _ = setup(sentinel)
    assert not observe(sentinel, sid, 1)["should_notify"]
    assert not observe(sentinel, sid, 2)["should_notify"]
    result = observe(sentinel, sid, 3)
    assert result["should_notify"]
    assert result["reminder"]["severity"] == "warning"
    assert result["health"]["token_utilization"] is None
    assert sentinel.store.active("project") is None


def test_session_and_event_retries_are_idempotent(sentinel):
    sid, _ = setup(sentinel)
    assert sentinel.monitor.open("project", "chat-1")["session_id"] == sid
    first = observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    repeated = observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    assert repeated["replayed"] and repeated["observed_turns"] == 1
    assert repeated["reminder"]["id"] == first["reminder"]["id"]
    with pytest.raises(IntegrityError):
        observe(sentinel, sid, 1, signals={"failed_verifications": 2})


def test_hooks_in_same_turn_do_not_inflate_count(sentinel):
    sid, _ = setup(sentinel)
    observe(sentinel, sid, 1)
    result = sentinel.monitor.observe({"session_id": sid, "event_id": "verification", "turn_id": "t-1", "trigger": "verification_failure"})
    assert result["observed_turns"] == 1
    assert result["should_notify"]


def test_restart_keeps_unacknowledged_reminder(sentinel):
    sid, _ = setup(sentinel)
    result = observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    restarted = Sentinel(sentinel.config)
    assert restarted.monitor.status(sid)["reminder"]["id"] == result["reminder"]["id"]


def test_acknowledged_replay_is_quiet(sentinel):
    sid, _ = setup(sentinel)
    result = observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    rid = result["reminder"]["id"]
    assert sentinel.monitor.ack(sid, rid)["status"] == "acknowledged"
    assert sentinel.monitor.ack(sid, rid)["status"] == "already_acknowledged"
    assert not observe(sentinel, sid, 1, signals={"failed_verifications": 3})["should_notify"]


def test_same_level_requires_both_turn_and_time_cooldown(sentinel):
    sid, now = setup(sentinel, cooldown_turns=2, cooldown_seconds=60)
    for turn in range(1, 4):
        result = observe(sentinel, sid, turn)
    sentinel.monitor.ack(sid, result["reminder"]["id"])
    now[0] += 61
    assert not observe(sentinel, sid, 4)["should_notify"]
    assert observe(sentinel, sid, 5)["should_notify"]


def test_elapsed_turns_alone_do_not_end_cooldown(sentinel):
    sid, _ = setup(sentinel, cooldown_turns=1, cooldown_seconds=60)
    for turn in range(1, 4):
        result = observe(sentinel, sid, turn)
    sentinel.monitor.ack(sid, result["reminder"]["id"])
    assert not observe(sentinel, sid, 4)["should_notify"]


def test_escalation_bypasses_cooldown(sentinel):
    sid, _ = setup(sentinel)
    warning = observe(sentinel, sid, 1, trigger="verification_failure")
    sentinel.monitor.ack(sid, warning["reminder"]["id"])
    high = observe(sentinel, sid, 2, signals={"failed_verifications": 3})
    assert high["should_notify"] and high["reminder"]["severity"] == "migration"
    assert "新聊天" in high["reminder"]["message"]


def test_escalation_supersedes_pending_and_stale_ack_is_safe(sentinel):
    sid, _ = setup(sentinel)
    warning = observe(sentinel, sid, 1, trigger="verification_failure")
    high = observe(sentinel, sid, 2, signals={"failed_verifications": 3})
    assert sentinel.monitor.ack(sid, warning["reminder"]["id"])["status"] == "not_pending"
    assert sentinel.monitor.status(sid)["reminder"]["id"] == high["reminder"]["id"]


def test_recovery_withdraws_stale_reminder(sentinel):
    sid, _ = setup(sentinel)
    high = observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    assert not observe(sentinel, sid, 2)["should_notify"]
    assert sentinel.monitor.ack(sid, high["reminder"]["id"])["status"] == "not_pending"


def test_risk_downgrade_replaces_undelivered_migration(sentinel):
    sid, _ = setup(sentinel)
    high = observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    warning = observe(sentinel, sid, 2, trigger="verification_failure")
    assert warning["reminder"]["severity"] == "warning"
    assert warning["reminder"]["id"] != high["reminder"]["id"]
    assert sentinel.monitor.ack(sid, high["reminder"]["id"])["status"] == "not_pending"


def test_snooze_suppresses_escalation_then_resumes(sentinel):
    sid, now = setup(sentinel)
    sentinel.monitor.snooze(sid, 60)
    assert not observe(sentinel, sid, 1, signals={"failed_verifications": 3})["should_notify"]
    now[0] += 61
    assert observe(sentinel, sid, 2, signals={"failed_verifications": 3})["should_notify"]


def test_pending_is_hidden_while_snoozed(sentinel):
    sid, _ = setup(sentinel)
    observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    sentinel.monitor.snooze(sid, 60)
    assert not sentinel.monitor.status(sid)["should_notify"]
    sentinel.monitor.snooze(sid, 0)
    assert sentinel.monitor.status(sid)["should_notify"]


def test_disabled_monitor_does_not_generate(sentinel):
    sid, _ = setup(sentinel, enabled=False)
    result = observe(sentinel, sid, 1, signals={"failed_verifications": 3})
    assert not result["should_notify"] and result["reminder"] is None


def test_separate_chats_have_separate_counters(sentinel):
    sid, _ = setup(sentinel)
    observe(sentinel, sid, 1)
    fresh = sentinel.monitor.open("project", "chat-2")["session_id"]
    assert fresh != sid
    assert sentinel.monitor.status(fresh)["observed_turns"] == 0


def test_concurrent_observations_create_one_reminder(sentinel):
    sid, _ = setup(sentinel)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: observe(sentinel, sid, 1, signals={"failed_verifications": 3}), range(8)))
    assert len({r["reminder"]["id"] for r in results}) == 1
    assert sentinel.monitor.status(sid)["observed_turns"] == 1


@pytest.mark.parametrize("seconds", [-1, 86401, True, 1.5])
def test_invalid_snooze_rejected(sentinel, seconds):
    sid, _ = setup(sentinel)
    with pytest.raises(ValueError):
        sentinel.monitor.snooze(sid, seconds)


def test_missing_session_rejected(sentinel):
    with pytest.raises(KeyError):
        observe(sentinel, "unknown", 1)


def test_policy_and_observation_validation(sentinel):
    with pytest.raises(ValidationError):
        MonitorPolicy(warn_turns=20, migrate_turns=10)
    sid, _ = setup(sentinel)
    with pytest.raises(ValidationError):
        observe(sentinel, sid, 1, trigger="invented_background_callback")


def test_jsonl_host_bridge_reports_risk_without_ui(tmp_path, sentinel):
    sid, _ = setup(sentinel)
    config = tmp_path / "monitor.toml"
    config.write_text('data_dir = ' + json.dumps(sentinel.config.data_dir) + '\nworkspace = ' + json.dumps(str(tmp_path)) + '\n', encoding="utf-8")
    event = {"session_id": sid, "event_id": "host-event", "turn_id": "host-turn", "signals": {"failed_verifications": 3}}
    command = [sys.executable, "-m", "context_sentinel.cli", "--config", str(config), "watch"]
    result = subprocess.run(command, input=json.dumps(event) + '\ninvalid-json\n', text=True, encoding="utf-8",
                            capture_output=True, check=True, timeout=20, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    output = [json.loads(line) for line in result.stdout.splitlines()]
    assert output[0]["should_notify"] and output[0]["background_monitor"] == "unsupported"
    assert output[1]["error"] == "JSONDecodeError"
