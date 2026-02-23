"""
Tests for tool call instrumentation.

Covers:
- ToolCallPayload schema and serialization
- ToolCallRecorder context manager (capture, timing, error handling)
- record_tool_call decorator
- Integration with RunRecorder (storage boundary + redaction)
- Replay mode guardrails
- Determinism of tool call event capture
- No raw secrets persisted (end-to-end)
"""

import json
import tempfile
import time
import unittest

from forkline.core.redaction import (
    RedactionAction,
    RedactionPolicy,
    RedactionRule,
    RegexRedactionRule,
)
from forkline.core.replay import DeterminismViolationError, replay_mode
from forkline.core.tool_call import (
    ToolCallPayload,
    ToolCallRecorder,
    ToolCallTiming,
    record_tool_call,
)
from forkline.storage.recorder import RunRecorder


class TestToolCallPayload(unittest.TestCase):
    """Test ToolCallPayload schema and serialization."""

    def test_to_dict_minimal(self):
        payload = ToolCallPayload(
            tool_name="http.request",
            invocation_id="inv-001",
            request={"url": "https://api.example.com"},
        )
        d = payload.to_dict()
        self.assertEqual(d["tool_name"], "http.request")
        self.assertEqual(d["invocation_id"], "inv-001")
        self.assertEqual(d["request"], {"url": "https://api.example.com"})
        self.assertNotIn("response", d)
        self.assertNotIn("error", d)
        self.assertIn("timing", d)

    def test_to_dict_full(self):
        payload = ToolCallPayload(
            tool_name="db.query",
            invocation_id="inv-002",
            request={"sql": "SELECT 1"},
            response={"rows": [{"id": 1}]},
            error=None,
            timing=ToolCallTiming(
                started_at="2026-01-01T00:00:00Z",
                ended_at="2026-01-01T00:00:01Z",
                duration_ms=1000.0,
            ),
            metadata={"row_count": 1, "cache_hit": False},
        )
        d = payload.to_dict()
        self.assertEqual(d["tool_name"], "db.query")
        self.assertEqual(d["response"], {"rows": [{"id": 1}]})
        self.assertEqual(d["timing"]["duration_ms"], 1000.0)
        self.assertEqual(d["metadata"]["row_count"], 1)
        self.assertNotIn("error", d)

    def test_to_dict_with_error(self):
        payload = ToolCallPayload(
            tool_name="http.request",
            invocation_id="inv-003",
            request={"url": "https://api.example.com"},
            error={"type": "ConnectionError", "message": "timeout"},
        )
        d = payload.to_dict()
        self.assertIn("error", d)
        self.assertEqual(d["error"]["type"], "ConnectionError")
        self.assertNotIn("response", d)

    def test_from_dict_roundtrip(self):
        original = ToolCallPayload(
            tool_name="file.read",
            invocation_id="inv-004",
            request={"path": "/tmp/test.txt"},
            response={"content": "hello world"},
            timing=ToolCallTiming(
                started_at="2026-01-01T00:00:00Z",
                ended_at="2026-01-01T00:00:00.100Z",
                duration_ms=100.0,
            ),
            metadata={"bytes_read": 11},
        )
        d = original.to_dict()
        reconstructed = ToolCallPayload.from_dict(d)
        self.assertEqual(reconstructed.tool_name, original.tool_name)
        self.assertEqual(reconstructed.invocation_id, original.invocation_id)
        self.assertEqual(reconstructed.request, original.request)
        self.assertEqual(reconstructed.response, original.response)
        self.assertEqual(reconstructed.timing.duration_ms, original.timing.duration_ms)
        self.assertEqual(reconstructed.metadata, original.metadata)

    def test_from_dict_ignores_unknown_fields(self):
        d = {
            "tool_name": "test",
            "invocation_id": "inv-005",
            "request": {},
            "future_field": "should be ignored",
        }
        payload = ToolCallPayload.from_dict(d)
        self.assertEqual(payload.tool_name, "test")

    def test_json_serializable(self):
        payload = ToolCallPayload(
            tool_name="http.request",
            invocation_id="inv-006",
            request={"url": "https://example.com"},
            response={"status": 200},
        )
        json_str = json.dumps(payload.to_dict(), sort_keys=True)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["tool_name"], "http.request")


