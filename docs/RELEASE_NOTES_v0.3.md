# Forkline v0.3 Release Notes — First-Divergence Diffing

**Release:** v0.3.0  
**Date:** 2026-02-21  
**Milestone:** v0.3 — First-Divergence Diffing (Roadmap item 3 of 5)

---

## Summary

This release delivers **first-divergence diffing**: given two recorded runs, Forkline now compares them step-by-step and returns the **first point of divergence** with deterministic classification, structured JSON diff patches, and rule-based explanations.

This is the core feature that turns Forkline from a recording/replay tool into a **forensic debugging tool** — answering not just *that* two runs differ, but *where*, *how*, and *what changed*.

---

## What's New

### 1. Deterministic Canonicalization (`forkline/core/canon.py`)

A canonicalization layer that produces **stable, deterministic byte representations** of any value before hashing or diffing.

**Functions:**
- `canon(value, profile="strict") -> bytes` — Canonicalize any value to bytes
- `sha256_hex(data: bytes) -> str` — SHA-256 hex digest
- `bytes_preview(data: bytes) -> str` — Human-readable `sha256:<hash>:<hex_prefix>` format

**Canonicalization guarantees:**
- **Dict key order is irrelevant.** Keys are sorted lexicographically before serialization.
- **Unicode is NFC-normalized.** `"café"` (precomposed) and `"café"` (decomposed e + combining accent) produce identical output.
- **Newlines are normalized to LF.** `\r\n` and `\r` are collapsed to `\n`.
- **Floats use 17-significant-digit precision.** `-0.0` collapses to `0.0`. `NaN` and `Inf` are serialized as stable strings.
- **Booleans and integers are distinct.** `True` and `1` produce different canonical bytes.
- **Bytes pass through unchanged.** Binary data is not re-encoded; hashing uses SHA-256 with a hex prefix preview for display.
- **Compact JSON encoding.** No whitespace separators (`","` and `":"`), `ensure_ascii=False`.

**Zero dependencies.** Uses only `hashlib`, `json`, `math`, `unicodedata` from the standard library.

---

### 2. Deterministic JSON Diff Patches (`forkline/core/json_diff.py`)

A recursive JSON diff algorithm that produces a **stable, ordered list of patch operations** for any two JSON-like values.

**Function:**
- `json_diff(old, new, path="$") -> List[Dict]`

**Patch operation format:**
```json
[
  {"op": "remove", "path": "$.a.b", "old": "<removed_value>"},
  {"op": "add",    "path": "$.x",   "value": "<added_value>"},
  {"op": "replace","path": "$.k",   "old": "<old_value>", "new": "<new_value>"}
]
```

**Ordering guarantees (deterministic across invocations):**
- **Dicts:** removed keys (sorted) → added keys (sorted) → common keys (sorted, recursed).
- **Lists:** compared by index; removes at tail, then adds at tail.
- **Type mismatch:** replace whole node.
- **Numeric compatibility:** `int` vs `float` compared as numeric, not as type mismatch.

**Paths use JSONPath-style notation:** `$.outer.inner`, `$.list[0]`, `$.nested.array[2].field`.

---

### 3. First-Divergence Engine (`forkline/core/first_divergence.py`)

The core diffing algorithm: compare two `Run` objects step-by-step, classify the first mismatch, and return a structured result.

#### Algorithm

1. **Lockstep comparison.** Walk both runs at the same index. At each step, classify by comparing (in priority order): step name → input hash → error state → output hash → all events hash.

2. **Resync window.** On mismatch, search within a configurable window (default W=10) for matching "soft signatures" — `(step_name, input_hash)` tuples. The search iterates by increasing combined distance from the mismatch point, finding the **nearest** resync.

3. **Gap classification.**
   - Resync with `gap_a > 0, gap_b == 0` → `missing_steps` (steps in run_a absent from run_b)
   - Resync with `gap_b > 0, gap_a == 0` → `extra_steps` (steps in run_b not in run_a)
   - Both gaps > 0 → falls through to classify the mismatch at current position
   - No resync → classify by what differs at current position

