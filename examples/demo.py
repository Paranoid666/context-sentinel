"""Run from project root: python examples/demo.py [--output report.json]. No external effects."""
import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path

from context_sentinel.models import Config
from context_sentinel.service import Sentinel
from context_sentinel.store import IntegrityError


def acknowledgement(env):
    return {"checkpoint_id": env["checkpoint_id"], "payload_sha256": env["payload_sha256"],
            **{f: env["payload"][f] for f in ("objective", "constraints", "pending", "next_action")}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    base = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix="sentinel-demo-") as temp:
        workspace = Path(temp)
        artifact = workspace / "result.txt"
        artifact.write_text("verified demo artifact", encoding="utf-8")
        sentinel = Sentinel(Config(data_dir=str(workspace / "state"), workspace=str(workspace)))
        payload = json.loads((base / "handoff.json").read_text(encoding="utf-8"))
        payload["artifacts"] = [{"path": "result.txt", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]
        risk = sentinel.health(json.loads((base / "signals.json").read_text(encoding="utf-8")))
        initial = sentinel.checkpoint(payload, "demo-v1")
        retry = sentinel.checkpoint(payload, "demo-v1")
        restored = sentinel.commit_restore(acknowledgement(initial), None)
        updated = copy.deepcopy(payload)
        updated["pending"] = ["Run independent evidence verification"]
        second = sentinel.checkpoint(updated, "demo-v2")
        artifact.write_text("unexpected change", encoding="utf-8")
        try:
            sentinel.commit_restore(acknowledgement(second), initial["checkpoint_id"])
            raise AssertionError("tampered artifact was accepted")
        except IntegrityError:
            rollback = sentinel.store.active(payload["project_id"]) == initial["checkpoint_id"]
        artifact.write_text("verified demo artifact", encoding="utf-8")
        final = sentinel.commit_restore(acknowledgement(second), initial["checkpoint_id"])
        receiver = Sentinel(Config(data_dir=str(workspace / "receiver"), workspace=str(workspace)))
        receiver.store.import_envelope(second)
        imported = receiver.commit_restore(acknowledgement(second), None)
        report = {"health": risk, "idempotent_retry": initial["checkpoint_id"] == retry["checkpoint_id"],
                  "initial_restore": restored["status"], "verification_failure_preserved_old_state": rollback,
                  "recovery_after_artifact_repair": final["status"], "portable_import_restore": imported["status"],
                  "model_or_ui_control": "unsupported; not attempted"}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
