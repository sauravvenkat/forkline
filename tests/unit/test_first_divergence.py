"""Tests for first-divergence diffing: canonicalization, JSON diff, and engine.

All tests are hermetic — no database, no disk I/O, no network calls.
Run objects are constructed in-memory from frozen dataclasses.
"""

from __future__ import annotations

import json
import unittest

from forkline.core.canon import bytes_preview, canon, sha256_hex
from forkline.core.first_divergence import (
    DivergenceType,
    FirstDivergenceResult,
    StepSummary,
    find_first_divergence,
)
from forkline.core.json_diff import json_diff
from forkline.core.types import Event, Run, Step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evt(step_idx: int, typ: str, payload: dict, run_id: str = "test") -> Event:
    return Event(
        event_id=None,
        run_id=run_id,
        step_idx=step_idx,
        type=typ,
        created_at="2024-01-01T00:00:00Z",
        payload=payload,
    )


def _step(
    idx: int, name: str, events: list[Event] | None = None, run_id: str = "test"
) -> Step:
    return Step(
        step_id=None,
        run_id=run_id,
        idx=idx,
        name=name,
        started_at="2024-01-01T00:00:00Z",
        ended_at="2024-01-01T00:00:01Z",
        events=events or [],
    )


def _step_io(idx: int, name: str, inp: dict, out: dict, run_id: str = "test") -> Step:
    return _step(
        idx,
        name,
        [_evt(idx, "input", inp, run_id), _evt(idx, "output", out, run_id)],
        run_id,
    )


def _run(run_id: str, steps: list[Step]) -> Run:
    return Run(run_id=run_id, created_at="2024-01-01T00:00:00Z", steps=steps)


# ============================================================================
# Canonicalization stability
# ============================================================================


class TestCanonStability(unittest.TestCase):
    def test_dict_key_order_irrelevant(self):
        self.assertEqual(
            canon({"z": 1, "a": 2, "m": 3}), canon({"a": 2, "m": 3, "z": 1})
        )

    def test_nested_dict_stability(self):
        a = {"outer": {"b": 2, "a": 1}, "list": [3, 2, 1]}
        b = {"list": [3, 2, 1], "outer": {"a": 1, "b": 2}}
        self.assertEqual(canon(a), canon(b))

    def test_unicode_normalization(self):
        self.assertEqual(canon("caf\u00e9"), canon("cafe\u0301"))

    def test_newline_normalization(self):
        self.assertEqual(canon("a\r\nb"), canon("a\nb"))
        self.assertEqual(canon("a\rb"), canon("a\nb"))

    def test_float_stability(self):
        a = {"val": 1.0000000000000002}
        self.assertEqual(canon(a), canon(a))

    def test_negative_zero(self):
        self.assertEqual(canon({"v": -0.0}), canon({"v": 0.0}))

    def test_bytes_passthrough(self):
        data = b"\x00\x01\x02\x03"
        self.assertEqual(canon(data), data)

    def test_sha256_deterministic(self):
        h1 = sha256_hex(b"hello world")
        h2 = sha256_hex(b"hello world")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_bytes_preview_format(self):
        preview = bytes_preview(b"hello world")
        self.assertTrue(preview.startswith("sha256:"))

    def test_repeated_canonicalization_stable(self):
        value = {"key": [1, 2, {"nested": "value"}], "other": True}
        results = [canon(value) for _ in range(100)]
        self.assertEqual(len(set(results)), 1)

    def test_string_in_json_normalized(self):
        self.assertEqual(canon({"text": "caf\u00e9"}), canon({"text": "cafe\u0301"}))

    def test_empty_structures(self):
        self.assertEqual(canon({}), canon({}))
        self.assertEqual(canon([]), canon([]))
        self.assertNotEqual(canon({}), canon([]))

    def test_none_value(self):
        self.assertEqual(canon(None), canon(None))

    def test_bool_vs_int_distinct(self):
        self.assertNotEqual(canon(True), canon(1))


# ============================================================================
# JSON diff patch determinism
# ============================================================================


