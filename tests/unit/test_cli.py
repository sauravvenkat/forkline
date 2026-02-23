"""Tests for the Forkline CLI.

Covers argument parsing, command execution, rendering, and the event-level
diff engine.  Uses a temporary database for each test to avoid side effects.
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from io import StringIO
from unittest.mock import patch

from forkline.cli import _diff_events, main
from forkline.cli.render import (
    render_diff_json,
    render_diff_pretty,
    render_list_json,
    render_list_table,
    render_replay_json,
    render_replay_summary,
    render_run_result,
)
from forkline.storage.recorder import RunRecorder


class _TempDBMixin:
    """Provides a per-test temporary database and recorder."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.recorder = RunRecorder(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _create_run(
        self,
        entrypoint: str = "test.py",
        status: str = "ok",
        events: list | None = None,
    ) -> str:
        run_id = self.recorder.start_run(entrypoint=entrypoint)
        for ev in events or []:
            self.recorder.log_event(run_id, ev["type"], ev["payload"])
        self.recorder.end_run(run_id, status=status)
        return run_id


# =========================================================================
# Render helpers
# =========================================================================


class TestRenderRunResult(unittest.TestCase):
    def test_format(self):
        self.assertEqual(render_run_result("abc123"), "run_id: abc123")


class TestRenderListTable(_TempDBMixin, unittest.TestCase):
    def test_empty(self):
        self.assertEqual(render_list_table([]), "No runs found.")

    def test_header_and_row(self):
        runs = [
            {
                "run_id": "r1",
                "started_at": "2026-02-22T19:05:12+00:00",
                "entrypoint": "examples/minimal.py",
                "status": "ok",
            }
        ]
        out = render_list_table(runs)
        self.assertIn("ID", out)
        self.assertIn("Created", out)
        self.assertIn("r1", out)
        self.assertIn("2026-02-22 19:05:12", out)
        self.assertIn("examples/minimal.py", out)
        self.assertIn("ok", out)


class TestRenderListJSON(unittest.TestCase):
    def test_json_array(self):
        runs = [
            {
                "run_id": "r1",
                "started_at": "2026-02-22T19:05:12+00:00",
                "entrypoint": "ex.py",
                "status": "ok",
                "ended_at": "2026-02-22T19:05:13+00:00",
            }
        ]
        parsed = json.loads(render_list_json(runs))
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["run_id"], "r1")


class TestRenderReplaySummary(unittest.TestCase):
    def test_contains_fields(self):
        run = {
            "run_id": "r1",
            "entrypoint": "test.py",
            "status": "ok",
            "started_at": "2026-02-22T19:05:12+00:00",
            "ended_at": "2026-02-22T19:05:14+00:00",
        }
        events = [
            {"type": "input", "payload": {}},
            {"type": "output", "payload": {}},
            {"type": "tool_call", "payload": {}},
        ]
        out = render_replay_summary(run, events)
        self.assertIn("Run: r1", out)
        self.assertIn("Status: ok", out)
        self.assertIn("Total events: 3", out)
        self.assertIn("Duration:", out)
        self.assertIn("input: 1", out)
        self.assertIn("output: 1", out)
        self.assertIn("tool_call: 1", out)


class TestRenderReplayJSON(unittest.TestCase):
    def test_valid_json_with_all_fields(self):
        run = {
            "run_id": "r1",
            "entrypoint": "test.py",
            "status": "ok",
            "started_at": "2026-02-22T19:05:12+00:00",
            "ended_at": "2026-02-22T19:05:14+00:00",
        }
        events = [
            {"type": "input", "payload": {"prompt": "hello"}},
            {"type": "output", "payload": {"result": "world"}},
        ]
        parsed = json.loads(render_replay_json(run, events))
        self.assertEqual(parsed["run_id"], "r1")
        self.assertEqual(parsed["entrypoint"], "test.py")
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["total_events"], 2)
        self.assertEqual(len(parsed["events"]), 2)
        self.assertEqual(parsed["events"][0]["type"], "input")

    def test_empty_events(self):
        run = {
            "run_id": "r2",
            "entrypoint": "empty.py",
            "status": "ok",
            "started_at": None,
            "ended_at": None,
        }
        parsed = json.loads(render_replay_json(run, []))
        self.assertEqual(parsed["total_events"], 0)
        self.assertEqual(parsed["events"], [])


class TestRenderDiffPretty(unittest.TestCase):
    def test_identical(self):
        result = {"identical": True, "total_events_a": 2, "total_events_b": 2}
        self.assertEqual(render_diff_pretty(result, "a", "b"), "No differences")

    def test_diverged(self):
        result = {
            "identical": False,
            "divergence_index": 1,
            "old": {"type": "input", "payload": {"x": 1}},
            "new": {"type": "input", "payload": {"x": 2}},
            "total_events_a": 3,
            "total_events_b": 3,
        }
        out = render_diff_pretty(result, "a", "b")
        self.assertIn("Step 1 diverged:", out)
        self.assertIn("old.type: input", out)
        self.assertIn("new.type: input", out)


