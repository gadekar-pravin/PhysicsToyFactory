"""Typed product state and browser-facing API models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RunKind(StrEnum):
    """Why an S17 run belongs to the product session."""

    CREATE = "create"
    FOLLOW_UP = "follow_up"
    REPAIR_PROOF = "repair_proof"


class RunOutcome(StrEnum):
    """Product interpretation of one linked run."""

    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class SessionState(StrEnum):
    """Allowed states for the single process-local product session."""

    EMPTY = "empty"
    RUNNING = "running"
    READY = "ready"
    MODIFYING = "modifying"
    FAILED = "failed"
    RESET_REQUIRED = "reset_required"


class RunLink(BaseModel):
    """The product-owned link from a browser action to a durable S17 run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    kind: RunKind
    parent_run_id: str | None = None
    user_prompt: str
    started_at: datetime
    finished_at: datetime | None = None
    outcome: RunOutcome = RunOutcome.RUNNING
    verified_sketch_sha256: str | None = None


class SessionRecord(BaseModel):
    """The one session owned by a product process."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, frozen=True)
    session_id: str
    state: SessionState = SessionState.EMPTY
    active_run_id: str | None = None
    current_sketch_sha256: str | None = None
    follow_up_used: bool = False
    browser_error: dict[str, object] | None = None
    runs: list[RunLink] = Field(default_factory=list)


class PromptBody(BaseModel):
    """A bounded request whose configured maximum is applied by the route."""

    model_config = ConfigDict(extra="forbid")

    prompt: str


class PreviewLeaseBody(BaseModel):
    """Request one browser cage for the exact verified revision."""

    model_config = ConfigDict(extra="forbid")

    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class BrowserErrorBody(BaseModel):
    """Bounded runtime failure reported by the trusted preview shell."""

    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=0, le=1_000_000)
    column: int = Field(ge=0, le=1_000_000)


class StartEnvelope(BaseModel):
    """The 202 response returned before S17 execution completes."""

    session_id: str
    run_id: str
    kind: RunKind
    events_url: str


class CodeResponse(BaseModel):
    """The only generated source path exposed by the product."""

    path: str = "sketch.js"
    content: str
    bytes: int
    sha256: str
    verified: bool
    verified_run_id: str | None