class TestJsonDiffDeterminism(unittest.TestCase):
    def test_identical_values_no_diff(self):
        self.assertEqual(json_diff({"a": 1}, {"a": 1}), [])

    def test_added_key(self):
        ops = json_diff({"a": 1}, {"a": 1, "b": 2})
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "add")
        self.assertEqual(ops[0]["path"], "$.b")
        self.assertEqual(ops[0]["value"], 2)

    def test_removed_key(self):
        ops = json_diff({"a": 1, "b": 2}, {"a": 1})
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "remove")
        self.assertEqual(ops[0]["path"], "$.b")

    def test_replaced_value(self):
        ops = json_diff({"a": 1}, {"a": 2})
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "replace")
        self.assertEqual(ops[0]["old"], 1)
        self.assertEqual(ops[0]["new"], 2)

    def test_ordering_remove_before_add_before_common(self):
        ops = json_diff({"a": 1, "c": 3}, {"b": 2, "c": 4})
        self.assertEqual(ops[0]["op"], "remove")
        self.assertEqual(ops[0]["path"], "$.a")
        self.assertEqual(ops[1]["op"], "add")
        self.assertEqual(ops[1]["path"], "$.b")
        self.assertEqual(ops[2]["op"], "replace")
        self.assertEqual(ops[2]["path"], "$.c")

    def test_nested_diff(self):
        ops = json_diff({"outer": {"inner": 1}}, {"outer": {"inner": 2}})
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["path"], "$.outer.inner")

    def test_list_same_length(self):
        ops = json_diff([1, 2, 3], [1, 4, 3])
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["path"], "$[1]")

    def test_list_shorter(self):
        ops = json_diff([1, 2, 3], [1, 2])
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "remove")
        self.assertEqual(ops[0]["path"], "$[2]")

    def test_list_longer(self):
        ops = json_diff([1, 2], [1, 2, 3])
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "add")
        self.assertEqual(ops[0]["path"], "$[2]")

    def test_type_change(self):
        ops = json_diff({"a": [1, 2]}, {"a": "string"})
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "replace")

    def test_deterministic_across_runs(self):
        old = {"z": 1, "a": 2, "m": {"x": [1, 2, 3]}}
        new = {"a": 3, "m": {"x": [1, 4, 3], "y": True}, "n": 5}
        results = [json_diff(old, new) for _ in range(100)]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_empty_dicts(self):
        self.assertEqual(json_diff({}, {}), [])

    def test_empty_lists(self):
        self.assertEqual(json_diff([], []), [])

    def test_none_values(self):
        self.assertEqual(json_diff(None, None), [])

    def test_int_float_comparison(self):
        ops = json_diff({"a": 1}, {"a": 1.5})
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "replace")


# ============================================================================
# First-divergence engine
# ============================================================================


