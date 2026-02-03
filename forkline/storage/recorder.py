"""
Deterministic run recording v0.

Local-first, append-only, boring infrastructure for recording execution runs.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from forkline.core.redaction import RedactionPolicy, create_default_policy
from forkline.version import (
    DEFAULT_FORKLINE_VERSION,
    DEFAULT_SCHEMA_VERSION,
    FORKLINE_VERSION,
    SCHEMA_VERSION,
)


@dataclass
class RunRecorder:
    """
    Explicit, boring run recorder.

    No decorators. No magic. Just append-only event logging.

    Redaction is applied at the storage boundary: all event payloads are
    redacted before being persisted to disk.
    """

    db_path: str = "runs.db"
    redaction_policy: Optional[RedactionPolicy] = None

    def __post_init__(self) -> None:
        # Match SQLiteStore behavior: ensure parent directory exists.
        # Without this, sqlite3.connect() fails if the directory is missing.
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

        # Use default SAFE mode policy if none provided
        if self.redaction_policy is None:
            self.redaction_policy = create_default_policy()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize runs.db with versioned schema."""
        with self._connect() as conn:
            # Runs table with versioned schema
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    forkline_version TEXT NOT NULL,
                    entrypoint TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT,
                    python_version TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    cwd TEXT NOT NULL
                )
                """
            )

            # Migration: add forkline_version column if it doesn't exist
            self._migrate_add_forkline_version(conn)

            # Events table - append-only
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
                """
            )

            # Index for fast event retrieval
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_run_id 
                ON events(run_id, event_id)
                """
            )

    def _migrate_add_forkline_version(self, conn: sqlite3.Connection) -> None:
        """Migration: add forkline_version column to existing databases."""
        try:
            conn.execute("SELECT forkline_version FROM runs LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN forkline_version TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

    def _utc_now(self) -> str:
        """ISO8601 UTC timestamp."""
        return datetime.now(timezone.utc).isoformat()

    def _capture_env(self) -> Dict[str, str]:
        """Capture environment snapshot."""
        return {
            "python_version": sys.version,
            "platform": platform.platform(),
            "cwd": os.getcwd(),
        }

    def start_run(self, entrypoint: str, run_id: Optional[str] = None) -> str:
        """
        Start a new run.

        Args:
            entrypoint: Entry point identifier (e.g., "examples/minimal.py")
            run_id: Optional explicit run ID (generates UUID if not provided)

        Returns:
            run_id
        """
        if run_id is None:
            run_id = uuid.uuid4().hex

        started_at = self._utc_now()
        env = self._capture_env()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs 
                (run_id, schema_version, forkline_version, entrypoint, started_at, 
                 python_version, platform, cwd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    SCHEMA_VERSION,
                    FORKLINE_VERSION,
                    entrypoint,
                    started_at,
                    env["python_version"],
                    env["platform"],
                    env["cwd"],
                ),
            )

        return run_id

    def log_event(
        self,
        run_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> int:
        """
        Log an event. Append-only.

        Redaction is applied at the storage boundary: the payload is redacted
        before persistence. The input payload is never mutated.

        Args:
            run_id: Run identifier
            event_type: Event type (input, output, tool_call, artifact_ref)
            payload: Event payload (will be redacted and JSON-serialized)

        Returns:
            event_id
        """
        ts = self._utc_now()

        # Apply redaction at storage boundary
        # This is security-critical: storage never sees raw payloads
        redacted_payload = self.redaction_policy.redact(event_type, payload)

        payload_json = json.dumps(redacted_payload, sort_keys=True)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (run_id, ts, type, payload)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, ts, event_type, payload_json),
            )
            event_id = cursor.lastrowid

        return event_id

    def end_run(self, run_id: str, status: str = "success") -> None:
        """
        End a run.

        Args:
            run_id: Run identifier
            status: Final status (success, failure, error)
        """
        ended_at = self._utc_now()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET ended_at = ?, status = ?
                WHERE run_id = ?
                """,
                (ended_at, status, run_id),
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a run by ID.

        Returns:
            Run metadata as dict, or None if not found
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, schema_version, forkline_version, entrypoint, 
                       started_at, ended_at, status, python_version, platform, cwd
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            if row is None:
                return None

            result = dict(row)

            # Backward compatibility: use defaults for older artifacts
            if result.get("schema_version") is None:
                result["schema_version"] = DEFAULT_SCHEMA_VERSION
            if result.get("forkline_version") is None:
                result["forkline_version"] = DEFAULT_FORKLINE_VERSION

            return result

    def get_events(self, run_id: str) -> list[Dict[str, Any]]:
        """
        Retrieve all events for a run, ordered by event_id.

        Returns:
            List of events
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, run_id, ts, type, payload
                FROM events
                WHERE run_id = ?
                ORDER BY event_id ASC
                """,
                (run_id,),
            ).fetchall()

            events = []
            for row in rows:
                event = dict(row)
                event["payload"] = json.loads(event["payload"])
                events.append(event)

            return events