class TestToolCallRecorder(unittest.TestCase):
    """Test ToolCallRecorder context manager."""

    def test_basic_recording(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            with ToolCallRecorder(recorder, run_id, "http.request") as tc:
                tc.set_request({"url": "https://api.example.com"})
                tc.set_response({"status": 200, "body": "OK"})

            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "tool_call")

            payload = events[0]["payload"]
            self.assertEqual(payload["tool_name"], "http.request")
            self.assertEqual(payload["request"]["url"], "https://api.example.com")
            self.assertEqual(payload["response"]["status"], 200)

    def test_timing_captured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            with ToolCallRecorder(recorder, run_id, "slow.tool") as tc:
                tc.set_request({})
                time.sleep(0.05)
                tc.set_response({"done": True})

            events = recorder.get_events(run_id)
            timing = events[0]["payload"]["timing"]
            self.assertGreater(timing["duration_ms"], 40)
            self.assertTrue(timing["started_at"])
            self.assertTrue(timing["ended_at"])

    def test_error_captured_on_exception(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            with self.assertRaises(ValueError):
                with ToolCallRecorder(recorder, run_id, "failing.tool") as tc:
                    tc.set_request({"input": "bad"})
                    raise ValueError("something went wrong")

            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 1)
            payload = events[0]["payload"]
            self.assertIn("error", payload)
            self.assertEqual(payload["error"]["type"], "ValueError")
            self.assertEqual(payload["error"]["message"], "something went wrong")
            self.assertIsNone(payload.get("response"))

    def test_explicit_error_overrides_exception(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            with self.assertRaises(RuntimeError):
                with ToolCallRecorder(recorder, run_id, "tool") as tc:
                    tc.set_request({})
                    tc.set_error({"type": "CustomError", "message": "explicit"})
                    raise RuntimeError("implicit")

            events = recorder.get_events(run_id)
            payload = events[0]["payload"]
            self.assertEqual(payload["error"]["type"], "CustomError")

    def test_metadata_captured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            with ToolCallRecorder(recorder, run_id, "db.query") as tc:
                tc.set_request({"sql": "SELECT 1"})
                tc.set_response({"rows": [1]})
                tc.set_metadata({"row_count": 1, "cache_hit": True})

            events = recorder.get_events(run_id)
            metadata = events[0]["payload"]["metadata"]
            self.assertEqual(metadata["row_count"], 1)
            self.assertTrue(metadata["cache_hit"])

    def test_invocation_id_unique(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            for _ in range(3):
                with ToolCallRecorder(recorder, run_id, "tool") as tc:
                    tc.set_request({})
                    tc.set_response({})

            events = recorder.get_events(run_id)
            ids = [e["payload"]["invocation_id"] for e in events]
            self.assertEqual(len(set(ids)), 3)

    def test_replay_mode_blocks_tool_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            with replay_mode("test-run"):
                with self.assertRaises(DeterminismViolationError):
                    with ToolCallRecorder(recorder, run_id, "http.request") as tc:
                        tc.set_request({})

    def test_allow_in_replay_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            with replay_mode("test-run"):
                with ToolCallRecorder(
                    recorder, run_id, "tool", allow_in_replay=True
                ) as tc:
                    tc.set_request({"re_exec": True})
                    tc.set_response({"ok": True})

            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 1)


class TestRecordToolCallDecorator(unittest.TestCase):
    """Test the record_tool_call decorator."""

    def test_decorator_captures_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            @record_tool_call(recorder, run_id, "math.add")
            def add(a, b):
                return a + b

            result = add(2, 3)
            self.assertEqual(result, 5)

            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 1)
            payload = events[0]["payload"]
            self.assertEqual(payload["tool_name"], "math.add")
            self.assertEqual(payload["response"]["result"], 5)

    def test_decorator_captures_dict_return(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            @record_tool_call(recorder, run_id, "api.call")
            def api_call(endpoint):
                return {"status": 200, "body": "ok"}

            result = api_call(endpoint="/health")
            self.assertEqual(result["status"], 200)

            events = recorder.get_events(run_id)
            payload = events[0]["payload"]
            self.assertEqual(payload["response"]["status"], 200)

    def test_decorator_captures_exception(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            @record_tool_call(recorder, run_id, "failing.tool")
            def fail():
                raise RuntimeError("boom")

            with self.assertRaises(RuntimeError):
                fail()

            events = recorder.get_events(run_id)
            payload = events[0]["payload"]
            self.assertEqual(payload["error"]["type"], "RuntimeError")


class TestToolCallRedaction(unittest.TestCase):
    """Test that tool calls are redacted before persistence."""

    def test_default_policy_redacts_tool_call_secrets(self):
        """Sensitive fields in tool call payloads are redacted by default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(db_path=f"{tmpdir}/test.db")
            run_id = recorder.start_run("test.py")

            with ToolCallRecorder(recorder, run_id, "http.request") as tc:
                tc.set_request(
                    {
                        "url": "https://api.example.com",
                        "headers": {
                            "Authorization": "Bearer sk-12345",
                            "Content-Type": "application/json",
                        },
                        "api_key": "secret-key-123",
                    }
                )
                tc.set_response({"status": 200, "body": "ok"})

            events = recorder.get_events(run_id)
            payload = events[0]["payload"]

            self.assertEqual(payload["request"]["url"], "https://api.example.com")
            self.assertEqual(payload["request"]["api_key"], "[REDACTED]")

    def test_no_raw_secrets_persisted_end_to_end(self):
        """End-to-end test: raw secrets never appear in stored events."""
        raw_secret = "super-secret-api-key-12345"
        raw_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123"

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            recorder = RunRecorder(db_path=db_path)
            run_id = recorder.start_run("test.py")

            recorder.log_tool_call(
                run_id=run_id,
                tool_name="http.request",
                request={
                    "url": "https://api.example.com",
                    "api_key": raw_secret,
                    "headers": {"Authorization": f"Bearer {raw_token}"},
                },
                response={"status": 200},
            )

            events = recorder.get_events(run_id)
            event_json = json.dumps(events[0]["payload"])

            self.assertNotIn(raw_secret, event_json)
            self.assertNotIn(raw_token, event_json)

    def test_custom_redaction_policy_on_tool_calls(self):
        """Custom redaction policy is applied to tool call payloads."""
        policy = RedactionPolicy(
            rules=[
                RedactionRule(action=RedactionAction.MASK, key_pattern="sql"),
            ],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="email",
                    pattern_str=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
                    replacement="[REDACTED:email]",
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(db_path=f"{tmpdir}/test.db", redaction_policy=policy)
            run_id = recorder.start_run("test.py")

            recorder.log_tool_call(
                run_id=run_id,
                tool_name="db.query",
                request={
                    "sql": "SELECT * FROM users",
                    "note": "contact user@example.com",
                },
                response={"rows": [{"name": "Alice", "email": "alice@corp.com"}]},
            )

            events = recorder.get_events(run_id)
            payload = events[0]["payload"]

            self.assertEqual(payload["request"]["sql"], "[REDACTED]")
            self.assertIn("[REDACTED:email]", payload["request"]["note"])
            self.assertIn("[REDACTED:email]", payload["response"]["rows"][0]["email"])


class TestToolCallEventOrdering(unittest.TestCase):
    """Test that tool call events maintain strict ordering."""

    def test_event_ordering_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            for i in range(5):
                with ToolCallRecorder(recorder, run_id, f"tool.{i}") as tc:
                    tc.set_request({"index": i})
                    tc.set_response({"result": i * 2})

            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 5)

            for i, event in enumerate(events):
                self.assertEqual(event["payload"]["tool_name"], f"tool.{i}")
                self.assertEqual(event["payload"]["request"]["index"], i)

            event_ids = [e["event_id"] for e in events]
            self.assertEqual(event_ids, sorted(event_ids))

    def test_tool_calls_in_json_export(self):
        """Tool call events survive JSON export/import cycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            recorder.log_tool_call(
                run_id=run_id,
                tool_name="http.get",
                request={"url": "https://example.com"},
                response={"status": 200},
                timing={
                    "started_at": "2026-01-01T00:00:00Z",
                    "ended_at": "2026-01-01T00:00:01Z",
                    "duration_ms": 1000,
                },
                metadata={"bytes_read": 1024},
            )

            recorder.end_run(run_id)

            json_str = recorder.export_artifact_json(run_id)
            self.assertIsNotNone(json_str)

            from forkline.artifact.schema import RunArtifact

            artifact = RunArtifact.from_json(json_str)
            tool_events = [e for e in artifact.events if e.type == "tool_call"]
            self.assertEqual(len(tool_events), 1)

            payload = tool_events[0].payload
            self.assertEqual(payload["tool_name"], "http.get")
            self.assertEqual(payload["response"]["status"], 200)
            self.assertEqual(payload["metadata"]["bytes_read"], 1024)


class TestLogToolCallConvenience(unittest.TestCase):
    """Test RunRecorder.log_tool_call convenience method."""

    def test_log_tool_call_basic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            event_id = recorder.log_tool_call(
                run_id=run_id,
                tool_name="file.read",
                request={"path": "/tmp/data.txt"},
                response={"content": "hello"},
            )

            self.assertIsNotNone(event_id)
            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "tool_call")
            self.assertEqual(events[0]["payload"]["tool_name"], "file.read")

    def test_log_tool_call_auto_invocation_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            recorder.log_tool_call(run_id, "tool.a", request={})
            recorder.log_tool_call(run_id, "tool.b", request={})

            events = recorder.get_events(run_id)
            id_a = events[0]["payload"]["invocation_id"]
            id_b = events[1]["payload"]["invocation_id"]
            self.assertNotEqual(id_a, id_b)
            self.assertTrue(len(id_a) > 0)

    def test_log_tool_call_explicit_invocation_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            recorder.log_tool_call(
                run_id, "tool", request={}, invocation_id="stable-id-001"
            )

            events = recorder.get_events(run_id)
            self.assertEqual(events[0]["payload"]["invocation_id"], "stable-id-001")

    def test_log_tool_call_with_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                db_path=f"{tmpdir}/test.db",
                redaction_policy=RedactionPolicy(rules=[]),
            )
            run_id = recorder.start_run("test.py")

            recorder.log_tool_call(
                run_id,
                "http.request",
                request={"url": "https://example.com"},
                error={"type": "Timeout", "message": "30s exceeded"},
            )

            events = recorder.get_events(run_id)
            payload = events[0]["payload"]
            self.assertIn("error", payload)
            self.assertEqual(payload["error"]["type"], "Timeout")
            self.assertNotIn("response", payload)


class TestToolCallDeterminism(unittest.TestCase):
    """Test that tool call event capture is deterministic."""

    def test_same_input_same_output(self):
        """Same tool call payload produces identical stored events."""
        results = []
        for _ in range(3):
            with tempfile.TemporaryDirectory() as tmpdir:
                recorder = RunRecorder(db_path=f"{tmpdir}/test.db")
                run_id = recorder.start_run("test.py", run_id="fixed-run-id")

                recorder.log_tool_call(
                    run_id=run_id,
                    tool_name="http.get",
                    request={
                        "url": "https://api.example.com",
                        "api_key": "secret-123",
                        "headers": {"Authorization": "Bearer token"},
                    },
                    response={"status": 200, "body": "ok"},
                    invocation_id="fixed-inv-id",
                )

                events = recorder.get_events(run_id)
                results.append(events[0]["payload"])

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])


if __name__ == "__main__":
    unittest.main()
