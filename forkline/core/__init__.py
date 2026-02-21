"""Core types and logic for Forkline."""

from .canon import bytes_preview, canon, sha256_hex
from .diff import diff_runs
from .first_divergence import (
    DivergenceType,
    FirstDivergenceResult,
    StepSummary,
    find_first_divergence,
)
from .json_diff import json_diff
from .redaction import (
    RedactionAction,
    RedactionPolicy,
    RedactionRule,
    create_default_policy,
)
from .replay import (
    # Exceptions
    DeterminismViolationError,
    # Data models
    Divergence,
    DivergencePoint,
    DivergenceReason,
    FieldDiff,
    MissingArtifactError,
    # Engine and context
    ReplayContext,
    ReplayEngine,
    ReplayError,
    ReplayOrderError,
    ReplayPolicy,
    ReplayResult,
    ReplayStatus,
    ReplayStepResult,
    # Replay mode guardrails
    assert_not_in_replay_mode,
    compare_events,
    compare_steps,
    deep_compare,
    get_replay_run_id,
    guard_live_call,
    is_replay_mode_active,
    # Legacy
    replay,
    replay_mode,
)
from .types import Event, Run, Step

__all__ = [
    # Core types
    "Event",
    "Run",
    "Step",
    # Canonicalization
    "canon",
    "sha256_hex",
    "bytes_preview",
    # Diff
    "diff_runs",
    # First-divergence diffing
    "find_first_divergence",
    "FirstDivergenceResult",
    "StepSummary",
    "DivergenceType",
    "json_diff",
    # Redaction
    "RedactionAction",
    "RedactionPolicy",
    "RedactionRule",
    "create_default_policy",
    # Replay exceptions
    "ReplayError",
    "MissingArtifactError",
    "DeterminismViolationError",
    "ReplayOrderError",
    # Replay data models
    "Divergence",
    "DivergencePoint",
    "DivergenceReason",
    "FieldDiff",
    "ReplayPolicy",
    "ReplayResult",
    "ReplayStatus",
    "ReplayStepResult",
    # Replay engine and context
    "ReplayEngine",
    "ReplayContext",
    # Replay mode guardrails
    "replay_mode",
    "is_replay_mode_active",
    "get_replay_run_id",
    "assert_not_in_replay_mode",
    "guard_live_call",
    # Replay utilities
    "deep_compare",
    "compare_events",
    "compare_steps",
    # Legacy
    "replay",
]
