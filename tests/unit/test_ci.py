"""Tests for Forkline CI integration layer.

Covers exit codes, offline enforcement, artifact normalization,
CI commands, and the end-to-end record/diff/check workflow.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import textwrap
import unittest
from io import StringIO
from unittest.mock import patch

from forkline.ci.commands import (
    ci_check,
    ci_diff,
    ci_normalize,
    ci_record,
    ci_replay,
)
from forkline.ci.exitcodes import (
    EXIT_ARTIFACT_ERROR,
    EXIT_DIFF_DETECTED,
    EXIT_INTERNAL_ERROR,
    EXIT_OFFLINE_VIOLATION,
    EXIT_REPLAY_FAILED,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
)
from forkline.ci.normalize import (
    NORMALIZED_TIMESTAMP,
    normalize_artifact,
    normalize_artifact_json,
)
from forkline.ci.offline import (
    ForklineOfflineError,
    disable_offline_mode,
    enable_offline_mode,
    is_offline_mode,
    offline_context,
)
from forkline.cli import main


class _TempDirMixin:
    """Provides a per-test temporary directory."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write_script(self, name: str, content: str) -> str:
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as f:
            f.write(textwrap.dedent(content))
        return path

    def _write_artifact(self, name: str, data: dict) -> str:
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        return path

    def _make_artifact(self, **overrides) -> dict:
        base = {
            "schema_version": "1.0",
            "run_id": "test-run-001",
            "entrypoint": "test.py",
            "started_at": "2026-01-01T00:00:00+00:00",
            "ended_at": "2026-01-01T00:00:01+00:00",
            "status": "ok",
            "forkline_version": "0.4.0",
            "events": [
                {
                    "event_id": 0,
                    "run_id": "test-run-001",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "type": "input",
                    "payload": {"prompt": "hello"},
                },
                {
                    "event_id": 1,
                    "run_id": "test-run-001",
                    "ts": "2026-01-01T00:00:01+00:00",
                    "type": "output",
                    "payload": {"result": "world"},
                },
            ],
            "metadata": {
                "python_version": "3.12.0",
                "platform": "macOS-15.0",
                "cwd": "/tmp/test",
            },
        }
        base.update(overrides)
        return base


# =========================================================================
# Exit Codes
# =========================================================================


class TestExitCodes(unittest.TestCase):
    """Exit codes must have specific stable values."""

    def test_exit_code_values(self):
        self.assertEqual(EXIT_SUCCESS, 0)
        self.assertEqual(EXIT_DIFF_DETECTED, 1)
        self.assertEqual(EXIT_USAGE_ERROR, 2)
        self.assertEqual(EXIT_REPLAY_FAILED, 3)
        self.assertEqual(EXIT_OFFLINE_VIOLATION, 4)
        self.assertEqual(EXIT_ARTIFACT_ERROR, 5)
        self.assertEqual(EXIT_INTERNAL_ERROR, 6)

    def test_all_distinct(self):
        codes = [
            EXIT_SUCCESS,
            EXIT_DIFF_DETECTED,
            EXIT_USAGE_ERROR,
            EXIT_REPLAY_FAILED,
            EXIT_OFFLINE_VIOLATION,
            EXIT_ARTIFACT_ERROR,
            EXIT_INTERNAL_ERROR,
        ]
        self.assertEqual(len(codes), len(set(codes)))


# =========================================================================
# Offline Enforcement
# =========================================================================


