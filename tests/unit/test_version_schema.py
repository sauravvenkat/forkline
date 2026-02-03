"""
Tests for version/schema handling in Forkline artifacts.

These tests verify:
1. New runs include forkline_version and schema_version
2. Backward compatibility: older artifacts get default versions
3. Version constants are consistent
"""

import os
import sqlite3
import tempfile
import unittest

from forkline import (
    DEFAULT_FORKLINE_VERSION,
    DEFAULT_SCHEMA_VERSION,
    FORKLINE_VERSION,
    SCHEMA_VERSION,
    Run,
    SQLiteStore,
)
from forkline.storage.recorder import RunRecorder


class TestVersionConstants(unittest.TestCase):
    """Tests for version constants."""

    def test_version_constants_exist(self):
        """Version constants should be defined."""
        self.assertIsNotNone(FORKLINE_VERSION)
        self.assertIsNotNone(SCHEMA_VERSION)
        self.assertIsNotNone(DEFAULT_FORKLINE_VERSION)
        self.assertIsNotNone(DEFAULT_SCHEMA_VERSION)

    def test_version_format(self):
        """Version should be a valid semver-like string."""
        # FORKLINE_VERSION should be like "0.1.1"
        parts = FORKLINE_VERSION.split(".")
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertTrue(part.isdigit())

    def test_schema_version_format(self):
        """Schema version should be a descriptive string."""
        # SCHEMA_VERSION should be like "recording_v0"
        self.assertTrue(SCHEMA_VERSION.startswith("recording_"))
        self.assertIn("v", SCHEMA_VERSION)

    def test_default_versions_are_reasonable(self):
        """Default versions for backward compat should be sensible."""
        # Default forkline version should be older or "unknown"
        self.assertTrue(
            DEFAULT_FORKLINE_VERSION == "0.1.0" or DEFAULT_FORKLINE_VERSION == "unknown"
        )
        # Default schema should match current (recording format hasn't changed)
        self.assertEqual(DEFAULT_SCHEMA_VERSION, "recording_v0")


class TestSQLiteStoreVersioning(unittest.TestCase):
    """Tests for SQLiteStore version handling."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.store = SQLiteStore(path=self.db_path)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_new_run_includes_versions(self):
        """New runs should include version fields."""
        run = self.store.start_run("test-run")

        self.assertEqual(run.forkline_version, FORKLINE_VERSION)
        self.assertEqual(run.schema_version, SCHEMA_VERSION)

    def test_loaded_run_includes_versions(self):
        """Loaded runs should include version fields."""
        self.store.start_run("test-run")

        loaded = self.store.load_run("test-run")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.forkline_version, FORKLINE_VERSION)
        self.assertEqual(loaded.schema_version, SCHEMA_VERSION)

    def test_backward_compat_missing_version_columns(self):
        """Runs from older DBs without version columns should get defaults."""
        # Create a legacy database without version columns
        legacy_db_path = os.path.join(self.tmpdir, "legacy.db")
        conn = sqlite3.connect(legacy_db_path)
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE steps (
                step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_idx INTEGER NOT NULL,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """
        )
        # Insert a legacy run without version fields
        conn.execute(
            "INSERT INTO runs (run_id, created_at) VALUES (?, ?)",
            ("legacy-run", "2024-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Load with new SQLiteStore - should trigger migration
        store = SQLiteStore(path=legacy_db_path)
        loaded = store.load_run("legacy-run")

        # Should get default versions
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.forkline_version, DEFAULT_FORKLINE_VERSION)
        self.assertEqual(loaded.schema_version, DEFAULT_SCHEMA_VERSION)

    def test_backward_compat_null_version_values(self):
        """Runs with NULL version values should get defaults."""
        # Create a DB with version columns but NULL values
        null_db_path = os.path.join(self.tmpdir, "null_versions.db")
        conn = sqlite3.connect(null_db_path)
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                forkline_version TEXT,
                schema_version TEXT
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE steps (
                step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_idx INTEGER NOT NULL,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """
        )
        # Insert run with NULL versions
        conn.execute(
            "INSERT INTO runs (run_id, created_at, forkline_version, schema_version) VALUES (?, ?, ?, ?)",
            ("null-run", "2024-01-01T00:00:00Z", None, None),
        )
        conn.commit()
        conn.close()

        store = SQLiteStore(path=null_db_path)
        loaded = store.load_run("null-run")

        # Should get default versions
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.forkline_version, DEFAULT_FORKLINE_VERSION)
        self.assertEqual(loaded.schema_version, DEFAULT_SCHEMA_VERSION)


class TestRunRecorderVersioning(unittest.TestCase):
    """Tests for RunRecorder version handling."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "recorder.db")
        self.recorder = RunRecorder(db_path=self.db_path)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_new_run_includes_versions(self):
        """New runs should include version fields."""
        run_id = self.recorder.start_run(entrypoint="test.py")
        run = self.recorder.get_run(run_id)

        self.assertIsNotNone(run)
        self.assertEqual(run["forkline_version"], FORKLINE_VERSION)
        self.assertEqual(run["schema_version"], SCHEMA_VERSION)

    def test_backward_compat_missing_forkline_version(self):
        """Runs without forkline_version should get default."""
        # Create a legacy database with old schema (only schema_version, no forkline_version)
        legacy_db_path = os.path.join(self.tmpdir, "legacy_recorder.db")
        conn = sqlite3.connect(legacy_db_path)
        conn.execute(
            """
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
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """
        )
        conn.execute(
            """
            INSERT INTO runs (run_id, schema_version, entrypoint, started_at, 
                             python_version, platform, cwd)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "0.1",
                "test.py",
                "2024-01-01T00:00:00Z",
                "3.10.0",
                "linux",
                "/tmp",
            ),
        )
        conn.commit()
        conn.close()

        # Load with new RunRecorder - should trigger migration
        recorder = RunRecorder(db_path=legacy_db_path)
        run = recorder.get_run("legacy-run")

        # Should get default forkline_version
        self.assertIsNotNone(run)
        self.assertEqual(run["forkline_version"], DEFAULT_FORKLINE_VERSION)
        # schema_version should be preserved
        self.assertEqual(run["schema_version"], "0.1")


class TestRunDataclass(unittest.TestCase):
    """Tests for Run dataclass version fields."""

    def test_run_has_version_fields(self):
        """Run dataclass should have version fields."""
        run = Run(
            run_id="test",
            created_at="2024-01-01T00:00:00Z",
            steps=[],
            forkline_version="0.1.1",
            schema_version="recording_v0",
        )

        self.assertEqual(run.forkline_version, "0.1.1")
        self.assertEqual(run.schema_version, "recording_v0")

    def test_run_version_fields_optional(self):
        """Run version fields should be optional for backward compat."""
        run = Run(
            run_id="test",
            created_at="2024-01-01T00:00:00Z",
        )

        self.assertIsNone(run.forkline_version)
        self.assertIsNone(run.schema_version)


if __name__ == "__main__":
    unittest.main()