4. **Length mismatch.** If one run is longer after lockstep exhausts the shorter, classify as `missing_steps` or `extra_steps`.

#### Divergence Types

| Type | Trigger | Explanation Pattern |
|---|---|---|
| `exact_match` | Runs identical | `"Runs are identical (N steps compared)"` |
| `op_divergence` | Step names differ | `"Step 3: operation mismatch ('tool_call' vs 'llm_call')"` |
| `input_divergence` | Same name, different input | `"Step 3 'tool_call': input differs"` |
| `output_divergence` | Same name + input, different output | `"Step 3 'tool_call': output differs (same input)"` |
| `error_divergence` | Error presence or content differs | `"Step 3 'tool_call': error state differs"` |
| `missing_steps` | Steps in run_a not in run_b | `"Step 5 from run_a missing in run_b"` |
| `extra_steps` | Steps in run_b not in run_a | `"Steps 3..4 in run_b not present in run_a"` |

All explanations are **deterministic and rule-based** — no LLM narration, no randomness.

#### Classification Priority

When two steps share a name but differ, classification follows strict priority:

1. **Input divergence** — checked first because differing inputs explain differing outputs
2. **Error divergence** — error presence/absence or content differs
3. **Output divergence** — same input but different output (nondeterminism signal)
4. **All-events fallback** — catches differences in `tool_call`, `artifact_ref`, or other event types

#### Data Models

**`StepSummary`** — Compact step representation included in results:
```python
StepSummary(
    idx=2,
    name="generate_response",
    input_hash="a1b2c3d4...",
    output_hash="e5f6a7b8...",
    event_count=3,
    has_error=False,
)
```

**`FirstDivergenceResult`** — Complete result object:
```python
FirstDivergenceResult(
    status="output_divergence",        # DivergenceType
    idx_a=2,                           # Index in run_a at divergence
    idx_b=2,                           # Index in run_b at divergence
    explanation="Step 2 'generate_response': output differs (same input)",
    old_step=StepSummary(...),         # Step from run_a
    new_step=StepSummary(...),         # Step from run_b
    input_diff=None,                   # JSON patch (when applicable)
    output_diff=[{"op": "replace", "path": "$[0].text", ...}],
    last_equal_idx=1,                  # Last step where both matched
    context_a=[StepSummary(...),...],   # 2 steps before/after in run_a
    context_b=[StepSummary(...),...],   # 2 steps before/after in run_b
)
```

Both models are **frozen dataclasses** with `.to_dict()` for JSON serialization.

#### API

```python
from forkline.core.first_divergence import find_first_divergence, DivergenceType

result = find_first_divergence(
    run_a,
    run_b,
    window=10,          # Resync window size
    context_size=2,     # Steps before/after divergence in context
    show="both",        # "input", "output", or "both"
)

# JSON-serializable output
import json
print(json.dumps(result.to_dict(), indent=2))
```

---

### 4. CLI — `forkline diff` (`forkline/cli.py`)

The first CLI subcommand, establishing the `forkline` command-line interface.

**Usage:**
```bash
forkline diff --first <run_a> <run_b> [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--first` | `true` | Show first divergence only |
| `--window N` | `10` | Resync window size |
| `--format json\|text` | `text` | Output format |
| `--show input\|output\|both` | `both` | Which diffs to include |
| `--canon strict` | `strict` | Canonicalization profile |
| `--db PATH` | `forkline.db` | SQLite database path |

**Exit codes:**
- `0` — Runs are identical (`exact_match`)
- `1` — Divergence detected (any other status)

This makes `forkline diff` directly usable in CI pipelines and shell scripts.

