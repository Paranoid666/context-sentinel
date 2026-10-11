import copy
import json
from pathlib import Path
import pytest
from context_sentinel.models import Config
from context_sentinel.service import Sentinel


@pytest.fixture
def payload():
    return copy.deepcopy(json.loads((Path(__file__).parents[1] / "examples/handoff.json").read_text(encoding="utf-8")))


@pytest.fixture
def sentinel(tmp_path):
    return Sentinel(Config(data_dir=str(tmp_path / "state"), workspace=str(tmp_path)))


def ack_for(env):
    return {"checkpoint_id": env["checkpoint_id"], "payload_sha256": env["payload_sha256"],
            **{field: env["payload"][field] for field in ("objective", "constraints", "pending", "next_action")}}
