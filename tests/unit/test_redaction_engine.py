"""
Tests for the enhanced redaction engine.

Covers:
- Regex-based string value redaction
- Deterministic sorted key traversal
- Custom replacement strings
- RedactionConfig loading from JSON
- Config-to-policy conversion
- Rule order stability
- Cross-run determinism (dict construction order independence)
- Default policy regex rules (JWT, Bearer, AWS keys)
"""

import json
import os
import tempfile
import unittest

from forkline.core.redaction import (
    RedactionAction,
    RedactionConfig,
    RedactionPolicy,
    RedactionRule,
    RegexRedactionRule,
    create_default_policy,
    load_redaction_config,
)


class TestRegexRedactionRule(unittest.TestCase):
    """Test RegexRedactionRule construction and matching."""

    def test_from_config(self):
        rule = RegexRedactionRule.from_config(
            name="jwt",
            pattern_str=r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            replacement="[REDACTED:jwt]",
        )
        self.assertEqual(rule.name, "jwt")
        self.assertEqual(rule.replacement, "[REDACTED:jwt]")

    def test_pattern_matches(self):
        rule = RegexRedactionRule.from_config(
            name="bearer",
            pattern_str=r"(?i)bearer\s+[A-Za-z0-9._\-]+",
            replacement="Bearer [REDACTED]",
        )
        result = rule.pattern.sub(rule.replacement, "Bearer abc123.xyz")
        self.assertEqual(result, "Bearer [REDACTED]")


class TestRegexRedaction(unittest.TestCase):
    """Test regex-based string value redaction."""

    def test_jwt_redaction(self):
        policy = RedactionPolicy(
            rules=[],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="jwt",
                    pattern_str=r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                    replacement="[REDACTED:jwt]",
                ),
            ],
        )
        jwt = "eyJhbGciOiJIUzI1NiJ9" ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" ".abcdef123456"
        payload = {
            "message": f"Token is {jwt}",
            "safe": "no jwt here",
        }
        redacted = policy.redact("test", payload)
        self.assertIn("[REDACTED:jwt]", redacted["message"])
        self.assertNotIn("eyJ", redacted["message"])
        self.assertEqual(redacted["safe"], "no jwt here")

    def test_bearer_token_redaction(self):
        policy = RedactionPolicy(
            rules=[],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="bearer",
                    pattern_str=r"(?i)bearer\s+[A-Za-z0-9._\-]+",
                    replacement="Bearer [REDACTED]",
                ),
            ],
        )
        payload = {"auth": "Bearer sk-12345.abc_def"}
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["auth"], "Bearer [REDACTED]")

    def test_aws_key_redaction(self):
        policy = RedactionPolicy(
            rules=[],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="aws_key",
                    pattern_str=r"AKIA[A-Z0-9]{16}",
                    replacement="[REDACTED:aws_key]",
                ),
            ],
        )
        payload = {"credentials": "key=AKIAIOSFODNN7EXAMPLE and more text"}
        redacted = policy.redact("test", payload)
        self.assertIn("[REDACTED:aws_key]", redacted["credentials"])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted["credentials"])

    def test_regex_applied_in_nested_structures(self):
        policy = RedactionPolicy(
            rules=[],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="jwt",
                    pattern_str=r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                    replacement="[REDACTED:jwt]",
                ),
            ],
        )
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
        payload = {
            "level1": {
                "level2": {"token_value": f"prefix {jwt} suffix"},
            },
            "list_items": [f"item {jwt}", "clean item"],
        }
        redacted = policy.redact("test", payload)
        self.assertNotIn("eyJ", json.dumps(redacted))
        self.assertIn("[REDACTED:jwt]", redacted["level1"]["level2"]["token_value"])
        self.assertIn("[REDACTED:jwt]", redacted["list_items"][0])
        self.assertEqual(redacted["list_items"][1], "clean item")

    def test_multiple_regex_rules_applied_in_order(self):
        policy = RedactionPolicy(
            rules=[],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="ssn",
                    pattern_str=r"\d{3}-\d{2}-\d{4}",
                    replacement="[REDACTED:ssn]",
                ),
                RegexRedactionRule.from_config(
                    name="phone",
                    pattern_str=r"\d{3}-\d{3}-\d{4}",
                    replacement="[REDACTED:phone]",
                ),
            ],
        )
        payload = {
            "ssn": "123-45-6789",
            "phone": "555-123-4567",
        }
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["ssn"], "[REDACTED:ssn]")
        self.assertEqual(redacted["phone"], "[REDACTED:phone]")

    def test_regex_determinism(self):
        """Same regex rules + same input = same output."""
        policy = RedactionPolicy(
            rules=[],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="jwt",
                    pattern_str=r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                    replacement="[REDACTED:jwt]",
                ),
            ],
        )
        payload = {"token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig"}

        results = [policy.redact("test", payload) for _ in range(10)]
        for r in results[1:]:
            self.assertEqual(results[0], r)


