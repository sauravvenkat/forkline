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


@dataclass
class RunRecorder:
    """
    Explicit, boring run recorder.

    No decorators. No magic. Just append-only event logging.
    """

    db_path: str = "runs.db"

    def __post_init__(self) -> None:
        # Match SQLiteStore behavior: ensure parent directory exists.
        # Without this, sqlite3.connect() fails if the directory is missing.
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

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
                    schema_version TEXT NOT NULL DEFAULT '0.1',
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
                (run_id, schema_version, entrypoint, started_at, 
                 python_version, platform, cwd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "0.1",
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

        Args:
            run_id: Run identifier
            event_type: Event type (input, output, tool_call, artifact_ref)
            payload: Event payload (will be JSON-serialized)

        Returns:
            event_id
        """
        ts = self._utc_now()
        payload_json = json.dumps(payload, sort_keys=True)

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
                SELECT run_id, schema_version, entrypoint, started_at, ended_at,
                       status, python_version, platform, cwd
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            if row is None:
                return None

            return dict(row)

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
