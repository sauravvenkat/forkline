# Redaction

Forkline provides deterministic, configurable redaction of sensitive data in
run artifacts. Redaction happens at the storage boundary — raw sensitive data
is **never** written to disk.

## Design Principles

- **Deterministic**: Same input + same rules = same output. Always.
- **Fail closed**: Default policy redacts common secrets automatically.
- **No mutation**: Input payloads are never modified; redaction returns a new copy.
- **Before persistence**: Redaction is applied before any write to SQLite or JSON.
- **Sorted traversal**: Dict keys are traversed in sorted order for cross-run determinism.

## Matching Strategies

Forkline supports three redaction strategies, applied in this order:

### 1. Key-Based Matching (Structural)

Matches dict keys by case-insensitive substring. If a key contains the
pattern, the value is redacted.

```yaml
redact_keys:
  - password
  - token
  - api_key
```

This matches `password`, `user_password`, `PASSWORD`, `api_key_v2`, etc.

### 2. Path-Based Matching (Structural)

Matches on dot-separated paths. Only redacts values at the specific
location in the object tree.

```yaml
redact_paths:
  - "headers.authorization"
  - "request.params.api_key"
```

`headers.authorization` matches `{"headers": {"authorization": "..."}}` but
**not** `{"body": {"authorization": "..."}}`.

### 3. Regex-Based Matching (Value)

Matches against string values using regex patterns. Applied to all string
values that survive structural redaction.

```yaml
redact_regex:
  - name: jwt
    pattern: "eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+"
    replacement: "[REDACTED:jwt]"
  - name: bearer
    pattern: "(?i)bearer\\s+[A-Za-z0-9._-]+"
    replacement: "Bearer [REDACTED]"
```

## Rule Application Order

Rules are applied deterministically:

1. **Structural rules** (key/path) are evaluated per dict key, first match wins
2. If a structural rule matches, it replaces the entire value (regex is not applied)
3. **Regex rules** are applied in order to all surviving string values
4. Dict keys are iterated in **sorted order** for deterministic traversal

This means:
- A key matching a structural MASK rule gets `"[REDACTED]"` — no regex processing
- A key that doesn't match any structural rule has its string value processed by all regex rules in order

## Redaction Actions

| Action | Behavior |
|--------|----------|
| `mask` | Replace value with `"[REDACTED]"` or a custom replacement string |
| `hash` | Replace with deterministic SHA-256: `"hash:<hex>"` |
| `drop` | Remove the key-value pair entirely |

## Configuration

### Config File Format

Create a `forkline.redact.yaml` (or `.json`) file:

```yaml
fields:
  redact_keys:
    - password
    - passwd
    - token
    - api_key
    - authorization
    - cookie
    - secret
    - session

  redact_paths:
    - "tool.request.headers.Authorization"
    - "tool.request.headers.Cookie"
    - "tool.request.params.api_key"

  redact_regex:
    - name: jwt
      pattern: "eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+"
      replacement: "[REDACTED:jwt]"

    - name: bearer
      pattern: "(?i)bearer\\s+[A-Za-z0-9._-]+"
      replacement: "Bearer [REDACTED]"

    - name: aws_key
      pattern: "AKIA[A-Z0-9]{16}"
      replacement: "[REDACTED:aws_key]"
```

JSON format is also supported (no PyYAML dependency required):

```json
{
  "fields": {
    "redact_keys": ["password", "token", "api_key"],
    "redact_paths": ["headers.authorization"],
    "redact_regex": [
      {
        "name": "jwt",
        "pattern": "eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+",
        "replacement": "[REDACTED:jwt]"
      }
    ]
  }
}
```

### CLI Usage

```bash
# Run with custom redaction config
forkline run my_agent.py --redact-config forkline.redact.yaml

# Replay does not require config (reads already-redacted artifacts)
forkline replay <run_id>
```

### Programmatic Usage

```python
from forkline.core.redaction import load_redaction_config, RedactionConfig

# From file
config = load_redaction_config("forkline.redact.yaml")
policy = config.to_policy()

# From code
config = RedactionConfig(
    redact_keys=["password", "token"],
    redact_paths=["headers.authorization"],
    redact_regex=[
        {"name": "jwt", "pattern": r"eyJ...", "replacement": "[REDACTED:jwt]"}
    ],
)
policy = config.to_policy()

# Use with RunRecorder
from forkline.storage.recorder import RunRecorder
recorder = RunRecorder(db_path="runs.db", redaction_policy=policy)

# Or use the factory method
recorder = RunRecorder.with_config(
    db_path="runs.db",
    redact_config_path="forkline.redact.yaml",
)
```

## Default Policy

When no custom config is provided, Forkline applies a safe default policy
that covers common secret patterns.

### Default Key Patterns (structural, MASK action)

| Pattern | Matches |
|---------|---------|
| `key` | api_key, secret_key, etc. |
| `token` | token, access_token, refresh_token, bearer_token |
| `secret` | secret, client_secret, SECRET_KEY |
| `password` | password, user_password |
| `passwd` | passwd |
| `api_key` | api_key |
| `apikey` | apikey |
| `auth` | auth, authorization, auth_token |
| `authorization` | authorization |
| `cookie` | cookie, set-cookie |
| `credentials` | credentials |
| `private_key` | private_key |
| `privatekey` | privatekey |
| `access_token` | access_token |
| `refresh_token` | refresh_token |
| `session` | session, session_id |
| `csrf` | csrf, csrf_token |

### Default Regex Patterns (value-level)

| Name | Pattern | Replacement |
|------|---------|-------------|
| `jwt` | `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` | `[REDACTED:jwt]` |
| `bearer` | `(?i)bearer\s+[A-Za-z0-9._-]+` | `Bearer [REDACTED]` |
| `aws_key` | `AKIA[A-Z0-9]{16}` | `[REDACTED:aws_key]` |

## Determinism Guarantees

Forkline's redaction is fully deterministic:

1. **Same input + same rules = same output** — tested and enforced
2. **No randomness** — no random IDs, no timestamps in redaction output
3. **Stable rule ordering** — rules are applied in the order they are defined
4. **Sorted key traversal** — dict keys are visited in sorted order
5. **Stable hash output** — HASH action uses `json.dumps(sort_keys=True)` for dict values

This means replaying a run with the same redaction config will always produce
identical stored artifacts.

## Where Redaction Happens

```
Tool call happens
       │
       ▼
Build tool_call payload (raw)
       │
       ▼
apply redact(payload)  ← storage boundary
       │
       ▼
Write event to SQLite
       │
       ▼
Export to JSON (already redacted)
```

Raw sensitive data **never** touches disk. The redaction policy is enforced
by `RunRecorder.log_event()` which applies `RedactionPolicy.redact()` before
serializing the payload to JSON for SQLite insertion.

## YAML Dependency

YAML config files require the `pyyaml` package:

```bash
pip install pyyaml
```

JSON config files work with no additional dependencies. If you attempt to load
a `.yaml` file without PyYAML installed, a clear error message is shown.