class TestFirstDivergenceEngine(unittest.TestCase):
    def test_identical_runs_exact_match(self):
        """a) identical runs => status 'exact_match'"""
        steps = [
            _step_io(0, "init", {"prompt": "hello"}, {"result": "world"}),
            _step_io(1, "process", {"data": [1, 2]}, {"sum": 3}),
            _step_io(2, "finalize", {"ok": True}, {"done": True}),
        ]
        result = find_first_divergence(_run("a", steps), _run("b", steps))

        self.assertEqual(result.status, DivergenceType.EXACT_MATCH)
        self.assertIsNone(result.idx_a)
        self.assertIsNone(result.idx_b)
        self.assertIn("identical", result.explanation)
        self.assertIn("3 steps", result.explanation)

    def test_output_divergence_same_input(self):
        """b) output divergence same input => output_divergence"""
        run_a = _run(
            "a",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "generate", {"prompt": "hi"}, {"text": "hello"}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "generate", {"prompt": "hi"}, {"text": "hey"}),
            ],
        )

        result = find_first_divergence(run_a, run_b)

        self.assertEqual(result.status, DivergenceType.OUTPUT_DIVERGENCE)
        self.assertEqual(result.idx_a, 1)
        self.assertEqual(result.idx_b, 1)
        self.assertIn("output differs", result.explanation)
        self.assertIsNotNone(result.output_diff)
        self.assertEqual(result.last_equal_idx, 0)

    def test_inserted_step_extra_steps(self):
        """c) inserted step in run_b => extra_steps at insertion point"""
        run_a = _run(
            "a",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "step_one", {"a": 1}, {"b": 2}),
                _step_io(2, "step_two", {"c": 3}, {"d": 4}),
                _step_io(3, "finalize", {"ok": True}, {"done": True}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "step_one", {"a": 1}, {"b": 2}),
                _step_io(2, "extra_step", {"extra": True}, {"extra_out": True}),
                _step_io(3, "step_two", {"c": 3}, {"d": 4}),
                _step_io(4, "finalize", {"ok": True}, {"done": True}),
            ],
        )

        result = find_first_divergence(run_a, run_b)

        self.assertEqual(result.status, DivergenceType.EXTRA_STEPS)
        self.assertEqual(result.idx_b, 2)
        self.assertEqual(result.last_equal_idx, 1)

    def test_run_b_shorter_missing_steps(self):
        """d) run_b shorter => missing_steps"""
        run_a = _run(
            "a",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "process", {"a": 1}, {"b": 2}),
                _step_io(2, "finalize", {"ok": True}, {"done": True}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
            ],
        )

        result = find_first_divergence(run_a, run_b)

        self.assertEqual(result.status, DivergenceType.MISSING_STEPS)
        self.assertEqual(result.idx_a, 1)
        self.assertIn("missing", result.explanation)
        self.assertEqual(result.last_equal_idx, 0)

    def test_op_mismatch_no_resync(self):
        """e) op mismatch without resync => op_divergence"""
        run_a = _run(
            "a",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "process_a", {"a": 1}, {"b": 2}),
                _step_io(2, "cleanup_a", {"c": 3}, {"d": 4}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "process_b", {"e": 5}, {"f": 6}),
                _step_io(2, "cleanup_b", {"g": 7}, {"h": 8}),
            ],
        )

        result = find_first_divergence(run_a, run_b)

        self.assertEqual(result.status, DivergenceType.OP_DIVERGENCE)
        self.assertEqual(result.idx_a, 1)
        self.assertEqual(result.idx_b, 1)
        self.assertIn("operation mismatch", result.explanation)
        self.assertIn("process_a", result.explanation)
        self.assertIn("process_b", result.explanation)

    def test_empty_runs_match(self):
        result = find_first_divergence(_run("a", []), _run("b", []))
        self.assertEqual(result.status, DivergenceType.EXACT_MATCH)

    def test_error_divergence(self):
        """One step has an error event, the other doesn't."""
        run_a = _run(
            "a",
            [
                _step(
                    0,
                    "process",
                    [
                        _evt(0, "input", {"x": 1}),
                        _evt(0, "output", {"y": 2}),
                    ],
                ),
            ],
        )
        run_b = _run(
            "b",
            [
                _step(
                    0,
                    "process",
                    [
                        _evt(0, "input", {"x": 1}),
                        _evt(0, "error", {"message": "failed"}),
                    ],
                ),
            ],
        )

        result = find_first_divergence(run_a, run_b)
        self.assertEqual(result.status, DivergenceType.ERROR_DIVERGENCE)

    def test_input_divergence(self):
        """Same step name but different input."""
        run_a = _run(
            "a",
            [
                _step_io(0, "process", {"prompt": "hello"}, {"result": "world"}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "process", {"prompt": "goodbye"}, {"result": "world"}),
            ],
        )

        result = find_first_divergence(run_a, run_b)

        self.assertEqual(result.status, DivergenceType.INPUT_DIVERGENCE)
        self.assertIsNotNone(result.input_diff)

    def test_result_json_serialization(self):
        """DiffResult can be serialized to JSON."""
        run_a = _run("a", [_step_io(0, "init", {"x": 1}, {"y": 2})])
        run_b = _run("b", [_step_io(0, "init", {"x": 1}, {"y": 3})])

        result = find_first_divergence(run_a, run_b)
        d = result.to_dict()

        self.assertIsInstance(d, dict)
        self.assertIn("status", d)
        self.assertIn("explanation", d)
        self.assertIn("context_a", d)
        json_str = json.dumps(d)
        self.assertIsInstance(json_str, str)

    def test_context_window(self):
        """Context includes steps before and after divergence."""
        steps = [_step_io(i, f"step_{i}", {"i": i}, {"o": i}) for i in range(6)]
        run_a = _run("a", steps)
        steps_b = list(steps)
        steps_b[3] = _step_io(3, "step_3", {"i": 3}, {"o": 999})
        run_b = _run("b", steps_b)

        result = find_first_divergence(run_a, run_b, context_size=2)

        self.assertEqual(result.status, DivergenceType.OUTPUT_DIVERGENCE)
        self.assertEqual(result.idx_a, 3)
        self.assertTrue(len(result.context_a) >= 3)

    def test_deterministic_across_invocations(self):
        """Same inputs always produce identical results."""
        run_a = _run(
            "a",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "process", {"data": [1, 2, 3]}, {"sum": 6}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "process", {"data": [1, 2, 3]}, {"sum": 7}),
            ],
        )

        results = [find_first_divergence(run_a, run_b).to_dict() for _ in range(50)]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_run_a_shorter_extra_steps(self):
        """run_a shorter than run_b => extra_steps in run_b."""
        run_a = _run(
            "a",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "extra", {"e": 1}, {"e": 2}),
                _step_io(2, "extra2", {"e": 3}, {"e": 4}),
            ],
        )

        result = find_first_divergence(run_a, run_b)

        self.assertEqual(result.status, DivergenceType.EXTRA_STEPS)
        self.assertEqual(result.idx_b, 1)
        self.assertIn("not present", result.explanation)

    def test_deleted_step_missing_steps_via_resync(self):
        """Step removed from run_b detected via resync as missing_steps."""
        run_a = _run(
            "a",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "middle", {"m": 1}, {"m": 2}),
                _step_io(2, "end", {"z": 9}, {"z": 10}),
            ],
        )
        run_b = _run(
            "b",
            [
                _step_io(0, "init", {"x": 1}, {"y": 2}),
                _step_io(1, "end", {"z": 9}, {"z": 10}),
            ],
        )

        result = find_first_divergence(run_a, run_b)

        self.assertEqual(result.status, DivergenceType.MISSING_STEPS)
        self.assertEqual(result.idx_a, 1)
        self.assertEqual(result.last_equal_idx, 0)

    def test_step_summary_fields(self):
        """StepSummary.to_dict() contains all expected fields."""
        step = _step_io(5, "my_step", {"k": "v"}, {"out": 42})
        result = find_first_divergence(
            _run("a", [step]),
            _run("b", [_step_io(5, "my_step", {"k": "v"}, {"out": 99})]),
        )
        self.assertIsNotNone(result.old_step)
        d = result.old_step.to_dict()
        for field in (
            "idx",
            "name",
            "input_hash",
            "output_hash",
            "event_count",
            "has_error",
        ):
            self.assertIn(field, d)

    def test_show_input_only(self):
        """show='input' omits output diff even on output divergence."""
        run_a = _run("a", [_step_io(0, "s", {"i": 1}, {"o": 1})])
        run_b = _run("b", [_step_io(0, "s", {"i": 1}, {"o": 2})])

        result = find_first_divergence(run_a, run_b, show="input")
        self.assertIsNone(result.output_diff)

    def test_show_output_only(self):
        """show='output' omits input diff even on input divergence."""
        run_a = _run("a", [_step_io(0, "s", {"i": 1}, {"o": 1})])
        run_b = _run("b", [_step_io(0, "s", {"i": 2}, {"o": 1})])

        result = find_first_divergence(run_a, run_b, show="output")
        self.assertIsNone(result.input_diff)


if __name__ == "__main__":
    unittest.main()
