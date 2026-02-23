# Tool Visibility

Forkline records tool invocations (DB queries, API calls, file operations) as
first-class `tool_call` events in the run artifact. This gives agents full
visibility into what tools did, when, and how long they took.

## Event Schema

Each tool invocation is captured as a single event with `type: "tool_call"`.
This uses the **single-event model** (Option 1): one event contains both the
request and response, linked by a stable `invocation_id`.

### Payload Structure

```json
{
  "tool_name": "http.request",
  "invocation_id": "a1b2c3d4e5f6...",
  "request": {
    "url": "https://api.example.com/data",
    "method": "GET",
    "headers": { "Authorization": "[REDACTED]" }
  },
  "response": {
    "status": 200,
    "body": "{...}"
  },
  "timing": {
    "started_at": "2026-01-15T10:30:00.000Z",
    "ended_at": "2026-01-15T10:30:00.250Z",
    "duration_ms": 250.0
  },
  "metadata": {
    "bytes_read": 1024,
    "cache_hit": false
  }
}
```

### Field Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `tool_name` | string | yes | Dotted tool identifier (e.g., `bigquery.query`, `http.request`, `file.read`) |
| `invocation_id` | string | yes | Stable UUID per call. Auto-generated if not provided. |
| `request` | dict | yes | Request payload (redacted before persistence) |
| `response` | dict | no | Response payload (redacted). Absent if call errored. |
| `error` | dict | no | Error info with `type` and `message` fields. Absent on success. |
| `timing.started_at` | string | yes | ISO8601 UTC timestamp when call started |
| `timing.ended_at` | string | yes | ISO8601 UTC timestamp when call completed |
| `timing.duration_ms` | float | yes | Wall-clock duration in milliseconds |
| `metadata` | dict | no | Optional metadata: `status_code`, `row_count`, `bytes_read`, `retry_count`, `cache_hit` |

### Error Events

When a tool call fails, the `error` field is populated and `response` is absent:

```json
{
  "tool_name": "http.request",
  "invocation_id": "...",
  "request": { "url": "https://api.example.com" },
  "error": {
    "type": "ConnectionError",
    "message": "Connection refused"
  },
  "timing": { "started_at": "...", "ended_at": "...", "duration_ms": 5023.0 }
}
```

## Recording Tool Calls

### Context Manager (recommended)

Use `ToolCallRecorder` for explicit control over request/response capture:

```python
from forkline.core.tool_call import ToolCallRecorder
from forkline.storage.recorder import RunRecorder

recorder = RunRecorder(db_path="runs.db")
run_id = recorder.start_run("my_agent.py")

with ToolCallRecorder(recorder, run_id, "http.request") as tc:
    tc.set_request({"url": "https://api.example.com", "method": "GET"})
    response = requests.get("https://api.example.com")
    tc.set_response({"status": response.status_code, "body": response.text})
    tc.set_metadata({"bytes_read": len(response.content)})
```

Timing is captured automatically. If an exception occurs inside the `with`
block, the error is recorded and the exception re-raised.

### Decorator

Use `record_tool_call` for simpler wrapping of existing functions:

```python
from forkline.core.tool_call import record_tool_call

@record_tool_call(recorder, run_id, "db.query")
def query_db(sql, params=None):
    return db.execute(sql, params).fetchall()

rows = query_db("SELECT * FROM users WHERE active = ?", params=[True])
```

The function's arguments become the request payload. The return value becomes
the response payload (wrapped in `{"result": ...}` if not a dict).

### Convenience Method

Use `RunRecorder.log_tool_call()` for manual construction:

```python
recorder.log_tool_call(
    run_id=run_id,
    tool_name="bigquery.query",
    request={"sql": "SELECT count(*) FROM events"},
    response={"rows": [{"count": 42}]},
    timing={
        "started_at": "2026-01-15T10:30:00Z",
        "ended_at": "2026-01-15T10:30:01Z",
        "duration_ms": 1000
    },
    metadata={"row_count": 1},
)
```

## Redaction

All tool call payloads are redacted **before** persistence. Sensitive data
in `request`, `response`, `error`, and `metadata` fields is redacted according
to the active redaction policy. See [redaction.md](redaction.md) for details.

The redaction pipeline:

1. Tool call happens (raw data)
2. Build `tool_call` payload
3. Apply `redact(payload)` at storage boundary
4. Write redacted event to SQLite
5. Raw sensitive data is **never** stored

## Replay Integration

During replay, `ToolCallRecorder` enforces determinism guardrails:

- **Replay mode active**: `ToolCallRecorder.__enter__()` raises
  `DeterminismViolationError` to prevent live tool calls.
- **Re-exec mode**: Set `allow_in_replay=True` to permit tool execution
  during replay (the call is still recorded with redaction).
- **Recorded response substitution**: Use `ReplayContext.get_events_by_type()`
  to retrieve recorded `tool_call` events and inject their responses.

```python
from forkline.core.replay import replay_mode

with replay_mode("run-abc123"):
    # This raises DeterminismViolationError:
    with ToolCallRecorder(recorder, run_id, "http.request") as tc:
        ...

    # This is allowed (re-exec mode):
    with ToolCallRecorder(recorder, run_id, "http.request", allow_in_replay=True) as tc:
        ...
```

## Event Ordering

Tool call events maintain strict monotonic ordering via SQLite's
auto-incrementing `event_id`. This guarantees:

- Events are stored in the order they occurred
- Replay can reconstruct the exact sequence of tool calls
- `event_id` ordering matches wall-clock ordering within a run

## Storage

Tool call events are stored in both:

- **SQLite** (`events` table): For efficient querying and local replay
- **JSON export** (`RunArtifact.to_json()`): For portable artifact exchange

Both formats preserve the full `tool_call` payload structure.
