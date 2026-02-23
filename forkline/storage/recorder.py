"""
Deterministic run recording.

Local-first, append-only, boring infrastructure for recording execution runs.
All new artifacts are written with schema_version "1.0".
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

from forkline.artifact.migrate import migrate_artifact
from forkline.artifact.schema import (
    RunArtifact,
)
from forkline.core.redaction import (
    RedactionPolicy,
    create_default_policy,
    load_redaction_config,
)
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

    @classmethod
    def with_config(
        cls,
        db_path: str = "runs.db",
        redact_config_path: Optional[str] = None,
    ) -> "RunRecorder":
        """
        Create a RunRecorder with redaction policy loaded from a config file.

        If no config path is provided, uses the default SAFE policy.

        Args:
            db_path: Path to SQLite database
            redact_config_path: Path to redaction config file (YAML or JSON)
        """
        policy = None
        if redact_config_path is not None:
            config = load_redaction_config(redact_config_path)
            policy = config.to_policy()
        return cls(db_path=db_path, redaction_policy=policy)

    def log_tool_call(
        self,
        run_id: str,
        tool_name: str,
        request: Dict[str, Any],
        response: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
        timing: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        invocation_id: Optional[str] = None,
    ) -> int:
        """
        Log a tool call event with the canonical payload structure.

        Convenience method that constructs a properly-structured tool_call
        event payload and logs it. Redaction is applied at the storage boundary.

        Args:
            run_id: Run identifier
            tool_name: Tool name (e.g., "bigquery.query", "http.request")
            request: Request payload (will be redacted)
            response: Response payload (will be redacted), or None
            error: Error payload (will be redacted), or None
            timing: Timing dict with started_at, ended_at, duration_ms
            metadata: Optional metadata (status_code, row_count, etc.)
            invocation_id: Stable invocation ID (auto-generated if not provided)

        Returns:
            event_id
        """
        if invocation_id is None:
            invocation_id = uuid.uuid4().hex

        payload: Dict[str, Any] = {
            "tool_name": tool_name,
            "invocation_id": invocation_id,
            "request": request,
            "timing": timing or {},
        }
        if response is not None:
            payload["response"] = response
        if error is not None:
            payload["error"] = error
        if metadata:
            payload["metadata"] = metadata

        return self.log_event(run_id, "tool_call", payload)

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

    def list_runs(self, limit: Optional[int] = None) -> list[Dict[str, Any]]:
        """
        List all runs, newest first.

        Args:
            limit: Maximum number of runs to return (None = all)

        Returns:
            List of run metadata dicts
        """
        with self._connect() as conn:
            query = """
                SELECT run_id, schema_version, forkline_version, entrypoint,
                       started_at, ended_at, status, python_version, platform, cwd
                FROM runs
                ORDER BY started_at DESC
            """
            if limit is not None and limit > 0:
                query += f" LIMIT {int(limit)}"
            rows = conn.execute(query).fetchall()

        results: list[Dict[str, Any]] = []
        for row in rows:
            result = dict(row)
            if result.get("schema_version") is None:
                result["schema_version"] = DEFAULT_SCHEMA_VERSION
            if result.get("forkline_version") is None:
                result["forkline_version"] = DEFAULT_FORKLINE_VERSION
            results.append(result)
        return results

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

    def load_artifact(self, run_id: str) -> Optional[RunArtifact]:
        """
        Load a run as a canonical RunArtifact, applying migrations if needed.

        This is the preferred way to load artifacts for replay or export.
        It handles:
        - Missing schema_version (older artifacts without versioning)
        - Older schema versions (applies deterministic migration)
        - Newer schema versions (best-effort parse with warning)

        Returns:
            RunArtifact if found, None if run does not exist.

        Raises:
            SchemaVersionError: If the artifact cannot be migrated.
        """
        run = self.get_run(run_id)
        if run is None:
            return None

        events = self.get_events(run_id)

        raw: Dict[str, Any] = {
            "schema_version": run.get("schema_version"),
            "run_id": run["run_id"],
            "entrypoint": run.get("entrypoint", ""),
            "started_at": run.get("started_at", ""),
            "ended_at": run.get("ended_at"),
            "status": run.get("status"),
            "forkline_version": run.get("forkline_version"),
            "events": events,
            "metadata": {},
        }

        for env_field in ("python_version", "platform", "cwd"):
            if env_field in run and run[env_field] is not None:
                raw["metadata"][env_field] = run[env_field]

        migrated = migrate_artifact(raw)

        return RunArtifact.from_dict(migrated)

    def export_artifact_json(self, run_id: str, indent: int = 2) -> Optional[str]:
        """
        Export a run as a canonical JSON artifact string.

        The exported JSON always includes schema_version and conforms
        to the current canonical schema.

        Returns:
            JSON string if run exists, None otherwise.
        """
        artifact = self.load_artifact(run_id)
        if artifact is None:
            return None
        return artifact.to_json(indent=indent)
