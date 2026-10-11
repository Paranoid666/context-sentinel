from .models import Policy, Signals


def assess(signals: Signals, policy: Policy) -> dict:
    """Heuristic risk indicators, never a hallucination probability."""
    reasons = []
    score = min(45, signals.contradictions * 15)
    score += min(75, signals.failed_verifications * 25)
    score += min(30, signals.repeated_errors * 10)
    score += round(signals.goal_drift * 30)
    if signals.contradictions:
        reasons.append("contradictions reported")
    if signals.failed_verifications:
        reasons.append("verification failures reported")
    if signals.repeated_errors:
        reasons.append("repeated errors reported")
    if signals.goal_drift:
        reasons.append("goal drift reported")
    if signals.turns >= policy.turn_warning:
        score += 10
        reasons.append("long conversation")
    utilization = None
    if signals.tokens_used is not None:
        utilization = signals.tokens_used / signals.token_limit
        if utilization >= policy.migrate_utilization:
            score = max(score, policy.migrate_score)
            reasons.append("context utilization above migration threshold")
        elif utilization >= policy.warn_utilization:
            score = max(score, policy.warn_score)
            reasons.append("context utilization above warning threshold")
    score = min(100, score)
    action = "prepare_handoff" if score >= policy.migrate_score else "checkpoint" if score >= policy.warn_score else "continue"
    return {"score": score, "action": action, "reasons": reasons,
            "token_utilization": utilization, "measurement_source": signals.source,
            "observability": "partial" if utilization is None else "reported",
            "automatic_switch": False,
            "note": "Heuristic only. Unknown token usage is not evidence of low risk; a fresh context does not guarantee correctness."}

