import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Handoff


class IntegrityError(ValueError):
    pass


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    """SQLite is authoritative; JSON is an atomic, repairable export, not a second commit log."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.exports = self.root / "checkpoints"
        self.exports.mkdir(exist_ok=True)
        self.db = self.root / "state.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS checkpoints(
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, request_key TEXT NOT NULL,
                    payload TEXT NOT NULL, hash TEXT NOT NULL, created TEXT NOT NULL,
                    UNIQUE(project, request_key));
                CREATE TABLE IF NOT EXISTS active(project TEXT PRIMARY KEY, checkpoint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
                    checkpoint TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS migrations(
                    checkpoint TEXT PRIMARY KEY, state TEXT NOT NULL, target TEXT, detail TEXT);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _export(self, envelope: dict):
        # Windows disallows replacement while another process has the destination open.
        # Serialize cooperating readers and writers through SQLite, across Store instances/processes.
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._write_export(envelope)

    def _write_export(self, envelope: dict):
        dest = self.exports / (envelope["checkpoint_id"] + ".json")
        temp = dest.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as f:
                f.write(canonical(envelope))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, dest)
        finally:
            temp.unlink(missing_ok=True)

    def save(self, handoff: Handoff, request_key: str) -> dict:
        if not request_key or len(request_key) > 256:
            raise ValueError("request_key must contain 1..256 characters")
        payload = handoff.model_dump()
        checksum = digest(payload)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM checkpoints WHERE project=? AND request_key=?",
                               (handoff.project_id, request_key)).fetchone()
            if row:
                if row["hash"] != checksum:
                    raise IntegrityError("idempotency key reused with different content")
                checkpoint_id = row["id"]
            else:
                checkpoint_id = uuid.uuid4().hex
                conn.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?)",
                             (checkpoint_id, handoff.project_id, request_key, canonical(payload), checksum, now))
                conn.execute("INSERT INTO events(project,kind,checkpoint,created) VALUES(?,?,?,?)",
                             (handoff.project_id, "checkpoint_saved", checkpoint_id, now))
        # A failed export does not lose the SQLite checkpoint; retry with the same key repairs it.
        envelope = self.get(checkpoint_id, verify_export=False)
        self._export(envelope)
        return envelope

    def get(self, checkpoint_id: str, verify_export: bool = True) -> dict:
        if len(checkpoint_id) != 32 or any(c not in "0123456789abcdef" for c in checkpoint_id):
            raise ValueError("invalid checkpoint id")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if row is None:
            raise KeyError("checkpoint not found")
        payload = json.loads(row["payload"])
        Handoff.model_validate(payload)
        if digest(payload) != row["hash"]:
            raise IntegrityError("SQLite payload checksum mismatch")
        envelope = {"checkpoint_id": row["id"], "created_at": row["created"],
                    "payload_sha256": row["hash"], "payload": payload}
        if verify_export:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                dest = self.exports / (checkpoint_id + ".json")
                if not dest.exists():
                    self._write_export(envelope)
                elif json.loads(dest.read_text(encoding="utf-8")) != envelope:
                    raise IntegrityError("JSON export differs from SQLite; refusing restore")
        return envelope

    def repair_export(self, checkpoint_id: str) -> dict:
        envelope = self.get(checkpoint_id, verify_export=False)
        self._export(envelope)
        return envelope

    def import_envelope(self, envelope: dict) -> dict:
        if set(envelope) != {"checkpoint_id", "created_at", "payload_sha256", "payload"}:
            raise IntegrityError("unexpected envelope fields")
        cid = envelope["checkpoint_id"]
        if not isinstance(cid, str) or len(cid) != 32 or any(c not in "0123456789abcdef" for c in cid):
            raise IntegrityError("invalid imported checkpoint id")
        handoff = Handoff.model_validate(envelope["payload"])
        payload = handoff.model_dump()
        if digest(payload) != envelope["payload_sha256"]:
            raise IntegrityError("import checksum mismatch")
        created = envelope["created_at"]
        parsed = datetime.fromisoformat(created)
        if parsed.tzinfo is None:
            raise IntegrityError("created_at must have a timezone")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM checkpoints WHERE id=?", (cid,)).fetchone()
            if row:
                if row["hash"] != envelope["payload_sha256"] or row["created"] != created:
                    raise IntegrityError("import conflicts with existing checkpoint")
            else:
                conn.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?)",
                             (cid, handoff.project_id, "import:" + cid, canonical(payload), envelope["payload_sha256"], created))
                conn.execute("INSERT INTO events(project,kind,checkpoint,created) VALUES(?,?,?,?)",
                             (handoff.project_id, "checkpoint_imported", cid, datetime.now(timezone.utc).isoformat()))
        # Imports remain inactive until artifact verification and restore acknowledgement.
        self._export(envelope)
        return envelope

    def active(self, project: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT checkpoint FROM active WHERE project=?", (project,)).fetchone()
        return row[0] if row else None

    def activate(self, project: str, checkpoint_id: str, expected_previous: str | None):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT checkpoint FROM active WHERE project=?", (project,)).fetchone()
            previous = row[0] if row else None
            if previous == checkpoint_id:
                return
            if previous != expected_previous:
                raise IntegrityError("active state changed; revalidate before committing")
            conn.execute("INSERT INTO active VALUES(?,?) ON CONFLICT(project) DO UPDATE SET checkpoint=excluded.checkpoint",
                         (project, checkpoint_id))
            conn.execute("INSERT INTO events(project,kind,checkpoint,created) VALUES(?,?,?,?)",
                         (project, "restore_committed", checkpoint_id, datetime.now(timezone.utc).isoformat()))

    def begin_migration(self, checkpoint_id: str) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO migrations VALUES(?, 'creating', NULL, NULL)", (checkpoint_id,))
            row = dict(conn.execute("SELECT * FROM migrations WHERE checkpoint=?", (checkpoint_id,)).fetchone())
            # Caller needs to know whether it owns the new operation; never retry an ambiguous remote create.
            fresh = conn.execute("SELECT changes()").fetchone()[0] == 1
            return {**row, "fresh": fresh}

    def migration_update(self, checkpoint_id: str, state: str, target: str | None, detail: str):
        with self.connect() as conn:
            conn.execute("UPDATE migrations SET state=?,target=?,detail=? WHERE checkpoint=?",
                         (state, target, detail, checkpoint_id))

