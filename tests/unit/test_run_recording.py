"""Tests for deterministic run recording v0."""

import os
import sqlite3
import tempfile
import unittest

from forkline.storage.recorder import RunRecorder


class TestRunRecorder(unittest.TestCase):
    """Test suite for RunRecorder."""

    def test_start_run_creates_versioned_record(self):
        """Test that starting a run creates a versioned record with env snapshot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test/example.py")

            run = recorder.get_run(run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run["run_id"], run_id)
            self.assertEqual(run["schema_version"], "0.1")
            self.assertEqual(run["entrypoint"], "test/example.py")
            self.assertIsNotNone(run["started_at"])
            self.assertIsNone(run["ended_at"])
            self.assertIsNone(run["status"])
            self.assertIsNotNone(run["python_version"])
            self.assertIsNotNone(run["platform"])
            self.assertEqual(run["cwd"], os.getcwd())

    def test_log_event_is_append_only(self):
        """Test that events are append-only and ordered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test.py")

            event1_id = recorder.log_event(
                run_id,
                event_type="input",
                payload={"prompt": "hello"},
            )

            event2_id = recorder.log_event(
                run_id,
                event_type="output",
                payload={"result": "world"},
            )

            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event_id"], event1_id)
            self.assertEqual(events[1]["event_id"], event2_id)
            self.assertEqual(events[0]["type"], "input")
            self.assertEqual(events[1]["type"], "output")
            self.assertEqual(events[0]["payload"], {"prompt": "hello"})
            self.assertEqual(events[1]["payload"], {"result": "world"})

    def test_end_run_updates_status(self):
        """Test that ending a run updates status and ended_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test.py")

            run_before = recorder.get_run(run_id)
            self.assertIsNone(run_before["ended_at"])
            self.assertIsNone(run_before["status"])

            recorder.end_run(run_id, status="success")

            run_after = recorder.get_run(run_id)
            self.assertIsNotNone(run_after["ended_at"])
            self.assertEqual(run_after["status"], "success")

    def test_all_event_types(self):
        """Test all canonical event types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test.py")

            recorder.log_event(run_id, "input", {"prompt": "test"})
            recorder.log_event(run_id, "output", {"result": "test"})
            recorder.log_event(
                run_id,
                "tool_call",
                {"name": "search", "args": {}, "result": {}},
            )
            recorder.log_event(run_id, "artifact_ref", {"path": "/tmp/file.txt"})

            events = recorder.get_events(run_id)
            self.assertEqual(len(events), 4)
            self.assertEqual(events[0]["type"], "input")
            self.assertEqual(events[1]["type"], "output")
            self.assertEqual(events[2]["type"], "tool_call")
            self.assertEqual(events[3]["type"], "artifact_ref")

    def test_multiple_runs_are_independent(self):
        """Test that multiple runs are stored independently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            recorder = RunRecorder(db_path=db_path)

            run1_id = recorder.start_run(entrypoint="test1.py")
            recorder.log_event(run1_id, "input", {"prompt": "run1"})
            recorder.end_run(run1_id, status="success")

            run2_id = recorder.start_run(entrypoint="test2.py")
            recorder.log_event(run2_id, "input", {"prompt": "run2"})
            recorder.end_run(run2_id, status="failure")

            run1 = recorder.get_run(run1_id)
            run2 = recorder.get_run(run2_id)

            self.assertNotEqual(run1["run_id"], run2["run_id"])
            self.assertEqual(run1["entrypoint"], "test1.py")
            self.assertEqual(run2["entrypoint"], "test2.py")
            self.assertEqual(run1["status"], "success")
            self.assertEqual(run2["status"], "failure")

            events1 = recorder.get_events(run1_id)
            events2 = recorder.get_events(run2_id)

            self.assertEqual(len(events1), 1)
            self.assertEqual(len(events2), 1)
            self.assertEqual(events1[0]["payload"], {"prompt": "run1"})
            self.assertEqual(events2[0]["payload"], {"prompt": "run2"})

    def test_database_is_human_inspectable(self):
        """Test that runs.db can be inspected with sqlite3 directly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test.py")
            recorder.log_event(run_id, "input", {"data": "test"})
            recorder.end_run(run_id, status="success")

            # Verify we can inspect with raw SQLite
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            runs = conn.execute("SELECT * FROM runs").fetchall()
            self.assertEqual(len(runs), 1)

            events = conn.execute("SELECT * FROM events").fetchall()
            self.assertEqual(len(events), 1)

            schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table'"
            ).fetchall()
            self.assertGreaterEqual(len(schema), 2)  # runs and events tables

            conn.close()

    def test_explicit_run_id(self):
        """Test that explicit run_id can be provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            recorder = RunRecorder(db_path=db_path)

            explicit_id = "my-custom-run-id"
            run_id = recorder.start_run(entrypoint="test.py", run_id=explicit_id)

            self.assertEqual(run_id, explicit_id)

            run = recorder.get_run(explicit_id)
            self.assertEqual(run["run_id"], explicit_id)


if __name__ == "__main__":
    unittest.main()
