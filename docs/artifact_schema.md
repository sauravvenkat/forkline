# Artifact Schema Specification

**Version:** 1.0  
**Status:** Stable  
**Last Updated:** 2026-02-23

---

## Overview

Forkline run artifacts are the atomic unit of replay, diffing, and forensic debugging. Every artifact is a self-contained, immutable record of a single execution run.

This document is the canonical specification for the artifact schema. All storage backends (SQLite, JSON export) and all consumers (replay engine, diff engine, CLI) MUST conform to this schema.

---

## Versioning Policy

Forkline artifact schemas follow **SemVer-style** versioning:

| Version Component | Meaning |
|---|---|
| **Major** (e.g. 1.x → 2.x) | Breaking changes to field names, types, or semantics. Requires migration support. |
| **Minor** (e.g. 1.0 → 1.1) | Additive changes only: new optional fields with defaults. Fully backward compatible. |

### Rules

1. **Every artifact MUST include `schema_version`** — this field is mandatory and must be present in both SQLite and JSON formats.
2. **No breaking renames in minor versions** — field names are stable within a major version.
3. **New fields must be optional with defaults** — minor version bumps never require changes to existing consumers.
4. **Unknown fields must be ignored, not rejected** — forward compatibility requires tolerance of unrecognized fields.
5. **Artifacts are immutable once written** — no in-place mutation, ever.

---

## Schema v1.0

### RunArtifact (top-level)

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | `string` | **YES** | Schema version (e.g. `"1.0"`). Must always be present. |
| `run_id` | `string` | **YES** | Unique identifier for the run (hex UUID). |
| `entrypoint` | `string` | **YES** | Script or function that was executed (e.g. `"examples/minimal.py"`). |
| `started_at` | `string` (ISO8601) | **YES** | UTC timestamp of run start. |
| `ended_at` | `string` (ISO8601) | no | UTC timestamp of run end. `null` if still running or unknown. |
| `status` | `string` | no | Terminal status: `"success"`, `"failure"`, `"error"`, `"ok"`. |
| `forkline_version` | `string` | no | Version of Forkline that created this artifact (e.g. `"0.3.0"`). |
| `events` | `array[Event]` | **YES** | Ordered list of events captured during the run. |
| `metadata` | `object` | no | Extensible key-value metadata. Used for environment info and forward-compat fields. |

### Event

| Field | Type | Required | Description |
|---|---|---|---|
| `event_id` | `integer` | **YES** | Auto-incrementing event identifier. |
| `run_id` | `string` | **YES** | The run this event belongs to. |
| `ts` | `string` (ISO8601) | **YES** | UTC timestamp of the event. |
| `type` | `string` | **YES** | Event classification: `"input"`, `"output"`, `"tool_call"`, `"system"`. Unknown types are preserved. |
| `payload` | `object` | **YES** | Arbitrary JSON-serializable event data. Structure depends on event type. |

---

## Example Artifact (JSON)

```json
{
  "schema_version": "1.0",
  "run_id": "8a3f1b2c4d5e6f7890abcdef12345678",
  "entrypoint": "examples/ollama_qwen3.py",
  "started_at": "2026-02-23T01:04:20.123456+00:00",
  "ended_at": "2026-02-23T01:04:30.789012+00:00",
  "status": "success",
  "forkline_version": "0.3.0",
  "events": [
    {
      "event_id": 1,
      "run_id": "8a3f1b2c4d5e6f7890abcdef12345678",
      "ts": "2026-02-23T01:04:20.200000+00:00",
      "type": "input",
      "payload": {
        "model": "qwen3",
        "prompt": "[REDACTED:sha256:a1b2c3...]"
      }
    },
    {
      "event_id": 2,
      "run_id": "8a3f1b2c4d5e6f7890abcdef12345678",
      "ts": "2026-02-23T01:04:30.500000+00:00",
      "type": "output",
      "payload": {
        "model": "qwen3",
        "response": "[REDACTED:sha256:d4e5f6...]"
      }
    }
  ],
  "metadata": {
    "python_version": "3.12.0",
    "platform": "macOS-15.3-arm64",
    "cwd": "/Users/dev/project"
  }
}
```

---

## Backward Compatibility

### Loading older artifacts

| Source Version | Behavior |
|---|---|
| `schema_version` missing | `SchemaVersionError` raised. Artifacts without version info cannot be reliably loaded. For SQLite databases predating versioning, `DEFAULT_SCHEMA_VERSION` (`"recording_v0"`) is assigned automatically. |
| `"recording_v0"` | Migrated to `"1.0"` via the migration registry. Environment fields (`python_version`, `platform`, `cwd`) are moved to `metadata`. Event timestamps are normalized to the `ts` field. |
| `"1.0"` | Loaded directly, no migration needed. |
| Newer than current | Warning issued. Best-effort parse with unknown-field tolerance. |

### Migration guarantees

1. **Migrations are deterministic** — same input always produces same output.
2. **Migrations are side-effect free** — no I/O, no network, no state mutation.
3. **Migrations never mutate the input** — a deep copy is always made.
4. **Migration chains compose** — `recording_v0` → `1.0` → (future) `1.1` are applied sequentially.

### Adding migrations for future versions

```python
from forkline.artifact import register_migration

def migrate_1_0_to_1_1(raw: dict) -> dict:
    """Add new optional field with default."""
    result = dict(raw)  # shallow copy is fine if not modifying nested structures
    result.setdefault("new_field", "default_value")
    return result

register_migration("1.0", "1.1", migrate_1_0_to_1_1)
```

---

## Storage Formats

### SQLite

The `runs` table includes `schema_version` as a `TEXT NOT NULL` column. The `events` table stores individual events with foreign key to `runs.run_id`.

```sql
CREATE TABLE runs (
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
);

CREATE TABLE events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
```

### JSON Export

JSON exports conform exactly to the `RunArtifact` schema above. Use `RunRecorder.export_artifact_json()` or `SQLiteStore.export_artifact_json()` to produce canonical exports.

---

## Replay Integration

The replay engine validates `schema_version` at load time:

1. **Current version**: loaded normally.
2. **Older version**: loaded via migration layer (transparent to caller).
3. **Newer version**: warning issued, best-effort parse.
4. **Missing version**: warning issued, default assumptions applied.

Replay never crashes due to unknown fields or version mismatches. Degradation is always graceful.

---

## Stability Guarantees

1. **Replay compatibility across minor versions** — artifacts created with schema `1.0` will always be loadable by Forkline versions that support schema `1.x`.
2. **Breaking changes require major version increment** — a schema `2.0` will include migration support from all `1.x` versions.
3. **Migration support is permanent** — once a migration path is registered, it is never removed.
4. **Artifacts are never modified in place** — migrations produce new representations, original data is preserved.

---

## Design Principles

- **Explicit schema over ad-hoc dicts** — every field is typed and documented.
- **Additive evolution only in minor versions** — never remove or rename fields.
- **Never mutate artifacts in place** — immutability is a core invariant.
- **Migrations must be deterministic** — no randomness, no timestamps, no I/O.
- **Replay must remain pure** — schema loading is read-only.
- **Trust > speed** — correctness over performance in all schema decisions.
