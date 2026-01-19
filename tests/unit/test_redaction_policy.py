"""
Tests for RedactionPolicy v0.

These tests verify security-critical redaction behavior:
- Determinism (same input → same output)
- Input immutability (no mutation)
- Correct application of redaction rules
- Integration with storage boundary
"""

import tempfile
import unittest

from forkline.core.redaction import (
    RedactionAction,
    RedactionPolicy,
    RedactionRule,
    create_default_policy,
)
from forkline.storage.recorder import RunRecorder


class TestRedactionRule(unittest.TestCase):
    """Test RedactionRule validation and behavior."""

    def test_rule_requires_at_least_one_pattern(self):
        """RedactionRule must have at least one pattern specified."""
        with self.assertRaises(ValueError):
            RedactionRule(action=RedactionAction.MASK)

    def test_rule_with_key_pattern_is_valid(self):
        """RedactionRule with only key_pattern is valid."""
        rule = RedactionRule(action=RedactionAction.MASK, key_pattern="secret")
        self.assertEqual(rule.key_pattern, "secret")
        self.assertIsNone(rule.path_pattern)

    def test_rule_with_path_pattern_is_valid(self):
        """RedactionRule with only path_pattern is valid."""
        rule = RedactionRule(
            action=RedactionAction.MASK, path_pattern="headers.authorization"
        )
        self.assertIsNone(rule.key_pattern)
        self.assertEqual(rule.path_pattern, "headers.authorization")

    def test_rule_with_both_patterns_is_valid(self):
        """RedactionRule with both patterns is valid."""
        rule = RedactionRule(
            action=RedactionAction.MASK,
            key_pattern="token",
            path_pattern="auth.token",
        )
        self.assertEqual(rule.key_pattern, "token")
        self.assertEqual(rule.path_pattern, "auth.token")


