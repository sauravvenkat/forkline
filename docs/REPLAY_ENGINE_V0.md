# Deterministic Replay Engine v0

**Status:** ✅ Implemented

Offline, local-first, deterministic replay engine for Forkline runs.

## Overview

The replay engine enables deterministic comparison of recorded runs. It loads runs from local storage, compares them step-by-step, and halts at the first point of divergence.

This is **not** observability, tracing, or production monitoring.  
This is **offline, local-first, deterministic debugging**.

### Core invariants

These are non-negotiable:

1. **Replay is deterministic** — No live network calls, no fresh randomness, no implicit clocks
2. **Artifacts are the source of truth** — LLM prompts, tool I/O, execution order
3. **First divergence wins** — Stop at first observable difference, never "heal"
4. **Replay is read-only** — Never mutate stored artifacts

## Architecture

### Data models

```
ReplayStatus (Enum)
├── MATCH              # Runs are identical
├── DIVERGED           # First divergence found
├── INCOMPLETE         # Replay has fewer steps
├── ORIGINAL_NOT_FOUND # Original run missing
└── REPLAY_NOT_FOUND   # Replay run missing

DivergencePoint
├── step_idx           # 0-based step index
├── step_name          # Human-readable step name
├── event_idx          # Event index (None if step-level)
├── divergence_type    # Category of divergence
├── field_diffs[]      # List of FieldDiff
└── context            # Additional debug info

FieldDiff
├── path               # JSON path (e.g., "payload.response")
├── expected           # Expected value
└── actual             # Actual value

ReplayResult
├── original_run_id
├── replay_run_id
├── status             # ReplayStatus
├── steps_compared
├── total_events_compared
├── divergence         # DivergencePoint (if diverged)
└── step_results[]     # Per-step results
```

### Components

**ReplayEngine** — Core engine for comparing runs

```python
from forkline import ReplayEngine, SQLiteStore

store = SQLiteStore()
engine = ReplayEngine(store)

result = engine.compare_runs("original-run-id", "replay-run-id")

if result.status == ReplayStatus.DIVERGED:
    print(result.divergence.summary())
```

**ReplayContext** — Injection mechanism for deterministic replay

```python
from forkline import ReplayContext

ctx = ReplayContext.from_run(original_run)

# Sequential access with cursor
event = ctx.next_event(step_idx=0, expected_type="llm_call")

# Peek without advancing
next_event = ctx.peek_event(step_idx=0)

# Filter by type
tool_calls = ctx.get_events_by_type(step_idx=1, event_type="tool_call")
```

**Comparison utilities** — Semantic comparison functions

```python
from forkline import deep_compare, compare_events, compare_steps

# Deep compare any two values
diffs = deep_compare(expected_dict, actual_dict)

# Compare events (ignores timestamps by default)
event_diffs = compare_events(expected_event, actual_event)

# Compare steps with all their events
matched, divergence = compare_steps(expected_step, actual_step)
```

## API

### ReplayEngine

#### `compare_runs(original_id, replay_id, ignore_timestamps=True) -> ReplayResult`

Compare two stored runs and find the first divergence.

```python
result = engine.compare_runs("run-abc", "run-xyz")

if result.is_match():
    print(f"Identical: {result.steps_compared} steps compared")
else:
    print(f"Diverged: {result.divergence.summary()}")
```

**Parameters:**
- `original_id`: ID of the expected (original) run
- `replay_id`: ID of the actual (replayed) run
- `ignore_timestamps`: If True, ignore timestamp metadata (default: True)

**Returns:** `ReplayResult`

#### `compare_loaded_runs(original, replay, ignore_timestamps=True) -> ReplayResult`

Compare two in-memory Run objects. Use when you already have Run objects loaded.

```python
result = engine.compare_loaded_runs(original_run, replay_run)
```

#### `validate_run(run_id) -> ReplayResult`

Self-comparison sanity check. A valid run should always match itself.

```python
result = engine.validate_run("run-abc")
assert result.status == ReplayStatus.MATCH
```

#### `load_run(run_id) -> Optional[Run]`

Load a run from storage.

```python
run = engine.load_run("run-abc")
if run is None:
    print("Run not found")
```

### ReplayContext

The `ReplayContext` provides recorded outputs as an "oracle" for deterministic replay.

#### `from_run(run) -> ReplayContext`

Create context from a Run object.

```python
ctx = ReplayContext.from_run(run)
```

#### `from_store(store, run_id) -> Optional[ReplayContext]`

Create context by loading from storage.

```python
ctx = ReplayContext.from_store(store, "run-abc")
```

#### `next_event(step_idx, expected_type=None) -> Optional[Event]`

Get next event in sequence, advancing cursor.

```python
# Get next event (any type)
event = ctx.next_event(step_idx=0)

# Get next event with type validation
event = ctx.next_event(step_idx=0, expected_type="llm_call")
# Raises ReplayOrderError if type doesn't match
```

#### `peek_event(step_idx) -> Optional[Event]`

Look at next event without advancing cursor.

```python
event = ctx.peek_event(step_idx=0)
# Cursor remains at same position
```

#### `get_events_by_type(step_idx, event_type) -> List[Event]`

Get all events of a specific type within a step.

