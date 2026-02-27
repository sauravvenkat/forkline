# Forkline CI Integration

Deterministic, offline, build-failing diffs for CI/CD pipelines.

Forkline CI lets you gate merges on **behavioral identity**: if an agent's output changes, your build fails. No flaky tests, no network dependencies, no non-determinism.

## Quick Start

```bash
# 1. Record a baseline (local dev)
forkline ci record --entrypoint examples/my_flow.py --out tests/testdata/my_flow.run.json

# 2. Commit the artifact to version control
git add tests/testdata/my_flow.run.json

# 3. In CI, check that behavior hasn't changed
forkline ci check --entrypoint examples/my_flow.py --expected tests/testdata/my_flow.run.json
# Exit code 0 = no diff, 1 = behavior changed
```

## Commands

### `forkline ci record`

Record a baseline artifact to a JSON file.

```bash
forkline ci record --entrypoint <script> --out <path> [--offline]
```

- Runs the script under Forkline tracing
- Produces a **normalized** artifact (timestamps stripped, metadata cleaned)
- Suitable for committing to version control or storing in `testdata/`

### `forkline ci replay`

Validate a recorded artifact offline.

```bash
forkline ci replay --artifact <path> [--strict]
```

- Loads and validates the artifact schema
- With `--strict`: verifies all events have complete payloads
- Runs with zero network access

### `forkline ci diff`

Compare two artifact files.

```bash
forkline ci diff --expected <path> --actual <path> [--format json|text] [--fail-on any|first-divergence|semantic]
```

- Normalizes both artifacts before comparison (timestamps, metadata)
- Returns exit code 1 on any behavioral diff
- JSON mode produces machine-readable output with:
  - First divergent event index
  - Event type
  - Payload diff summary
  - Suggested fix (re-record baseline)

### `forkline ci check`

All-in-one: record actual, diff against expected.

```bash
forkline ci check --entrypoint <script> --expected <path> [--offline] [--format json|text]
```

- Internally records into a temp directory
- Diffs against the expected artifact
- Default: `--offline` is **on** (blocks all network access)

### `forkline ci normalize`

Normalize an artifact for stable diffs.

```bash
forkline ci normalize <artifact> [--out <path>]
```

- Strips timestamps, platform metadata
- Sorts events by event_id
- Overwrites in place unless `--out` is specified

## Exit Codes

Forkline CI uses a strict exit code contract. These values are stable across releases.

| Code | Meaning | CI Action |
|------|---------|-----------|
| `0` | Success, no diff / replay ok | Pass |
| `1` | Diff detected (policy violation) | **Fail build** |
| `2` | Usage/config error (bad args, missing file) | Fix config |
| `3` | Replay failed (runtime exception) | Fix script |
| `4` | Offline violation (network attempted) | Fix test isolation |
| `5` | Artifact/schema error (cannot parse) | Re-record or fix schema |
| `6` | Internal error (unexpected bug) | Report issue |

## Offline Mode

Forkline CI enforces a **hard no-network guarantee** when `--offline` is set (or `FORKLINE_OFFLINE=1`).

When active:
- `socket.connect()`, `socket.create_connection()`, and `socket.getaddrinfo()` are monkeypatched
- Any network attempt raises `ForklineOfflineError` immediately (no hang, no timeout)
- The error message is deterministic and includes the operation that was attempted

```bash
# Via flag
forkline ci check --entrypoint flow.py --expected baseline.json --offline

# Via environment variable
FORKLINE_OFFLINE=1 forkline ci check --entrypoint flow.py --expected baseline.json
```

## Artifact Normalization

CI artifacts are normalized to remove unstable fields:

**Normalized by default:**
- All timestamp fields (`ts`, `started_at`, `ended_at`, `created_at`) → `2000-01-01T00:00:00+00:00`
- Platform metadata (`python_version`, `platform`, `cwd`) → removed
- Events sorted by `event_id`

**Preserved:**
- Event types and payloads (the behavioral data)
- Schema version
- Entrypoint path

This means artifacts recorded on different machines, at different times, produce **identical diffs** when behavior is the same.

## Recommended Repo Layout

```
my-repo/
├── examples/
│   └── my_flow.py              # Your agentic workflow
├── tests/
│   ├── testdata/
│   │   └── my_flow.run.json    # Committed baseline artifact
│   └── test_flows.py           # Python tests using assert_no_diff
├── .github/
│   └── workflows/
│       └── ci.yml              # GitHub Actions workflow
└── pyproject.toml
```

## Re-recording Baselines

When you intentionally change behavior:

```bash
# Re-record the baseline
forkline ci record --entrypoint examples/my_flow.py --out tests/testdata/my_flow.run.json

# Verify it replays cleanly
forkline ci replay --artifact tests/testdata/my_flow.run.json

# Commit the updated baseline
git add tests/testdata/my_flow.run.json
git commit -m "Update baseline: my_flow now returns improved results"
```

## Python Test Helper

For integration with pytest or unittest:

```python
from forkline.testing import assert_no_diff

def test_my_flow():
    assert_no_diff(
        entrypoint="examples/my_flow.py",
        expected_artifact="tests/testdata/my_flow.run.json",
        offline=True,
    )
```

On diff, raises `ArtifactDiffError` with:
- First divergent event index
- Expected vs actual payloads
- Suggested re-record command

## GitHub Actions Example

```yaml
name: Forkline CI

on:
  pull_request:
    branches: [main]

jobs:
  behavioral-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -e .

      - name: Validate baselines
        run: |
          forkline ci replay --artifact tests/testdata/my_flow.run.json --strict

      - name: Check for behavioral diffs
        run: |
          forkline ci check \
            --entrypoint examples/my_flow.py \
            --expected tests/testdata/my_flow.run.json \
            --offline \
            --format text

      # Or run multiple checks
      - name: Check all flows
        run: |
          for baseline in tests/testdata/*.run.json; do
            script="examples/$(basename "$baseline" .run.json).py"
            echo "Checking $script against $baseline"
            forkline ci check --entrypoint "$script" --expected "$baseline" --offline
          done
```

## Programmatic Usage

```python
from forkline.ci.commands import ci_record, ci_diff, ci_check
from forkline.ci.exitcodes import EXIT_SUCCESS, EXIT_DIFF_DETECTED

# Record
code = ci_record("my_flow.py", "baseline.run.json", offline=True)
assert code == EXIT_SUCCESS

# Diff
code = ci_diff("baseline.run.json", "actual.run.json", output_format="json")
if code == EXIT_DIFF_DETECTED:
    print("Behavior changed!")

# Check (record + diff in one call)
code = ci_check("my_flow.py", "baseline.run.json", offline=True)
assert code == EXIT_SUCCESS
```

## Design Principles

1. **Determinism over convenience** — same input always produces same output
2. **Strict by default** — CI should fail on any behavioral change unless explicitly accepted
3. **Offline first** — no network dependencies in CI
4. **Fast** — normalization and diffing are pure compute, no I/O beyond reading artifacts
5. **Zero dependencies** — uses only Python stdlib
6. **Meaningful exit codes** — automation-friendly, no ambiguity