class TestOfflineMode(unittest.TestCase):
    def tearDown(self):
        disable_offline_mode()

    def test_offline_context_blocks_socket(self):
        with offline_context():
            self.assertTrue(is_offline_mode())
            with self.assertRaises(ForklineOfflineError) as ctx:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("example.com", 80))
            self.assertIn("offline mode", str(ctx.exception))

    def test_offline_blocks_create_connection(self):
        with offline_context():
            with self.assertRaises(ForklineOfflineError):
                socket.create_connection(("example.com", 80))

    def test_offline_blocks_getaddrinfo(self):
        with offline_context():
            with self.assertRaises(ForklineOfflineError):
                socket.getaddrinfo("example.com", 80)

    def test_offline_error_is_deterministic(self):
        msg1 = None
        msg2 = None
        with offline_context():
            try:
                socket.create_connection(("example.com", 80))
            except ForklineOfflineError as e:
                msg1 = str(e)
            try:
                socket.create_connection(("example.com", 80))
            except ForklineOfflineError as e:
                msg2 = str(e)
        self.assertIsNotNone(msg1)
        self.assertEqual(msg1, msg2)

    def test_offline_restores_after_context(self):
        with offline_context():
            self.assertTrue(is_offline_mode())
        self.assertFalse(is_offline_mode())

    def test_enable_disable_idempotent(self):
        enable_offline_mode()
        enable_offline_mode()
        self.assertTrue(is_offline_mode())
        disable_offline_mode()
        disable_offline_mode()
        self.assertFalse(is_offline_mode())

    def test_offline_error_attributes(self):
        err = ForklineOfflineError("socket.connect")
        self.assertEqual(err.operation, "socket.connect")
        self.assertIn("FORKLINE_OFFLINE", str(err))


# =========================================================================
# Artifact Normalization
# =========================================================================


class TestNormalization(_TempDirMixin, unittest.TestCase):
    def test_timestamps_normalized(self):
        artifact = self._make_artifact()
        result = normalize_artifact(artifact)
        self.assertEqual(result["started_at"], NORMALIZED_TIMESTAMP)
        self.assertEqual(result["ended_at"], NORMALIZED_TIMESTAMP)
        for event in result["events"]:
            self.assertEqual(event["ts"], NORMALIZED_TIMESTAMP)

    def test_metadata_stripped(self):
        artifact = self._make_artifact()
        result = normalize_artifact(artifact)
        self.assertNotIn("metadata", result)

    def test_metadata_preserved_when_disabled(self):
        artifact = self._make_artifact()
        result = normalize_artifact(artifact, normalize_metadata=False)
        self.assertIn("metadata", result)
        self.assertIn("python_version", result["metadata"])

    def test_timestamps_preserved_when_disabled(self):
        artifact = self._make_artifact()
        original_ts = artifact["started_at"]
        result = normalize_artifact(artifact, normalize_timestamps=False)
        self.assertEqual(result["started_at"], original_ts)

    def test_events_ordered_by_event_id(self):
        artifact = self._make_artifact()
        artifact["events"][0]["event_id"] = 5
        artifact["events"][1]["event_id"] = 2
        result = normalize_artifact(artifact)
        self.assertEqual(result["events"][0]["event_id"], 2)
        self.assertEqual(result["events"][1]["event_id"], 5)

    def test_normalize_ids(self):
        artifact = self._make_artifact()
        result = normalize_artifact(artifact, normalize_ids=True)
        self.assertEqual(result["run_id"], "normalized-run-id")
        self.assertEqual(result["events"][0]["event_id"], 0)
        self.assertEqual(result["events"][1]["event_id"], 1)

    def test_normalize_deterministic(self):
        artifact = self._make_artifact()
        r1 = normalize_artifact(artifact)
        r2 = normalize_artifact(artifact)
        self.assertEqual(
            json.dumps(r1, sort_keys=True),
            json.dumps(r2, sort_keys=True),
        )

    def test_normalize_json_roundtrip(self):
        artifact = self._make_artifact()
        json_str = json.dumps(artifact)
        result = normalize_artifact_json(json_str)
        parsed = json.loads(result)
        self.assertEqual(parsed["started_at"], NORMALIZED_TIMESTAMP)

    def test_original_not_mutated(self):
        artifact = self._make_artifact()
        original_ts = artifact["started_at"]
        normalize_artifact(artifact)
        self.assertEqual(artifact["started_at"], original_ts)