```python
tool_calls = ctx.get_events_by_type(1, "tool_call")
for call in tool_calls:
    print(f"Tool: {call.payload['name']}")
```

#### `reset_cursor(step_idx=None) -> None`

Reset event cursor(s) to beginning.

```python
ctx.reset_cursor(step_idx=0)  # Reset specific step
ctx.reset_cursor()             # Reset all cursors
```

### ReplayResult

#### `is_match() -> bool`

Check if runs are identical.

```python
if result.is_match():
    print("Runs match")
```

#### `summary() -> str`

Human-readable summary.

```python
print(result.summary())
# "MATCH: 5 steps, 15 events compared"
# "DIVERGED: [event_payload_mismatch] at step[2]:process/event[1]: payload.response: expected 'Hi', got 'Hello'"
```

### DivergencePoint

#### `summary() -> str`

Human-readable summary of divergence location and cause.

```python
print(divergence.summary())
# "[event_payload_mismatch] at step[1]:process/event[0]: payload.tokens: expected 100, got 150"
```

## Divergence types

The engine classifies divergences into these types:

| Type | Description |
|------|-------------|
| `step_name_mismatch` | Step names differ |
| `event_count_mismatch` | Different number of events in step |
| `event_payload_mismatch` | Event payloads differ |
| `extra_steps_in_replay` | Replay has more steps than original |

## Usage examples

### Basic comparison

```python
from forkline import ReplayEngine, SQLiteStore, ReplayStatus

store = SQLiteStore()
engine = ReplayEngine(store)

result = engine.compare_runs("baseline-run", "new-run")

if result.status == ReplayStatus.MATCH:
    print("No behavioral changes detected")
elif result.status == ReplayStatus.DIVERGED:
    print(f"Divergence at: {result.divergence.summary()}")
    for diff in result.divergence.field_diffs:
        print(f"  {diff.path}: {diff.expected} -> {diff.actual}")
```

### Using ReplayContext for injection

```python
from forkline import ReplayContext, SQLiteStore

store = SQLiteStore()
run = store.load_run("recorded-run-id")
ctx = ReplayContext.from_run(run)

# Simulate replay with recorded outputs
for step_idx, step in enumerate(run.steps):
    print(f"Replaying step: {step.name}")
    
    # Get recorded LLM response
    llm_event = ctx.next_event(step_idx, expected_type="llm_call")
    if llm_event:
        recorded_response = llm_event.payload.get("response")
        # Use recorded_response instead of calling LLM
```

### CI integration

```python
import sys
from forkline import ReplayEngine, SQLiteStore, ReplayStatus

def check_for_regression(baseline_id: str, current_id: str) -> int:
    """CI check: fail if runs diverge."""
    engine = ReplayEngine(SQLiteStore())
    result = engine.compare_runs(baseline_id, current_id)
    
    if result.status == ReplayStatus.MATCH:
        print("✓ No regression detected")
        return 0
    elif result.status == ReplayStatus.DIVERGED:
        print(f"✗ Regression detected:")
        print(f"  {result.divergence.summary()}")
        return 1
    else:
        print(f"✗ Comparison failed: {result.status.value}")
        return 2

if __name__ == "__main__":
    sys.exit(check_for_regression(sys.argv[1], sys.argv[2]))
```

## Testing

Tests live in `tests/unit/test_replay_engine.py`.

```bash
python -m unittest tests.unit.test_replay_engine -v
```

Tests verify:
- Identical runs produce MATCH
- Any divergence in step name, event count, or payload is caught
- First divergence wins — engine halts at first difference
- Replay is deterministic — same inputs yield identical results
- ReplayContext cursor behavior
- Error handling for missing runs

## What's NOT in v0

Explicitly deferred:

- ❌ Automatic re-execution with injection (manual via ReplayContext)
- ❌ Partial replay (resume from step N)
- ❌ Retry/healing semantics
- ❌ Async replay
- ❌ Streaming comparison

v0 is **comparison infrastructure**. Automatic re-execution is future work.

## Comparison semantics

### Timestamps are metadata

By default, timestamps are **ignored** in comparisons because:
- They are metadata, not control flow
- Re-running code produces different timestamps
- Determinism requires ignoring wall-clock time

Disable with `ignore_timestamps=False` if timestamps are semantically meaningful.

### Field-by-field comparison

The engine uses **semantic comparison**, not textual diff:
- Dicts are compared field-by-field with JSON paths
- Lists are compared element-by-element with indices
- Type mismatches are caught explicitly

This produces actionable diffs like:
```
payload.response.tokens: expected 100, got 150
```

Not:
```
- {"response": {"tokens": 100}}
+ {"response": {"tokens": 150}}
```

### First divergence halts

The engine **stops at first divergence** because:
- Later divergences may be cascading effects
- Root cause is always the first difference
- Debugging should start at the first change

## Philosophy

> If the engine cannot tell you exactly where behavior changed, it has failed.

The replay engine is intentionally:
- **Boring** — explicit control flow, no magic
- **Inspectable** — all state is visible
- **Deterministic** — same inputs, same outputs, always
- **Loud** — fails fast on ambiguity

This makes it trustworthy infrastructure for forensic debugging.
