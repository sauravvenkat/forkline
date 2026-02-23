"""
Forkline version constants.

This module defines version constants for the Forkline library and its
artifact schemas. These are used to track compatibility and enable
backward-compatible loading of older artifacts.

Schema versioning policy:
- SCHEMA_VERSION is the version stamped on newly created artifacts.
- Older artifacts are loaded via the migration registry in forkline.artifact.migrate.
- Minor version bumps (e.g. 1.0 -> 1.1) are additive only — new optional fields.
- Major version bumps (e.g. 1.x -> 2.0) indicate breaking changes and require migration.
"""

# Library version (matches pyproject.toml)
FORKLINE_VERSION = "0.3.0"

# Current schema version for new artifacts.
# This is the version stamped on every artifact written by this release.
SCHEMA_VERSION = "1.0"

# Legacy schema version identifier, used by artifacts created before v1.0.
LEGACY_SCHEMA_VERSION = "recording_v0"

# Default values for backward compatibility when loading older artifacts
# that predate version tracking entirely (NULL columns in SQLite).
DEFAULT_FORKLINE_VERSION = "0.1.0"
DEFAULT_SCHEMA_VERSION = "recording_v0"