# =========================================================================
# CI Record Command
# =========================================================================


class TestCIRecord(_TempDirMixin, unittest.TestCase):
    def test_record_success(self):
        script = self._write_script("ok.py", "print('hello')\n")
        out_path = os.path.join(self.tmpdir, "out.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_record(script, out_path)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertTrue(os.path.isfile(out_path))
        with open(out_path) as f:
            artifact = json.load(f)
        self.assertEqual(artifact["schema_version"], "1.0")
        self.assertIn("events", artifact)

    def test_record_missing_entrypoint(self):
        out_path = os.path.join(self.tmpdir, "out.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_record("/nonexistent.py", out_path)
        self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_record_failed_script(self):
        script = self._write_script("fail.py", "import sys; sys.exit(1)\n")
        out_path = os.path.join(self.tmpdir, "out.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_record(script, out_path)
        self.assertEqual(code, EXIT_REPLAY_FAILED)

    def test_record_creates_directories(self):
        script = self._write_script("ok.py", "print('hello')\n")
        out_path = os.path.join(self.tmpdir, "sub", "dir", "out.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_record(script, out_path)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertTrue(os.path.isfile(out_path))

    def test_record_artifact_is_normalized(self):
        script = self._write_script("ok.py", "print('hello')\n")
        out_path = os.path.join(self.tmpdir, "out.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            ci_record(script, out_path)
        with open(out_path) as f:
            artifact = json.load(f)
        self.assertEqual(artifact["started_at"], NORMALIZED_TIMESTAMP)

    def test_record_deterministic(self):
        script = self._write_script("ok.py", "print('hello')\n")
        out1 = os.path.join(self.tmpdir, "out1.run.json")
        out2 = os.path.join(self.tmpdir, "out2.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            ci_record(script, out1)
        with patch("sys.stderr", new_callable=StringIO):
            ci_record(script, out2)
        with open(out1) as f:
            a1 = json.load(f)
        with open(out2) as f:
            a2 = json.load(f)
        n1 = normalize_artifact(a1, normalize_ids=True)
        n2 = normalize_artifact(a2, normalize_ids=True)
        self.assertEqual(
            json.dumps(n1, sort_keys=True),
            json.dumps(n2, sort_keys=True),
        )


# =========================================================================
# CI Replay Command
# =========================================================================


class TestCIReplay(_TempDirMixin, unittest.TestCase):
    def test_replay_valid_artifact(self):
        artifact_path = self._write_artifact("test.run.json", self._make_artifact())
        with patch("sys.stdout", new_callable=StringIO) as out:
            code = ci_replay(artifact_path)
        self.assertEqual(code, EXIT_SUCCESS)
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["event_count"], 2)

    def test_replay_missing_file(self):
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_replay("/nonexistent.json")
        self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_replay_invalid_json(self):
        path = os.path.join(self.tmpdir, "bad.json")
        with open(path, "w") as f:
            f.write("not json{{{")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_replay(path)
        self.assertEqual(code, EXIT_ARTIFACT_ERROR)

    def test_replay_missing_schema_version(self):
        artifact = self._make_artifact()
        del artifact["schema_version"]
        path = self._write_artifact("no_schema.json", artifact)
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_replay(path)
        self.assertEqual(code, EXIT_ARTIFACT_ERROR)

    def test_replay_strict_empty_payload(self):
        artifact = self._make_artifact()
        artifact["events"].append(
            {
                "event_id": 2,
                "run_id": "test-run-001",
                "ts": "2026-01-01T00:00:02+00:00",
                "type": "output",
                "payload": {},
            }
        )
        path = self._write_artifact("strict.json", artifact)
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_replay(path, strict=True)
        self.assertEqual(code, EXIT_ARTIFACT_ERROR)

    def test_replay_not_strict_allows_empty_payload(self):
        artifact = self._make_artifact()
        artifact["events"].append(
            {
                "event_id": 2,
                "run_id": "test-run-001",
                "ts": "2026-01-01T00:00:02+00:00",
                "type": "output",
                "payload": {},
            }
        )
        path = self._write_artifact("lax.json", artifact)
        with patch("sys.stdout", new_callable=StringIO):
            code = ci_replay(path, strict=False)
        self.assertEqual(code, EXIT_SUCCESS)


# =========================================================================
# CI Diff Command
# =========================================================================


class TestCIDiff(_TempDirMixin, unittest.TestCase):
    def test_identical_artifacts(self):
        artifact = self._make_artifact()
        expected = self._write_artifact("expected.json", artifact)
        actual = self._write_artifact("actual.json", artifact)
        with patch("sys.stdout", new_callable=StringIO) as out:
            code = ci_diff(expected, actual)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("No differences", out.getvalue())

    def test_different_artifacts(self):
        expected_art = self._make_artifact()
        actual_art = self._make_artifact()
        actual_art["events"][1]["payload"]["result"] = "changed"
        expected = self._write_artifact("expected.json", expected_art)
        actual = self._write_artifact("actual.json", actual_art)
        with patch("sys.stdout", new_callable=StringIO) as out:
            code = ci_diff(expected, actual)
        self.assertEqual(code, EXIT_DIFF_DETECTED)
        self.assertIn("DIFF", out.getvalue())

    def test_diff_json_format(self):
        expected_art = self._make_artifact()
        actual_art = self._make_artifact()
        actual_art["events"][0]["payload"]["prompt"] = "goodbye"
        expected = self._write_artifact("expected.json", expected_art)
        actual = self._write_artifact("actual.json", actual_art)
        with patch("sys.stdout", new_callable=StringIO) as out:
            code = ci_diff(expected, actual, output_format="json")
        self.assertEqual(code, EXIT_DIFF_DETECTED)
        parsed = json.loads(out.getvalue())
        self.assertFalse(parsed["identical"])
        self.assertEqual(parsed["first_divergent_index"], 0)
        self.assertIn("suggestion", parsed)

    def test_diff_json_identical(self):
        artifact = self._make_artifact()
        expected = self._write_artifact("expected.json", artifact)
        actual = self._write_artifact("actual.json", artifact)
        with patch("sys.stdout", new_callable=StringIO) as out:
            code = ci_diff(expected, actual, output_format="json")
        self.assertEqual(code, EXIT_SUCCESS)
        parsed = json.loads(out.getvalue())
        self.assertTrue(parsed["identical"])

    def test_diff_missing_expected(self):
        actual = self._write_artifact("actual.json", self._make_artifact())
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_diff("/nonexistent.json", actual)
        self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_diff_missing_actual(self):
        expected = self._write_artifact("expected.json", self._make_artifact())
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_diff(expected, "/nonexistent.json")
        self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_diff_event_count_mismatch(self):
        expected_art = self._make_artifact()
        actual_art = self._make_artifact()
        actual_art["events"].append(
            {
                "event_id": 2,
                "run_id": "test-run-001",
                "ts": "2026-01-01T00:00:02+00:00",
                "type": "system",
                "payload": {"msg": "extra"},
            }
        )
        expected = self._write_artifact("expected.json", expected_art)
        actual = self._write_artifact("actual.json", actual_art)
        with patch("sys.stdout", new_callable=StringIO) as out:
            code = ci_diff(expected, actual)
        self.assertEqual(code, EXIT_DIFF_DETECTED)
        self.assertIn("mismatch", out.getvalue())

    def test_diff_bad_json(self):
        expected = self._write_artifact("expected.json", self._make_artifact())
        bad_path = os.path.join(self.tmpdir, "bad.json")
        with open(bad_path, "w") as f:
            f.write("{not valid")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_diff(expected, bad_path)
        self.assertEqual(code, EXIT_ARTIFACT_ERROR)

    def test_diff_normalizes_timestamps(self):
        """Artifacts recorded at different times should still match."""
        art1 = self._make_artifact()
        art1["started_at"] = "2026-02-01T12:00:00+00:00"
        art1["events"][0]["ts"] = "2026-02-01T12:00:00+00:00"
        art2 = self._make_artifact()
        art2["started_at"] = "2026-03-15T08:00:00+00:00"
        art2["events"][0]["ts"] = "2026-03-15T08:00:00+00:00"
        p1 = self._write_artifact("a.json", art1)
        p2 = self._write_artifact("b.json", art2)
        with patch("sys.stdout", new_callable=StringIO):
            code = ci_diff(p1, p2)
        self.assertEqual(code, EXIT_SUCCESS)


# =========================================================================
# CI Normalize Command
# =========================================================================


class TestCINormalize(_TempDirMixin, unittest.TestCase):
    def test_normalize_in_place(self):
        artifact = self._make_artifact()
        path = self._write_artifact("test.json", artifact)
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_normalize(path)
        self.assertEqual(code, EXIT_SUCCESS)
        with open(path) as f:
            result = json.load(f)
        self.assertEqual(result["started_at"], NORMALIZED_TIMESTAMP)

    def test_normalize_to_new_path(self):
        artifact = self._make_artifact()
        src = self._write_artifact("src.json", artifact)
        dst = os.path.join(self.tmpdir, "dst.json")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_normalize(src, out_path=dst)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertTrue(os.path.isfile(dst))

    def test_normalize_missing_file(self):
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_normalize("/nonexistent.json")
        self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_normalize_bad_json(self):
        path = os.path.join(self.tmpdir, "bad.json")
        with open(path, "w") as f:
            f.write("{bad")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_normalize(path)
        self.assertEqual(code, EXIT_ARTIFACT_ERROR)


# =========================================================================
# CI Check Command (end-to-end)
# =========================================================================


class TestCICheck(_TempDirMixin, unittest.TestCase):
    def test_check_pass(self):
        """Record expected, then check passes."""
        script = self._write_script("ok.py", "print('hello')\n")
        expected_path = os.path.join(self.tmpdir, "expected.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            ci_record(script, expected_path, offline=False)
        with patch("sys.stderr", new_callable=StringIO):
            with patch("sys.stdout", new_callable=StringIO):
                code = ci_check(script, expected_path, offline=False)
        self.assertEqual(code, EXIT_SUCCESS)

    def test_check_fail_on_behavior_change(self):
        """Behavior change must produce exit code 1."""
        script_v1 = self._write_script(
            "v1.py",
            """\
            import os, json
            db = os.environ.get("FORKLINE_DB", "runs.db")
            run_id = os.environ.get("FORKLINE_RUN_ID", "")
            if db and run_id:
                import sqlite3
                conn = sqlite3.connect(db)
                conn.execute(
                "INSERT INTO events (run_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                    (run_id, "2026-01-01T00:00:00+00:00", "output",
                     json.dumps({"result": "v1"})),
                )
                conn.commit()
                conn.close()
        """,
        )
        expected_path = os.path.join(self.tmpdir, "expected.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            ci_record(script_v1, expected_path, offline=False)

        script_v2 = self._write_script(
            "v2.py",
            """\
            import os, json
            db = os.environ.get("FORKLINE_DB", "runs.db")
            run_id = os.environ.get("FORKLINE_RUN_ID", "")
            if db and run_id:
                import sqlite3
                conn = sqlite3.connect(db)
                conn.execute(
                "INSERT INTO events (run_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                    (run_id, "2026-01-01T00:00:00+00:00", "output",
                     json.dumps({"result": "v2_changed"})),
                )
                conn.commit()
                conn.close()
        """,
        )
        with patch("sys.stderr", new_callable=StringIO):
            with patch("sys.stdout", new_callable=StringIO):
                code = ci_check(script_v2, expected_path, offline=False)
        self.assertEqual(code, EXIT_DIFF_DETECTED)

    def test_check_missing_entrypoint(self):
        expected = self._write_artifact("expected.json", self._make_artifact())
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_check("/nonexistent.py", expected, offline=False)
        self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_check_missing_expected(self):
        script = self._write_script("ok.py", "print('hello')\n")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_check(script, "/nonexistent.json", offline=False)
        self.assertEqual(code, EXIT_USAGE_ERROR)


# =========================================================================
# CLI Integration (forkline ci ...)
# =========================================================================


class TestCLICICommands(_TempDirMixin, unittest.TestCase):
    def test_ci_no_subcommand(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO):
                main(["ci"])
        self.assertEqual(ctx.exception.code, 1)

    def test_ci_record_via_cli(self):
        script = self._write_script("ok.py", "print('hello')\n")
        out_path = os.path.join(self.tmpdir, "out.run.json")
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new_callable=StringIO):
                main(["ci", "record", "--entrypoint", script, "--out", out_path])
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)
        self.assertTrue(os.path.isfile(out_path))

    def test_ci_replay_via_cli(self):
        path = self._write_artifact("test.json", self._make_artifact())
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO):
                main(["ci", "replay", "--artifact", path])
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)

    def test_ci_diff_identical_via_cli(self):
        artifact = self._make_artifact()
        p1 = self._write_artifact("a.json", artifact)
        p2 = self._write_artifact("b.json", artifact)
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO):
                main(["ci", "diff", "--expected", p1, "--actual", p2])
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)

    def test_ci_diff_diverged_via_cli(self):
        a1 = self._make_artifact()
        a2 = self._make_artifact()
        a2["events"][0]["payload"]["prompt"] = "different"
        p1 = self._write_artifact("a.json", a1)
        p2 = self._write_artifact("b.json", a2)
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stdout", new_callable=StringIO):
                main(["ci", "diff", "--expected", p1, "--actual", p2])
        self.assertEqual(ctx.exception.code, EXIT_DIFF_DETECTED)

    def test_ci_normalize_via_cli(self):
        path = self._write_artifact("test.json", self._make_artifact())
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new_callable=StringIO):
                main(["ci", "normalize", path])
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)


