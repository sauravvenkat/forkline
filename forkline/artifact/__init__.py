"""
Forkline artifact schema — versioned, documented, forward-compatible.

This module defines the canonical artifact schema for Forkline run artifacts.
All run data persisted to SQLite or exported to JSON MUST conform to this schema.

Key guarantees:
- Every artifact has a mandatory schema_version field.
- Unknown fields are ignored on load, never rejected.
- Artifacts are immutable once written.
- Migrations are deterministic and side-effect free.
"""

from .migrate import migrate_artifact, register_migration
from .schema import ArtifactEvent, RunArtifact, SchemaVersionError

__all__ = [
    "RunArtifact",
    "ArtifactEvent",
    "SchemaVersionError",
    "migrate_artifact",
    "register_migration",
]
