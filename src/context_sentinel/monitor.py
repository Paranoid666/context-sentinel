"""Persistent observation hooks and reminder outbox. No chat scraping or background UI control."""
import json
import time
import uuid

from .health import assess
from .models import Config, Observation
from .store import IntegrityError, Store, canonical, digest


class Monitor:
    def __init__(self, store: Store, config: Config, clock=time.time):
        self.store, self.config, self.clock = store, config, clock
        with store.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS monitor_sessions(
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, request_key TEXT NOT NULL,
                    created REAL NOT NULL, turn_count INTEGER NOT NULL DEFAULT 0,
                    report TEXT, pending_id TEXT, last_ack_at REAL NOT NULL DEFAULT 0,
                    last_ack_turn INTEGER NOT NULL DEFAULT 0, last_ack_level INTEGER NOT NULL DEFAULT 0,
                    snoozed_until REAL NOT NULL DEFAULT 0, UNIQUE(project,request_key));
                CREATE TABLE IF NOT EXISTS monitor_events(
                    session TEXT NOT NULL, event TEXT NOT NULL, hash TEXT NOT NULL,
                    PRIMARY KEY(session,event));
                CREATE TABLE IF NOT EXISTS monitor_turns(
                    session TEXT NOT NULL, turn TEXT NOT NULL, PRIMARY KEY(session,turn));
                CREATE TABLE IF NOT EXISTS monitor_reminders(
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, level INTEGER NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            """)

    def open(self, project_id: str, request_key: str) -> dict:
        for value in (project_id, request_key):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError("project_id and request_key must contain 1..128 characters")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO monitor_sessions(id,project,request_key,created) VALUES(?,?,?,?)",
                         (uuid.uuid4().hex, project_id, request_key, self.clock()))
            row = conn.execute("SELECT * FROM monitor_sessions WHERE project=? AND request_key=?", (project_id, request_key)).fetchone()
            return {"session_id": row["id"], "project_id": project_id,
                    "observed_turns": row["turn_count"], "host_thread_id": "unknown",
                    "note": "Local monitoring session only. Call observe at turn hooks; opening does not start a background monitor."}

    def _row(self, conn, session_id):
        row = conn.execute("SELECT * FROM monitor_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError("monitoring session not found; call monitor_open first")
        return row

    def _status(self, conn, session_id, replayed=False):
        row = self._row(conn, session_id)
        reminder = None
        if row["pending_id"]:
            alert = conn.execute("SELECT * FROM monitor_reminders WHERE id=?", (row["pending_id"],)).fetchone()
            if alert and alert["status"] == "pending":
                reminder = json.loads(alert["payload"])
        snoozed = self.clock() < row["snoozed_until"]
        return {"session_id": session_id, "observed_turns": row["turn_count"],
                "health": json.loads(row["report"]) if row["report"] else None,
                "reminder": reminder, "should_notify": bool(reminder) and not snoozed and self.config.monitor.enabled,
                "snoozed": snoozed, "replayed": replayed,
                "delivery": "host_must_display_then_ack", "background_monitor": "unsupported"}

    def status(self, session_id):
        with self.store.connect() as conn:
            return self._status(conn, session_id)

    def observe(self, payload: dict) -> dict:
        observation = Observation.model_validate(payload)
        checksum = digest(observation.model_dump())
        sid, now = observation.session_id, self.clock()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, sid)
            event = conn.execute("SELECT hash FROM monitor_events WHERE session=? AND event=?", (sid, observation.event_id)).fetchone()
            if event:
                if event["hash"] != checksum:
                    raise IntegrityError("event id reused with different observation")
                return self._status(conn, sid, replayed=True)
            conn.execute("INSERT INTO monitor_events VALUES(?,?,?)", (sid, observation.event_id, checksum))
            conn.execute("INSERT OR IGNORE INTO monitor_turns VALUES(?,?)", (sid, observation.turn_id))
            turns = conn.execute("SELECT count(*) FROM monitor_turns WHERE session=?", (sid,)).fetchone()[0]
            signals = observation.signals.model_copy(update={"turns": max(turns, observation.signals.turns)})
            report = assess(signals, self.config.policy)
            level = {"continue": 0, "checkpoint": 1, "prepare_handoff": 2}[report["action"]]
            # Count only hooks actually reported, not hidden history. Length is a continuity heuristic.
            if turns >= self.config.monitor.migrate_turns:
                level = max(level, 2)
                report["reasons"].append("observed conversation reached migration turn threshold")
            elif turns >= self.config.monitor.warn_turns:
                level = max(level, 1)
                report["reasons"].append("observed conversation reached checkpoint turn threshold")
            if observation.trigger == "verification_failure":
                level = max(level, 1)
                report["reasons"].append("verification failure hook")
            report["recommended_action"] = ["continue", "checkpoint", "prepare_handoff"][level]
            conn.execute("UPDATE monitor_sessions SET turn_count=?,report=? WHERE id=?", (turns, canonical(report), sid))
            pending = conn.execute("SELECT * FROM monitor_reminders WHERE id=?", (row["pending_id"],)).fetchone() if row["pending_id"] else None
            if pending and level < pending["level"]:
                conn.execute("UPDATE monitor_reminders SET status='withdrawn' WHERE id=?", (pending["id"],))
                conn.execute("UPDATE monitor_sessions SET pending_id=NULL WHERE id=?", (sid,))
                pending = None
            if level == 0:
                if pending:
                    conn.execute("UPDATE monitor_reminders SET status='withdrawn' WHERE id=?", (pending["id"],))
                conn.execute("UPDATE monitor_sessions SET pending_id=NULL,last_ack_level=0 WHERE id=?", (sid,))
            elif self.config.monitor.enabled and now >= row["snoozed_until"]:
                escalation = level > (pending["level"] if pending else row["last_ack_level"])
                cooled = (turns - row["last_ack_turn"] >= self.config.monitor.cooldown_turns
                          and now - row["last_ack_at"] >= self.config.monitor.cooldown_seconds)
                if (pending and escalation) or (not pending and (escalation or cooled)):
                    if pending:
                        conn.execute("UPDATE monitor_reminders SET status='superseded' WHERE id=?", (pending["id"],))
                    rid = uuid.uuid4().hex
                    message = ("建议换到新聊天继续，先保存并验证交接清单；当前聊天会保留，窗口需要手动切换。"
                               if level == 2 else "建议先保存当前进度并核对任务约束；如果还要继续长任务，可以准备交接到新聊天。")
                    alert = {"id": rid, "severity": "migration" if level == 2 else "warning",
                             "message": message, "reasons": report["reasons"],
                             "next_action": report["recommended_action"], "automatic_switch": False}
                    conn.execute("INSERT INTO monitor_reminders VALUES(?,?,?,'pending',?,?)", (rid, sid, level, canonical(alert), now))
                    conn.execute("UPDATE monitor_sessions SET pending_id=? WHERE id=?", (rid, sid))
            return self._status(conn, sid)

    def ack(self, session_id: str, reminder_id: str) -> dict:
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, session_id)
            alert = conn.execute("SELECT * FROM monitor_reminders WHERE id=? AND session=?", (reminder_id, session_id)).fetchone()
            if alert is None:
                raise KeyError("reminder not found in session")
            if alert["status"] == "acknowledged":
                return {"status": "already_acknowledged", "reminder_id": reminder_id}
            if row["pending_id"] != reminder_id or alert["status"] != "pending":
                return {"status": "not_pending", "reminder_id": reminder_id}
            conn.execute("UPDATE monitor_reminders SET status='acknowledged' WHERE id=?", (reminder_id,))
            conn.execute("UPDATE monitor_sessions SET pending_id=NULL,last_ack_at=?,last_ack_turn=?,last_ack_level=? WHERE id=?",
                         (self.clock(), row["turn_count"], alert["level"], session_id))
            return {"status": "acknowledged", "reminder_id": reminder_id,
                    "note": "Delivery acknowledgement only; not consent to create a thread or a restore acknowledgement."}

    def snooze(self, session_id: str, seconds: int) -> dict:
        if type(seconds) is not int or not 0 <= seconds <= 86400:
            raise ValueError("snooze seconds must be an integer in 0..86400")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._row(conn, session_id)
            until = self.clock() + seconds
            conn.execute("UPDATE monitor_sessions SET snoozed_until=? WHERE id=?", (until, session_id))
        return {"status": "snoozed" if seconds else "resumed", "until": until,
                "note": "Only snooze at the user's request; no background notification is scheduled."}
