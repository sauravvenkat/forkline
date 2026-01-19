# RedactionPolicy v0 Implementation

## Overview

Forkline's RedactionPolicy v0 is a **security-critical, deterministic data redaction system** that operates at the storage boundary. All event payloads are redacted before being persisted to disk.

## Core Principles

✅ **Deterministic**: Same input → same output  
✅ **Pure function**: No I/O, no randomness  
✅ **Immutable**: Never mutates input payloads  
✅ **Storage boundary**: Redaction happens before persistence  
✅ **Human-inspectable**: JSON output remains readable  

## Architecture

### Components

1. **RedactionAction** (enum): `MASK`, `HASH`, `DROP`
2. **RedactionRule**: Pattern-based matching rules
3. **RedactionPolicy**: Applies rules deterministically
4. **DefaultRedactionPolicy**: SAFE mode for production

### Integration Points

- `forkline/core/redaction.py`: Core redaction primitives
- `forkline/storage/recorder.py`: Storage boundary integration
- `tests/unit/test_redaction_policy.py`: Comprehensive test suite

## Default Redaction Behavior (SAFE Mode)

The default policy automatically redacts:

### Secrets & Credentials
- `api_key`, `apikey`, `token`, `secret`, `password`
- `access_token`, `refresh_token`, `private_key`
- `credentials`, `auth`, `session`, `csrf`

### HTTP Headers
- `Authorization`, `Cookie`, `Set-Cookie`

### Action: MASK
All matched fields are replaced with `"[REDACTED]"`

## Usage

### Automatic (Default)

```python
from forkline.storage.recorder import RunRecorder

# Default SAFE mode redaction applied automatically
recorder = RunRecorder(db_path="runs.db")

run_id = recorder.start_run(entrypoint="my_script.py")

# Sensitive data is redacted before storage
recorder.log_event(
    run_id,
    "tool_call",
    payload={
        "api_key": "sk-secret123",  # → "[REDACTED]"
        "url": "https://api.example.com",  # → preserved
    }
)
```

### Custom Policy

```python
from forkline.core.redaction import (
    RedactionPolicy,
    RedactionRule,
    RedactionAction,
)
from forkline.storage.recorder import RunRecorder

# Define custom rules
policy = RedactionPolicy(
    rules=[
        RedactionRule(action=RedactionAction.HASH, key_pattern="email"),
        RedactionRule(action=RedactionAction.DROP, key_pattern="debug"),
        RedactionRule(action=RedactionAction.MASK, key_pattern="password"),
    ]
)

recorder = RunRecorder(db_path="runs.db", redaction_policy=policy)
```

## Redaction Actions

### MASK
Replaces value with `"[REDACTED]"`

```python
{"api_key": "secret"} → {"api_key": "[REDACTED]"}
```

### HASH
Replaces value with deterministic SHA-256 hash

```python
{"email": "user@example.com"} → {"email": "hash:d0901b1a..."}
```

Same input always produces the same hash (enables diffing).

### DROP
Removes the field entirely

```python
{"debug": "verbose", "data": "value"} → {"data": "value"}
```

## Pattern Matching

### Key Pattern
Matches key names (case-insensitive substring)

```python
RedactionRule(action=RedactionAction.MASK, key_pattern="secret")
```

Matches: `secret`, `client_secret`, `SECRET_KEY`, `my_secret_token`

### Path Pattern
Matches dot-separated paths

```python
RedactionRule(action=RedactionAction.MASK, path_pattern="headers.authorization")
```

Matches: `payload["headers"]["authorization"]`  
Does NOT match: `payload["body"]["authorization"]` (different path)

### Combined Patterns
Both patterns must match

```python
RedactionRule(
    action=RedactionAction.MASK,
    key_pattern="token",
    path_pattern="auth.token",
)
```

## Structural Redaction

Redaction operates **recursively** on nested structures:

```python
# Before
{
    "user": {
        "email": "user@example.com",
        "settings": {
            "api_key": "secret123"
        }
    }
}

# After (with default policy)
{
    "user": {
        "email": "user@example.com",
        "settings": {
            "api_key": "[REDACTED]"
        }
    }
}
```

Lists are also processed recursively:

```python
# Before
{"tool_calls": [{"api_key": "secret1"}, {"api_key": "secret2"}]}

# After
{"tool_calls": [{"api_key": "[REDACTED]"}, {"api_key": "[REDACTED]"}]}
```

## Safe Fields (Never Redacted)

The default policy preserves:
- `run_id`, `event_id`, `step_id`
- `timestamp`, `created_at`, `started_at`, `ended_at`
- `status`, `duration`, `type`, `name`
- `tool`, `model`, `entrypoint`

These are structural metadata, not sensitive data.

## Testing

Comprehensive test suite (29 tests):

```bash
python3 -m unittest tests.unit.test_redaction_policy -v
```

Tests verify:
- ✅ Determinism (same input → same output)
- ✅ Input immutability (no mutation)
- ✅ Correct rule application
- ✅ Recursive nested redaction
- ✅ Storage boundary integration
- ✅ Default policy behavior

## Security Properties

### Hard Constraints (Enforced)

❌ **No randomness**: Deterministic hashing  
❌ **No I/O**: Pure functions only  
❌ **No mutation**: Deep copy before redaction  
❌ **No regex-only hacking**: Proper structural redaction  

### Guarantees

✅ Storage never sees raw sensitive payloads  
✅ Same input always produces same output  
✅ Input payloads are never mutated  
✅ Redaction is applied before disk write  

## Design Decisions

### Why at storage boundary?
- Single point of enforcement
- Tracer code stays simple (no redaction logic)
- Storage is treated as hostile surface

### Why no regex?
- Structural redaction is more reliable
- Key/path patterns are sufficient
- Avoids false positives in values

### Why deterministic hashing?
- Enables diffing across runs
- Correlation without exposing data
- Stable for forensic analysis

## Future Extensions (Not in v0)

- [ ] Event-type-specific rules
- [ ] User-configurable policies
- [ ] Advanced path matching (wildcards)
- [ ] Encrypted DEBUG mode
- [ ] Redaction auditing/logging

## References

- `docs/REDACTION_POLICY.md`: Policy specification
- `examples/redaction_demo.py`: Working demonstrations
- `tests/unit/test_redaction_policy.py`: Test suite
