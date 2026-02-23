"""
RedactionPolicy: Deterministic, security-critical data redaction.

Redaction occurs at the storage boundary. All payloads are redacted before
persistence. This is a pure, deterministic compiler pass - no I/O, no randomness.

Design principles:
- Explicit over clever
- Deterministic (same input → same output, sorted key traversal)
- No mutation of inputs
- Human-inspectable output
- Security-focused (fail closed)

Supports three matching strategies:
1. Key-based: case-insensitive substring match on dict key names
2. Path-based: case-insensitive substring match on dotted paths
3. Regex-based: regex match on string values (e.g., JWTs, Bearer tokens)

Rules are applied in deterministic order:
- Structural rules (key/path) are evaluated per dict key, first match wins
- Regex rules are applied to all surviving string values, in rule order

Dict keys are traversed in sorted order for cross-run determinism.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RedactionAction(str, Enum):
    """Action to take when a redaction rule matches."""

    MASK = "mask"  # Replace with "[REDACTED]" or custom replacement
    HASH = "hash"  # Replace with deterministic SHA-256 hash
    DROP = "drop"  # Remove the field entirely


@dataclass(frozen=True)
class RedactionRule:
    """
    A single redaction rule.

    Rules match on:
    - key_pattern: matches key names (case-insensitive substring match)
    - path_pattern: matches dot-separated paths (e.g., "headers.authorization")

    Both patterns are optional. If both are specified, BOTH must match.
    If neither is specified, the rule never matches.

    replacement: Custom replacement string (default: "[REDACTED]").
                 Only used with MASK action.
    """

    action: RedactionAction
    key_pattern: Optional[str] = None
    path_pattern: Optional[str] = None
    replacement: str = "[REDACTED]"

    def __post_init__(self):
        """Validate that at least one pattern is specified."""
        if self.key_pattern is None and self.path_pattern is None:
            raise ValueError("RedactionRule requires at least one pattern")


@dataclass(frozen=True)
class RegexRedactionRule:
    """
    A regex-based redaction rule for string values.

    Applied to all string values after structural (key/path) redaction.
    Matches are replaced with the given replacement string.

    Attributes:
        name: Human-readable name for this rule (e.g., "jwt", "bearer").
        pattern: Compiled regex pattern.
        replacement: Replacement string for matches.
    """

    name: str
    pattern: re.Pattern
    replacement: str

    @classmethod
    def from_config(
        cls, name: str, pattern_str: str, replacement: str
    ) -> "RegexRedactionRule":
        return cls(name=name, pattern=re.compile(pattern_str), replacement=replacement)


@dataclass
class RedactionConfig:
    """
    Complete redaction configuration loaded from a config file.

    Aggregates structural rules (key/path) and regex rules into a
    single deterministic config object.
    """

    redact_keys: List[str] = field(default_factory=list)
    redact_paths: List[str] = field(default_factory=list)
    redact_regex: List[Dict[str, str]] = field(default_factory=list)

    def to_policy(self) -> "RedactionPolicy":
        """Convert config to a RedactionPolicy."""
        structural_rules: List[RedactionRule] = []

        for key in self.redact_keys:
            structural_rules.append(
                RedactionRule(action=RedactionAction.MASK, key_pattern=key)
            )

        for path in self.redact_paths:
            structural_rules.append(
                RedactionRule(action=RedactionAction.MASK, path_pattern=path)
            )

        regex_rules: List[RegexRedactionRule] = []
        for entry in self.redact_regex:
            regex_rules.append(
                RegexRedactionRule.from_config(
                    name=entry["name"],
                    pattern_str=entry["pattern"],
                    replacement=entry.get("replacement", f"[REDACTED:{entry['name']}]"),
                )
            )

        return RedactionPolicy(rules=structural_rules, regex_rules=regex_rules)


class RedactionPolicy:
    """
    Deterministic redaction policy.

    Applies rules in order to event payloads. The first matching rule wins
    for structural (key/path) rules. Regex rules are applied to all string
    values that survive structural redaction.

    Dict keys are traversed in sorted order for cross-run determinism.

    This is a pure function: same input → same output.
    No I/O. No randomness. No mutation of inputs.
    """

    def __init__(
        self,
        rules: List[RedactionRule],
        regex_rules: Optional[List[RegexRedactionRule]] = None,
    ):
        """
        Create a redaction policy.

        Args:
            rules: Ordered list of structural redaction rules (first match wins)
            regex_rules: Ordered list of regex rules applied to string values
        """
        self.rules = rules
        self.regex_rules: List[RegexRedactionRule] = regex_rules or []

    def redact(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Redact a payload according to policy rules.

        Args:
            event_type: Event type (for future event-specific redaction)
            payload: Event payload to redact

        Returns:
            Redacted payload (new dict, input not mutated)
        """
        redacted = copy.deepcopy(payload)
        return self._redact_value(redacted, path="")

    def _redact_value(self, value: Any, path: str) -> Any:
        if isinstance(value, dict):
            return self._redact_dict(value, path)
        elif isinstance(value, list):
            return self._redact_list(value, path)
        elif isinstance(value, str):
            return self._apply_regex_rules(value)
        else:
            return value

    def _redact_dict(self, d: Dict[str, Any], path: str) -> Dict[str, Any]:
        """
        Redact a dictionary by applying rules to each key-value pair.

        Keys are iterated in sorted order for deterministic traversal
        regardless of dict construction order.
        """
        result = {}

        for key in sorted(d.keys()):
            value = d[key]
            current_path = f"{path}.{key}" if path else key

            matched_rule = self._find_matching_rule(key, current_path)

            if matched_rule is None:
                result[key] = self._redact_value(value, current_path)
            elif matched_rule.action == RedactionAction.DROP:
                pass
            elif matched_rule.action == RedactionAction.MASK:
                result[key] = matched_rule.replacement
            elif matched_rule.action == RedactionAction.HASH:
                result[key] = self._hash_value(value)

        return result

    def _redact_list(self, lst: List[Any], path: str) -> List[Any]:
        return [self._redact_value(item, path) for item in lst]

    def _apply_regex_rules(self, value: str) -> str:
        """
        Apply regex redaction rules to a string value.

        Rules are applied in order. Each rule's replacement is applied
        to the full string. This is deterministic: same rules in same
        order on same string always produce the same output.
        """
        result = value
        for rule in self.regex_rules:
            result = rule.pattern.sub(rule.replacement, result)
        return result

    def _find_matching_rule(self, key: str, path: str) -> Optional[RedactionRule]:
        for rule in self.rules:
            key_matches = (
                rule.key_pattern is None or rule.key_pattern.lower() in key.lower()
            )
            path_matches = (
                rule.path_pattern is None or rule.path_pattern.lower() in path.lower()
            )
            if key_matches and path_matches:
                return rule
        return None

    def _hash_value(self, value: Any) -> str:
        """
        Create a deterministic hash of a value.

        Uses json.dumps with sort_keys for stable serialization of dicts,
        falling back to repr for non-JSON-serializable values.
        """
        try:
            serialized = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
        except (TypeError, ValueError):
            serialized = repr(value).encode("utf-8")
        digest = hashlib.sha256(serialized).hexdigest()
        return f"hash:{digest}"


