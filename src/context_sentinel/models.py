from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Signals(StrictModel):
    tokens_used: int | None = Field(default=None, ge=0)
    token_limit: int | None = Field(default=None, gt=0)
    turns: int = Field(default=0, ge=0)
    contradictions: int = Field(default=0, ge=0)
    failed_verifications: int = Field(default=0, ge=0)
    repeated_errors: int = Field(default=0, ge=0)
    goal_drift: float = Field(default=0.0, ge=0, le=1)
    source: Literal["host", "user", "estimate"] = "estimate"

    @model_validator(mode="after")
    def paired_tokens(self):
        if (self.tokens_used is None) != (self.token_limit is None):
            raise ValueError("tokens_used and token_limit must be supplied together")
        return self


class Policy(StrictModel):
    warn_score: int = Field(default=35, ge=1, le=99)
    migrate_score: int = Field(default=65, ge=2, le=100)
    warn_utilization: float = Field(default=0.65, gt=0, lt=1)
    migrate_utilization: float = Field(default=0.85, gt=0, le=1)
    turn_warning: int = Field(default=40, gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.warn_score >= self.migrate_score or self.warn_utilization >= self.migrate_utilization:
            raise ValueError("warning thresholds must precede migration thresholds")
        return self


class Fact(StrictModel):
    claim: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    status: Literal["verified", "unverified"] = "unverified"


class Artifact(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Handoff(StrictModel):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1, max_length=128)
    source_thread: str = Field(min_length=1, max_length=256)
    objective: str = Field(min_length=1)
    constraints: list[str]
    decisions: list[str]
    facts: list[Fact]
    completed: list[str]
    pending: list[str]
    next_action: str = Field(min_length=1)
    artifacts: list[Artifact]
    unresolved: list[str]
    authorizations: list[str]
    # All fields are mandatory so an absent ledger cannot silently become an empty ledger.


class RestoreAck(StrictModel):
    checkpoint_id: str
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    objective: str
    constraints: list[str]
    pending: list[str]
    next_action: str


class Config(StrictModel):
    data_dir: str = ".context-sentinel"
    workspace: str = "."
    policy: Policy = Field(default_factory=Policy)
    app_server_enabled: bool = False
    codex_executable: str = "codex"

