"""Durable, product-owned run history stored outside the resettable workspace."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from physics_toy_factory.errors import ProductError, conflict
from physics_toy_factory.models import RunLink, SessionRecord, SessionState

HISTORY_ID_PATTERN = re.compile(r"^history-[0-9a-f]{32}$")
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ArchivedSketch:
    """Hash-bound generated source retained for a verified historical run."""

    content: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class HistoryPage:
    """One bounded page of newest-first run summaries."""

    items: list[dict[str, Any]]
    next_cursor: str | None
    total: int


class HistoryStore:
    """Own the SQLite session record, run allowlist, and immutable snapshots."""

    def __init__(self, artifact_dir: Path, *, max_sketch_bytes: int) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self.path = artifact_dir / "history.sqlite3"
        self._max_sketch_bytes = max_sketch_bytes
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = FULL")
        self._initialize()

    def close(self) -> None:
        self._db.close()

    def _initialize(self) -> None:
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, SCHEMA_VERSION}:
            raise RuntimeError("unsupported Physics Toy Factory history schema")
        if version == SCHEMA_VERSION:
            return
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE current_session (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE run_history (
                    history_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    parent_run_id TEXT,
                    user_prompt TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    outcome TEXT NOT NULL,
                    graph_json TEXT,
                    graph_observed_at TEXT,
                    sketch_content BLOB,
                    sketch_bytes INTEGER,
                    sketch_sha256 TEXT,
                    browser_error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (
                        (sketch_content IS NULL AND sketch_bytes IS NULL AND sketch_sha256 IS NULL)
                        OR
                        (sketch_content IS NOT NULL AND sketch_bytes IS NOT NULL AND sketch_sha256 IS NOT NULL)
                    )
                );
                CREATE INDEX run_history_started_idx
                    ON run_history(started_at DESC, history_id DESC);
                CREATE INDEX run_history_session_idx ON run_history(session_id);
                PRAGMA user_version = 1;
                """
            )

    def load_or_create_session(self, default: SessionRecord) -> SessionRecord:
        row = self._db.execute(
            "SELECT record_json FROM current_session WHERE singleton = 1"
        ).fetchone()
        if row is not None:
            try:
                return SessionRecord.model_validate_json(row["record_json"])
            except (ValueError, TypeError) as exc:
                raise RuntimeError("saved Physics Toy Factory session is invalid") from exc
        self.save_session(default)
        return default.model_copy(deep=True)

    def save_session(self, record: SessionRecord) -> None:
        try:
            with self._db:
                self._write_session(record)
        except sqlite3.Error as exc:
            raise ProductError(
                503,
                "history_write_failed",
                "The factory session could not be saved safely.",
                retryable=True,
            ) from exc

    def add_run(self, record: SessionRecord, link: RunLink) -> str:
        history_id = f"history-{uuid4().hex}"
        moment = _now()
        try:
            with self._db:
                self._db.execute(
                    """
                    INSERT INTO run_history (
                        history_id, run_id, session_id, kind, parent_run_id, user_prompt,
                        started_at, finished_at, outcome, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history_id,
                        link.run_id,
                        record.session_id,
                        link.kind.value,
                        link.parent_run_id,
                        link.user_prompt,
                        link.started_at.isoformat(),
                        None,
                        link.outcome.value,
                        moment,
                        moment,
                    ),
                )
                self._write_session(record)
        except sqlite3.Error as exc:
            raise ProductError(
                503,
                "history_write_failed",
                "The accepted run could not be recorded durably. Reset after operator review.",
            ) from exc
        return history_id

    def update_graph(self, run_id: str, graph: dict[str, Any]) -> None:
        payload = _json(graph)
        try:
            with self._db:
                changed = self._db.execute(
                    """
                    UPDATE run_history
                       SET graph_json = ?, graph_observed_at = ?, updated_at = ?
                     WHERE run_id = ?
                    """,
                    (payload, _now(), _now(), run_id),
                ).rowcount
        except sqlite3.Error as exc:
            raise ProductError(
                503,
                "history_write_failed",
                "The latest run evidence could not be saved safely.",
                retryable=True,
            ) from exc
        if changed != 1:
            raise ProductError(409, "history_inconsistent", "Saved run ownership is inconsistent.")

    def finish_run(
        self,
        record: SessionRecord,
        link: RunLink,
        graph: dict[str, Any],
        sketch: ArchivedSketch | None,
    ) -> None:
        graph_json = _json(graph)
        content = sketch.content.encode("utf-8") if sketch is not None else None
        moment = _now()
        try:
            with self._db:
                changed = self._db.execute(
                    """
                    UPDATE run_history
                       SET finished_at = ?, outcome = ?, graph_json = ?, graph_observed_at = ?,
                           sketch_content = ?, sketch_bytes = ?, sketch_sha256 = ?, updated_at = ?
                     WHERE run_id = ?
                    """,
                    (
                        link.finished_at.isoformat() if link.finished_at else None,
                        link.outcome.value,
                        graph_json,
                        moment,
                        content,
                        sketch.bytes if sketch else None,
                        sketch.sha256 if sketch else None,
                        moment,
                        link.run_id,
                    ),
                ).rowcount
                if changed != 1:
                    raise sqlite3.IntegrityError("saved run is missing")
                self._write_session(record)
        except sqlite3.Error as exc:
            raise ProductError(
                503,
                "history_write_failed",
                "The completed run could not be saved safely. Reset after operator review.",
            ) from exc

    def record_browser_error(
        self, record: SessionRecord, run_id: str, error: dict[str, object]
    ) -> None:
        try:
            with self._db:
                changed = self._db.execute(
                    """
                    UPDATE run_history
                       SET browser_error_json = ?, updated_at = ?
                     WHERE run_id = ?
                    """,
                    (_json(error), _now(), run_id),
                ).rowcount
                if changed != 1:
                    raise ProductError(
                        409, "history_inconsistent", "Saved run ownership is inconsistent."
                    )
                self._write_session(record)
        except sqlite3.Error as exc:
            raise ProductError(
                503,
                "history_write_failed",
                "The preview failure could not be saved safely.",
                retryable=True,
            ) from exc

    def list_runs(self, *, limit: int, cursor: str | None, query: str) -> HistoryPage:
        before = self._decode_cursor(cursor) if cursor else None
        conditions: list[str] = []
        values: list[object] = []
        if before is not None:
            conditions.append("(started_at < ? OR (started_at = ? AND history_id < ?))")
            values.extend((before[0], before[0], before[1]))
        if query:
            conditions.append("(instr(lower(user_prompt), ?) > 0 OR instr(lower(run_id), ?) > 0)")
            lowered = query.lower()
            values.extend((lowered, lowered))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._db.execute(
            f"""
            SELECT * FROM run_history
            {where}
            ORDER BY started_at DESC, history_id DESC
            LIMIT ?
            """,
            (*values, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = [self._summary(row) for row in selected]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = self._encode_cursor(last["started_at"], last["history_id"])
        count_conditions = [item for item in conditions if not item.startswith("(started_at <")]
        count_values = values[3:] if before is not None else values
        count_where = f"WHERE {' AND '.join(count_conditions)}" if count_conditions else ""
        total = int(
            self._db.execute(
                f"SELECT count(*) FROM run_history {count_where}", tuple(count_values)
            ).fetchone()[0]
        )
        return HistoryPage(items, next_cursor, total)

    def detail(self, history_id: str) -> dict[str, Any]:
        row = self._row(history_id)
        graph = self._decode_object(row["graph_json"], "saved run graph")
        return {"history": self._summary(row), "graph": graph}

    def archived_sketch(self, history_id: str) -> ArchivedSketch:
        row = self._row(history_id)
        content = row["sketch_content"]
        expected_bytes = row["sketch_bytes"]
        expected_hash = row["sketch_sha256"]
        if not isinstance(content, bytes) or not isinstance(expected_bytes, int) or not isinstance(
            expected_hash, str
        ):
            raise ProductError(404, "history_preview_unavailable", "This saved run has no verified preview.")
        if not (1 <= len(content) <= self._max_sketch_bytes) or len(content) != expected_bytes:
            raise ProductError(409, "history_corrupt", "The saved sketch failed integrity validation.")
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_hash:
            raise ProductError(409, "history_corrupt", "The saved sketch failed integrity validation.")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductError(409, "history_corrupt", "The saved sketch is not valid UTF-8.") from exc
        return ArchivedSketch(text, len(content), digest)

    def delete(self, history_id: str, current: SessionRecord) -> None:
        row = self._row(history_id)
        if any(link.run_id == row["run_id"] for link in current.runs):
            raise conflict(
                "history_run_current",
                "Reset the current factory session before deleting this saved run.",
            )
        try:
            with self._db:
                changed = self._db.execute(
                    "DELETE FROM run_history WHERE history_id = ?", (history_id,)
                ).rowcount
        except sqlite3.Error as exc:
            raise ProductError(
                503,
                "history_write_failed",
                "The saved run could not be deleted safely.",
                retryable=True,
            ) from exc
        if changed != 1:
            raise ProductError(404, "history_not_found", "Saved run was not found.")

    def history_id_for_run(self, run_id: str) -> str | None:
        row = self._db.execute(
            "SELECT history_id FROM run_history WHERE run_id = ?", (run_id,)
        ).fetchone()
        return str(row["history_id"]) if row is not None else None

    def _write_session(self, record: SessionRecord) -> None:
        self._db.execute(
            """
            INSERT INTO current_session(singleton, record_json, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(singleton) DO UPDATE
            SET record_json = excluded.record_json, updated_at = excluded.updated_at
            """,
            (record.model_dump_json(), _now()),
        )

    def _row(self, history_id: str) -> sqlite3.Row:
        if not HISTORY_ID_PATTERN.fullmatch(history_id):
            raise ProductError(404, "history_not_found", "Saved run was not found.")
        row = self._db.execute(
            "SELECT * FROM run_history WHERE history_id = ?", (history_id,)
        ).fetchone()
        if row is None:
            raise ProductError(404, "history_not_found", "Saved run was not found.")
        return row

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        browser_error = HistoryStore._decode_object(row["browser_error_json"], "browser error")
        return {
            "history_id": row["history_id"],
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "kind": row["kind"],
            "parent_run_id": row["parent_run_id"],
            "user_prompt": row["user_prompt"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "outcome": row["outcome"],
            "graph_observed_at": row["graph_observed_at"],
            "preview_available": row["sketch_content"] is not None,
            "verified_sketch_sha256": row["sketch_sha256"],
            "browser_error": browser_error,
        }

    @staticmethod
    def _decode_object(payload: object, label: str) -> dict[str, Any] | None:
        if payload is None:
            return None
        try:
            value = json.loads(str(payload))
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProductError(409, "history_corrupt", f"The {label} is invalid.") from exc
        if not isinstance(value, dict):
            raise ProductError(409, "history_corrupt", f"The {label} is invalid.")
        return value

    @staticmethod
    def _encode_cursor(started_at: str, history_id: str) -> str:
        raw = _json([started_at, history_id]).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, str]:
        try:
            padding = "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        except (ValueError, TypeError) as exc:
            raise ProductError(422, "invalid_history_cursor", "History cursor is invalid.") from exc
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, str) for item in value)
            or not HISTORY_ID_PATTERN.fullmatch(value[1])
        ):
            raise ProductError(422, "invalid_history_cursor", "History cursor is invalid.")
        try:
            datetime.fromisoformat(value[0])
        except ValueError as exc:
            raise ProductError(422, "invalid_history_cursor", "History cursor is invalid.") from exc
        return value[0], value[1]


def new_session(*, reset_required: bool) -> SessionRecord:
    """Create the initial durable session for a new catalog."""

    state = SessionState.RESET_REQUIRED if reset_required else SessionState.EMPTY
    return SessionRecord(session_id=f"session-{uuid4().hex}", state=state)
