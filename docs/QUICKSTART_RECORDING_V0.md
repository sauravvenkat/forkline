# Quick Start: Deterministic Run Recording v0

Get started with Forkline's run recording in 2 minutes.

## Setup

```bash
cd /path/to/forkline

# Source the dev environment (sets PYTHONPATH)
source dev.env
```

This sets up your environment for development without requiring package installation.

## Basic usage

Create a script:

```python
from forkline.storage.recorder import RunRecorder

# Create recorder (creates runs.db)
recorder = RunRecorder()

# Start a run
run_id = recorder.start_run(entrypoint="my_script.py")

# Log events
recorder.log_event(run_id, "input", {"prompt": "hello"})
recorder.log_event(run_id, "output", {"result": "world"})

# End the run
recorder.end_run(run_id, status="success")

print(f"Run recorded: {run_id}")
```

Run it:
```bash
python my_script.py
```

## Inspect runs

### List all runs
```bash
python scripts/inspect_runs.py
```

Output:
```
Found 1 run(s):

1. Run ID: b8f3c1d4c05f4a53bab634b88d6761a2
   Entrypoint: my_script.py
   Status: success
   Started: 2026-01-18T04:02:35.814116+00:00
```

### Show run details
```bash
python scripts/inspect_runs.py --run-id <run_id>
```

### Direct SQLite inspection
```bash
# Show runs
sqlite3 runs.db "SELECT * FROM runs;"

# Show events
sqlite3 runs.db "SELECT * FROM events;"

# Interactive mode
sqlite3 runs.db
```

## Event types

Use these four canonical event types:

```python
# 1. Input events
recorder.log_event(run_id, "input", {
    "prompt": "user query here"
})

# 2. Output events
recorder.log_event(run_id, "output", {
    "result": "agent response here"
})

# 3. Tool calls
recorder.log_event(run_id, "tool_call", {
    "name": "search",
    "args": {"query": "python"},
    "result": {"status": "ok", "items": 5}
})

# 4. Artifact references
recorder.log_event(run_id, "artifact_ref", {
    "path": "/tmp/output.txt",
    "size": 1024
})
```

## Run the example

```bash
python examples/minimal.py
```

This demonstrates all four event types.

## Run tests

```bash
python -m unittest tests.test_recorder -v
```

All 7 tests should pass.

## What you get

After running scripts, you'll have:

* ✅ `runs.db` - SQLite database with all runs
* ✅ Human-inspectable records
* ✅ Append-only event log
* ✅ Versioned schema (v0.1)
* ✅ Environment snapshots

## API reference

### Core methods

```python
# Start
run_id = recorder.start_run(
    entrypoint="script.py",
    run_id="optional-custom-id"  # Auto-generated if omitted
)

# Log
event_id = recorder.log_event(
    run_id,
    event_type="input",  # input, output, tool_call, artifact_ref
    payload={"key": "value"}  # Any JSON-serializable dict
)

# End
recorder.end_run(
    run_id,
    status="success"  # success, failure, error
)

# Query
run = recorder.get_run(run_id)  # Returns dict or None
events = recorder.get_events(run_id)  # Returns list of dicts
```

## Database location

By default, `runs.db` is created in the current directory.

Override with:
```python
recorder = RunRecorder(db_path="path/to/custom.db")
```

## Next steps

* Read `docs/RECORDING_V0.md` for full recording documentation
* Read `docs/REPLAY_ENGINE_V0.md` for replay and comparison
* Check out `examples/minimal.py` for a complete example

## Comparing runs (Replay Engine)

Once you have recorded runs, you can compare them:

```python
from forkline import ReplayEngine, SQLiteStore, ReplayStatus

engine = ReplayEngine(SQLiteStore())
result = engine.compare_runs("baseline-run-id", "new-run-id")

if result.is_match():
    print("Runs are identical")
else:
    print(f"Diverged: {result.divergence.summary()}")
```

See `docs/REPLAY_ENGINE_V0.md` for full documentation.

## Philosophy

This is **boring infrastructure** by design:
* Explicit over clever
* No decorators, no magic
* Just append-only logging
* Human-inspectable at every step

If it feels too simple, that's the point.

---

**Ready to record and replay deterministic runs.**