**Text output sample:**
```
First divergence: output_divergence
  Step 2 'generate_response': output differs (same input)

  Run A step 2 'generate_response':
    input_hash:  a1b2c3d4e5f6a7b8...
    output_hash: 1234567890abcdef...
    events: 3
    has_error: False

  Run B step 2 'generate_response':
    input_hash:  a1b2c3d4e5f6a7b8...
    output_hash: fedcba0987654321...
    events: 3
    has_error: False

  Output diff:
    replace $[0].text: "Expected response" -> "Different response"

  Last equal: step 1
  Context A: [step 0 'init', step 1 'prepare', step 2 'generate_response']
  Context B: [step 0 'init', step 1 'prepare', step 2 'generate_response']
```

**Entry point:** Registered as `forkline = "forkline.cli:main"` in `pyproject.toml` (`[project.scripts]`).

---

### 5. Module Exports

New public symbols exported from `forkline` and `forkline.core`:

| Symbol | Module | Description |
|---|---|---|
| `find_first_divergence` | `forkline.core.first_divergence` | Main engine function |
| `FirstDivergenceResult` | `forkline.core.first_divergence` | Result dataclass |
| `StepSummary` | `forkline.core.first_divergence` | Compact step summary |
| `DivergenceType` | `forkline.core.first_divergence` | Type classification constants |
| `canon` | `forkline.core.canon` | Value → canonical bytes |
| `sha256_hex` | `forkline.core.canon` | Bytes → SHA-256 hex |
| `bytes_preview` | `forkline.core.canon` | Bytes → human-readable hash preview |
| `json_diff` | `forkline.core.json_diff` | Deterministic JSON diff patches |

---

## Tests

**45 new tests** across 3 test classes in `tests/unit/test_first_divergence.py`. All hermetic — no database, no disk I/O, no network.

### TestCanonStability (14 tests)

| Test | Validates |
|---|---|
| `test_dict_key_order_irrelevant` | `{"z":1,"a":2}` == `{"a":2,"z":1}` |
| `test_nested_dict_stability` | Deep nesting with mixed key order |
| `test_unicode_normalization` | NFC: `\u00e9` == `e\u0301` |
| `test_newline_normalization` | `\r\n` and `\r` → `\n` |
| `test_float_stability` | Same float always produces same bytes |
| `test_negative_zero` | `-0.0` == `0.0` |
| `test_bytes_passthrough` | Raw bytes returned unchanged |
| `test_sha256_deterministic` | Same input → same hash, always 64 hex chars |
| `test_bytes_preview_format` | `sha256:` prefix format |
| `test_repeated_canonicalization_stable` | 100 runs → 1 unique result |
| `test_string_in_json_normalized` | Unicode normalization inside JSON values |
| `test_empty_structures` | `{}` == `{}`, `[]` == `[]`, `{}` != `[]` |
| `test_none_value` | `None` == `None` |
| `test_bool_vs_int_distinct` | `True` != `1` |

### TestJsonDiffDeterminism (15 tests)

| Test | Validates |
|---|---|
| `test_identical_values_no_diff` | No ops for equal dicts |
| `test_added_key` | `add` op with correct path and value |
| `test_removed_key` | `remove` op with correct path |
| `test_replaced_value` | `replace` op with old and new |
| `test_ordering_remove_before_add_before_common` | Stable operation ordering |
| `test_nested_diff` | Deep path: `$.outer.inner` |
| `test_list_same_length` | Index-based comparison: `$[1]` |
| `test_list_shorter` | `remove` at tail index |
| `test_list_longer` | `add` at tail index |
| `test_type_change` | `replace` on type mismatch |
| `test_deterministic_across_runs` | 100 runs → identical patches |
| `test_empty_dicts` / `test_empty_lists` | No ops for empty structures |
| `test_none_values` | No ops for `None == None` |
| `test_int_float_comparison` | Numeric cross-type comparison |

### TestFirstDivergenceEngine (16 tests)

