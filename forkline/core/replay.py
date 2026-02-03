"""
Deterministic replay engine for Forkline.

This module implements offline, local-first, deterministic debugging of recorded
agentic workflow runs. It compares runs step-by-step and halts at the first
point of divergence.

Core Invariants:
- Replay is deterministic: no live calls, no fresh randomness, no implicit clocks
- Artifacts are the source of truth
- First divergence wins: stop at first observable difference
- Replay is read-only: never mutate stored artifacts

Design Philosophy:
- Explicit, boring, inspectable code
- Clear data models
- Deterministic comparison functions
- Fail loudly on ambiguity
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Iterator,
    List,
    Optional,
    Tuple,
)

from ..storage.store import SQLiteStore
from .types import Event, Run, Step

# =============================================================================
# Replay Mode Context (Determinism Guardrails)
# =============================================================================

# Thread-safe context variable for replay mode state
# This works correctly in async code and nested function calls
_replay_mode_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "replay_mode_active", default=False
)
_replay_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "replay_run_id", default=None
)


def is_replay_mode_active() -> bool:
    """
    Check if replay mode is currently active.

    Returns True if code is executing within a replay context,
    meaning live tool/LLM calls should be forbidden.

    This is thread-safe and works in nested function calls.
    """
    return _replay_mode_active.get()


def get_replay_run_id() -> Optional[str]:
    """
    Get the run ID being replayed, if in replay mode.

    Returns None if not in replay mode.
    """
    if is_replay_mode_active():
        return _replay_run_id.get()
    return None


def assert_not_in_replay_mode(operation: str = "live call") -> None:
    """
    Assert that we are NOT in replay mode.

    Call this at the start of any live tool/LLM executor to ensure
    determinism during replay. If replay mode is active, raises
    DeterminismViolationError.

    Args:
        operation: Description of the operation being attempted
                  (e.g., "LLM call", "tool execution", "network request")

    Raises:
        DeterminismViolationError: If called while replay mode is active

    Example:
        def call_llm(prompt: str) -> str:
            assert_not_in_replay_mode("LLM call")
            # ... actual LLM call ...
    """
    if is_replay_mode_active():
        run_id = get_replay_run_id() or "unknown"
        raise DeterminismViolationError(
            f"Live {operation} attempted during replay of run '{run_id}'. "
            f"Replay mode forbids live calls to ensure determinism. "
            f"Use recorded artifacts instead.",
            step_idx=-1,  # Unknown step context
            expected="<recorded artifact>",
            actual=f"<live {operation}>",
            violation_type="live_call_during_replay",
        )


def guard_live_call(operation: str = "live call") -> None:
    """
    Alias for assert_not_in_replay_mode.

    Use this in tool/LLM adapters to guard against live calls during replay.
    """
    assert_not_in_replay_mode(operation)


@contextmanager
def replay_mode(run_id: Optional[str] = None) -> Generator[None, None, None]:
    """
    Context manager that activates replay mode.

    While inside this context, any call to assert_not_in_replay_mode()
    will raise DeterminismViolationError.

    This is thread-safe and supports nesting (though nesting is not
    recommended - the innermost context's run_id takes precedence).

    Args:
        run_id: Optional run ID being replayed (for error messages)

    Example:
        with replay_mode("run-123"):
            # Inside here, live calls will raise DeterminismViolationError
            replayed_step = execute_step_with_injection(step, ctx)

    Example with guard:
        def my_tool_executor(args):
            guard_live_call("tool execution")  # Raises if in replay mode
            return call_external_api(args)

        with replay_mode():
            my_tool_executor({})  # Raises DeterminismViolationError
    """
    # Save current state
    token_active = _replay_mode_active.set(True)
    token_run_id = _replay_run_id.set(run_id)

    try:
        yield
    finally:
        # Restore previous state
        _replay_mode_active.reset(token_active)
        _replay_run_id.reset(token_run_id)


# =============================================================================
# Exceptions
# =============================================================================


class ReplayError(Exception):
    """Base exception for replay-related errors."""

    pass


class MissingArtifactError(ReplayError):
    """
    Raised when a required artifact is missing from the recording.

    This is a hard failure - replay cannot proceed without the artifact.
    The error includes the run_id, step information, and what was missing.
    """

    def __init__(
        self,
        message: str,
        run_id: str,
        step_idx: Optional[int] = None,
        event_idx: Optional[int] = None,
        artifact_type: Optional[str] = None,
    ):
        super().__init__(message)
        self.run_id = run_id
        self.step_idx = step_idx
        self.event_idx = event_idx
        self.artifact_type = artifact_type

    def __str__(self) -> str:
        location = f"run={self.run_id}"
        if self.step_idx is not None:
            location += f", step={self.step_idx}"
        if self.event_idx is not None:
            location += f", event={self.event_idx}"
        if self.artifact_type:
            location += f", type={self.artifact_type}"
        return f"MissingArtifactError({location}): {self.args[0]}"


class DeterminismViolationError(ReplayError):
    """
    Raised when replay detects a violation of determinism invariants.

    This indicates that the replay produced different outputs than expected,
    which should not happen if the system is truly deterministic.

    Common causes:
    - Live network calls during replay (should be mocked)
    - Wall-clock time dependence
    - Random number generation without seeding
    - Shared mutable state
    """

    def __init__(
        self,
        message: str,
        step_idx: int,
        expected: Any,
        actual: Any,
        violation_type: str = "output_mismatch",
    ):
        super().__init__(message)
        self.step_idx = step_idx
        self.expected = expected
        self.actual = actual
        self.violation_type = violation_type

    def __str__(self) -> str:
        return (
            f"DeterminismViolationError at step {self.step_idx} "
            f"[{self.violation_type}]: {self.args[0]}"
        )


# =============================================================================
# Replay Policy
# =============================================================================


class DivergenceReason(Enum):
    """
    Enumerated reasons for divergence.

    Used in Divergence to classify the type of mismatch.
    """

    STEP_NAME_MISMATCH = "step_name_mismatch"
    STEP_COUNT_MISMATCH = "step_count_mismatch"
    EVENT_COUNT_MISMATCH = "event_count_mismatch"
    EVENT_TYPE_MISMATCH = "event_type_mismatch"
    EVENT_PAYLOAD_MISMATCH = "event_payload_mismatch"
    TOOL_OUTPUT_MISMATCH = "tool_output_mismatch"
    LLM_OUTPUT_MISMATCH = "llm_output_mismatch"
    EXTRA_STEPS = "extra_steps"
    MISSING_STEPS = "missing_steps"


@dataclass(frozen=True)
class ReplayPolicy:
    """
    Configuration for replay behavior.

    Controls how the replay engine handles various scenarios.

    Attributes:
        ignore_timestamps: If True, ignore timestamp fields in comparisons.
        strict_event_order: If True, events must match in exact order.
        fail_on_missing_artifact: If True, raise MissingArtifactError.
        compare_tool_outputs: If True, compare tool call outputs.
        compare_llm_outputs: If True, compare LLM call outputs.
    """

    ignore_timestamps: bool = True
    strict_event_order: bool = True
    fail_on_missing_artifact: bool = True
    compare_tool_outputs: bool = True
    compare_llm_outputs: bool = True

    @classmethod
    def default(cls) -> "ReplayPolicy":
        """Create default replay policy."""
        return cls()

    @classmethod
    def strict(cls) -> "ReplayPolicy":
        """Create strict replay policy - all comparisons enabled."""
        return cls(
            ignore_timestamps=False,
            strict_event_order=True,
            fail_on_missing_artifact=True,
            compare_tool_outputs=True,
            compare_llm_outputs=True,
        )

    @classmethod
    def lenient(cls) -> "ReplayPolicy":
        """Create lenient replay policy - skip missing artifacts."""
        return cls(
            ignore_timestamps=True,
            strict_event_order=True,
            fail_on_missing_artifact=False,
            compare_tool_outputs=True,
            compare_llm_outputs=True,
        )


# =============================================================================
# Replay Status and Results
# =============================================================================


class ReplayStatus(Enum):
    """
    Status of a replay comparison.

    MATCH: Runs are identical in all compared aspects
    DIVERGED: First divergence found - replay halted
    INCOMPLETE: Original run has more steps than replay (truncated replay)
    ERROR: Replay failed due to an error (missing artifact, etc.)
    ORIGINAL_NOT_FOUND: Original run does not exist in storage
    REPLAY_NOT_FOUND: Replay run does not exist in storage
    """

    MATCH = "match"
    DIVERGED = "diverged"
    INCOMPLETE = "incomplete"
    ERROR = "error"
    ORIGINAL_NOT_FOUND = "original_not_found"
    REPLAY_NOT_FOUND = "replay_not_found"


@dataclass(frozen=True)
class FieldDiff:
    """
    Represents a difference in a specific field.

    This is the atomic unit of divergence - a single field that differs
    between expected and actual values.
    """

    path: str  # JSON path to the field, e.g., "payload.prompt" or "events[0].type"
    expected: Any
    actual: Any

    def __str__(self) -> str:
        exp = _truncate(self.expected)
        act = _truncate(self.actual)
        return f"{self.path}: expected {exp}, got {act}"


@dataclass(frozen=True)
class Divergence:
    """
    Captures the exact point where two runs diverge.

    This is the answer to: "Where did this agent's behavior change - exactly?"

    Attributes:
        step_index: Index of the divergent step (0-based)
        step_name: Name of the divergent step for human readability
        event_index: Index of the divergent event within the step (None if step-level)
        reason: Enumerated reason for divergence (DivergenceReason)
        expected: The expected (recorded) payload
        actual: The actual (replayed) payload
        diff: Structured diff as list of FieldDiff
        context: Additional context for debugging
    """

    step_index: int
    step_name: str
    reason: DivergenceReason
    expected: Any
    actual: Any
    diff: List[FieldDiff] = field(default_factory=list)
    event_index: Optional[int] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """Human-readable summary of the divergence."""
        location = f"step[{self.step_index}]:{self.step_name}"
        if self.event_index is not None:
            location += f"/event[{self.event_index}]"

        diff_summary = "; ".join(str(d) for d in self.diff[:3])
        if len(self.diff) > 3:
            diff_summary += f" (+{len(self.diff) - 3} more)"

        return f"[{self.reason.value}] at {location}: {diff_summary}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "step_index": self.step_index,
            "step_name": self.step_name,
            "event_index": self.event_index,
            "reason": self.reason.value,
            "expected": self.expected,
            "actual": self.actual,
            "diff": [
                {"path": d.path, "expected": d.expected, "actual": d.actual}
                for d in self.diff
            ],
        }


@dataclass(frozen=True)
class DivergencePoint:
    """
    Captures the exact point where two runs diverge.

    This is the answer to: "Where did this agent's behavior change - exactly?"

    Attributes:
        step_idx: Index of the divergent step (0-based)
        step_name: Name of the divergent step for human readability
        event_idx: Index of the divergent event (None if step-level)
        divergence_type: Category of divergence (step_count, event_count, etc.)
        field_diffs: List of specific field differences
        context: Additional context for debugging

    Note: This is the legacy format. Prefer Divergence for new code.
    """

    step_idx: int
    step_name: str
    event_idx: Optional[int]
    divergence_type: str
    field_diffs: List[FieldDiff] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """Human-readable summary of the divergence."""
        location = f"step[{self.step_idx}]:{self.step_name}"
        if self.event_idx is not None:
            location += f"/event[{self.event_idx}]"

        diff_summary = "; ".join(str(d) for d in self.field_diffs[:3])
        if len(self.field_diffs) > 3:
            diff_summary += f" (+{len(self.field_diffs) - 3} more)"

        return f"[{self.divergence_type}] at {location}: {diff_summary}"

    def to_divergence(self) -> Divergence:
        """Convert to the new Divergence format."""
        # Map string type to enum
        reason_map = {
            "step_name_mismatch": DivergenceReason.STEP_NAME_MISMATCH,
            "step_count_mismatch": DivergenceReason.STEP_COUNT_MISMATCH,
            "event_count_mismatch": DivergenceReason.EVENT_COUNT_MISMATCH,
            "event_payload_mismatch": DivergenceReason.EVENT_PAYLOAD_MISMATCH,
            "extra_steps_in_replay": DivergenceReason.EXTRA_STEPS,
        }
        reason = reason_map.get(
            self.divergence_type, DivergenceReason.EVENT_PAYLOAD_MISMATCH
        )

        # Extract expected/actual from field_diffs if available
        expected = self.field_diffs[0].expected if self.field_diffs else None
        actual = self.field_diffs[0].actual if self.field_diffs else None

        return Divergence(
            step_index=self.step_idx,
            step_name=self.step_name,
            reason=reason,
            expected=expected,
            actual=actual,
            diff=self.field_diffs,
            event_index=self.event_idx,
            context=self.context,
        )


@dataclass(frozen=True)
class ReplayStepResult:
    """
    Result of comparing a single step.

    Used to build up the full replay result and provide step-by-step visibility.
    """

    step_idx: int
    step_name: str
    events_compared: int
    matched: bool
    divergence: Optional[DivergencePoint] = None


@dataclass(frozen=True)
class ReplayResult:
    """
    Complete result of a replay operation.

    This is the structured answer to a replay operation. It contains:
    - Overall status (MATCH, DIVERGED, ERROR, INCOMPLETE)
    - If diverged: exactly where and why
    - Full list of step results for forensic inspection
    - Lineage information for derived runs
    - Error message if status is ERROR
    """

    original_run_id: str
    replay_run_id: str
    status: ReplayStatus
    steps_compared: int
    total_events_compared: int
    divergence: Optional[DivergencePoint] = None
    step_results: List[ReplayStepResult] = field(default_factory=list)
    error_message: Optional[str] = None

    def is_match(self) -> bool:
        """Returns True if runs are identical."""
        return self.status == ReplayStatus.MATCH

    def is_diverged(self) -> bool:
        """Returns True if runs diverged."""
        return self.status == ReplayStatus.DIVERGED

    def is_error(self) -> bool:
        """Returns True if replay encountered an error."""
        return self.status == ReplayStatus.ERROR

    def get_divergence(self) -> Optional[Divergence]:
        """Get divergence in the new Divergence format."""
        if self.divergence is None:
            return None
        return self.divergence.to_divergence()

    def summary(self) -> str:
        """Human-readable summary of the replay result."""
        if self.status == ReplayStatus.MATCH:
            steps = self.steps_compared
            events = self.total_events_compared
            return f"MATCH: {steps} steps, {events} events compared"
        elif self.status == ReplayStatus.DIVERGED:
            return f"DIVERGED: {self.divergence.summary()}"
        elif self.status == ReplayStatus.INCOMPLETE:
            return "INCOMPLETE: replay has fewer steps than original"
        elif self.status == ReplayStatus.ERROR:
            return f"ERROR: {self.error_message or 'unknown error'}"
        else:
            return f"{self.status.value.upper()}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        result = {
            "original_run_id": self.original_run_id,
            "replay_run_id": self.replay_run_id,
            "status": self.status.value,
            "steps_compared": self.steps_compared,
            "total_events_compared": self.total_events_compared,
        }
        if self.divergence:
            result["divergence"] = self.divergence.to_divergence().to_dict()
        if self.error_message:
            result["error_message"] = self.error_message
        return result


# =============================================================================
# Semantic Comparison Utilities
# =============================================================================


def _truncate(value: Any, max_len: int = 50) -> str:
    """Truncate a value for display."""
    s = repr(value)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def deep_compare(
    expected: Any,
    actual: Any,
    path: str = "",
    ignore_fields: Optional[set] = None,
) -> List[FieldDiff]:
    """
    Deep semantic comparison of two values.

    Returns a list of FieldDiff for each difference found.
    Compares recursively through dicts and lists.

    Args:
        expected: The expected (recorded) value
        actual: The actual (replayed) value
        path: Current path in the object graph (for error messages)
        ignore_fields: Set of field names to ignore in comparison

    Returns:
        List of FieldDiff objects, empty if values match
    """
    ignore_fields = ignore_fields or set()
    diffs: List[FieldDiff] = []

    # Type mismatch is an immediate difference
    if type(expected) is not type(actual):
        diffs.append(
            FieldDiff(
                path=path or "(root)",
                expected=f"type:{type(expected).__name__}",
                actual=f"type:{type(actual).__name__}",
            )
        )
        return diffs

    # Dict comparison
    if isinstance(expected, dict):
        all_keys = set(expected.keys()) | set(actual.keys())
        for key in sorted(all_keys):
            if key in ignore_fields:
                continue
            child_path = f"{path}.{key}" if path else key
            if key not in expected:
                diffs.append(
                    FieldDiff(path=child_path, expected="<missing>", actual=actual[key])
                )
            elif key not in actual:
                diffs.append(
                    FieldDiff(
                        path=child_path, expected=expected[key], actual="<missing>"
                    )
                )
            else:
                diffs.extend(
                    deep_compare(expected[key], actual[key], child_path, ignore_fields)
                )
        return diffs

    # List comparison
    if isinstance(expected, list):
        if len(expected) != len(actual):
            diffs.append(
                FieldDiff(
                    path=f"{path}.(length)" if path else "(length)",
                    expected=len(expected),
                    actual=len(actual),
                )
            )
            # Still compare common elements
        for i, (exp_item, act_item) in enumerate(zip(expected, actual)):
            child_path = f"{path}[{i}]"
            diffs.extend(deep_compare(exp_item, act_item, child_path, ignore_fields))
        return diffs

    # Primitive comparison
    if expected != actual:
        diffs.append(
            FieldDiff(
                path=path or "(root)",
                expected=expected,
                actual=actual,
            )
        )

    return diffs


def compare_events(
    expected: Event,
    actual: Event,
    ignore_timestamps: bool = True,
) -> List[FieldDiff]:
    """
    Compare two events semantically.

    By default, ignores timestamp fields since they are metadata, not control flow.

    Args:
        expected: The expected (original) event
        actual: The actual (replayed) event
        ignore_timestamps: If True, ignore created_at and similar timestamp fields

    Returns:
        List of FieldDiff objects, empty if events match
    """
    diffs: List[FieldDiff] = []

    # Compare type
    if expected.type != actual.type:
        diffs.append(FieldDiff(path="type", expected=expected.type, actual=actual.type))

    # Compare payload semantically
    ignore_fields = {"created_at", "ts", "timestamp"} if ignore_timestamps else set()
    payload_diffs = deep_compare(
        expected.payload, actual.payload, "payload", ignore_fields
    )
    diffs.extend(payload_diffs)

    return diffs


def compare_steps(
    expected: Step,
    actual: Step,
    ignore_timestamps: bool = True,
) -> Tuple[bool, Optional[DivergencePoint]]:
    """
    Compare two steps and all their events.

    Returns (matched, divergence_point). If matched is True, divergence_point is None.
    Halts at first divergence per the core invariant.

    Args:
        expected: The expected (original) step
        actual: The actual (replayed) step
        ignore_timestamps: If True, ignore timestamp metadata

    Returns:
        Tuple of (matched: bool, divergence: Optional[DivergencePoint])
    """
    # Compare step name
    if expected.name != actual.name:
        return False, DivergencePoint(
            step_idx=expected.idx,
            step_name=expected.name,
            event_idx=None,
            divergence_type="step_name_mismatch",
            field_diffs=[
                FieldDiff(path="name", expected=expected.name, actual=actual.name)
            ],
        )

    # Compare event count
    if len(expected.events) != len(actual.events):
        return False, DivergencePoint(
            step_idx=expected.idx,
            step_name=expected.name,
            event_idx=None,
            divergence_type="event_count_mismatch",
            field_diffs=[
                FieldDiff(
                    path="events.(length)",
                    expected=len(expected.events),
                    actual=len(actual.events),
                )
            ],
            context={
                "expected_event_types": [e.type for e in expected.events],
                "actual_event_types": [e.type for e in actual.events],
            },
        )

    # Compare events in order
    for event_idx, (exp_event, act_event) in enumerate(
        zip(expected.events, actual.events)
    ):
        event_diffs = compare_events(exp_event, act_event, ignore_timestamps)
        if event_diffs:
            return False, DivergencePoint(
                step_idx=expected.idx,
                step_name=expected.name,
                event_idx=event_idx,
                divergence_type="event_payload_mismatch",
                field_diffs=event_diffs,
                context={
                    "expected_event_type": exp_event.type,
                    "actual_event_type": act_event.type,
                },
            )

    return True, None


# =============================================================================
# Replay Engine
# =============================================================================


class ReplayEngine:
    """
    Deterministic replay engine for Forkline runs.

    This engine:
    1. Loads recorded runs from local storage
    2. Compares runs step-by-step for divergence
    3. Halts at first divergence
    4. Returns structured ReplayResult with exact divergence location

    The engine is read-only: it never mutates stored artifacts.
    All comparisons are deterministic and reproducible.

    Usage:
        engine = ReplayEngine(store)
        result = engine.compare_runs("original-run-id", "replay-run-id")
        if result.status == ReplayStatus.DIVERGED:
            print(result.divergence.summary())
    """

    def __init__(self, store: Optional[SQLiteStore] = None):
        """
        Initialize the replay engine.

        Args:
            store: SQLiteStore instance. If None, uses default store.
        """
        self.store = store or SQLiteStore()

    def load_run(self, run_id: str) -> Optional[Run]:
        """
        Load a run from storage.

        This is a thin wrapper around store.load_run for consistency.

        Args:
            run_id: The run identifier

        Returns:
            Run object if found, None otherwise
        """
        return self.store.load_run(run_id)

    def compare_runs(
        self,
        original_run_id: str,
        replay_run_id: str,
        ignore_timestamps: bool = True,
    ) -> ReplayResult:
        """
        Compare two runs and find the first point of divergence.

        This is the core replay operation. It:
        1. Loads both runs from storage
        2. Compares steps in strict original order
        3. Halts at first divergence (per core invariant)
        4. Returns structured result with exact divergence location

        Args:
            original_run_id: ID of the original (expected) run
            replay_run_id: ID of the replay (actual) run
            ignore_timestamps: If True, ignore timestamp metadata in comparisons

        Returns:
            ReplayResult with status, divergence info, and step-by-step results
        """
        # Load runs
        original_run = self.load_run(original_run_id)
        if original_run is None:
            return ReplayResult(
                original_run_id=original_run_id,
                replay_run_id=replay_run_id,
                status=ReplayStatus.ORIGINAL_NOT_FOUND,
                steps_compared=0,
                total_events_compared=0,
            )

        replay_run = self.load_run(replay_run_id)
        if replay_run is None:
            return ReplayResult(
                original_run_id=original_run_id,
                replay_run_id=replay_run_id,
                status=ReplayStatus.REPLAY_NOT_FOUND,
                steps_compared=0,
                total_events_compared=0,
            )

        return self._compare_loaded_runs(original_run, replay_run, ignore_timestamps)

    def compare_loaded_runs(
        self,
        original: Run,
        replay: Run,
        ignore_timestamps: bool = True,
    ) -> ReplayResult:
        """
        Compare two already-loaded runs.

        Use this when you already have Run objects in memory.

        Args:
            original: The original (expected) run
            replay: The replay (actual) run
            ignore_timestamps: If True, ignore timestamp metadata

        Returns:
            ReplayResult with divergence details
        """
        return self._compare_loaded_runs(original, replay, ignore_timestamps)

    def _compare_loaded_runs(
        self,
        original: Run,
        replay: Run,
        ignore_timestamps: bool,
    ) -> ReplayResult:
        """
        Internal implementation of run comparison.

        Invariant: Halts at first divergence.
        """
        step_results: List[ReplayStepResult] = []
        total_events_compared = 0

        # Check step count
        original_step_count = len(original.steps)
        replay_step_count = len(replay.steps)

        if replay_step_count < original_step_count:
            # Replay has fewer steps - incomplete
            # Still compare what we have
            pass

        if replay_step_count > original_step_count:
            # Replay has more steps - this is a divergence
            return ReplayResult(
                original_run_id=original.run_id,
                replay_run_id=replay.run_id,
                status=ReplayStatus.DIVERGED,
                steps_compared=0,
                total_events_compared=0,
                divergence=DivergencePoint(
                    step_idx=original_step_count,
                    step_name="<beyond original>",
                    event_idx=None,
                    divergence_type="extra_steps_in_replay",
                    field_diffs=[
                        FieldDiff(
                            path="steps.(length)",
                            expected=original_step_count,
                            actual=replay_step_count,
                        )
                    ],
                ),
                step_results=[],
            )

        # Compare steps in order
        for step_idx, (orig_step, replay_step) in enumerate(
            zip(original.steps, replay.steps)
        ):
            matched, divergence = compare_steps(
                orig_step, replay_step, ignore_timestamps
            )
            events_in_step = min(len(orig_step.events), len(replay_step.events))

            step_result = ReplayStepResult(
                step_idx=step_idx,
                step_name=orig_step.name,
                events_compared=events_in_step,
                matched=matched,
                divergence=divergence,
            )
            step_results.append(step_result)

            if matched:
                total_events_compared += len(orig_step.events)
            else:
                # First divergence - halt immediately
                # Count events up to divergence point
                if divergence.event_idx is not None:
                    total_events_compared += divergence.event_idx

                return ReplayResult(
                    original_run_id=original.run_id,
                    replay_run_id=replay.run_id,
                    status=ReplayStatus.DIVERGED,
                    steps_compared=step_idx + 1,
                    total_events_compared=total_events_compared,
                    divergence=divergence,
                    step_results=step_results,
                )

        # All compared steps match
        if replay_step_count < original_step_count:
            return ReplayResult(
                original_run_id=original.run_id,
                replay_run_id=replay.run_id,
                status=ReplayStatus.INCOMPLETE,
                steps_compared=replay_step_count,
                total_events_compared=total_events_compared,
                step_results=step_results,
            )

        return ReplayResult(
            original_run_id=original.run_id,
            replay_run_id=replay.run_id,
            status=ReplayStatus.MATCH,
            steps_compared=len(step_results),
            total_events_compared=total_events_compared,
            step_results=step_results,
        )

    def validate_run(self, run_id: str) -> ReplayResult:
        """
        Validate a run's internal consistency by comparing it to itself.

        This is useful for sanity checking that a run was recorded correctly.
        A valid run should always match itself.

        Args:
            run_id: The run to validate

        Returns:
            ReplayResult (should always be MATCH for a valid run)
        """
        return self.compare_runs(run_id, run_id)

    def replay(
        self,
        run_id: str,
        *,
        policy: Optional[ReplayPolicy] = None,
        executor: Optional[Callable[[Step, "ReplayContext"], Step]] = None,
    ) -> ReplayResult:
        """
        Replay a recorded run with deterministic injection.

        This is the primary replay API. It:
        1. Loads the recorded run from storage
        2. Creates a ReplayContext for artifact injection
        3. Optionally re-executes steps using the provided executor
        4. Compares actual vs expected outputs step-by-step
        5. Halts at first divergence
        6. Returns structured ReplayResult

        If no executor is provided, this validates the run can be loaded
        and returns a self-comparison result (always MATCH for valid runs).

        If an executor is provided, it is called for each step with the
        ReplayContext, and the executor's output is compared against the
        recorded step.

        Args:
            run_id: ID of the run to replay
            policy: Replay policy configuration (default: ReplayPolicy.default())
            executor: Optional function that takes (Step, ReplayContext) and
                     returns the replayed Step. If None, performs self-validation.

        Returns:
            ReplayResult with status, divergence info, and step-by-step results

        Raises:
            MissingArtifactError: If required artifacts are missing and
                                  policy.fail_on_missing_artifact is True
        """
        policy = policy or ReplayPolicy.default()

        # Load the recorded run
        recorded_run = self.load_run(run_id)
        if recorded_run is None:
            raise MissingArtifactError(
                f"Run not found: {run_id}",
                run_id=run_id,
                artifact_type="run",
            )

        # Validate run has steps
        if not recorded_run.steps:
            if policy.fail_on_missing_artifact:
                raise MissingArtifactError(
                    f"Run has no steps: {run_id}",
                    run_id=run_id,
                    artifact_type="steps",
                )
            # Return empty match for lenient policy
            return ReplayResult(
                original_run_id=run_id,
                replay_run_id=run_id,
                status=ReplayStatus.MATCH,
                steps_compared=0,
                total_events_compared=0,
            )

        # Create replay context for artifact injection
        ctx = ReplayContext(recorded_run)

        # If no executor, just validate the run (self-comparison)
        if executor is None:
            return self._validate_recorded_run(recorded_run, policy)

        # Execute replay with the provided executor
        return self._execute_replay(recorded_run, ctx, executor, policy)

    def _validate_recorded_run(
        self,
        run: Run,
        policy: ReplayPolicy,
    ) -> ReplayResult:
        """
        Validate a recorded run's internal consistency.

        Checks that all steps have required artifacts based on policy.
        """
        step_results: List[ReplayStepResult] = []
        total_events = 0

        for step_idx, step in enumerate(run.steps):
            # Validate step has events if required
            if not step.events and policy.fail_on_missing_artifact:
                raise MissingArtifactError(
                    f"Step has no events: {step.name}",
                    run_id=run.run_id,
                    step_idx=step_idx,
                    artifact_type="events",
                )

            # Validate tool/LLM outputs exist if comparison is enabled
            if policy.compare_tool_outputs:
                tool_events = [e for e in step.events if e.type == "tool_call"]
                for event_idx, event in enumerate(tool_events):
                    if (
                        "result" not in event.payload
                        and policy.fail_on_missing_artifact
                    ):
                        name = event.payload.get("name", "unknown")
                        raise MissingArtifactError(
                            f"Tool call missing result: {name}",
                            run_id=run.run_id,
                            step_idx=step_idx,
                            event_idx=event_idx,
                            artifact_type="tool_result",
                        )

            if policy.compare_llm_outputs:
                llm_events = [
                    e for e in step.events if e.type in ("llm_call", "output")
                ]
                for event_idx, event in enumerate(llm_events):
                    # LLM events should have some response content
                    if not event.payload and policy.fail_on_missing_artifact:
                        raise MissingArtifactError(
                            "LLM call missing output",
                            run_id=run.run_id,
                            step_idx=step_idx,
                            event_idx=event_idx,
                            artifact_type="llm_output",
                        )

            step_results.append(
                ReplayStepResult(
                    step_idx=step_idx,
                    step_name=step.name,
                    events_compared=len(step.events),
                    matched=True,
                    divergence=None,
                )
            )
            total_events += len(step.events)

        return ReplayResult(
            original_run_id=run.run_id,
            replay_run_id=run.run_id,
            status=ReplayStatus.MATCH,
            steps_compared=len(step_results),
            total_events_compared=total_events,
            step_results=step_results,
        )

    def _execute_replay(
        self,
        recorded_run: Run,
        ctx: ReplayContext,
        executor: Callable[[Step, "ReplayContext"], Step],
        policy: ReplayPolicy,
    ) -> ReplayResult:
        """
        Execute replay using the provided executor and compare results.

        The executor is called for each step with the ReplayContext,
        and must return a Step with the replayed events.
        """
        step_results: List[ReplayStepResult] = []
        total_events_compared = 0
        replay_run_id = f"replay-{uuid.uuid4().hex[:8]}"

        for step_idx, recorded_step in enumerate(recorded_run.steps):
            try:
                # Execute the step using the provided executor
                # The executor should use ctx to inject recorded artifacts
                replayed_step = executor(recorded_step, ctx)
            except Exception as e:
                # Execution error - return ERROR status
                return ReplayResult(
                    original_run_id=recorded_run.run_id,
                    replay_run_id=replay_run_id,
                    status=ReplayStatus.ERROR,
                    steps_compared=step_idx,
                    total_events_compared=total_events_compared,
                    step_results=step_results,
                    error_message=f"Executor failed at step {step_idx}: {str(e)}",
                )

            # Compare recorded vs replayed step
            matched, divergence = compare_steps(
                recorded_step,
                replayed_step,
                ignore_timestamps=policy.ignore_timestamps,
            )

            events_in_step = min(len(recorded_step.events), len(replayed_step.events))

            step_result = ReplayStepResult(
                step_idx=step_idx,
                step_name=recorded_step.name,
                events_compared=events_in_step,
                matched=matched,
                divergence=divergence,
            )
            step_results.append(step_result)

            if matched:
                total_events_compared += len(recorded_step.events)
            else:
                # First divergence - halt immediately (core invariant)
                if divergence and divergence.event_idx is not None:
                    total_events_compared += divergence.event_idx

                return ReplayResult(
                    original_run_id=recorded_run.run_id,
                    replay_run_id=replay_run_id,
                    status=ReplayStatus.DIVERGED,
                    steps_compared=step_idx + 1,
                    total_events_compared=total_events_compared,
                    divergence=divergence,
                    step_results=step_results,
                )

        # All steps matched
        return ReplayResult(
            original_run_id=recorded_run.run_id,
            replay_run_id=replay_run_id,
            status=ReplayStatus.MATCH,
            steps_compared=len(step_results),
            total_events_compared=total_events_compared,
            step_results=step_results,
        )


# =============================================================================
# Replay Context (Injection Mechanism)
# =============================================================================


class ReplayContext:
    """
    Context for replaying a run with injected recorded outputs.

    This provides the "oracle" that answers: "What should this tool/LLM call return?"
    by looking up the recorded output from the original run.

    Usage:
        ctx = ReplayContext.from_run(original_run)

        # In your workflow code:
        with ctx.step("process_input") as step_ctx:
            # Instead of calling LLM:
            response = step_ctx.get_recorded_output("llm_call", call_params)

    The context maintains a cursor through the recorded events, ensuring
    strict replay order. Accessing events out of order is an error.
    """

    def __init__(self, run: Run):
        """
        Initialize replay context from a run.

        Args:
            run: The recorded run to replay from
        """
        self.run = run
        self._step_cursor = 0
        self._event_cursors: Dict[int, int] = {}  # step_idx -> event cursor

    @classmethod
    def from_run(cls, run: Run) -> "ReplayContext":
        """Create a ReplayContext from a Run object."""
        return cls(run)

    @classmethod
    def from_store(cls, store: SQLiteStore, run_id: str) -> Optional["ReplayContext"]:
        """
        Create a ReplayContext by loading a run from storage.

        Returns None if run not found.
        """
        run = store.load_run(run_id)
        if run is None:
            return None
        return cls(run)

    def get_step(self, step_idx: int) -> Optional[Step]:
        """
        Get a step by index.

        Args:
            step_idx: 0-based step index

        Returns:
            Step if exists, None otherwise
        """
        if step_idx < 0 or step_idx >= len(self.run.steps):
            return None
        return self.run.steps[step_idx]

    def get_step_by_name(self, name: str) -> Optional[Step]:
        """
        Get the first step with the given name.

        Note: Step names are not guaranteed unique. This returns the first match.

        Args:
            name: Step name to search for

        Returns:
            Step if found, None otherwise
        """
        for step in self.run.steps:
            if step.name == name:
                return step
        return None

    def get_event(self, step_idx: int, event_idx: int) -> Optional[Event]:
        """
        Get a specific event.

        Args:
            step_idx: 0-based step index
            event_idx: 0-based event index within the step

        Returns:
            Event if exists, None otherwise
        """
        step = self.get_step(step_idx)
        if step is None:
            return None
        if event_idx < 0 or event_idx >= len(step.events):
            return None
        return step.events[event_idx]

    def get_events_by_type(self, step_idx: int, event_type: str) -> List[Event]:
        """
        Get all events of a specific type within a step.

        Args:
            step_idx: 0-based step index
            event_type: Event type to filter by (e.g., "llm_call", "tool_call")

        Returns:
            List of matching events (may be empty)
        """
        step = self.get_step(step_idx)
        if step is None:
            return []
        return [e for e in step.events if e.type == event_type]

    def iter_events(self, step_idx: int) -> Iterator[Event]:
        """
        Iterate over events in a step.

        Args:
            step_idx: 0-based step index

        Yields:
            Events in order
        """
        step = self.get_step(step_idx)
        if step is not None:
            yield from step.events

    def next_event(
        self, step_idx: int, expected_type: Optional[str] = None
    ) -> Optional[Event]:
        """
        Get the next event in sequence for a step.

        This advances the internal cursor, ensuring strict replay order.

        Args:
            step_idx: 0-based step index
            expected_type: If provided, validates the event type matches

        Returns:
            Next event, or None if exhausted

        Raises:
            ReplayOrderError: If expected_type doesn't match
        """
        step = self.get_step(step_idx)
        if step is None:
            return None

        cursor = self._event_cursors.get(step_idx, 0)
        if cursor >= len(step.events):
            return None

        event = step.events[cursor]

        if expected_type is not None and event.type != expected_type:
            raise ReplayOrderError(
                f"Expected event type '{expected_type}' at "
                f"step[{step_idx}]/event[{cursor}], got '{event.type}'"
            )

        self._event_cursors[step_idx] = cursor + 1
        return event

    def peek_event(self, step_idx: int) -> Optional[Event]:
        """
        Peek at the next event without advancing cursor.

        Args:
            step_idx: 0-based step index

        Returns:
            Next event, or None if exhausted
        """
        step = self.get_step(step_idx)
        if step is None:
            return None

        cursor = self._event_cursors.get(step_idx, 0)
        if cursor >= len(step.events):
            return None

        return step.events[cursor]

    def reset_cursor(self, step_idx: Optional[int] = None) -> None:
        """
        Reset event cursor(s).

        Args:
            step_idx: If provided, reset only that step's cursor.
                     If None, reset all cursors.
        """
        if step_idx is not None:
            self._event_cursors[step_idx] = 0
        else:
            self._event_cursors.clear()
            self._step_cursor = 0


class ReplayOrderError(Exception):
    """
    Raised when replay events are accessed out of order.

    This indicates a divergence between the expected replay sequence
    and the actual execution sequence.
    """

    pass


# =============================================================================
# Legacy API (backwards compatibility)
# =============================================================================


def replay(run_id: str, store: Optional[SQLiteStore] = None) -> Optional[Run]:
    """
    Load a recorded run from storage.

    This is the legacy API for backwards compatibility.
    For full replay functionality, use ReplayEngine.

    Args:
        run_id: The run identifier
        store: Optional SQLiteStore instance

    Returns:
        Run object if found, None otherwise
    """
    engine = ReplayEngine(store)
    return engine.load_run(run_id)
