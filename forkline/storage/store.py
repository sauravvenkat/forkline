from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from ..artifact.migrate import migrate_artifact
from ..artifact.schema import RunArtifact
from ..core.types import Event, Run, Step
from ..version import (
    DEFAULT_FORKLINE_VERSION,
    DEFAULT_SCHEMA_VERSION,
    FORKLINE_VERSION,
    SCHEMA_VERSION,
)


@dataclass
class SQLiteStore:
    path: str = "forkline.db"

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    forkline_version TEXT,
                    schema_version TEXT
                )
                """
            )

            # Migration: add version columns if they don't exist (for older DBs)
            self._migrate_add_version_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS steps (
                    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_idx INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _migrate_add_version_columns(self, conn: sqlite3.Connection) -> None:
        """
        Migration: add version columns to existing databases.

        This enables backward compatibility with older artifacts that
        don't have version fields.
        """
        # Check if columns exist by trying to select them
        try:
            conn.execute("SELECT forkline_version, schema_version FROM runs LIMIT 1")
        except sqlite3.OperationalError:
            # Columns don't exist, add them
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN forkline_version TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN schema_version TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def start_run(self, run_id: str) -> Run:
        created_at = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs 
                (run_id, created_at, forkline_version, schema_version) 
                VALUES (?, ?, ?, ?)
                """,
                (run_id, created_at, FORKLINE_VERSION, SCHEMA_VERSION),
            )
        return Run(
            run_id=run_id,
            created_at=created_at,
            steps=[],
            forkline_version=FORKLINE_VERSION,
            schema_version=SCHEMA_VERSION,
        )

    def start_step(self, run_id: str, idx: int, name: str) -> Step:
        started_at = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO steps (run_id, idx, name, started_at, ended_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, idx, name, started_at, None),
            )
            step_row = conn.execute(
                """
                SELECT step_id, run_id, idx, name, started_at, ended_at
                FROM steps
                WHERE run_id = ? AND idx = ?
                """,
                (run_id, idx),
            ).fetchone()
        return Step(
            step_id=step_row["step_id"],
            run_id=step_row["run_id"],
            idx=step_row["idx"],
            name=step_row["name"],
            started_at=step_row["started_at"],
            ended_at=step_row["ended_at"],
            events=[],
        )

    def end_step(self, run_id: str, idx: int) -> None:
        ended_at = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE steps
                SET ended_at = ?
                WHERE run_id = ? AND idx = ?
                """,
                (ended_at, run_id, idx),
            )

    def append_event(
        self,
        run_id: str,
        step_idx: int,
        type: str,
        payload_dict: dict,
    ) -> Event:
        created_at = self._utc_now()
        payload_json = json.dumps(payload_dict, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (run_id, step_idx, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, step_idx, type, payload_json, created_at),
            )
            row = conn.execute(
                """
                SELECT event_id, run_id, step_idx, type, payload_json, created_at
                FROM events
                WHERE run_id = ? AND step_idx = ?
                ORDER BY event_id DESC
                LIMIT 1
                """,
                (run_id, step_idx),
            ).fetchone()
        return Event(
            event_id=row["event_id"],
            run_id=row["run_id"],
            step_idx=row["step_idx"],
            type=row["type"],
            created_at=row["created_at"],
            payload=payload_dict,
        )

    def load_run(self, run_id: str) -> Optional[Run]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, created_at, forkline_version, schema_version 
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                return None

        steps = self._load_steps(run_id)

        # Backward compat: use defaults for older artifacts missing version fields
        forkline_version = row["forkline_version"]
        schema_version = row["schema_version"]

        if forkline_version is None:
            forkline_version = DEFAULT_FORKLINE_VERSION
        if schema_version is None:
            schema_version = DEFAULT_SCHEMA_VERSION

        return Run(
            run_id=row["run_id"],
            created_at=row["created_at"],
            steps=steps,
            forkline_version=forkline_version,
            schema_version=schema_version,
        )

    def _load_steps(self, run_id: str) -> list[Step]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT step_id, run_id, idx, name, started_at, ended_at
                FROM steps
                WHERE run_id = ?
                ORDER BY idx ASC
                """,
                (run_id,),
            ).fetchall()
        steps: list[Step] = []
        for row in rows:
            events = list(self._load_events(run_id, row["idx"]))
            steps.append(
                Step(
                    step_id=row["step_id"],
                    run_id=row["run_id"],
                    idx=row["idx"],
                    name=row["name"],
                    started_at=row["started_at"],
                    ended_at=row["ended_at"],
                    events=events,
                )
            )
        return steps

    def _load_events(self, run_id: str, step_idx: int) -> Iterable[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, run_id, step_idx, type, payload_json, created_at
                FROM events
                WHERE run_id = ? AND step_idx = ?
                ORDER BY event_id ASC
                """,
                (run_id, step_idx),
            ).fetchall()
        for row in rows:
            yield Event(
                event_id=row["event_id"],
                run_id=row["run_id"],
                step_idx=row["step_idx"],
                type=row["type"],
                created_at=row["created_at"],
                payload=json.loads(row["payload_json"]),
            )

    def load_artifact(self, run_id: str) -> Optional[RunArtifact]:
        """
        Load a run as a canonical RunArtifact, applying migrations if needed.

        Flattens the step-based event hierarchy into a flat event list
        for the canonical artifact schema.

        Returns:
            RunArtifact if found, None if run does not exist.

        Raises:
            SchemaVersionError: If the artifact cannot be migrated.
        """
        run = self.load_run(run_id)
        if run is None:
            return None

        flat_events: List[Dict[str, Any]] = []
        for step in run.steps:
            for event in step.events:
                flat_events.append(
                    {
                        "event_id": event.event_id or 0,
                        "run_id": event.run_id,
                        "ts": event.created_at,
                        "type": event.type,
                        "payload": event.payload,
                    }
                )

        raw: Dict[str, Any] = {
            "schema_version": run.schema_version or DEFAULT_SCHEMA_VERSION,
            "run_id": run.run_id,
            "entrypoint": "",
            "started_at": run.created_at,
            "forkline_version": run.forkline_version,
            "events": flat_events,
        }

        migrated = migrate_artifact(raw)

        return RunArtifact.from_dict(migrated)

    def export_artifact_json(self, run_id: str, indent: int = 2) -> Optional[str]:
        """
        Export a run as a canonical JSON artifact string.

        Returns:
            JSON string if run exists, None otherwise.
        """
        artifact = self.load_artifact(run_id)
        if artifact is None:
            return None
        return artifact.to_json(indent=indent)
