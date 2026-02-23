"""
Basic tool call recording example.

Demonstrates the three ways to record tool calls:
1. ToolCallRecorder context manager
2. record_tool_call decorator
3. RunRecorder.log_tool_call() convenience method

Run:
    python examples/tool_call_basic.py

Inspect:
    sqlite3 runs.db \
      "SELECT json_extract(payload, '$.tool_name') as tool, \
              json_extract(payload, '$.timing.duration_ms') as ms \
       FROM events WHERE type='tool_call';"
"""

import json
import os
import tempfile
import time

from forkline.core.redaction import RedactionPolicy
from forkline.core.tool_call import ToolCallRecorder, record_tool_call
from forkline.storage.recorder import RunRecorder


def main() -> None:
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "demo.db")
    recorder = RunRecorder(
        db_path=db_path,
        redaction_policy=RedactionPolicy(rules=[]),
    )
    run_id = recorder.start_run("examples/tool_call_basic.py")

    # --- 1. Context manager ---
    with ToolCallRecorder(recorder, run_id, "http.get") as tc:
        tc.set_request({"url": "https://api.example.com/users"})
        time.sleep(0.01)  # simulate latency
        tc.set_response({"status": 200, "count": 42})
        tc.set_metadata({"bytes_read": 2048})

    # --- 2. Decorator ---
    @record_tool_call(recorder, run_id, "math.multiply")
    def multiply(a, b):
        return a * b

    multiply(6, 7)

    # --- 3. Convenience method ---
    recorder.log_tool_call(
        run_id=run_id,
        tool_name="file.read",
        request={"path": "/etc/hostname"},
        response={"content": "myhost"},
        metadata={"bytes_read": 6},
    )

    recorder.end_run(run_id)

    # Print what was stored
    events = recorder.get_events(run_id)
    print(f"Recorded {len(events)} tool call events:\n")
    for e in events:
        p = e["payload"]
        ms = p["timing"].get("duration_ms", "n/a")
        print(f"  {p['tool_name']:20s}  {ms:>8} ms")
    print()
    print(json.dumps(events[0]["payload"], indent=2))


if __name__ == "__main__":
    main()
