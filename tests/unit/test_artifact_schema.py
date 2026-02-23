"""
Tests for the artifact schema, migration registry, and backward compatibility.

These tests verify the acceptance criteria:
1. Every artifact includes schema_version.
2. Replay works on older artifacts via migration.
3. Unknown fields do not break parsing.
4. Diff works across versions.
5. No existing artifacts are broken.
"""

import copy
import json
import os
import sqlite3
import tempfile
import unittest
import warnings

from forkline.artifact.migrate import (
    _compare_versions,
    _find_migration_path,
    migrate_artifact,
)
from forkline.artifact.schema import (
    CURRENT_SCHEMA_VERSION,
    ArtifactEvent,
    RunArtifact,
    SchemaVersionError,
)
from forkline.storage.recorder import RunRecorder
from forkline.storage.store import SQLiteStore
from forkline.version import SCHEMA_VERSION


class TestRunArtifactSchema(unittest.TestCase):
    """Tests for the RunArtifact dataclass."""

    def _make_artifact(self, **overrides):
        defaults = {
            "schema_version": "1.0",
            "run_id": "abc123",
            "entrypoint": "test.py",
            "started_at": "2026-01-01T00:00:00Z",
            "events": [],
        }
        defaults.update(overrides)
        return RunArtifact(**defaults)

    def test_schema_version_required(self):
        """schema_version must be present."""
        artifact = self._make_artifact()
        self.assertEqual(artifact.schema_version, "1.0")

    def test_to_dict_includes_schema_version(self):
        """Serialized artifact must include schema_version."""
        d = self._make_artifact().to_dict()
        self.assertIn("schema_version", d)
        self.assertEqual(d["schema_version"], "1.0")

    def test_to_json_roundtrip(self):
        """JSON serialization/deserialization roundtrip preserves data."""
        original = self._make_artifact(
            ended_at="2026-01-01T00:01:00Z",
            status="success",
            forkline_version="0.3.0",
            events=[
                ArtifactEvent(
                    event_id=1,
                    run_id="abc123",
                    ts="2026-01-01T00:00:01Z",
                    type="input",
                    payload={"key": "value"},
                )
            ],
        )
        json_str = original.to_json()
        loaded = RunArtifact.from_json(json_str)

        self.assertEqual(loaded.schema_version, original.schema_version)
        self.assertEqual(loaded.run_id, original.run_id)
        self.assertEqual(loaded.entrypoint, original.entrypoint)
        self.assertEqual(loaded.started_at, original.started_at)
        self.assertEqual(loaded.ended_at, original.ended_at)
        self.assertEqual(loaded.status, original.status)
        self.assertEqual(len(loaded.events), 1)
        self.assertEqual(loaded.events[0].type, "input")
        self.assertEqual(loaded.events[0].payload, {"key": "value"})

    def test_from_dict_rejects_missing_schema_version(self):
        """from_dict must raise SchemaVersionError if schema_version is missing."""
        with self.assertRaises(SchemaVersionError):
            RunArtifact.from_dict({"run_id": "x", "entrypoint": "y"})

    def test_from_dict_ignores_unknown_fields(self):
        """Unknown fields must be silently ignored."""
        data = {
            "schema_version": "1.0",
            "run_id": "x",
            "entrypoint": "y",
            "started_at": "2026-01-01T00:00:00Z",
            "events": [],
            "completely_unknown_field": "should be ignored",
            "another_unknown": 42,
        }
        artifact = RunArtifact.from_dict(data)
        self.assertEqual(artifact.schema_version, "1.0")
        self.assertEqual(artifact.run_id, "x")

    def test_validate_catches_missing_required_fields(self):
        """validate() should report missing required fields."""
        artifact = RunArtifact(
            schema_version="",
            run_id="",
            entrypoint="",
            started_at="",
        )
        errors = artifact.validate()
        self.assertTrue(len(errors) > 0)
        field_names = " ".join(errors)
        self.assertIn("schema_version", field_names)
        self.assertIn("run_id", field_names)

    def test_validate_passes_for_valid_artifact(self):
        """validate() should return empty list for valid artifact."""
        artifact = self._make_artifact()
        errors = artifact.validate()
        self.assertEqual(errors, [])

    def test_metadata_extensibility(self):
        """metadata dict should support arbitrary keys."""
        artifact = self._make_artifact(
            metadata={"python_version": "3.12", "custom_key": "custom_value"}
        )
        self.assertEqual(artifact.metadata["python_version"], "3.12")
        self.assertEqual(artifact.metadata["custom_key"], "custom_value")

    def test_immutability(self):
        """RunArtifact should be frozen (immutable)."""
        artifact = self._make_artifact()
        with self.assertRaises(AttributeError):
            artifact.run_id = "modified"


