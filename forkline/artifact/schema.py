"""
Canonical run artifact schema (v1.0).

This module defines the typed, versioned schema for Forkline run artifacts.
All fields, types, and constraints are documented here as the single source
of truth for artifact structure.

Stability contract:
- No breaking renames in minor versions.
- New fields must be optional with defaults.
- Unknown fields are ignored, never rejected.
- Artifacts are immutable once written.

This uses dataclasses (not Pydantic) to preserve Forkline's zero-dependency
property. Validation is explicit and deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CURRENT_SCHEMA_VERSION = "1.0"

KNOWN_EVENT_TYPES = frozenset({"input", "output", "tool_call", "system"})


class SchemaVersionError(Exception):
    """Raised when schema_version is missing or cannot be handled."""

    def __init__(self, message: str, version: Optional[str] = None):
        super().__init__(message)
        self.version = version


@dataclass(frozen=True)
class ArtifactEvent:
    """
    A single event within a run artifact.

    Attributes:
        event_id: Auto-incrementing event identifier.
        run_id: The run this event belongs to.
        ts: ISO8601 UTC timestamp of the event.
        type: Event classification. Core types are "input", "output",
              "tool_call", "system". Unknown types are preserved, not rejected.
        payload: Arbitrary JSON-serializable event data. Structure depends
                 on event type. Never mutated after write.
    """

    event_id: int
    run_id: str
    ts: str
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "ts": self.ts,
            "type": self.type,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactEvent:
        """Construct from dict, ignoring unknown fields."""
        return cls(
            event_id=data.get("event_id", 0),
            run_id=data.get("run_id", ""),
            ts=data.get("ts", ""),
            type=data.get("type", "system"),
            payload=data.get("payload", {}),
        )


@dataclass(frozen=True)
class RunArtifact:
    """
    Canonical run artifact (v1.0).

    This is the top-level schema for a complete Forkline run artifact,
    suitable for SQLite storage or JSON export.

    Attributes:
        schema_version: REQUIRED. The artifact schema version (e.g. "1.0").
                        Must always be present. Replay fails gracefully
                        if missing.
        run_id: Unique identifier for this run.
        entrypoint: The script or function that was executed.
        started_at: ISO8601 UTC timestamp of run start.
        ended_at: ISO8601 UTC timestamp of run end, or None if still running.
        status: Terminal status of the run (e.g. "success", "failure", "error").
        forkline_version: Version of the Forkline library that recorded this artifact.
        events: Ordered list of events captured during the run.
        metadata: Optional extensible metadata dict for forward compatibility.
                  New minor-version fields go here before being promoted to
                  top-level fields.
    """

    schema_version: str
    run_id: str
    entrypoint: str
    started_at: str
    ended_at: Optional[str] = None
    status: Optional[str] = None
    forkline_version: Optional[str] = None
    events: List[ArtifactEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        result: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "entrypoint": self.entrypoint,
            "started_at": self.started_at,
        }
        if self.ended_at is not None:
            result["ended_at"] = self.ended_at
        if self.status is not None:
            result["status"] = self.status
        if self.forkline_version is not None:
            result["forkline_version"] = self.forkline_version
        result["events"] = [e.to_dict() for e in self.events]
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RunArtifact:
        """
        Construct from dict, ignoring unknown fields.

        Raises SchemaVersionError if schema_version is missing.
        Unknown fields in data are silently dropped — this is by design
        for forward compatibility.
        """
        schema_version = data.get("schema_version")
        if schema_version is None:
            raise SchemaVersionError(
                "Artifact is missing required field 'schema_version'. "
                "Cannot load artifacts without explicit version information. "
                "This artifact may predate schema versioning.",
                version=None,
            )

        raw_events = data.get("events", [])
        events = [ArtifactEvent.from_dict(e) for e in raw_events]

        return cls(
            schema_version=str(schema_version),
            run_id=data.get("run_id", ""),
            entrypoint=data.get("entrypoint", ""),
            started_at=data.get("started_at", ""),
            ended_at=data.get("ended_at"),
            status=data.get("status"),
            forkline_version=data.get("forkline_version"),
            events=events,
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> RunArtifact:
        """Deserialize from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def validate(self) -> List[str]:
        """
        Validate the artifact for structural correctness.

        Returns a list of validation error messages. Empty list means valid.
        This never raises — callers decide how to handle errors.
        """
        errors: List[str] = []

        if not self.schema_version:
            errors.append("schema_version is required and must be non-empty")
        if not self.run_id:
            errors.append("run_id is required and must be non-empty")
        if not self.entrypoint:
            errors.append("entrypoint is required and must be non-empty")
        if not self.started_at:
            errors.append("started_at is required and must be non-empty")

        for i, event in enumerate(self.events):
            if not event.run_id:
                errors.append(f"events[{i}].run_id is required")
            if not event.ts:
                errors.append(f"events[{i}].ts is required")
            if not event.type:
                errors.append(f"events[{i}].type is required")

        return errors