class TestRedactionPolicy(unittest.TestCase):
    """Test RedactionPolicy core behavior."""

    def test_empty_policy_does_not_redact(self):
        """Policy with no rules should not redact anything."""
        policy = RedactionPolicy(rules=[])
        payload = {"api_key": "secret123", "data": "value"}

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted, {"api_key": "secret123", "data": "value"})

    def test_mask_action_replaces_with_sentinel(self):
        """MASK action should replace matched values with [REDACTED]."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.MASK, key_pattern="secret")]
        )
        payload = {"secret_key": "sensitive_value", "normal_key": "normal_value"}

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["secret_key"], "[REDACTED]")
        self.assertEqual(redacted["normal_key"], "normal_value")

    def test_drop_action_removes_field(self):
        """DROP action should remove the matched field entirely."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.DROP, key_pattern="secret")]
        )
        payload = {"secret_key": "sensitive_value", "normal_key": "normal_value"}

        redacted = policy.redact("test", payload)

        self.assertNotIn("secret_key", redacted)
        self.assertEqual(redacted["normal_key"], "normal_value")

    def test_hash_action_produces_deterministic_hash(self):
        """HASH action should produce a deterministic hash."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.HASH, key_pattern="secret")]
        )
        payload = {"secret_key": "sensitive_value", "normal_key": "normal_value"}

        redacted = policy.redact("test", payload)

        # Verify hash format
        self.assertTrue(redacted["secret_key"].startswith("hash:"))

        # Verify determinism
        redacted2 = policy.redact("test", payload)
        self.assertEqual(redacted["secret_key"], redacted2["secret_key"])

        # Verify it's actually a hash (64 hex chars + "hash:" prefix)
        hash_value = redacted["secret_key"][5:]  # Remove "hash:" prefix
        self.assertEqual(len(hash_value), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in hash_value))

    def test_key_pattern_matching_is_case_insensitive(self):
        """Key pattern matching should be case-insensitive."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.MASK, key_pattern="secret")]
        )
        payload = {
            "SECRET_KEY": "value1",
            "secret_key": "value2",
            "Secret_Key": "value3",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["SECRET_KEY"], "[REDACTED]")
        self.assertEqual(redacted["secret_key"], "[REDACTED]")
        self.assertEqual(redacted["Secret_Key"], "[REDACTED]")

    def test_key_pattern_matching_is_substring(self):
        """Key pattern should match as substring, not exact match."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.MASK, key_pattern="secret")]
        )
        payload = {
            "secret": "value1",
            "my_secret_key": "value2",
            "secret_token": "value3",
            "unrelated": "value4",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["secret"], "[REDACTED]")
        self.assertEqual(redacted["my_secret_key"], "[REDACTED]")
        self.assertEqual(redacted["secret_token"], "[REDACTED]")
        self.assertEqual(redacted["unrelated"], "value4")

    def test_path_pattern_matching(self):
        """Path pattern should match on dot-separated paths."""
        policy = RedactionPolicy(
            rules=[
                RedactionRule(
                    action=RedactionAction.MASK, path_pattern="headers.authorization"
                )
            ]
        )
        payload = {
            "headers": {"authorization": "Bearer token123", "content-type": "json"},
            "body": {"authorization": "should not match"},
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["headers"]["authorization"], "[REDACTED]")
        self.assertEqual(redacted["headers"]["content-type"], "json")
        # body.authorization should NOT match (different path)
        self.assertEqual(redacted["body"]["authorization"], "should not match")

    def test_first_matching_rule_wins(self):
        """When multiple rules match, first rule should win."""
        policy = RedactionPolicy(
            rules=[
                RedactionRule(action=RedactionAction.HASH, key_pattern="secret"),
                RedactionRule(action=RedactionAction.MASK, key_pattern="secret"),
            ]
        )
        payload = {"secret_key": "value"}

        redacted = policy.redact("test", payload)

        # Should be hashed (first rule), not masked
        self.assertTrue(redacted["secret_key"].startswith("hash:"))

    def test_nested_dict_redaction(self):
        """Redaction should work recursively on nested dicts."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.MASK, key_pattern="secret")]
        )
        payload = {
            "outer": {"inner": {"secret_key": "sensitive", "normal": "value"}},
            "secret_top": "also_sensitive",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["outer"]["inner"]["secret_key"], "[REDACTED]")
        self.assertEqual(redacted["outer"]["inner"]["normal"], "value")
        self.assertEqual(redacted["secret_top"], "[REDACTED]")

    def test_list_redaction(self):
        """Redaction should work recursively on lists."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.MASK, key_pattern="secret")]
        )
        payload = {
            "items": [
                {"secret_key": "value1", "normal": "value2"},
                {"secret_key": "value3", "normal": "value4"},
            ]
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["items"][0]["secret_key"], "[REDACTED]")
        self.assertEqual(redacted["items"][0]["normal"], "value2")
        self.assertEqual(redacted["items"][1]["secret_key"], "[REDACTED]")
        self.assertEqual(redacted["items"][1]["normal"], "value4")

    def test_input_immutability(self):
        """Redaction must not mutate the input payload."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.MASK, key_pattern="secret")]
        )
        original_payload = {
            "secret_key": "sensitive_value",
            "nested": {"secret_key": "nested_sensitive"},
        }

        # Make a copy to verify immutability
        payload_before = {
            "secret_key": "sensitive_value",
            "nested": {"secret_key": "nested_sensitive"},
        }

        redacted = policy.redact("test", original_payload)

        # Original should be unchanged
        self.assertEqual(original_payload, payload_before)
        self.assertEqual(original_payload["secret_key"], "sensitive_value")
        self.assertEqual(original_payload["nested"]["secret_key"], "nested_sensitive")

        # Redacted should be different
        self.assertEqual(redacted["secret_key"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["secret_key"], "[REDACTED]")

    def test_determinism(self):
        """Same input must always produce same output."""
        policy = RedactionPolicy(
            rules=[
                RedactionRule(action=RedactionAction.MASK, key_pattern="mask_me"),
                RedactionRule(action=RedactionAction.HASH, key_pattern="hash_me"),
            ]
        )
        payload = {
            "mask_me": "value1",
            "hash_me": "value2",
            "nested": {"mask_me": "value3", "hash_me": "value4"},
        }

        # Redact multiple times
        redacted1 = policy.redact("test", payload)
        redacted2 = policy.redact("test", payload)
        redacted3 = policy.redact("test", payload)

        # All results must be identical
        self.assertEqual(redacted1, redacted2)
        self.assertEqual(redacted2, redacted3)


class TestDefaultRedactionPolicy(unittest.TestCase):
    """Test the default SAFE mode redaction policy."""

    def test_default_policy_exists(self):
        """Default policy should be creatable."""
        policy = create_default_policy()
        self.assertIsInstance(policy, RedactionPolicy)
        self.assertGreater(len(policy.rules), 0)

    def test_default_policy_redacts_api_keys(self):
        """Default policy should redact API keys."""
        policy = create_default_policy()
        payload = {
            "api_key": "sk-12345",
            "apikey": "67890",
            "API_KEY": "ABCDE",
            "normal_field": "not_secret",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["apikey"], "[REDACTED]")
        self.assertEqual(redacted["API_KEY"], "[REDACTED]")
        self.assertEqual(redacted["normal_field"], "not_secret")

    def test_default_policy_redacts_tokens(self):
        """Default policy should redact tokens."""
        policy = create_default_policy()
        payload = {
            "token": "abc123",
            "access_token": "def456",
            "refresh_token": "ghi789",
            "bearer_token": "jkl012",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["token"], "[REDACTED]")
        self.assertEqual(redacted["access_token"], "[REDACTED]")
        self.assertEqual(redacted["refresh_token"], "[REDACTED]")
        self.assertEqual(redacted["bearer_token"], "[REDACTED]")

    def test_default_policy_redacts_secrets(self):
        """Default policy should redact secrets."""
        policy = create_default_policy()
        payload = {
            "secret": "value1",
            "client_secret": "value2",
            "SECRET_KEY": "value3",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["secret"], "[REDACTED]")
        self.assertEqual(redacted["client_secret"], "[REDACTED]")
        self.assertEqual(redacted["SECRET_KEY"], "[REDACTED]")

    def test_default_policy_redacts_passwords(self):
        """Default policy should redact passwords."""
        policy = create_default_policy()
        payload = {
            "password": "secret123",
            "user_password": "secret456",
            "PASSWORD": "secret789",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["password"], "[REDACTED]")
        self.assertEqual(redacted["user_password"], "[REDACTED]")
        self.assertEqual(redacted["PASSWORD"], "[REDACTED]")

    def test_default_policy_redacts_auth_headers(self):
        """Default policy should redact authorization headers."""
        policy = create_default_policy()
        payload = {
            "headers": {
                "authorization": "Bearer token123",
                "cookie": "session=abc123",
                "set-cookie": "session=def456",
                "content-type": "application/json",
            }
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["headers"]["authorization"], "[REDACTED]")
        self.assertEqual(redacted["headers"]["cookie"], "[REDACTED]")
        self.assertEqual(redacted["headers"]["set-cookie"], "[REDACTED]")
        self.assertEqual(redacted["headers"]["content-type"], "application/json")

    def test_default_policy_redacts_credentials(self):
        """Default policy should redact various credential fields."""
        policy = create_default_policy()
        payload = {
            "credentials": {"user": "admin", "pass": "secret"},
            "private_key": "-----BEGIN PRIVATE KEY-----",
            "privatekey": "key_data",
            "session": "session_id_123",
            "csrf": "csrf_token_456",
        }

        redacted = policy.redact("test", payload)

        self.assertEqual(redacted["credentials"], "[REDACTED]")
        self.assertEqual(redacted["private_key"], "[REDACTED]")
        self.assertEqual(redacted["privatekey"], "[REDACTED]")
        self.assertEqual(redacted["session"], "[REDACTED]")
        self.assertEqual(redacted["csrf"], "[REDACTED]")

    def test_default_policy_preserves_safe_fields(self):
        """Default policy should not redact safe fields."""
        policy = create_default_policy()
        payload = {
            "run_id": "run123",
            "event_id": "evt456",
            "timestamp": "2026-01-18T12:00:00Z",
            "tool_name": "search",
            "model": "gpt-4",
            "duration_ms": 123,
            "status": "success",
        }

        redacted = policy.redact("test", payload)

        # All safe fields should be preserved
        self.assertEqual(redacted, payload)

    def test_default_policy_handles_complex_nested_structure(self):
        """Default policy should handle complex nested structures."""
        policy = create_default_policy()
        payload = {
            "run_id": "run123",
            "tool_calls": [
                {
                    "name": "api_request",
                    "args": {"api_key": "secret123", "url": "https://api.example.com"},
                    "result": {"data": "response", "auth_token": "token456"},
                },
                {
                    "name": "database_query",
                    "args": {"query": "SELECT *", "password": "dbpass"},
                },
            ],
            "metadata": {"timestamp": "2026-01-18T12:00:00Z", "session": "sess123"},
        }

        redacted = policy.redact("test", payload)

        # Check structure is preserved
        self.assertEqual(redacted["run_id"], "run123")
        self.assertEqual(len(redacted["tool_calls"]), 2)

        # Check secrets are redacted
        self.assertEqual(redacted["tool_calls"][0]["args"]["api_key"], "[REDACTED]")
        self.assertEqual(
            redacted["tool_calls"][0]["args"]["url"], "https://api.example.com"
        )
        self.assertEqual(redacted["tool_calls"][0]["result"]["data"], "response")
        self.assertEqual(
            redacted["tool_calls"][0]["result"]["auth_token"], "[REDACTED]"
        )
        self.assertEqual(redacted["tool_calls"][1]["args"]["query"], "SELECT *")
        self.assertEqual(redacted["tool_calls"][1]["args"]["password"], "[REDACTED]")
        self.assertEqual(redacted["metadata"]["timestamp"], "2026-01-18T12:00:00Z")
        self.assertEqual(redacted["metadata"]["session"], "[REDACTED]")


class TestRecorderIntegration(unittest.TestCase):
    """Test integration of RedactionPolicy with RunRecorder."""

    def test_recorder_applies_redaction_by_default(self):
        """RunRecorder should apply default redaction policy automatically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test.py")

            # Log event with sensitive data
            recorder.log_event(
                run_id,
                "tool_call",
                payload={
                    "tool": "api_request",
                    "args": {"api_key": "secret123", "url": "https://api.example.com"},
                    "result": {"data": "response"},
                },
            )

            # Retrieve events
            events = recorder.get_events(run_id)

            # Verify sensitive data is redacted in storage
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["tool"], "api_request")
            self.assertEqual(events[0]["payload"]["args"]["api_key"], "[REDACTED]")
            self.assertEqual(
                events[0]["payload"]["args"]["url"], "https://api.example.com"
            )
            self.assertEqual(events[0]["payload"]["result"]["data"], "response")

    def test_recorder_with_custom_policy(self):
        """RunRecorder should accept custom redaction policy."""
        custom_policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.DROP, key_pattern="drop_me")]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            recorder = RunRecorder(db_path=db_path, redaction_policy=custom_policy)

            run_id = recorder.start_run(entrypoint="test.py")

            recorder.log_event(
                run_id,
                "test",
                payload={"drop_me": "sensitive", "keep_me": "safe"},
            )

            events = recorder.get_events(run_id)

            # Verify custom policy was applied
            self.assertNotIn("drop_me", events[0]["payload"])
            self.assertEqual(events[0]["payload"]["keep_me"], "safe")

    def test_recorder_does_not_mutate_input(self):
        """RunRecorder should not mutate the input payload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test.py")

            original_payload = {"api_key": "secret123", "data": "value"}
            payload_before = {"api_key": "secret123", "data": "value"}

            recorder.log_event(run_id, "test", payload=original_payload)

            # Verify input was not mutated
            self.assertEqual(original_payload, payload_before)
            self.assertEqual(original_payload["api_key"], "secret123")

    def test_recorder_redaction_is_deterministic(self):
        """RunRecorder redaction should be deterministic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            recorder = RunRecorder(db_path=db_path)

            run_id = recorder.start_run(entrypoint="test.py")

            # Log same payload multiple times
            payload = {"api_key": "secret123", "data": "value"}

            recorder.log_event(run_id, "test", payload=payload)
            recorder.log_event(run_id, "test", payload=payload)
            recorder.log_event(run_id, "test", payload=payload)

            events = recorder.get_events(run_id)

            # All events should have identical redacted payloads
            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["payload"], events[1]["payload"])
            self.assertEqual(events[1]["payload"], events[2]["payload"])


if __name__ == "__main__":
    unittest.main()
