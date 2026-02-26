"""CI exit code contract.

Strict, documented exit codes for CI automation.
These must remain stable across releases.
"""

EXIT_SUCCESS = 0
EXIT_DIFF_DETECTED = 1
EXIT_USAGE_ERROR = 2
EXIT_REPLAY_FAILED = 3
EXIT_OFFLINE_VIOLATION = 4
EXIT_ARTIFACT_ERROR = 5
EXIT_INTERNAL_ERROR = 6

EXIT_CODE_DESCRIPTIONS = {
    EXIT_SUCCESS: "Success, no diff / replay ok",
    EXIT_DIFF_DETECTED: "Diff detected (policy violation; should fail CI)",
    EXIT_USAGE_ERROR: "Usage/config error (bad args, missing file)",
    EXIT_REPLAY_FAILED: "Replay failed (runtime exception, missing dependencies)",
    EXIT_OFFLINE_VIOLATION: "Offline violation (network attempted in offline mode)",
    EXIT_ARTIFACT_ERROR: "Artifact/schema error (cannot parse, unsupported version)",
    EXIT_INTERNAL_ERROR: "Internal error (unexpected bug)",
}
