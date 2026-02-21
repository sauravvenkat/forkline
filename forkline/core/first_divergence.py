"""First-divergence diffing engine for Forkline.

Compares two recorded runs step-by-step and returns the FIRST point of
divergence with deterministic classification, explanation, and structured diffs.

Algorithm:
1. Fast-path lockstep comparison until mismatch.
2. On mismatch, attempt resync within a sliding window using soft signatures
   (step name + canonicalized input hash).
3. If resync succeeds, classify as missing_steps or extra_steps.
4. If resync fails, classify by what differs: op > input > error > output.
5. Return structured result with context, diffs, and deterministic explanation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .canon import canon, sha256_hex
from .json_diff import json_diff
from .types import Run, Step


class DivergenceType:
    """Classification of the first point of divergence between two runs."""

    EXACT_MATCH = "exact_match"
    INPUT_DIVERGENCE = "input_divergence"
    OUTPUT_DIVERGENCE = "output_divergence"
    OP_DIVERGENCE = "op_divergence"
    MISSING_STEPS = "missing_steps"
    EXTRA_STEPS = "extra_steps"
    ERROR_DIVERGENCE = "error_divergence"


@dataclass(frozen=True)
class StepSummary:
    """Compact summary of a step for inclusion in diff results."""

    idx: int
    name: str
    input_hash: str
    output_hash: str
    event_count: int
    has_error: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idx": self.idx,
            "name": self.name,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "event_count": self.event_count,
            "has_error": self.has_error,
        }


@dataclass(frozen=True)
class FirstDivergenceResult:
    """Result of first-divergence comparison between two runs."""

    status: str
    idx_a: Optional[int]
    idx_b: Optional[int]
    explanation: str
    old_step: Optional[StepSummary]
    new_step: Optional[StepSummary]
    input_diff: Optional[List[Dict[str, Any]]]
    output_diff: Optional[List[Dict[str, Any]]]
    last_equal_idx: int
    context_a: List[StepSummary]
    context_b: List[StepSummary]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "idx_a": self.idx_a,
            "idx_b": self.idx_b,
            "explanation": self.explanation,
            "last_equal_idx": self.last_equal_idx,
            "old_step": self.old_step.to_dict() if self.old_step else None,
            "new_step": self.new_step.to_dict() if self.new_step else None,
            "input_diff": self.input_diff,
            "output_diff": self.output_diff,
            "context_a": [s.to_dict() for s in self.context_a],
            "context_b": [s.to_dict() for s in self.context_b],
        }


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


def _step_events_by_type(step: Step, event_type: str) -> List[Dict[str, Any]]:
    return [e.payload for e in step.events if e.type == event_type]


def _step_input_hash(step: Step) -> str:
    return sha256_hex(canon(_step_events_by_type(step, "input")))


def _step_output_hash(step: Step) -> str:
    return sha256_hex(canon(_step_events_by_type(step, "output")))


def _step_has_error(step: Step) -> bool:
    return any(e.type == "error" for e in step.events)


def _step_signature(step: Step) -> Tuple[str, str]:
    """Soft signature for resync: (name, input_hash)."""
    return (step.name, _step_input_hash(step))


def _make_summary(step: Step) -> StepSummary:
    return StepSummary(
        idx=step.idx,
        name=step.name,
        input_hash=_step_input_hash(step),
        output_hash=_step_output_hash(step),
        event_count=len(step.events),
        has_error=_step_has_error(step),
    )


def _get_context(steps: List[Step], center: int, size: int = 2) -> List[StepSummary]:
    start = max(0, center - size)
    end = min(len(steps), center + size + 1)
    return [_make_summary(steps[i]) for i in range(start, end)]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify_step_divergence(step_a: Step, step_b: Step) -> str:
    if step_a.name != step_b.name:
        return DivergenceType.OP_DIVERGENCE

    if _step_input_hash(step_a) != _step_input_hash(step_b):
        return DivergenceType.INPUT_DIVERGENCE

    has_err_a = _step_has_error(step_a)
    has_err_b = _step_has_error(step_b)
    if has_err_a != has_err_b:
        return DivergenceType.ERROR_DIVERGENCE
    if has_err_a and has_err_b:
        errors_a = _step_events_by_type(step_a, "error")
        errors_b = _step_events_by_type(step_b, "error")
        if canon(errors_a) != canon(errors_b):
            return DivergenceType.ERROR_DIVERGENCE

    if _step_output_hash(step_a) != _step_output_hash(step_b):
        return DivergenceType.OUTPUT_DIVERGENCE

    # Fallback: compare all events (catches tool_call, artifact_ref, etc.)
    all_a = [(e.type, e.payload) for e in step_a.events]
    all_b = [(e.type, e.payload) for e in step_b.events]
    if canon(all_a) != canon(all_b):
        return DivergenceType.OUTPUT_DIVERGENCE

    return DivergenceType.EXACT_MATCH


# ---------------------------------------------------------------------------
# Resync
# ---------------------------------------------------------------------------


def _try_resync(
    steps_a: List[Step],
    steps_b: List[Step],
    start: int,
    window: int,
) -> Optional[Tuple[int, int]]:
    """Find earliest matching signature pair within the resync window.

    Iterates by increasing combined distance from *start* so that the
    closest resync point is found first. Ties broken by smaller offset_a.
    """
    for total_dist in range(1, 2 * window + 1):
        for offset_a in range(min(total_dist + 1, window)):
            offset_b = total_dist - offset_a
            if offset_b < 0 or offset_b >= window:
                continue
            ia = start + offset_a
            ib = start + offset_b
            if ia >= len(steps_a) or ib >= len(steps_b):
                continue
            if _step_signature(steps_a[ia]) == _step_signature(steps_b[ib]):
                return (ia, ib)
    return None


# ---------------------------------------------------------------------------
# Explanation
# ---------------------------------------------------------------------------


def _make_explanation(
    dtype: str,
    step_a: Optional[Step],
    step_b: Optional[Step],
    idx_a: Optional[int],
    idx_b: Optional[int],
    gap_a: int = 0,
    gap_b: int = 0,
) -> str:
    if dtype == DivergenceType.EXACT_MATCH:
        return "Runs are identical"

    if dtype == DivergenceType.OP_DIVERGENCE:
        name_a = step_a.name if step_a else "?"
        name_b = step_b.name if step_b else "?"
        return f"Step {idx_a}: operation mismatch ('{name_a}' vs '{name_b}')"

    if dtype == DivergenceType.INPUT_DIVERGENCE:
        name = step_a.name if step_a else "?"
        return f"Step {idx_a} '{name}': input differs"

    if dtype == DivergenceType.OUTPUT_DIVERGENCE:
        name = step_a.name if step_a else "?"
        return f"Step {idx_a} '{name}': output differs (same input)"

    if dtype == DivergenceType.ERROR_DIVERGENCE:
        name = step_a.name if step_a else "?"
        return f"Step {idx_a} '{name}': error state differs"

    if dtype == DivergenceType.MISSING_STEPS:
        if gap_a > 1:
            end_idx = idx_a + gap_a - 1
            return f"Steps {idx_a}..{end_idx} from run_a missing in run_b"
        return f"Step {idx_a} from run_a missing in run_b"

    if dtype == DivergenceType.EXTRA_STEPS:
        if gap_b > 1:
            end_idx = idx_b + gap_b - 1
            return f"Steps {idx_b}..{end_idx} in run_b not present in run_a"
        return f"Step {idx_b} in run_b not present in run_a"

    return f"Unknown divergence at indices ({idx_a}, {idx_b})"


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------


def _compute_diffs(
    step_a: Optional[Step],
    step_b: Optional[Step],
    dtype: str,
    show: str = "both",
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]]:
    input_diff: Optional[List[Dict[str, Any]]] = None
    output_diff: Optional[List[Dict[str, Any]]] = None

    if step_a is None or step_b is None:
        return None, None

    if dtype == DivergenceType.INPUT_DIVERGENCE and show in ("input", "both"):
        inputs_a = _step_events_by_type(step_a, "input")
        inputs_b = _step_events_by_type(step_b, "input")
        input_diff = json_diff(inputs_a, inputs_b)

    if dtype == DivergenceType.OUTPUT_DIVERGENCE and show in ("output", "both"):
        outputs_a = _step_events_by_type(step_a, "output")
        outputs_b = _step_events_by_type(step_b, "output")
        output_diff = json_diff(outputs_a, outputs_b)

    return input_diff, output_diff


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


def find_first_divergence(
    run_a: Run,
    run_b: Run,
    *,
    window: int = 10,
    context_size: int = 2,
    show: str = "both",
) -> FirstDivergenceResult:
    """Find the first point of divergence between two runs.

    Args:
        run_a: The baseline run.
        run_b: The comparison run.
        window: Resync window size (default 10).
        context_size: Steps before/after divergence in context (default 2).
        show: Which diffs to include: "input", "output", or "both".

    Returns:
        FirstDivergenceResult with classification, explanation, and diffs.
    """
    steps_a = run_a.steps
    steps_b = run_b.steps
    last_equal = -1

    i = 0
    while i < len(steps_a) and i < len(steps_b):
        dtype = _classify_step_divergence(steps_a[i], steps_b[i])
        if dtype == DivergenceType.EXACT_MATCH:
            last_equal = i
            i += 1
            continue

        # Mismatch — attempt resync within window
        resync = _try_resync(steps_a, steps_b, i, window)
        if resync is not None:
            ia, ib = resync
            gap_a = ia - i
            gap_b = ib - i

            if gap_a > 0 and gap_b == 0:
                return FirstDivergenceResult(
                    status=DivergenceType.MISSING_STEPS,
                    idx_a=i,
                    idx_b=i,
                    explanation=_make_explanation(
                        DivergenceType.MISSING_STEPS,
                        steps_a[i],
                        steps_b[i],
                        i,
                        i,
                        gap_a=gap_a,
                    ),
                    old_step=_make_summary(steps_a[i]),
                    new_step=_make_summary(steps_b[i]),
                    input_diff=None,
                    output_diff=None,
                    last_equal_idx=last_equal,
                    context_a=_get_context(steps_a, i, context_size),
                    context_b=_get_context(steps_b, i, context_size),
                )

            if gap_b > 0 and gap_a == 0:
                return FirstDivergenceResult(
                    status=DivergenceType.EXTRA_STEPS,
                    idx_a=i,
                    idx_b=i,
                    explanation=_make_explanation(
                        DivergenceType.EXTRA_STEPS,
                        steps_a[i],
                        steps_b[i],
                        i,
                        i,
                        gap_b=gap_b,
                    ),
                    old_step=_make_summary(steps_a[i]),
                    new_step=_make_summary(steps_b[i]),
                    input_diff=None,
                    output_diff=None,
                    last_equal_idx=last_equal,
                    context_a=_get_context(steps_a, i, context_size),
                    context_b=_get_context(steps_b, i, context_size),
                )
            # Both gaps > 0: steps were replaced — fall through to classify

        # No resync or replacement — classify at current position
        input_diff, output_diff = _compute_diffs(steps_a[i], steps_b[i], dtype, show)
        return FirstDivergenceResult(
            status=dtype,
            idx_a=i,
            idx_b=i,
            explanation=_make_explanation(dtype, steps_a[i], steps_b[i], i, i),
            old_step=_make_summary(steps_a[i]),
            new_step=_make_summary(steps_b[i]),
            input_diff=input_diff,
            output_diff=output_diff,
            last_equal_idx=last_equal,
            context_a=_get_context(steps_a, i, context_size),
            context_b=_get_context(steps_b, i, context_size),
        )

    # One run is longer than the other
    if len(steps_a) > len(steps_b):
        idx = len(steps_b)
        gap = len(steps_a) - len(steps_b)
        return FirstDivergenceResult(
            status=DivergenceType.MISSING_STEPS,
            idx_a=idx,
            idx_b=None,
            explanation=_make_explanation(
                DivergenceType.MISSING_STEPS,
                steps_a[idx],
                None,
                idx,
                None,
                gap_a=gap,
            ),
            old_step=_make_summary(steps_a[idx]),
            new_step=None,
            input_diff=None,
            output_diff=None,
            last_equal_idx=last_equal,
            context_a=_get_context(steps_a, idx, context_size),
            context_b=(
                _get_context(steps_b, len(steps_b) - 1, context_size) if steps_b else []
            ),
        )

    if len(steps_b) > len(steps_a):
        idx = len(steps_a)
        gap = len(steps_b) - len(steps_a)
        return FirstDivergenceResult(
            status=DivergenceType.EXTRA_STEPS,
            idx_a=None,
            idx_b=idx,
            explanation=_make_explanation(
                DivergenceType.EXTRA_STEPS,
                None,
                steps_b[idx],
                None,
                idx,
                gap_b=gap,
            ),
            old_step=None,
            new_step=_make_summary(steps_b[idx]),
            input_diff=None,
            output_diff=None,
            last_equal_idx=last_equal,
            context_a=(
                _get_context(steps_a, len(steps_a) - 1, context_size) if steps_a else []
            ),
            context_b=_get_context(steps_b, idx, context_size),
        )

    # Runs are identical
    return FirstDivergenceResult(
        status=DivergenceType.EXACT_MATCH,
        idx_a=None,
        idx_b=None,
        explanation=f"Runs are identical ({len(steps_a)} steps compared)",
        old_step=None,
        new_step=None,
        input_diff=None,
        output_diff=None,
        last_equal_idx=last_equal,
        context_a=[],
        context_b=[],
    )
