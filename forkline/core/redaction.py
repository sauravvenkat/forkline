"""
RedactionPolicy v0: Deterministic, security-critical data redaction.

Redaction occurs at the storage boundary. All payloads are redacted before
persistence. This is a pure, deterministic compiler pass - no I/O, no randomness.

Design principles:
- Explicit over clever
- Deterministic (same input → same output)
- No mutation of inputs
- Human-inspectable output
- Security-focused (fail closed)
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class RedactionAction(str, Enum):
    """Action to take when a redaction rule matches."""

    MASK = "mask"  # Replace with "[REDACTED]"
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
    """

    action: RedactionAction
    key_pattern: Optional[str] = None
    path_pattern: Optional[str] = None

    def __post_init__(self):
        """Validate that at least one pattern is specified."""
        if self.key_pattern is None and self.path_pattern is None:
            raise ValueError("RedactionRule requires at least one pattern")


class RedactionPolicy:
    """
    Deterministic redaction policy.

    Applies rules in order to event payloads. The first matching rule wins.

    This is a pure function: same input → same output.
    No I/O. No randomness. No mutation of inputs.
    """

    def __init__(self, rules: List[RedactionRule]):
        """
        Create a redaction policy.

        Args:
            rules: Ordered list of redaction rules (first match wins)
        """
        self.rules = rules

    def redact(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Redact a payload according to policy rules.

        Args:
            event_type: Event type (for future event-specific redaction)
            payload: Event payload to redact

        Returns:
            Redacted payload (new dict, input not mutated)
        """
        # Deep copy to ensure no mutation of input
        redacted = copy.deepcopy(payload)

        # Apply redaction recursively
        return self._redact_value(redacted, path="")

    def _redact_value(self, value: Any, path: str) -> Any:
        """
        Recursively redact a value.

        Args:
            value: Value to redact (dict, list, or primitive)
            path: Current dot-separated path (for path_pattern matching)

        Returns:
            Redacted value
        """
        if isinstance(value, dict):
            return self._redact_dict(value, path)
        elif isinstance(value, list):
            return self._redact_list(value, path)
        else:
            # Primitives: check if current path should be redacted
            # (relevant when called from _redact_dict with a specific key)
            return value

    def _redact_dict(self, d: Dict[str, Any], path: str) -> Dict[str, Any]:
        """
        Redact a dictionary by applying rules to each key-value pair.

        Args:
            d: Dictionary to redact
            path: Current dot-separated path

        Returns:
            Redacted dictionary
        """
        result = {}

        for key, value in d.items():
            # Build full path for this key
            current_path = f"{path}.{key}" if path else key

            # Check if this key should be redacted
            matched_rule = self._find_matching_rule(key, current_path)

            if matched_rule is None:
                # No match: recursively redact the value
                result[key] = self._redact_value(value, current_path)
            elif matched_rule.action == RedactionAction.DROP:
                # Drop: omit the key entirely
                pass
            elif matched_rule.action == RedactionAction.MASK:
                # Mask: replace with sentinel
                result[key] = "[REDACTED]"
            elif matched_rule.action == RedactionAction.HASH:
                # Hash: deterministic SHA-256
                result[key] = self._hash_value(value)

        return result

    def _redact_list(self, lst: List[Any], path: str) -> List[Any]:
        """
        Redact a list by recursively redacting each element.

        Args:
            lst: List to redact
            path: Current dot-separated path

        Returns:
            Redacted list
        """
        return [self._redact_value(item, path) for item in lst]

    def _find_matching_rule(self, key: str, path: str) -> Optional[RedactionRule]:
        """
        Find the first rule that matches the given key and path.

        Args:
            key: Key name
            path: Dot-separated path

        Returns:
            First matching rule, or None if no match
        """
        for rule in self.rules:
            # Check key_pattern (case-insensitive substring match)
            key_matches = (
                rule.key_pattern is None or rule.key_pattern.lower() in key.lower()
            )

            # Check path_pattern (case-insensitive substring match)
            path_matches = (
                rule.path_pattern is None or rule.path_pattern.lower() in path.lower()
            )

            # Both must match (if specified)
            if key_matches and path_matches:
                return rule

        return None

    def _hash_value(self, value: Any) -> str:
        """
        Create a deterministic hash of a value.

        Args:
            value: Value to hash

        Returns:
            Hex-encoded SHA-256 hash prefixed with "hash:"
        """
        # Serialize value to stable string representation
        # Use repr for determinism (same value → same repr)
        serialized = repr(value).encode("utf-8")

        # Compute SHA-256
        digest = hashlib.sha256(serialized).hexdigest()

        # Prefix for clarity
        return f"hash:{digest}"


def create_default_policy() -> RedactionPolicy:
    """
    Create the default SAFE mode redaction policy.

    This policy implements the SAFE mode behavior described in REDACTION_POLICY.md:
    - Masks secrets (env keys, headers, tokens)
    - Masks sensitive tool arguments
    - Prevents credential leakage

    Returns:
        Default RedactionPolicy for production use
    """
    rules = [
        # Environment variables containing secrets
        RedactionRule(action=RedactionAction.MASK, key_pattern="key"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="token"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="secret"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="password"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="api_key"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="apikey"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="auth"),
        # HTTP headers
        RedactionRule(action=RedactionAction.MASK, key_pattern="authorization"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="cookie"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="set-cookie"),
        # Common credential fields
        RedactionRule(action=RedactionAction.MASK, key_pattern="credentials"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="private_key"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="privatekey"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="access_token"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="refresh_token"),
        # Session identifiers
        RedactionRule(action=RedactionAction.MASK, key_pattern="session"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="csrf"),
    ]

    return RedactionPolicy(rules)
