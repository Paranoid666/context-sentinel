import pytest
from pydantic import ValidationError
from context_sentinel.health import assess
from context_sentinel.models import Policy, Signals


@pytest.mark.parametrize("used,action", [(64, "continue"), (65, "checkpoint"), (85, "prepare_handoff"), (110, "prepare_handoff")])
def test_context_boundaries(used, action):
    assert assess(Signals(tokens_used=used, token_limit=100), Policy())["action"] == action


def test_unknown_telemetry_is_visible():
    result = assess(Signals(), Policy())
    assert result["token_utilization"] is None
    assert result["observability"] == "partial"
    assert not result["automatic_switch"]


def test_failures_trigger_even_without_tokens():
    result = assess(Signals(failed_verifications=2, contradictions=1), Policy())
    assert result["action"] == "prepare_handoff"


@pytest.mark.parametrize("bad", [{"tokens_used": 20}, {"token_limit": 100}, {"token_limit": 0, "tokens_used": 1}, {"goal_drift": float("nan")}, {"contradictions": -1}, {"turns": "20"}])
def test_invalid_signals_rejected(bad):
    with pytest.raises(ValidationError):
        Signals.model_validate(bad)


def test_policy_order_validation():
    with pytest.raises(ValidationError):
        Policy(warn_score=80, migrate_score=50)
