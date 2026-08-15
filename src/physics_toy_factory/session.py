"""Locked process-local state for one product session and one active run."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from physics_toy_factory.errors import ProductError, conflict
from physics_toy_factory.models import (
    RunKind,
    RunLink,
    RunOutcome,
    SessionRecord,
    SessionState,
    StartEnvelope,
)


def _new_session_id() -> str:
    return f"session-{uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PreviewBinding:
    """Private server-side identity for the one current iframe load."""

    preview_id: str
    run_id: str
    revision: str
    shell_served: bool = False


class SessionService:
    """Own every read-modify-write operation on the product session."""

    def __init__(self, *, reset_required: bool = False) -> None:
        self._lock = asyncio.Lock()
        state = SessionState.RESET_REQUIRED if reset_required else SessionState.EMPTY
        self._record = SessionRecord(session_id=_new_session_id(), state=state)
        self._preview: PreviewBinding | None = None

    async def snapshot(self) -> SessionRecord:
        """Return a detached state value safe for response serialization."""

        async with self._lock:
            return self._record.model_copy(deep=True)

    async def owns(self, run_id: str) -> bool:
        """Check membership against product-owned links, never browser claims."""

        async with self._lock:
            return any(link.run_id == run_id for link in self._record.runs)

    async def require_owned(self, run_id: str) -> RunLink:
        """Return a detached linked run or a stable not-found response."""

        async with self._lock:
            for link in self._record.runs:
                if link.run_id == run_id:
                    return link.model_copy(deep=True)
        raise ProductError(404, "run_not_found", "Run does not belong to this session.")

    async def start(
        self,
        *,
        kind: RunKind,
        prompt: str,
        starter: Callable[[], Awaitable[str]],
    ) -> StartEnvelope:
        """Atomically validate, call S17, and publish an accepted run link."""

        async with self._lock:
            parent_run_id: str | None = None
            if self._record.active_run_id is not None:
                raise conflict("run_active", "A run is already active.")
            if kind is RunKind.CREATE:
                if self._record.state is not SessionState.EMPTY:
                    raise conflict("create_not_allowed", "Reset before creating a new toy.")
            elif kind is RunKind.FOLLOW_UP:
                if self._record.state is not SessionState.READY:
                    raise conflict("follow_up_not_ready", "Create and verify a toy before modifying it.")
                if self._record.follow_up_used:
                    raise conflict("follow_up_used", "The one follow-up has already been used.")
                parent_run_id = self._latest_ready_run_id()
                if parent_run_id is None:
                    raise conflict("follow_up_not_ready", "No verified run is available to modify.")
            else:
                raise ProductError(422, "unsupported_run_kind", "Run kind is not available here.")

            try:
                run_id = await starter()
            except ProductError as exc:
                if getattr(exc, "ambiguous", False):
                    self._record.state = SessionState.RESET_REQUIRED
                    if kind is RunKind.FOLLOW_UP:
                        self._record.follow_up_used = True
                raise

            if any(link.run_id == run_id for link in self._record.runs):
                self._record.state = SessionState.RESET_REQUIRED
                raise ProductError(502, "duplicate_run_id", "S17 returned a duplicate run identifier.")

            link = RunLink(
                run_id=run_id,
                kind=kind,
                parent_run_id=parent_run_id,
                user_prompt=prompt,
                started_at=_now(),
            )
            self._record.runs.append(link)
            self._preview = None
            self._record.active_run_id = run_id
            self._record.state = (
                SessionState.RUNNING if kind is RunKind.CREATE else SessionState.MODIFYING
            )
            if kind is RunKind.FOLLOW_UP:
                self._record.follow_up_used = True
                self._record.current_sketch_sha256 = None
            return StartEnvelope(
                session_id=self._record.session_id,
                run_id=run_id,
                kind=kind,
                events_url=f"/api/runs/{run_id}/events",
            )

    async def finish(self, run_id: str, *, ready: bool, sketch_sha256: str | None) -> None:
        """Apply one terminal graph classification idempotently."""

        async with self._lock:
            link = next((item for item in self._record.runs if item.run_id == run_id), None)
            if link is None:
                raise ProductError(404, "run_not_found", "Run does not belong to this session.")
            if link.outcome is not RunOutcome.RUNNING:
                return
            link.finished_at = _now()
            self._record.active_run_id = None
            if ready and sketch_sha256 is not None:
                link.outcome = RunOutcome.READY
                link.verified_sketch_sha256 = sketch_sha256
                self._record.current_sketch_sha256 = sketch_sha256
                self._record.state = SessionState.READY
                self._record.browser_error = None
            else:
                link.outcome = RunOutcome.FAILED
                self._record.current_sketch_sha256 = None
                self._record.state = SessionState.FAILED
            self._preview = None

    async def bind_preview(self, *, preview_id: str, revision: str) -> PreviewBinding:
        """Bind a fresh iframe identity to the latest verified run and revision."""

        async with self._lock:
            if (
                self._record.state is not SessionState.READY
                or self._record.current_sketch_sha256 != revision
            ):
                raise conflict("preview_not_ready", "Only the current verified sketch can be previewed.")
            run = next(
                (
                    item
                    for item in reversed(self._record.runs)
                    if item.outcome is RunOutcome.READY
                    and item.verified_sketch_sha256 == revision
                ),
                None,
            )
            if run is None:
                raise conflict("preview_not_ready", "Only the current verified sketch can be previewed.")
            self._preview = PreviewBinding(
                preview_id=preview_id,
                run_id=run.run_id,
                revision=revision,
            )
            return self._preview

    async def consume_preview_shell(
        self, *, preview_id: str, revision: str
    ) -> PreviewBinding:
        """Allow exactly one shell response for a freshly issued preview identity."""

        async with self._lock:
            binding = self._preview
            if (
                binding is None
                or binding.preview_id != preview_id
                or binding.revision != revision
                or binding.shell_served
                or self._record.state is not SessionState.READY
                or self._record.current_sketch_sha256 != revision
            ):
                raise conflict("preview_not_ready", "Only the current verified preview is available.")
            opened = PreviewBinding(
                preview_id=binding.preview_id,
                run_id=binding.run_id,
                revision=binding.revision,
                shell_served=True,
            )
            self._preview = opened
            return opened

    async def require_preview(self, *, preview_id: str, revision: str) -> PreviewBinding:
        """Reject stale, unopened, mismatched, or no-longer-ready asset requests."""

        async with self._lock:
            binding = self._preview
            if (
                binding is None
                or binding.preview_id != preview_id
                or binding.revision != revision
                or not binding.shell_served
                or self._record.state is not SessionState.READY
                or self._record.current_sketch_sha256 != revision
            ):
                raise conflict("preview_not_ready", "Only the current verified preview is available.")
            return binding

    async def record_browser_error(
        self, *, run_id: str, preview_id: str, error: dict[str, object]
    ) -> SessionRecord:
        """Fail the current preview only when its server-bound identity matches."""

        async with self._lock:
            binding = self._preview
            if (
                binding is None
                or binding.preview_id != preview_id
                or binding.run_id != run_id
                or not binding.shell_served
                or self._record.state is not SessionState.READY
            ):
                raise conflict("preview_mismatch", "The preview report is stale or does not match.")
            self._record.browser_error = error
            self._record.state = SessionState.FAILED
            self._preview = None
            return self._record.model_copy(deep=True)

    async def reset(self, resetter: Callable[[], Awaitable[None]]) -> SessionRecord:
        """Reset workspace and state under the same mutation lock."""

        async with self._lock:
            if self._record.active_run_id is not None:
                raise conflict("run_active", "Reset is forbidden while a run is active.")
            await resetter()
            self._record = SessionRecord(session_id=_new_session_id())
            self._preview = None
            return self._record.model_copy(deep=True)

    def _latest_ready_run_id(self) -> str | None:
        for link in reversed(self._record.runs):
            if link.outcome is RunOutcome.READY:
                return link.run_id
        return None
