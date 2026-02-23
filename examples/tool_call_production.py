"""
Production-grade tool call recording with redaction.

Simulates a realistic agentic workflow that:
1. Calls an LLM to plan a query
2. Executes a database query
3. Calls an external HTTP API with auth headers
4. Writes results to a file
5. Handles a failing tool call gracefully

All tool calls are recorded with full timing, metadata, and
deterministic redaction of secrets (API keys, tokens, JWTs,
auth headers) before anything touches disk.

Run:
    python examples/tool_call_production.py

Inspect:
    python -c "
    from forkline.storage.recorder import RunRecorder
    r = RunRecorder(db_path='/tmp/forkline_prod_demo/runs.db')
    for run in r.list_runs():
        print(run['run_id'][:12], run['status'])
        for e in r.get_events(run['run_id']):
            if e['type'] == 'tool_call':
                p = e['payload']
                print(f'  {p[\"tool_name\"]:25s}  '
                      f'{p[\"timing\"][\"duration_ms\"]:8.1f}ms')
    "
"""

import json
import os
import shutil
import time

from forkline.core.redaction import (
    RegexRedactionRule,
    create_default_policy,
)
from forkline.core.tool_call import ToolCallRecorder, record_tool_call
from forkline.storage.recorder import RunRecorder

# ── Simulated external services ───────────────────────────


def _sim_llm(prompt: str) -> dict:
    """Simulate an LLM API call."""
    time.sleep(0.02)
    return {
        "model": "gpt-4",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        "SELECT u.id, u.email, o.total "
                        "FROM users u JOIN orders o "
                        "ON u.id = o.user_id "
                        "WHERE o.created_at > '2026-01-01'"
                    ),
                }
            }
        ],
        "usage": {"prompt_tokens": 45, "completion_tokens": 32},
    }


def _sim_db_query(sql: str) -> dict:
    """Simulate a database query."""
    time.sleep(0.015)
    return {
        "columns": ["id", "email", "total"],
        "rows": [
            [1, "alice@corp.com", 149.99],
            [2, "bob@startup.io", 89.50],
            [3, "carol@enterprise.co", 320.00],
        ],
        "row_count": 3,
        "query_time_ms": 12.4,
    }


def _sim_http_post(url: str, headers: dict, body: dict) -> dict:
    """Simulate an HTTP POST to an external webhook."""
    time.sleep(0.01)
    return {
        "status_code": 202,
        "body": {"accepted": True, "id": "evt_abc123"},
    }


def _sim_file_write(path: str, content: str) -> dict:
    """Simulate writing a report file."""
    time.sleep(0.005)
    return {"bytes_written": len(content), "path": path}


def _sim_failing_call() -> dict:
    """Simulate a tool call that fails."""
    time.sleep(0.008)
    raise ConnectionError("upstream service unavailable (503)")


# ── Agent workflow ────────────────────────────────────────