# =========================================================================
# End-to-End: Record → Diff → Detect Change
# =========================================================================


class TestEndToEnd(_TempDirMixin, unittest.TestCase):
    """Full integration test: record, confirm pass, change, confirm fail."""

    def test_full_cycle(self):
        script = self._write_script(
            "flow.py",
            """\
            import os, json
            db = os.environ.get("FORKLINE_DB", "runs.db")
            run_id = os.environ.get("FORKLINE_RUN_ID", "")
            SQL = "INSERT INTO events (run_id, ts, type, payload) VALUES (?, ?, ?, ?)"
            if db and run_id:
                import sqlite3
                conn = sqlite3.connect(db)
                conn.execute(SQL,
                    (run_id, "2026-01-01T00:00:00+00:00", "input",
                     json.dumps({"prompt": "what is 2+2?"})),
                )
                conn.execute(SQL,
                    (run_id, "2026-01-01T00:00:01+00:00", "output",
                     json.dumps({"answer": "4"})),
                )
                conn.commit()
                conn.close()
        """,
        )

        # Step 1: Record baseline
        baseline_path = os.path.join(self.tmpdir, "baseline.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            code = ci_record(script, baseline_path, offline=False)
        self.assertEqual(code, EXIT_SUCCESS)

        # Step 2: Replay validates cleanly
        with patch("sys.stdout", new_callable=StringIO):
            code = ci_replay(baseline_path)
        self.assertEqual(code, EXIT_SUCCESS)

        # Step 3: Same script passes check
        with patch("sys.stderr", new_callable=StringIO):
            with patch("sys.stdout", new_callable=StringIO):
                code = ci_check(script, baseline_path, offline=False)
        self.assertEqual(code, EXIT_SUCCESS)

        # Step 4: Modified script fails check with exit code 1
        script_v2 = self._write_script(
            "flow_v2.py",
            """\
            import os, json
            db = os.environ.get("FORKLINE_DB", "runs.db")
            run_id = os.environ.get("FORKLINE_RUN_ID", "")
            SQL = "INSERT INTO events (run_id, ts, type, payload) VALUES (?, ?, ?, ?)"
            if db and run_id:
                import sqlite3
                conn = sqlite3.connect(db)
                conn.execute(SQL,
                    (run_id, "2026-01-01T00:00:00+00:00", "input",
                     json.dumps({"prompt": "what is 2+2?"})),
                )
                conn.execute(SQL,
                    (run_id, "2026-01-01T00:00:01+00:00", "output",
                     json.dumps({"answer": "5"})),
                )
                conn.commit()
                conn.close()
        """,
        )
        with patch("sys.stderr", new_callable=StringIO):
            with patch("sys.stdout", new_callable=StringIO) as out:
                code = ci_check(script_v2, baseline_path, offline=False)
        self.assertEqual(code, EXIT_DIFF_DETECTED)

        # Step 5: Diff output in JSON mode is machine-readable
        actual_path = os.path.join(self.tmpdir, "actual_v2.run.json")
        with patch("sys.stderr", new_callable=StringIO):
            ci_record(script_v2, actual_path, offline=False)
        with patch("sys.stdout", new_callable=StringIO) as out:
            code = ci_diff(baseline_path, actual_path, output_format="json")
        self.assertEqual(code, EXIT_DIFF_DETECTED)
        parsed = json.loads(out.getvalue())
        self.assertFalse(parsed["identical"])
        self.assertIn("first_divergent_index", parsed)
        self.assertIn("suggestion", parsed)


# =========================================================================
# Exit Code Scenarios
# =========================================================================


class TestExitCodeScenarios(_TempDirMixin, unittest.TestCase):
    """Each exit code must be exercised by a specific scenario."""

    def test_exit_0_success(self):
        artifact = self._make_artifact()
        p1 = self._write_artifact("a.json", artifact)
        p2 = self._write_artifact("b.json", artifact)
        with patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(ci_diff(p1, p2), EXIT_SUCCESS)

    def test_exit_1_diff_detected(self):
        a1 = self._make_artifact()
        a2 = self._make_artifact()
        a2["events"][0]["payload"]["prompt"] = "changed"
        p1 = self._write_artifact("a.json", a1)
        p2 = self._write_artifact("b.json", a2)
        with patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(ci_diff(p1, p2), EXIT_DIFF_DETECTED)

    def test_exit_2_usage_error(self):
        with patch("sys.stderr", new_callable=StringIO):
            self.assertEqual(
                ci_record("/no/such/file.py", "/tmp/out.json"), EXIT_USAGE_ERROR
            )

    def test_exit_3_replay_failed(self):
        script = self._write_script("fail.py", "import sys; sys.exit(1)\n")
        out = os.path.join(self.tmpdir, "out.json")
        with patch("sys.stderr", new_callable=StringIO):
            self.assertEqual(ci_record(script, out), EXIT_REPLAY_FAILED)

    def test_exit_5_artifact_error(self):
        path = os.path.join(self.tmpdir, "bad.json")
        with open(path, "w") as f:
            f.write("not json")
        with patch("sys.stderr", new_callable=StringIO):
            self.assertEqual(ci_replay(path), EXIT_ARTIFACT_ERROR)

    def test_exit_5_schema_error(self):
        artifact = self._make_artifact()
        del artifact["schema_version"]
        path = self._write_artifact("no_schema.json", artifact)
        with patch("sys.stderr", new_callable=StringIO):
            self.assertEqual(ci_replay(path), EXIT_ARTIFACT_ERROR)


if __name__ == "__main__":
    unittest.main()