class TestArtifactEvent(unittest.TestCase):
    """Tests for the ArtifactEvent dataclass."""

    def test_from_dict_ignores_unknown_fields(self):
        """Unknown fields in events must be silently ignored."""
        data = {
            "event_id": 1,
            "run_id": "x",
            "ts": "2026-01-01T00:00:00Z",
            "type": "input",
            "payload": {},
            "unknown_field": "ignored",
        }
        event = ArtifactEvent.from_dict(data)
        self.assertEqual(event.event_id, 1)
        self.assertEqual(event.type, "input")

    def test_to_dict_roundtrip(self):
        """to_dict/from_dict roundtrip preserves data."""
        original = ArtifactEvent(
            event_id=5,
            run_id="abc",
            ts="2026-01-01T00:00:00Z",
            type="tool_call",
            payload={"name": "search", "result": "ok"},
        )
        restored = ArtifactEvent.from_dict(original.to_dict())
        self.assertEqual(restored.event_id, original.event_id)
        self.assertEqual(restored.type, original.type)
        self.assertEqual(restored.payload, original.payload)


class TestMigrationRegistry(unittest.TestCase):
    """Tests for the migration registry and migrate_artifact()."""

    def test_migrate_current_version_is_noop(self):
        """Migrating a current-version artifact returns a copy unchanged."""
        raw = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "run_id": "x",
            "entrypoint": "test.py",
            "started_at": "2026-01-01T00:00:00Z",
            "events": [],
        }
        result = migrate_artifact(raw)
        self.assertEqual(result["schema_version"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(result["run_id"], "x")

    def test_migrate_current_version_returns_deep_copy(self):
        """Migrating current version should not return same object."""
        raw = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "run_id": "x",
            "events": [{"payload": {"nested": "data"}}],
        }
        result = migrate_artifact(raw)
        self.assertIsNot(result, raw)
        result["events"][0]["payload"]["nested"] = "mutated"
        self.assertEqual(raw["events"][0]["payload"]["nested"], "data")

    def test_migrate_missing_schema_version_raises(self):
        """Missing schema_version must raise SchemaVersionError."""
        with self.assertRaises(SchemaVersionError) as ctx:
            migrate_artifact({"run_id": "x"})
        self.assertIsNone(ctx.exception.version)

    def test_migrate_recording_v0_to_1_0(self):
        """recording_v0 artifacts should migrate to 1.0."""
        raw = {
            "schema_version": "recording_v0",
            "run_id": "legacy",
            "entrypoint": "old_script.py",
            "started_at": "2025-06-01T00:00:00Z",
            "python_version": "3.11.0",
            "platform": "linux",
            "cwd": "/tmp",
            "events": [
                {
                    "event_id": 1,
                    "run_id": "legacy",
                    "created_at": "2025-06-01T00:00:01Z",
                    "type": "input",
                    "payload": {"key": "value"},
                }
            ],
        }
        result = migrate_artifact(raw)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["run_id"], "legacy")
        # Environment fields should be in metadata
        self.assertIn("metadata", result)
        self.assertEqual(result["metadata"]["python_version"], "3.11.0")
        self.assertEqual(result["metadata"]["platform"], "linux")
        self.assertNotIn("python_version", result)
        self.assertNotIn("platform", result)
        # Event timestamps should be normalized
        self.assertEqual(result["events"][0]["ts"], "2025-06-01T00:00:01Z")
        self.assertNotIn("created_at", result["events"][0])

    def test_migrate_is_deterministic(self):
        """Same input must always produce same output."""
        raw = {
            "schema_version": "recording_v0",
            "run_id": "det-test",
            "entrypoint": "test.py",
            "started_at": "2025-01-01T00:00:00Z",
            "events": [],
        }
        result1 = migrate_artifact(raw)
        result2 = migrate_artifact(raw)
        self.assertEqual(result1, result2)

    def test_migrate_does_not_mutate_input(self):
        """migrate_artifact must not mutate the input dict."""
        raw = {
            "schema_version": "recording_v0",
            "run_id": "immutable",
            "entrypoint": "test.py",
            "started_at": "2025-01-01T00:00:00Z",
            "python_version": "3.11",
            "events": [],
        }
        original = copy.deepcopy(raw)
        migrate_artifact(raw)
        self.assertEqual(raw, original)

    def test_newer_version_returns_with_warning(self):
        """Newer schema versions should warn but not crash."""
        raw = {
            "schema_version": "99.0",
            "run_id": "future",
            "entrypoint": "future.py",
            "started_at": "2030-01-01T00:00:00Z",
            "events": [],
            "future_field": "should survive",
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = migrate_artifact(raw)
            self.assertTrue(any("newer" in str(warning.message) for warning in w))
        self.assertEqual(result["schema_version"], "99.0")
        self.assertEqual(result["future_field"], "should survive")

    def test_migrate_non_dict_raises(self):
        """Non-dict input must raise SchemaVersionError."""
        with self.assertRaises(SchemaVersionError):
            migrate_artifact("not a dict")
        with self.assertRaises(SchemaVersionError):
            migrate_artifact(42)


class TestVersionComparison(unittest.TestCase):
    """Tests for version comparison logic."""

    def test_compare_equal(self):
        self.assertEqual(_compare_versions("1.0", "1.0"), 0)

    def test_compare_less(self):
        self.assertEqual(_compare_versions("1.0", "2.0"), -1)

    def test_compare_greater(self):
        self.assertEqual(_compare_versions("2.0", "1.0"), 1)

    def test_legacy_less_than_semver(self):
        self.assertEqual(_compare_versions("recording_v0", "1.0"), -1)

    def test_migration_path_exists(self):
        path = _find_migration_path("recording_v0", "1.0")
        self.assertEqual(len(path), 1)
        self.assertEqual(path[0], ("recording_v0", "1.0"))

    def test_migration_path_same_version(self):
        path = _find_migration_path("1.0", "1.0")
        self.assertEqual(path, [])


class TestStorageArtifactIntegration(unittest.TestCase):
    """Tests that storage backends produce valid artifacts."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_recorder_load_artifact(self):
        """RunRecorder.load_artifact should return a valid RunArtifact."""
        db_path = os.path.join(self.tmpdir, "recorder.db")
        recorder = RunRecorder(db_path=db_path)
        run_id = recorder.start_run(entrypoint="test.py")
        recorder.log_event(run_id, "input", {"prompt": "hello"})
        recorder.log_event(run_id, "output", {"response": "world"})
        recorder.end_run(run_id)

        artifact = recorder.load_artifact(run_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(artifact.run_id, run_id)
        self.assertEqual(artifact.entrypoint, "test.py")
        self.assertEqual(len(artifact.events), 2)
        self.assertEqual(artifact.events[0].type, "input")
        self.assertEqual(artifact.events[1].type, "output")

        errors = artifact.validate()
        self.assertEqual(errors, [])

    def test_recorder_export_json(self):
        """
        RunRecorder.export_artifact_json should produce valid JSON
        with schema_version.
        """
        db_path = os.path.join(self.tmpdir, "export.db")
        recorder = RunRecorder(db_path=db_path)
        run_id = recorder.start_run(entrypoint="export_test.py")
        recorder.log_event(run_id, "system", {"msg": "started"})
        recorder.end_run(run_id)

        json_str = recorder.export_artifact_json(run_id)
        self.assertIsNotNone(json_str)

        data = json.loads(json_str)
        self.assertIn("schema_version", data)
        self.assertEqual(data["schema_version"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(data["run_id"], run_id)

    def test_recorder_load_artifact_nonexistent(self):
        """load_artifact for nonexistent run should return None."""
        db_path = os.path.join(self.tmpdir, "empty.db")
        recorder = RunRecorder(db_path=db_path)
        self.assertIsNone(recorder.load_artifact("does-not-exist"))

    def test_sqlitestore_load_artifact(self):
        """SQLiteStore.load_artifact should return a valid RunArtifact."""
        db_path = os.path.join(self.tmpdir, "store.db")
        store = SQLiteStore(path=db_path)
        store.start_run("test-run")
        store.start_step("test-run", 0, "step_0")
        store.append_event("test-run", 0, "input", {"data": "hello"})
        store.end_step("test-run", 0)

        artifact = store.load_artifact("test-run")
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(artifact.run_id, "test-run")
        self.assertEqual(len(artifact.events), 1)

    def test_sqlitestore_export_json(self):
        """SQLiteStore.export_artifact_json should produce valid JSON."""
        db_path = os.path.join(self.tmpdir, "export_store.db")
        store = SQLiteStore(path=db_path)
        store.start_run("json-run")
        store.start_step("json-run", 0, "step_0")
        store.append_event("json-run", 0, "output", {"result": "ok"})
        store.end_step("json-run", 0)

        json_str = store.export_artifact_json("json-run")
        self.assertIsNotNone(json_str)
        data = json.loads(json_str)
        self.assertEqual(data["schema_version"], CURRENT_SCHEMA_VERSION)

    def test_legacy_db_migrates_on_load_artifact(self):
        """Artifacts from legacy databases should migrate to current schema."""
        db_path = os.path.join(self.tmpdir, "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                forkline_version TEXT NOT NULL,
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
                payload TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            )
        """
        )
        conn.execute(
            """
            INSERT INTO runs VALUES (
                'legacy-001', 'recording_v0', '0.1.0', 'old.py',
                '2025-01-01T00:00:00Z', '2025-01-01T00:01:00Z',
                'success', '3.10', 'linux', '/tmp'
            )
        """
        )
        conn.execute(
            """
            INSERT INTO events (run_id, ts, type, payload)
            VALUES ('legacy-001', '2025-01-01T00:00:30Z', 'input', '{"key": "val"}')
        """
        )
        conn.commit()
        conn.close()

        recorder = RunRecorder(db_path=db_path)
        artifact = recorder.load_artifact("legacy-001")

        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(artifact.run_id, "legacy-001")
        self.assertIn("python_version", artifact.metadata)
        self.assertEqual(len(artifact.events), 1)


class TestSchemaVersionConsistency(unittest.TestCase):
    """
    Tests that SCHEMA_VERSION in version.py matches
    CURRENT_SCHEMA_VERSION in schema.py.
    """

    def test_versions_match(self):
        self.assertEqual(SCHEMA_VERSION, CURRENT_SCHEMA_VERSION)

    def test_schema_version_is_1_0(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, "1.0")


if __name__ == "__main__":
    unittest.main()
