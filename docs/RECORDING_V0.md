# Deterministic Run Recording v0

**Status:** ✅ Implemented

Local-first, append-only run recording infrastructure for Forkline.

## Overview

Recording v0 is the foundation for deterministic replay and diffing. It captures execution runs as self-contained, replayable artifacts stored in SQLite.

### Design principles

* **Local-first**: `runs.db` lives on disk
* **Append-only**: Events never update, only append
* **Boring**: No abstractions beyond necessary
* **Human-inspectable**: Readable with `sqlite3`
* **Versioned**: Schema version tracked in every run

## Schema

### Runs table

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL DEFAULT '0.1',
    entrypoint TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT,
    python_version TEXT NOT NULL,
    platform TEXT NOT NULL,
    cwd TEXT NOT NULL
);
```

Every run captures:
* Unique `run_id` (UUID hex)
* `schema_version` for forward compatibility
* `entrypoint` (e.g., "examples/minimal.py")
* Environment snapshot: Python version, platform, cwd
* Start/end timestamps and final status

### Events table

```sql
CREATE TABLE events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
```

Events are:
* **Ordered**: `event_id` auto-increments
* **Timestamped**: ISO8601 UTC
* **Typed**: Canonical event types (see below)
* **Flexible**: JSON payload for arbitrary data

### Event types

v0 defines four canonical event types:

| Type | Purpose | Example payload |
|------|---------|----------------|
| `input` | User/system input | `{"prompt": "hello"}` |
| `output` | Agent output | `{"result": "world"}` |
| `tool_call` | Tool invocation | `{"name": "search", "args": {...}, "result": {...}}` |
| `artifact_ref` | Reference to artifact | `{"path": "/tmp/file.txt", "size": 1024}` |

These types are sufficient for replaying most agent workflows.

## API

### RunRecorder

The `RunRecorder` class provides an explicit, boring API.

```python
from forkline.storage.recorder import RunRecorder

recorder = RunRecorder()  # Creates/opens runs.db

# Start a run
run_id = recorder.start_run(entrypoint="examples/minimal.py")

# Log events in order
recorder.log_event(
    run_id,
    event_type="input",
    payload={"prompt": "hello"},
)

recorder.log_event(
    run_id,
    event_type="output",
    payload={"result": "world"},
)

# End the run
recorder.end_run(run_id, status="success")
```

**No decorators. No magic. Just append-only logging.**

### Methods

#### `start_run(entrypoint: str, run_id: Optional[str] = None) -> str`

Start a new run. Captures environment snapshot automatically.

* `entrypoint`: Entry point identifier
* `run_id`: Optional explicit ID (generates UUID if not provided)
* Returns: `run_id`

#### `log_event(run_id: str, event_type: str, payload: Dict[str, Any]) -> int`

Log an event. Append-only.

* `run_id`: Run identifier
* `event_type`: Event type (input, output, tool_call, artifact_ref)
* `payload`: Event data (JSON-serializable dict)
* Returns: `event_id`

#### `end_run(run_id: str, status: str = "success") -> None`

End a run.

* `run_id`: Run identifier
* `status`: Final status (success, failure, error)

#### `get_run(run_id: str) -> Optional[Dict[str, Any]]`

Retrieve run metadata.

* Returns: Run dict or None if not found

#### `get_events(run_id: str) -> list[Dict[str, Any]]`

Retrieve all events for a run, ordered by `event_id`.

* Returns: List of event dicts

## Usage

### Basic example

See `examples/minimal.py`:

```python
from forkline.storage.recorder import RunRecorder

recorder = RunRecorder()
run_id = recorder.start_run(entrypoint="examples/minimal.py")

recorder.log_event(run_id, "input", {"prompt": "hello"})
recorder.log_event(run_id, "output", {"result": "world"})

recorder.end_run(run_id, status="success")
```

Run it:
```bash
python examples/minimal.py
```

This creates/updates `runs.db`.

### Inspecting runs

Use the helper script:

```bash
# List all runs
python scripts/inspect_runs.py

# Show specific run with events
python scripts/inspect_runs.py --run-id <run_id>
```

Or use sqlite3 directly:

```bash
sqlite3 runs.db "SELECT * FROM runs;"
sqlite3 runs.db "SELECT * FROM events;"
```

## Testing

Tests live in `tests/unit/test_run_recording.py`.

Run them:
```bash
python -m unittest tests.unit.test_run_recording -v
```

Tests verify:
* Versioned run creation
* Append-only event ordering
* Environment snapshot capture
* All event types
* Multiple independent runs
* Human inspectability with raw SQLite

## What's NOT in Recording v0

Recording v0 is **minimal infrastructure**. The following are explicitly deferred:

* ❌ CLI commands
* ❌ Decorators or automatic tracing
* ❌ Network exporters
* ❌ OpenTelemetry integration
* ❌ Monkey-patching
* ❌ Agent framework integration

Recording v0 is **just the storage layer**. For replay, see `docs/REPLAY_ENGINE_V0.md`.

## Schema versioning

Every run records `schema_version = "0.1"`.

This enables:
* Forward compatibility
* Migration scripts
* Version-specific replay logic

If the schema changes, increment the version and write migrations.

## File location

By default, `RunRecorder()` creates `runs.db` in the current directory.

Override with:
```python
recorder = RunRecorder(db_path="path/to/runs.db")
```

For tests, use tempfile:
```python
import tempfile
with tempfile.TemporaryDirectory() as tmpdir:
    recorder = RunRecorder(db_path=f"{tmpdir}/test.db")
```

## Performance notes

* SQLite is fast enough for most use cases
* Index on `(run_id, event_id)` speeds up event retrieval
* Events are written synchronously (ACID guarantees)
* For high-throughput recording, consider batching (future work)

## Next steps

With recording v0 complete, replay is now available:

👉 **See `docs/REPLAY_ENGINE_V0.md`** for the deterministic replay engine

The replay engine enables:
- Comparing two runs step-by-step
- Detecting first point of divergence
- Injecting recorded outputs for deterministic re-execution

## Philosophy

> If something feels "too simple," it's probably correct.

v0 is intentionally boring. It's:
* Explicit over clever
* Flat files over abstractions
* Clarity over extensibility

This makes it:
* Easy to debug
* Easy to inspect
* Easy to trust

Forkline is infrastructure. Infrastructure should be boring.