def create_default_policy() -> RedactionPolicy:
    """
    Create the default SAFE mode redaction policy.

    Default redaction covers:
    - Key-based: password, passwd, token, api_key, apikey, authorization,
      cookie, set-cookie, secret, credentials, private_key, privatekey,
      access_token, refresh_token, session, csrf, key, auth
    - Regex-based: JWTs (eyJ...), Bearer tokens, AWS access keys

    Returns:
        Default RedactionPolicy for production use
    """
    structural_rules = [
        RedactionRule(action=RedactionAction.MASK, key_pattern="key"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="token"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="secret"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="password"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="passwd"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="api_key"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="apikey"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="auth"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="authorization"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="cookie"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="set-cookie"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="credentials"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="private_key"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="privatekey"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="access_token"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="refresh_token"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="session"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="csrf"),
    ]

    regex_rules = [
        RegexRedactionRule.from_config(
            name="jwt",
            pattern_str=r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            replacement="[REDACTED:jwt]",
        ),
        RegexRedactionRule.from_config(
            name="bearer",
            pattern_str=r"(?i)bearer\s+[A-Za-z0-9._\-]+",
            replacement="Bearer [REDACTED]",
        ),
        RegexRedactionRule.from_config(
            name="aws_key",
            pattern_str=r"AKIA[A-Z0-9]{16}",
            replacement="[REDACTED:aws_key]",
        ),
    ]

    return RedactionPolicy(rules=structural_rules, regex_rules=regex_rules)


def load_redaction_config(path: str) -> RedactionConfig:
    """
    Load a RedactionConfig from a YAML or JSON file.

    YAML requires the ``pyyaml`` package. JSON is always available.
    File format is detected by extension (.yaml/.yml for YAML, .json for JSON).

    Config file schema::

        fields:
          redact_keys:
            - password
            - token
          redact_paths:
            - "tool.request.headers.Authorization"
          redact_regex:
            - name: jwt
              pattern: "eyJ..."
              replacement: "[REDACTED:jwt]"

    Args:
        path: Path to config file

    Returns:
        RedactionConfig

    Raises:
        FileNotFoundError: If file does not exist
        ValueError: If file format is unsupported or content is invalid
    """
    import os

    if not os.path.isfile(path):
        raise FileNotFoundError(f"Redaction config not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    with open(path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    if ext in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "PyYAML is required for YAML config files. "
                "Install it with: pip install pyyaml"
            )
        data = yaml.safe_load(raw_text)
    elif ext == ".json":
        data = json.loads(raw_text)
    else:
        raise ValueError(
            f"Unsupported config file extension: {ext}. Use .yaml, .yml, or .json"
        )

    if data is None:
        data = {}

    fields = data.get("fields", data)

    return RedactionConfig(
        redact_keys=fields.get("redact_keys", []),
        redact_paths=fields.get("redact_paths", []),
        redact_regex=fields.get("redact_regex", []),
    )