class TestRenderDiffJSON(unittest.TestCase):
    def test_identical(self):
        result = {"identical": True, "total_events_a": 0, "total_events_b": 0}
        parsed = json.loads(render_diff_json(result, "a", "b"))
        self.assertTrue(parsed["identical"])

    def test_diverged(self):
        result = {
            "identical": False,
            "divergence_index": 0,
            "old": {"type": "input", "payload": {}},
            "new": {"type": "output", "payload": {}},
            "total_events_a": 1,
            "total_events_b": 1,
        }
        parsed = json.loads(render_diff_json(result, "a", "b"))
        self.assertFalse(parsed["identical"])
        self.assertEqual(parsed["divergence_index"], 0)


# =========================================================================
# Event diff engine
# =========================================================================


class TestDiffEvents(unittest.TestCase):
    def test_identical(self):
        events = [
            {"type": "input", "payload": {"x": 1}},
            {"type": "output", "payload": {"y": 2}},
        ]
        result = _diff_events(events, list(events))
        self.assertTrue(result["identical"])

    def test_type_mismatch(self):
        a = [{"type": "input", "payload": {}}]
        b = [{"type": "output", "payload": {}}]
        result = _diff_events(a, b)
        self.assertFalse(result["identical"])
        self.assertEqual(result["divergence_index"], 0)

    def test_payload_mismatch(self):
        a = [{"type": "input", "payload": {"x": 1}}]
        b = [{"type": "input", "payload": {"x": 2}}]
        result = _diff_events(a, b)
        self.assertFalse(result["identical"])
        self.assertEqual(result["divergence_index"], 0)

    def test_different_lengths(self):
        a = [{"type": "input", "payload": {}}]
        b = [
            {"type": "input", "payload": {}},
            {"type": "output", "payload": {}},
        ]
        result = _diff_events(a, b)
        self.assertFalse(result["identical"])
        self.assertEqual(result["divergence_index"], 1)
        self.assertEqual(result["reason"], "different_event_count")

    def test_both_empty(self):
        result = _diff_events([], [])
        self.assertTrue(result["identical"])

    def test_finds_first_divergence(self):
        a = [
            {"type": "input", "payload": {"x": 1}},
            {"type": "tool_call", "payload": {"name": "a"}},
            {"type": "output", "payload": {"y": 1}},
        ]
        b = [
            {"type": "input", "payload": {"x": 1}},
            {"type": "tool_call", "payload": {"name": "b"}},
            {"type": "output", "payload": {"y": 2}},
        ]
        result = _diff_events(a, b)
        self.assertFalse(result["identical"])
        self.assertEqual(result["divergence_index"], 1)


# =========================================================================
# RunRecorder.list_runs
# =========================================================================


class TestListRuns(_TempDBMixin, unittest.TestCase):
    def test_empty(self):
        self.assertEqual(self.recorder.list_runs(), [])

    def test_ordered_newest_first(self):
        id_a = self._create_run(entrypoint="a.py")
        id_b = self._create_run(entrypoint="b.py")
        runs = self.recorder.list_runs()
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["run_id"], id_b)
        self.assertEqual(runs[1]["run_id"], id_a)

    def test_limit(self):
        self._create_run(entrypoint="a.py")
        self._create_run(entrypoint="b.py")
        self._create_run(entrypoint="c.py")
        runs = self.recorder.list_runs(limit=2)
        self.assertEqual(len(runs), 2)


# =========================================================================
# CLI integration (subprocess-free)
# =========================================================================


class TestCLIList(_TempDBMixin, unittest.TestCase):
    def test_list_shows_runs(self):
        run_id = self._create_run(entrypoint="script.py")
        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["list", "--db", self.db_path])
        output = out.getvalue()
        self.assertIn(run_id, output)
        self.assertIn("script.py", output)

    def test_list_json(self):
        run_id = self._create_run(entrypoint="script.py")
        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["list", "--db", self.db_path, "--json"])
        parsed = json.loads(out.getvalue())
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["run_id"], run_id)

    def test_list_empty(self):
        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["list", "--db", self.db_path])
        self.assertIn("No runs found", out.getvalue())


