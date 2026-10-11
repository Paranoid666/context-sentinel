"""Real persistent reminders, including crash recovery, deduplication and escalation."""
import argparse
import json
import tempfile
from pathlib import Path
from context_sentinel.models import Config, MonitorPolicy
from context_sentinel.service import Sentinel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sentinel-reminders-") as temp:
        config = Config(data_dir=str(Path(temp) / "state"), workspace=temp,
                        monitor=MonitorPolicy(warn_turns=3, migrate_turns=5))
        service = Sentinel(config)
        sid = service.monitor.open("demo", "current-chat")["session_id"]
        reminders = []
        for turn in range(1, 6):
            observation = {"session_id": sid, "event_id": f"turn-{turn}-start", "turn_id": f"turn-{turn}", "signals": {}}
            result = service.monitor.observe(observation)
            if result["should_notify"]:
                reminders.append({"turn": turn, **result["reminder"]})
                print(result["reminder"]["message"])
                service.monitor.ack(sid, result["reminder"]["id"])
        restart = Sentinel(config)
        replay = restart.monitor.observe(observation)
        report = {"reminders": reminders, "observed_turns_after_restart": replay["observed_turns"],
                  "acknowledged_event_replay_does_not_notify": not replay["should_notify"],
                  "warning_then_escalation": [r["severity"] for r in reminders] == ["warning", "migration"],
                  "host_delivery": "demo printed messages and acknowledged them",
                  "desktop_background_monitor": "unsupported", "demo_thresholds": "3/5; defaults are 16/48"}
        assert report["warning_then_escalation"] and report["acknowledged_event_replay_does_not_notify"]
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
