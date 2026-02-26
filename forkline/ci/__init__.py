"""Forkline CI integration layer.

Deterministic, offline, build-failing diffs for CI/CD pipelines.

Exit code contract:
    0 = Success (no diff, replay ok)
    1 = Diff detected (policy violation, should fail CI)
    2 = Usage/config error (bad args, missing file)
    3 = Replay failed (runtime exception, missing dependencies)
    4 = Offline violation (network attempted in offline mode)
    5 = Artifact/schema error (cannot parse, unsupported version)
    6 = Internal error (unexpected bug)
"""

from .exitcodes import (
    EXIT_ARTIFACT_ERROR,
    EXIT_DIFF_DETECTED,
    EXIT_INTERNAL_ERROR,
    EXIT_OFFLINE_VIOLATION,
    EXIT_REPLAY_FAILED,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
)
from .normalize import normalize_artifact
from .offline import ForklineOfflineError, enable_offline_mode, offline_context

__all__ = [
    "EXIT_SUCCESS",
    "EXIT_DIFF_DETECTED",
    "EXIT_USAGE_ERROR",
    "EXIT_REPLAY_FAILED",
    "EXIT_OFFLINE_VIOLATION",
    "EXIT_ARTIFACT_ERROR",
    "EXIT_INTERNAL_ERROR",
    "ForklineOfflineError",
    "enable_offline_mode",
    "offline_context",
    "normalize_artifact",
]