class TestCLIReplay(_TempDBMixin, unittest.TestCase):
    def test_replay_success(self):
        run_id = self._create_run(
            events=[
                {"type": "input", "payload": {"prompt": "hello"}},
                {"type": "output", "payload": {"result": "world"}},
            ]
        )
        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["replay", run_id, "--db", self.db_path])
        output = out.getvalue()
        self.assertIn(f"Run: {run_id}", output)
        self.assertIn("Status: ok", output)
        self.assertIn("Total events: 2", output)

    def test_replay_missing_run(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new_callable=StringIO):
                main(["replay", "nonexistent", "--db", self.db_path])
        self.assertEqual(ctx.exception.code, 2)

    def test_replay_json(self):
        run_id = self._create_run(events=[{"type": "input", "payload": {"x": 1}}])
        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["replay", run_id, "--db", self.db_path, "--json"])
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["run_id"], run_id)
        self.assertEqual(parsed["total_events"], 1)


class TestCLIDiff(_TempDBMixin, unittest.TestCase):
    def test_identical_runs(self):
        events = [
            {"type": "input", "payload": {"prompt": "hello"}},
            {"type": "output", "payload": {"result": "world"}},
        ]
        id_a = self._create_run(events=events)
        id_b = self._create_run(events=events)
        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["diff", id_a, id_b, "--db", self.db_path])
        self.assertIn("No differences", out.getvalue())

    def test_different_runs(self):
        id_a = self._create_run(
            events=[{"type": "input", "payload": {"prompt": "hello"}}]
        )
        id_b = self._create_run(
            events=[{"type": "input", "payload": {"prompt": "goodbye"}}]
        )
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO) as out:
                main(["diff", id_a, id_b, "--db", self.db_path])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Step 0 diverged", out.getvalue())

    def test_diff_json_format(self):
        events = [{"type": "input", "payload": {"x": 1}}]
        id_a = self._create_run(events=events)
        id_b = self._create_run(events=events)
        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["diff", id_a, id_b, "--db", self.db_path, "--format", "json"])
        parsed = json.loads(out.getvalue())
        self.assertTrue(parsed["identical"])

    def test_diff_missing_run(self):
        id_a = self._create_run()
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new_callable=StringIO):
                main(["diff", id_a, "nonexistent", "--db", self.db_path])
        self.assertEqual(ctx.exception.code, 2)

    def test_diff_different_event_counts(self):
        id_a = self._create_run(
            events=[
                {"type": "input", "payload": {}},
                {"type": "output", "payload": {}},
            ]
        )
        id_b = self._create_run(events=[{"type": "input", "payload": {}}])
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO) as out:
                main(["diff", id_a, id_b, "--db", self.db_path])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Event count differs", out.getvalue())

    def test_diff_json_diverged(self):
        id_a = self._create_run(events=[{"type": "input", "payload": {"x": 1}}])
        id_b = self._create_run(events=[{"type": "input", "payload": {"x": 2}}])
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO) as out:
                main(
                    [
                        "diff",
                        id_a,
                        id_b,
                        "--db",
                        self.db_path,
                        "--format",
                        "json",
                    ]
                )
        self.assertEqual(ctx.exception.code, 1)
        parsed = json.loads(out.getvalue())
        self.assertFalse(parsed["identical"])
        self.assertEqual(parsed["divergence_index"], 0)


class TestCLIRun(_TempDBMixin, unittest.TestCase):
    def test_run_missing_file(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new_callable=StringIO):
                main(["run", "--db", self.db_path, "nonexistent.py"])
        self.assertEqual(ctx.exception.code, 2)

    def test_run_success(self):
        script = os.path.join(self._tmpdir.name, "ok.py")
        with open(script, "w") as f:
            f.write("print('hello')\n")

        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["run", "--db", self.db_path, script])

        output = out.getvalue()
        self.assertIn("run_id:", output)

        runs = self.recorder.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "ok")
        self.assertEqual(runs[0]["entrypoint"], script)

    def test_run_failed_script(self):
        script = os.path.join(self._tmpdir.name, "fail.py")
        with open(script, "w") as f:
            f.write("import sys; sys.exit(42)\n")

        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO) as out:
                main(["run", "--db", self.db_path, script])

        self.assertEqual(ctx.exception.code, 42)
        self.assertIn("run_id:", out.getvalue())

        runs = self.recorder.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")

    def test_run_with_args(self):
        script = os.path.join(self._tmpdir.name, "args.py")
        with open(script, "w") as f:
            f.write(
                textwrap.dedent(
                    """\
                import sys
                assert len(sys.argv) == 3, f"expected 3 args, got {sys.argv}"
                assert sys.argv[1] == "--name"
                assert sys.argv[2] == "test"
                """
                )
            )

        with patch("sys.stdout", new_callable=StringIO) as out:
            main(["run", "--db", self.db_path, script, "--", "--name", "test"])

        self.assertIn("run_id:", out.getvalue())


class TestCLIHelp(unittest.TestCase):
    def test_no_command_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO):
                main([])
        self.assertEqual(ctx.exception.code, 1)

    def test_help_exits_0(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO):
                main(["--help"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