class TestSortedKeyTraversal(unittest.TestCase):
    """Test deterministic sorted key traversal in dict redaction."""

    def test_sorted_traversal_produces_sorted_output(self):
        policy = RedactionPolicy(rules=[])
        payload = {"z_key": 1, "a_key": 2, "m_key": 3}
        redacted = policy.redact("test", payload)
        self.assertEqual(list(redacted.keys()), ["a_key", "m_key", "z_key"])

    def test_different_construction_order_same_result(self):
        """Dicts constructed in different order produce identical redacted output."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.MASK, key_pattern="secret")]
        )

        payload_a = {"z": "val_z", "secret_key": "sensitive", "a": "val_a"}
        payload_b = {"a": "val_a", "secret_key": "sensitive", "z": "val_z"}

        redacted_a = policy.redact("test", payload_a)
        redacted_b = policy.redact("test", payload_b)

        self.assertEqual(redacted_a, redacted_b)
        self.assertEqual(
            json.dumps(redacted_a, sort_keys=True),
            json.dumps(redacted_b, sort_keys=True),
        )

    def test_nested_sorted_traversal(self):
        policy = RedactionPolicy(rules=[])
        payload = {
            "b": {"y": 1, "x": 2},
            "a": {"z": 3, "w": 4},
        }
        redacted = policy.redact("test", payload)
        self.assertEqual(list(redacted.keys()), ["a", "b"])
        self.assertEqual(list(redacted["a"].keys()), ["w", "z"])
        self.assertEqual(list(redacted["b"].keys()), ["x", "y"])


class TestCustomReplacement(unittest.TestCase):
    """Test custom replacement strings in redaction rules."""

    def test_custom_replacement_in_rule(self):
        rule = RedactionRule(
            action=RedactionAction.MASK,
            key_pattern="password",
            replacement="[REDACTED:password]",
        )
        policy = RedactionPolicy(rules=[rule])
        payload = {"password": "secret123"}
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["password"], "[REDACTED:password]")

    def test_default_replacement(self):
        rule = RedactionRule(action=RedactionAction.MASK, key_pattern="secret")
        self.assertEqual(rule.replacement, "[REDACTED]")


class TestRedactionConfig(unittest.TestCase):
    """Test RedactionConfig and config file loading."""

    def test_config_to_policy_keys(self):
        config = RedactionConfig(
            redact_keys=["password", "token", "api_key"],
        )
        policy = config.to_policy()
        payload = {"password": "secret", "token": "abc", "safe": "ok"}
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["password"], "[REDACTED]")
        self.assertEqual(redacted["token"], "[REDACTED]")
        self.assertEqual(redacted["safe"], "ok")

    def test_config_to_policy_paths(self):
        config = RedactionConfig(
            redact_paths=["headers.authorization"],
        )
        policy = config.to_policy()
        payload = {
            "headers": {"authorization": "Bearer token", "content-type": "json"},
        }
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["headers"]["authorization"], "[REDACTED]")
        self.assertEqual(redacted["headers"]["content-type"], "json")

    def test_config_to_policy_regex(self):
        config = RedactionConfig(
            redact_regex=[
                {
                    "name": "jwt",
                    "pattern": r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                    "replacement": "[REDACTED:jwt]",
                }
            ],
        )
        policy = config.to_policy()
        payload = {"data": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"}
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["data"], "[REDACTED:jwt]")

    def test_config_to_policy_combined(self):
        config = RedactionConfig(
            redact_keys=["api_key"],
            redact_paths=["headers.cookie"],
            redact_regex=[
                {
                    "name": "bearer",
                    "pattern": r"(?i)bearer\s+[A-Za-z0-9._\-]+",
                    "replacement": "Bearer [REDACTED]",
                }
            ],
        )
        policy = config.to_policy()
        payload = {
            "api_key": "secret",
            "headers": {"cookie": "session=abc", "x-custom": "Bearer my-token"},
        }
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["headers"]["cookie"], "[REDACTED]")
        self.assertEqual(redacted["headers"]["x-custom"], "Bearer [REDACTED]")

    def test_load_config_from_json(self):
        config_data = {
            "fields": {
                "redact_keys": ["password", "token"],
                "redact_paths": ["headers.authorization"],
                "redact_regex": [
                    {
                        "name": "jwt",
                        "pattern": r"eyJ[A-Za-z0-9_-]+",
                        "replacement": "[REDACTED:jwt]",
                    }
                ],
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            f.flush()
            config_path = f.name

        try:
            config = load_redaction_config(config_path)
            self.assertEqual(config.redact_keys, ["password", "token"])
            self.assertEqual(config.redact_paths, ["headers.authorization"])
            self.assertEqual(len(config.redact_regex), 1)
            self.assertEqual(config.redact_regex[0]["name"], "jwt")

            policy = config.to_policy()
            payload = {"password": "secret", "safe": "ok"}
            redacted = policy.redact("test", payload)
            self.assertEqual(redacted["password"], "[REDACTED]")
            self.assertEqual(redacted["safe"], "ok")
        finally:
            os.unlink(config_path)

    def test_load_config_flat_format(self):
        """Config without 'fields' wrapper also works."""
        config_data = {
            "redact_keys": ["secret"],
            "redact_paths": [],
            "redact_regex": [],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            f.flush()
            config_path = f.name

        try:
            config = load_redaction_config(config_path)
            self.assertEqual(config.redact_keys, ["secret"])
        finally:
            os.unlink(config_path)

    def test_load_config_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_redaction_config("/nonexistent/path/config.json")

    def test_load_config_unsupported_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not a config")
            config_path = f.name
        try:
            with self.assertRaises(ValueError):
                load_redaction_config(config_path)
        finally:
            os.unlink(config_path)


class TestRuleOrderStability(unittest.TestCase):
    """Test that rule application order is stable and deterministic."""

    def test_structural_rules_first_match_wins(self):
        policy = RedactionPolicy(
            rules=[
                RedactionRule(
                    action=RedactionAction.MASK,
                    key_pattern="token",
                    replacement="[REDACTED:first]",
                ),
                RedactionRule(
                    action=RedactionAction.MASK,
                    key_pattern="token",
                    replacement="[REDACTED:second]",
                ),
            ],
        )
        payload = {"token": "value"}
        redacted = policy.redact("test", payload)
        self.assertEqual(redacted["token"], "[REDACTED:first]")

    def test_structural_before_regex(self):
        """Structural rules (key/path match) apply before regex rules."""
        policy = RedactionPolicy(
            rules=[
                RedactionRule(action=RedactionAction.MASK, key_pattern="secret"),
            ],
            regex_rules=[
                RegexRedactionRule.from_config(
                    name="digits",
                    pattern_str=r"\d+",
                    replacement="[NUM]",
                ),
            ],
        )
        payload = {"secret": "value123", "safe": "text456"}
        redacted = policy.redact("test", payload)
        # structural rule applies first, replaces entire value
        self.assertEqual(redacted["secret"], "[REDACTED]")
        # regex applies to surviving string values
        self.assertEqual(redacted["safe"], "text[NUM]")

    def test_rule_order_deterministic_across_runs(self):
        """Same rules in same order produce same results every time."""

        def make_policy():
            return RedactionPolicy(
                rules=[
                    RedactionRule(action=RedactionAction.MASK, key_pattern="a"),
                    RedactionRule(action=RedactionAction.HASH, key_pattern="b"),
                    RedactionRule(action=RedactionAction.DROP, key_pattern="c"),
                ],
                regex_rules=[
                    RegexRedactionRule.from_config("r1", r"\d+", "[NUM]"),
                ],
            )

        payload = {"a_key": "v1", "b_key": "v2", "c_key": "v3", "d_key": "123"}

        results = [make_policy().redact("test", payload) for _ in range(5)]
        for r in results[1:]:
            self.assertEqual(results[0], r)


class TestDefaultPolicyRegex(unittest.TestCase):
    """Test default policy regex rules."""

    def test_default_policy_redacts_jwt_in_values(self):
        policy = create_default_policy()
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dG9rZW5fc2lnbmF0dXJl"
        payload = {"message": f"Login token: {jwt}"}
        redacted = policy.redact("test", payload)
        self.assertNotIn("eyJ", redacted["message"])
        self.assertIn("[REDACTED:jwt]", redacted["message"])

    def test_default_policy_redacts_bearer_in_values(self):
        policy = create_default_policy()
        payload = {"log_line": "Authorization: Bearer sk-12345abcdef"}
        redacted = policy.redact("test", payload)
        self.assertNotIn("sk-12345abcdef", redacted["log_line"])

    def test_default_policy_redacts_aws_keys(self):
        policy = create_default_policy()
        payload = {"debug": "Using key AKIAIOSFODNN7EXAMPLE for access"}
        redacted = policy.redact("test", payload)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted["debug"])
        self.assertIn("[REDACTED:aws_key]", redacted["debug"])


class TestHashDeterminism(unittest.TestCase):
    """Test that hash action produces stable, deterministic hashes."""

    def test_hash_deterministic(self):
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.HASH, key_pattern="secret")]
        )
        payload = {"secret_key": "sensitive_value"}
        r1 = policy.redact("test", payload)
        r2 = policy.redact("test", payload)
        self.assertEqual(r1["secret_key"], r2["secret_key"])
        self.assertTrue(r1["secret_key"].startswith("hash:"))

    def test_hash_of_dict_is_stable(self):
        """Hash of a dict value uses json.dumps with sort_keys for stability."""
        policy = RedactionPolicy(
            rules=[RedactionRule(action=RedactionAction.HASH, key_pattern="secret")]
        )
        payload_a = {"secret_data": {"z": 1, "a": 2}}
        payload_b = {"secret_data": {"a": 2, "z": 1}}
        r_a = policy.redact("test", payload_a)
        r_b = policy.redact("test", payload_b)
        self.assertEqual(r_a["secret_data"], r_b["secret_data"])


class TestRecorderWithConfig(unittest.TestCase):
    """Test RunRecorder.with_config factory method."""

    def test_with_config_loads_json(self):
        config_data = {
            "fields": {
                "redact_keys": ["custom_secret"],
                "redact_paths": [],
                "redact_regex": [],
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "redact.json")
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            from forkline.storage.recorder import RunRecorder

            recorder = RunRecorder.with_config(
                db_path=os.path.join(tmpdir, "test.db"),
                redact_config_path=config_path,
            )
            run_id = recorder.start_run("test.py")
            recorder.log_event(run_id, "test", {"custom_secret": "value", "safe": "ok"})

            events = recorder.get_events(run_id)
            self.assertEqual(events[0]["payload"]["custom_secret"], "[REDACTED]")
            self.assertEqual(events[0]["payload"]["safe"], "ok")

    def test_with_config_none_uses_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from forkline.storage.recorder import RunRecorder

            recorder = RunRecorder.with_config(
                db_path=os.path.join(tmpdir, "test.db"),
                redact_config_path=None,
            )
            self.assertIsNotNone(recorder.redaction_policy)


if __name__ == "__main__":
    unittest.main()