def run_agent():
    db_dir = "/tmp/forkline_prod_demo"
    if os.path.exists(db_dir):
        shutil.rmtree(db_dir)
    os.makedirs(db_dir)

    # Production redaction: default policy + custom regex for
    # connection strings with embedded credentials
    policy = create_default_policy()
    policy.regex_rules.append(
        RegexRedactionRule.from_config(
            name="connection_string",
            pattern_str=r"://[^:]+:[^@]+@",
            replacement="://[REDACTED:credentials]@",
        )
    )

    recorder = RunRecorder(
        db_path=os.path.join(db_dir, "runs.db"),
        redaction_policy=policy,
    )
    run_id = recorder.start_run("examples/tool_call_production.py")
    print(f"Run started: {run_id[:12]}...\n")

    # ── Step 1: LLM planning call ──
    print("1. Calling LLM for query planning...")
    with ToolCallRecorder(recorder, run_id, "llm.chat") as tc:
        tc.set_request(
            {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a SQL assistant.",
                    },
                    {
                        "role": "user",
                        "content": "Get all users with orders in 2026",
                    },
                ],
                "api_key": "sk-proj-REAL_SECRET_KEY_HERE",
            }
        )
        result = _sim_llm("Get all users with orders in 2026")
        tc.set_response(result)
        tc.set_metadata(
            {
                "prompt_tokens": result["usage"]["prompt_tokens"],
                "completion_tokens": result["usage"]["completion_tokens"],
            }
        )
    sql = result["choices"][0]["message"]["content"]
    print(f"   SQL: {sql[:50]}...")

    # ── Step 2: Database query ──
    print("2. Executing database query...")
    with ToolCallRecorder(recorder, run_id, "postgres.query") as tc:
        tc.set_request(
            {
                "sql": sql,
                "connection_string": "postgresql://admin:s3cret@db.internal:5432/prod",
            }
        )
        db_result = _sim_db_query(sql)
        tc.set_response(db_result)
        tc.set_metadata(
            {
                "row_count": db_result["row_count"],
                "query_time_ms": db_result["query_time_ms"],
            }
        )
    print(f"   Got {db_result['row_count']} rows")

    # ── Step 3: HTTP webhook with auth ──
    print("3. Posting results to webhook...")
    jwt_token = (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiJhZ2VudC0xIiwiZXhwIjoxNzM3MjAwMDAwfQ"
        ".kR9x7Yz_signature_bytes"
    )
    with ToolCallRecorder(recorder, run_id, "http.post") as tc:
        tc.set_request(
            {
                "url": "https://hooks.slack.com/workflows/T123/ABC",
                "headers": {
                    "Authorization": f"Bearer {jwt_token}",
                    "Content-Type": "application/json",
                    "X-Api-Key": "whsec_live_key_456",
                    "Cookie": "session=sess_abc123; csrf=tok_xyz",
                },
                "body": {
                    "text": f"Query returned {db_result['row_count']} rows",
                    "rows": db_result["rows"],
                },
            }
        )
        http_result = _sim_http_post(
            "https://hooks.slack.com/workflows/T123/ABC",
            headers={},
            body={},
        )
        tc.set_response(http_result)
        tc.set_metadata({"status_code": http_result["status_code"]})
    print(f"   Webhook accepted: {http_result['body']['id']}")

    # ── Step 4: File write ──
    print("4. Writing report file...")
    report = json.dumps(db_result["rows"], indent=2)

    @record_tool_call(recorder, run_id, "file.write")
    def write_report(path, content):
        return _sim_file_write(path, content)

    file_result = write_report(
        path="/tmp/forkline_prod_demo/report.json",
        content=report,
    )
    print(f"   Wrote {file_result['bytes_written']} bytes")

    # ── Step 5: Failing tool call ──
    print("5. Calling flaky upstream service...")
    with ToolCallRecorder(recorder, run_id, "http.get") as tc:
        tc.set_request(
            {
                "url": "https://flaky-api.internal/status",
                "headers": {
                    "Authorization": "Bearer internal-service-token",
                },
            }
        )
        try:
            _sim_failing_call()
        except ConnectionError as e:
            tc.set_error({"type": type(e).__name__, "message": str(e)})
            tc.set_metadata({"retry_count": 3})
            print(f"   Failed: {e}")

    recorder.end_run(run_id, status="success")

    # ── Inspect what was stored ──
    print("\n" + "=" * 60)
    print("STORED EVENTS (after redaction)")
    print("=" * 60 + "\n")

    events = recorder.get_events(run_id)
    for i, e in enumerate(events):
        if e["type"] != "tool_call":
            continue
        p = e["payload"]
        ms = p["timing"].get("duration_ms", 0)
        status = "error" if p.get("error") else "ok"
        print(f"Event {i}: {p['tool_name']:25s} " f"{ms:8.1f}ms  [{status}]")

    print("\n--- Full HTTP POST event (redacted) ---\n")
    http_event = [e for e in events if e["payload"].get("tool_name") == "http.post"][0]
    print(json.dumps(http_event["payload"], indent=2))

    # ── Verify no secrets in storage ──
    print("\n--- Security verification ---\n")
    all_json = json.dumps([e["payload"] for e in events])
    secrets = [
        "sk-proj-REAL_SECRET_KEY_HERE",
        "s3cret",
        "whsec_live_key_456",
        "sess_abc123",
        "internal-service-token",
    ]
    leaked = [s for s in secrets if s in all_json]
    if leaked:
        print(f"LEAKED secrets: {leaked}")
    else:
        print("No secrets leaked to storage.")

    has_jwt = "eyJhbGciOiJSUzI1NiI" in all_json
    print(f"JWT tokens in storage: {'YES (LEAK!)' if has_jwt else 'No'}")

    # ── JSON export ──
    print("\n--- JSON artifact export ---\n")
    artifact_json = recorder.export_artifact_json(run_id)
    artifact = json.loads(artifact_json)
    tool_events = [e for e in artifact["events"] if e["type"] == "tool_call"]
    print(f"Exported {len(tool_events)} tool_call events")
    print(f"Artifact schema: {artifact['schema_version']}")
    print(f"Total artifact size: {len(artifact_json)} bytes")


if __name__ == "__main__":
    run_agent()
