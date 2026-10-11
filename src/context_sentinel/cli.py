import argparse
import asyncio
import json
from pathlib import Path

from .adapters import capabilities, probe, stage_thread
from .service import Sentinel, load_config


def main():
    parser = argparse.ArgumentParser(description="Context Sentinel: health, checkpoints and verified restore")
    parser.add_argument("--config")
    commands = parser.add_subparsers(dest="command", required=True)
    health = commands.add_parser("health")
    health.add_argument("signals", help="JSON signals file")
    save = commands.add_parser("checkpoint")
    save.add_argument("payload", help="JSON handoff file")
    save.add_argument("--key", required=True)
    commands.add_parser("import").add_argument("envelope")
    for name in ("verify", "handoff", "prepare", "repair-export"):
        commands.add_parser(name).add_argument("checkpoint_id")
    restore = commands.add_parser("restore")
    restore.add_argument("ack", help="JSON RestoreAck file")
    restore.add_argument("--previous", default=None, help="Expected old checkpoint ID; omit for initial restore")
    cap = commands.add_parser("capabilities")
    cap.add_argument("host", choices=["chatgpt", "codex", "codex_app_server"])
    commands.add_parser("probe")
    opened = commands.add_parser("monitor-open")
    opened.add_argument("project_id")
    opened.add_argument("--key", required=True)
    commands.add_parser("observe").add_argument("observation", help="JSON observation file")
    commands.add_parser("monitor-status").add_argument("session_id")
    ack = commands.add_parser("monitor-ack")
    ack.add_argument("session_id")
    ack.add_argument("reminder_id")
    snooze = commands.add_parser("monitor-snooze")
    snooze.add_argument("session_id")
    snooze.add_argument("seconds", type=int)
    commands.add_parser("watch", help="Read host observation JSONL from stdin and emit JSONL decisions; no chat scraping or timer")
    stage = commands.add_parser("stage-thread")
    stage.add_argument("checkpoint_id")
    stage.add_argument("--confirm-create", action="store_true", help="Explicit permission to create one read-only target; no model turn or UI switch")
    args = parser.parse_args()
    sentinel = Sentinel(load_config(args.config))
    read = lambda file: json.loads(Path(file).read_text(encoding="utf-8"))
    if args.command == "watch":
        import sys
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                decision = sentinel.monitor.observe(json.loads(line))
            except (ValueError, KeyError) as error:
                decision = {"error": type(error).__name__, "should_notify": False}
            print(json.dumps(decision, ensure_ascii=False), flush=True)
        return
    match args.command:
        case "health": result = sentinel.health(read(args.signals))
        case "checkpoint": result = sentinel.checkpoint(read(args.payload), args.key)
        case "import": result = sentinel.store.import_envelope(read(args.envelope))
        case "verify": result = sentinel.verify(args.checkpoint_id)
        case "handoff": result = sentinel.handoff_text(args.checkpoint_id)
        case "prepare": result = sentinel.prepare_restore(args.checkpoint_id)
        case "restore": result = sentinel.commit_restore(read(args.ack), args.previous)
        case "repair-export": result = sentinel.store.repair_export(args.checkpoint_id)
        case "capabilities": result = capabilities(args.host, sentinel)
        case "probe": result = asyncio.run(probe(sentinel))
        case "monitor-open": result = sentinel.monitor.open(args.project_id, args.key)
        case "observe": result = sentinel.monitor.observe(read(args.observation))
        case "monitor-status": result = sentinel.monitor.status(args.session_id)
        case "monitor-ack": result = sentinel.monitor.ack(args.session_id, args.reminder_id)
        case "monitor-snooze": result = sentinel.monitor.snooze(args.session_id, args.seconds)
        case "stage-thread": result = asyncio.run(stage_thread(sentinel, args.checkpoint_id, args.confirm_create))
    print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