| Test | Validates |
|---|---|
| `test_identical_runs_exact_match` | (a) `exact_match`, explanation includes step count |
| `test_output_divergence_same_input` | (b) `output_divergence`, output diff populated, `last_equal_idx` correct |
| `test_inserted_step_extra_steps` | (c) `extra_steps` at insertion point via resync |
| `test_run_b_shorter_missing_steps` | (d) `missing_steps` when run_b is truncated |
| `test_op_mismatch_no_resync` | (e) `op_divergence` with both step names in explanation |
| `test_empty_runs_match` | Edge case: two empty runs → `exact_match` |
| `test_error_divergence` | Error event in one run, output event in the other |
| `test_input_divergence` | Same step name, different input payload |
| `test_result_json_serialization` | `.to_dict()` round-trips through `json.dumps` |
| `test_context_window` | Context includes surrounding steps |
| `test_deterministic_across_invocations` | 50 runs → identical `.to_dict()` output |
| `test_run_a_shorter_extra_steps` | `extra_steps` when run_b is longer |
| `test_deleted_step_missing_steps_via_resync` | Deleted step detected via resync as `missing_steps` |
| `test_step_summary_fields` | All 6 fields present in `StepSummary.to_dict()` |
| `test_show_input_only` | `show="input"` suppresses output diff |
| `test_show_output_only` | `show="output"` suppresses input diff |

**Total test count after this release:** 187 (142 existing + 45 new). All passing.

---

## Files Changed

### New Files (5)

| File | Lines | Purpose |
|---|---|---|
| `forkline/core/canon.py` | 96 | Canonicalization + SHA-256 |
| `forkline/core/json_diff.py` | 64 | Deterministic JSON diff patches |
| `forkline/core/first_divergence.py` | 449 | Engine, data models, resync, classification |
| `forkline/cli.py` | 195 | CLI entry point (`forkline diff`) |
| `tests/unit/test_first_divergence.py` | 532 | 45 hermetic tests |

### Modified Files (4)

| File | Change |
|---|---|
| `forkline/core/__init__.py` | Added exports for canon, json_diff, first_divergence |
| `forkline/__init__.py` | Added top-level exports |
| `pyproject.toml` | Added `[project.scripts]` entry point; listed subpackages |
| `README.md` | Added "First-Divergence Diffing" section |

---

## Design Decisions

### Adaptation to Existing Data Model

The implementation adapts to Forkline's existing `Step` model (which uses `idx`, `name`, and `events: List[Event]`) rather than the spec's assumed `seq`, `op`, `kind`, `input`, `output`, `error` fields:

| Spec Field | Actual Mapping |
|---|---|
| `seq` | `Step.idx` |
| `op` | `Step.name` |
| `kind` | Not present; not needed for diffing |
| `input` | Events where `event.type == "input"` (payloads aggregated) |
| `output` | Events where `event.type == "output"` (payloads aggregated) |
| `error` | Events where `event.type == "error"` |

The soft signature for resync uses `(name, input_hash)` instead of `(op, kind, input_hash)`.

### No New Dependencies

Everything uses the Python standard library: `hashlib`, `json`, `math`, `unicodedata`, `argparse`, `dataclasses`. This preserves the project's zero-dependency constraint.

### Fallback Event Comparison

After checking input, error, and output events individually, the engine performs a **full event comparison** as a catch-all. This ensures differences in non-standard event types (`tool_call`, `artifact_ref`, etc.) are detected and reported as `output_divergence`.

### CLI Exit Codes for CI

`forkline diff` exits `0` on `exact_match` and `1` on any divergence, making it directly usable in CI pipelines: `forkline diff --first baseline current --format json || exit 1`.

---

## Invariants Preserved

- **Deterministic.** Same inputs always produce identical output. Verified by repeated-invocation tests (50-100 iterations).
- **No async.** All computation is synchronous.
- **No network calls.** The engine operates entirely on in-memory `Run` objects.
- **No mutation.** All data models are frozen dataclasses. Input runs are never modified.
- **Canonicalization before comparison.** All hashing and diffing operates on canonicalized representations.

---

## What's Next

Per the [roadmap](ROADMAP.md), remaining v0 milestones:

- **v0.4 — Minimal CLI (in progress):** `forkline diff --first` is done. `forkline run` and `forkline replay` subcommands remain.
- **v0.5 — CI-friendly mode:** Deterministic execution for tests, fail CI on unexpected diffs.
