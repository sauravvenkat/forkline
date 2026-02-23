"""
Deterministic artifact migration registry.

This module implements a versioned migration pipeline that transforms
older artifact schemas to the current canonical format. Migrations are:

- Deterministic: same input always produces same output.
- Side-effect free: no I/O, no state mutation, no network calls.
- Composable: migrations chain from source version to current.
- Registered: future versions add migration functions via register_migration().

Migration strategy:
- If schema_version == current: return as-is.
- If schema_version is older: route through chained migration functions.
- If schema_version is newer: warn but attempt best-effort parse
  (forward compatibility via unknown-field tolerance).
- If schema_version is missing: raise SchemaVersionError.
"""

from __future__ import annotations

import copy
import logging
import warnings
from typing import Callable, Dict, List, Tuple

from .schema import CURRENT_SCHEMA_VERSION, SchemaVersionError

logger = logging.getLogger("forkline.artifact.migrate")

MigrationFn = Callable[[dict], dict]

_migration_registry: Dict[Tuple[str, str], MigrationFn] = {}

_version_order: List[str] = []


def _ensure_version_order() -> None:
    """Populate the version ordering if not already set."""
    global _version_order
    if not _version_order:
        _version_order = ["recording_v0", "1.0"]


def register_migration(
    from_version: str,
    to_version: str,
    fn: MigrationFn,
) -> None:
    """
    Register a migration function between two schema versions.

    The function must be deterministic and side-effect free.
    It receives a raw dict and must return a transformed dict.
    The input dict must not be mutated.

    Args:
        from_version: Source schema version (e.g. "recording_v0").
        to_version: Target schema version (e.g. "1.0").
        fn: Migration function. Must be pure: dict -> dict.

    Raises:
        ValueError: If a migration for this version pair is already registered.
    """
    key = (from_version, to_version)
    if key in _migration_registry:
        raise ValueError(
            f"Migration already registered: {from_version} -> {to_version}"
        )
    _migration_registry[key] = fn

    _ensure_version_order()
    if from_version not in _version_order:
        idx = (
            _version_order.index(to_version)
            if to_version in _version_order
            else len(_version_order)
        )
        _version_order.insert(idx, from_version)
    if to_version not in _version_order:
        _version_order.append(to_version)


def _parse_version(version: str) -> Tuple[int, ...]:
    """Parse a version string for ordering comparison."""
    if version.startswith("recording_v"):
        gen = int(version.split("v", 1)[1])
        return (0, gen)
    parts = version.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return (0, 0)


def _compare_versions(a: str, b: str) -> int:
    """
    Compare two version strings.

    Returns:
        -1 if a < b, 0 if a == b, 1 if a > b
    """
    pa = _parse_version(a)
    pb = _parse_version(b)
    if pa < pb:
        return -1
    elif pa > pb:
        return 1
    return 0


def _find_migration_path(from_version: str, to_version: str) -> List[Tuple[str, str]]:
    """
    Find the chain of migrations from from_version to to_version.

    Returns a list of (from, to) pairs representing the migration path.
    Returns empty list if no path exists or versions are equal.
    """
    if from_version == to_version:
        return []

    _ensure_version_order()

    try:
        from_idx = _version_order.index(from_version)
        to_idx = _version_order.index(to_version)
    except ValueError:
        return []

    if from_idx >= to_idx:
        return []

    path: List[Tuple[str, str]] = []
    for i in range(from_idx, to_idx):
        step = (_version_order[i], _version_order[i + 1])
        if step not in _migration_registry:
            return []
        path.append(step)

    return path


def migrate_artifact(raw_json: dict) -> dict:
    """
    Transform an artifact dict from any known older schema version to
    the current canonical structure.

    This function is the primary entry point for artifact loading.
    It is deterministic and side-effect free.

    Behavior:
    - If schema_version is missing: raises SchemaVersionError.
    - If schema_version == current: returns a copy unchanged.
    - If schema_version is older and migration path exists: applies
      chained migrations and returns the result.
    - If schema_version is newer than current: logs a warning and
      returns a copy unchanged (best-effort forward compat via
      unknown-field tolerance in from_dict).
    - If schema_version is older but no migration path exists:
      raises SchemaVersionError.

    Args:
        raw_json: Raw artifact dict (e.g. from JSON.loads or SQLite row).
                  This dict is never mutated.

    Returns:
        A new dict conforming to the current schema version.

    Raises:
        SchemaVersionError: If schema_version is missing or migration is
                           impossible.
    """
    if not isinstance(raw_json, dict):
        raise SchemaVersionError(
            "Artifact must be a dict",
            version=None,
        )

    version = raw_json.get("schema_version")

    if version is None:
        raise SchemaVersionError(
            "Artifact is missing required field 'schema_version'. "
            "Cannot migrate artifacts without explicit version information. "
            "This artifact may predate schema versioning.",
            version=None,
        )

    version = str(version)

    if version == CURRENT_SCHEMA_VERSION:
        return copy.deepcopy(raw_json)

    cmp = _compare_versions(version, CURRENT_SCHEMA_VERSION)

    if cmp > 0:
        warnings.warn(
            f"Artifact schema_version '{version}' is newer than the current "
            f"version '{CURRENT_SCHEMA_VERSION}'. Attempting best-effort parse. "
            f"Unknown fields will be ignored. Upgrade Forkline for full support.",
            UserWarning,
            stacklevel=2,
        )
        result = copy.deepcopy(raw_json)
        result["schema_version"] = version
        return result

    path = _find_migration_path(version, CURRENT_SCHEMA_VERSION)
    if not path:
        raise SchemaVersionError(
            f"No migration path from schema_version '{version}' to "
            f"'{CURRENT_SCHEMA_VERSION}'. This artifact format is not supported "
            f"by this version of Forkline.",
            version=version,
        )

    result = copy.deepcopy(raw_json)
    for from_v, to_v in path:
        fn = _migration_registry[(from_v, to_v)]
        result = fn(result)
        result["schema_version"] = to_v
        logger.debug("Migrated artifact from %s to %s", from_v, to_v)

    return result


def _migrate_recording_v0_to_1_0(raw: dict) -> dict:
    """
    Migrate recording_v0 artifacts to schema 1.0.

    Changes:
    - schema_version: "recording_v0" -> "1.0"
    - Preserves all existing fields.
    - Normalizes field names for the canonical schema.
    - Adds metadata dict for environment info if present.
    """
    result = copy.deepcopy(raw)

    result["schema_version"] = "1.0"

    if "entrypoint" not in result and "script" in result:
        result["entrypoint"] = result.pop("script")

    metadata: dict = result.get("metadata", {})

    for env_field in ("python_version", "platform", "cwd"):
        if env_field in result:
            metadata[env_field] = result.pop(env_field)

    if metadata:
        result["metadata"] = metadata

    if "events" in result:
        migrated_events = []
        for event in result["events"]:
            migrated_event = dict(event)
            if "created_at" in migrated_event and "ts" not in migrated_event:
                migrated_event["ts"] = migrated_event.pop("created_at")
            migrated_events.append(migrated_event)
        result["events"] = migrated_events

    return result


register_migration("recording_v0", "1.0", _migrate_recording_v0_to_1_0)
