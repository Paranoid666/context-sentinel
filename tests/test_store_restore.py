import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import ValidationError
from context_sentinel.models import Config
from context_sentinel.service import Sentinel
from context_sentinel.store import IntegrityError
from conftest import ack_for


def test_save_retry_and_key_conflict(sentinel, payload):
    env = sentinel.checkpoint(payload, "same")
    assert sentinel.checkpoint(payload, "same") == env
    payload["objective"] = "different"
    with pytest.raises(IntegrityError):
        sentinel.checkpoint(payload, "same")
    assert sentinel.store.active(payload["project_id"]) is None


def test_concurrent_idempotency(sentinel, payload):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: sentinel.checkpoint(payload, "concurrent"), range(8)))
    assert len({r["checkpoint_id"] for r in results}) == 1


def test_independent_store_instances_serialize_exports(sentinel, payload):
    stores = [Sentinel(sentinel.config) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: stores[i % 4].checkpoint(payload, "multi-store"), range(20)))
    assert len({r["checkpoint_id"] for r in results}) == 1
    assert sentinel.verify(results[0]["checkpoint_id"])["valid"]


def test_concurrent_restore_has_one_winner(sentinel, payload):
    first = sentinel.checkpoint(payload, "1")
    sentinel.commit_restore(ack_for(first), None)
    second_payload = copy.deepcopy(payload)
    second_payload["objective"] = "second"
    third_payload = copy.deepcopy(payload)
    third_payload["objective"] = "third"
    candidates = [sentinel.checkpoint(second_payload, "2"), sentinel.checkpoint(third_payload, "3")]
    def commit(env):
        try:
            sentinel.commit_restore(ack_for(env), first["checkpoint_id"])
            return "won"
        except IntegrityError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(commit, candidates))
    assert sorted(outcomes) == ["rejected", "won"]
    assert sentinel.store.active(payload["project_id"]) in [c["checkpoint_id"] for c in candidates]


def test_json_tamper_and_explicit_repair(sentinel, payload):
    env = sentinel.checkpoint(payload, "1")
    dest = sentinel.store.exports / (env["checkpoint_id"] + ".json")
    bad = copy.deepcopy(env)
    bad["payload"]["objective"] = "injected"
    dest.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(IntegrityError):
        sentinel.prepare_restore(env["checkpoint_id"])
    assert sentinel.store.repair_export(env["checkpoint_id"]) == env
    assert sentinel.verify(env["checkpoint_id"])["valid"]


def test_missing_export_recovers_from_db(sentinel, payload):
    env = sentinel.checkpoint(payload, "1")
    (sentinel.store.exports / (env["checkpoint_id"] + ".json")).unlink()
    assert sentinel.store.get(env["checkpoint_id"]) == env


def test_export_failure_retry_preserves_checkpoint(sentinel, payload, monkeypatch):
    exporter = sentinel.store._export
    monkeypatch.setattr(sentinel.store, "_export", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        sentinel.checkpoint(payload, "stable")
    monkeypatch.setattr(sentinel.store, "_export", exporter)
    env = sentinel.checkpoint(payload, "stable")
    with sentinel.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 1
    assert sentinel.verify(env["checkpoint_id"])["valid"]


def test_sqlite_corruption_fails_closed(sentinel, payload):
    env = sentinel.checkpoint(payload, "1")
    with sentinel.store.connect() as conn:
        altered = copy.deepcopy(payload)
        altered["objective"] = "corrupt"
        conn.execute("UPDATE checkpoints SET payload=? WHERE id=?", (json.dumps(altered), env["checkpoint_id"]))
    with pytest.raises(IntegrityError):
        sentinel.prepare_restore(env["checkpoint_id"])


def test_ack_failure_keeps_old_state_and_restore_is_idempotent(sentinel, payload):
    initial = sentinel.checkpoint(payload, "1")
    sentinel.commit_restore(ack_for(initial), None)
    assert sentinel.commit_restore(ack_for(initial), None)["status"] == "committed"
    payload["objective"] = "new goal"
    second = sentinel.checkpoint(payload, "2")
    ack = ack_for(second)
    ack["constraints"] = []
    with pytest.raises(IntegrityError):
        sentinel.commit_restore(ack, initial["checkpoint_id"])
    assert sentinel.store.active(payload["project_id"]) == initial["checkpoint_id"]
    with pytest.raises(IntegrityError):
        sentinel.commit_restore(ack_for(second), None)
    assert sentinel.commit_restore(ack_for(second), initial["checkpoint_id"])["status"] == "committed"


def test_artifact_change_blocks_restore(sentinel, payload, tmp_path):
    file = tmp_path / "artifact.txt"
    file.write_bytes(b"good")
    payload["artifacts"] = [{"path": "artifact.txt", "sha256": hashlib.sha256(b"good").hexdigest()}]
    env = sentinel.checkpoint(payload, "1")
    assert sentinel.verify(env["checkpoint_id"])["valid"]
    file.write_bytes(b"bad")
    with pytest.raises(IntegrityError):
        sentinel.commit_restore(ack_for(env), None)
    assert sentinel.store.active(payload["project_id"]) is None


@pytest.mark.parametrize("path", ["../outside", "C:/outside/file", "/outside"])
def test_artifact_path_escape_fails(sentinel, payload, path):
    payload["artifacts"] = [{"path": path, "sha256": "0" * 64}]
    env = sentinel.checkpoint(payload, "1")
    assert not sentinel.verify(env["checkpoint_id"])["valid"]


def test_portable_import_does_not_activate(sentinel, payload, tmp_path):
    env = sentinel.checkpoint(payload, "1")
    other = Sentinel(Config(data_dir=str(tmp_path / "receiver"), workspace=str(tmp_path)))
    assert other.store.import_envelope(env) == env
    assert other.store.import_envelope(env) == env
    assert other.store.active(payload["project_id"]) is None
    assert other.commit_restore(ack_for(env), None)["status"] == "committed"
    env["payload"]["objective"] = "tampered"
    with pytest.raises(IntegrityError):
        other.store.import_envelope(env)


def test_incomplete_ledger_and_unknown_schema_rejected(sentinel, payload):
    del payload["constraints"]
    with pytest.raises(ValidationError):
        sentinel.checkpoint(payload, "1")


def test_unsupported_schema_rejected(sentinel, payload):
    payload["schema_version"] = 2
    with pytest.raises(ValidationError):
        sentinel.checkpoint(payload, "future")


def test_ack_checksum_mismatch_does_not_activate(sentinel, payload):
    env = sentinel.checkpoint(payload, "1")
    ack = ack_for(env)
    ack["payload_sha256"] = "0" * 64
    with pytest.raises(IntegrityError):
        sentinel.commit_restore(ack, None)
    assert sentinel.store.active(payload["project_id"]) is None


def test_id_cannot_escape_store(sentinel):
    with pytest.raises(ValueError):
        sentinel.store.get("../etc/passwd")
