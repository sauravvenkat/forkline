"""
Demonstration of RedactionPolicy v0.

This example shows how Forkline automatically redacts sensitive data
at the storage boundary before persisting to disk.
"""

import json
import os
import tempfile

from forkline.core.redaction import (
    RedactionAction,
    RedactionPolicy,
    RedactionRule,
    create_default_policy,
)
from forkline.storage.recorder import RunRecorder


def demo_default_redaction():
    """Demonstrate default SAFE mode redaction."""
    print("=== Default Redaction Demo ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "demo.db")
        recorder = RunRecorder(db_path=db_path)

        run_id = recorder.start_run(entrypoint="redaction_demo.py")

        # Log an event with sensitive data
        sensitive_payload = {
            "tool": "api_request",
            "args": {
                "url": "https://api.example.com/data",
                "api_key": "sk-secret123456789",  # Will be redacted
                "headers": {
                    "Authorization": "Bearer token123",  # Will be redacted
                    "Content-Type": "application/json",  # Safe, preserved
                },
            },
            "result": {
                "status": 200,
                "data": "response content",
                "session": "sess_abc123",  # Will be redacted
            },
        }

        print("Original payload (before storage):")
        print(json.dumps(sensitive_payload, indent=2))
        print()

        # Log the event - redaction happens automatically
        recorder.log_event(run_id, "tool_call", payload=sensitive_payload)

        # Retrieve from storage - see redacted version
        events = recorder.get_events(run_id)
        stored_payload = events[0]["payload"]

        print("Stored payload (after redaction):")
        print(json.dumps(stored_payload, indent=2))
        print()

        # Verify original is unchanged (immutability)
        print("Original payload still intact (immutability check):")
        print(f"  api_key = {sensitive_payload['args']['api_key']}")
        auth_header = sensitive_payload['args']['headers']['Authorization']
        print(f"  Authorization = {auth_header}")
        print()

        recorder.end_run(run_id, status="success")


def demo_custom_policy():
    """Demonstrate custom redaction policy."""
    print("=== Custom Policy Demo ===\n")

    # Create a custom policy: hash PII, drop debug info
    custom_policy = RedactionPolicy(
        rules=[
            RedactionRule(action=RedactionAction.HASH, key_pattern="email"),
            RedactionRule(action=RedactionAction.HASH, key_pattern="user_id"),
            RedactionRule(action=RedactionAction.DROP, key_pattern="debug"),
            RedactionRule(action=RedactionAction.MASK, key_pattern="password"),
        ]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "demo.db")
        recorder = RunRecorder(db_path=db_path, redaction_policy=custom_policy)

        run_id = recorder.start_run(entrypoint="redaction_demo.py")

        payload = {
            "email": "user@example.com",  # Will be hashed
            "user_id": "usr_12345",  # Will be hashed
            "password": "secret123",  # Will be masked
            "debug_info": "verbose logs",  # Will be dropped
            "message": "Hello world",  # Preserved
        }

        print("Original payload:")
        print(json.dumps(payload, indent=2))
        print()

        recorder.log_event(run_id, "user_action", payload=payload)

        events = recorder.get_events(run_id)
        stored_payload = events[0]["payload"]

        print("Stored payload (custom redaction):")
        print(json.dumps(stored_payload, indent=2))
        print()

        # Show determinism: same input → same hash
        payload2 = {"email": "user@example.com", "message": "Different message"}
        recorder.log_event(run_id, "user_action", payload=payload2)

        events = recorder.get_events(run_id)
        print("Determinism check (same email, different context):")
        print(f"  First hash:  {events[0]['payload']['email']}")
        print(f"  Second hash: {events[1]['payload']['email']}")
        match = events[0]['payload']['email'] == events[1]['payload']['email']
        print(f"  Match: {match}")
        print()

        recorder.end_run(run_id, status="success")


def demo_nested_redaction():
    """Demonstrate recursive redaction on nested structures."""
    print("=== Nested Redaction Demo ===\n")

    policy = create_default_policy()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "demo.db")
        recorder = RunRecorder(db_path=db_path, redaction_policy=policy)

        run_id = recorder.start_run(entrypoint="redaction_demo.py")

        nested_payload = {
            "run_id": "run_123",
            "timestamp": "2026-01-18T12:00:00Z",
            "tool_calls": [
                {
                    "name": "database_query",
                    "args": {"query": "SELECT * FROM users", "password": "dbpass123"},
                    "result": {"rows": 42, "session": "sess_xyz"},
                },
                {
                    "name": "external_api",
                    "args": {
                        "url": "https://api.example.com",
                        "api_key": "sk-production-key",
                    },
                    "result": {"status": "ok"},
                },
            ],
        }

        print("Original nested payload:")
        print(json.dumps(nested_payload, indent=2))
        print()

        recorder.log_event(run_id, "execution", payload=nested_payload)

        events = recorder.get_events(run_id)
        stored_payload = events[0]["payload"]

        print("Stored payload (nested redaction):")
        print(json.dumps(stored_payload, indent=2))
        print()

        recorder.end_run(run_id, status="success")


if __name__ == "__main__":
    demo_default_redaction()
    print("\n" + "=" * 50 + "\n")
    demo_custom_policy()
    print("\n" + "=" * 50 + "\n")
    demo_nested_redaction()
    print("\n✅ All demos completed successfully!")
    print("Sensitive data was redacted before storage.")
    print("Original payloads remain unchanged (immutability).")
