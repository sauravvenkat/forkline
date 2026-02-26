"""Forkline test helpers for Python test suites.

Provides a simple API for snapshot/golden-file testing of agentic workflows.

Usage in pytest or unittest::

    from forkline.testing import assert_no_diff

    def test_my_flow():
        assert_no_diff(
            entrypoint="examples/my_flow.py",
            expected_artifact="tests/testdata/my_flow.run.json",
            offline=True,
        )
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from .ci.commands import _diff_event_lists, ci_record
from .ci.exitcodes import EXIT_SUCCESS
from .ci.normalize import normalize_artifact


class ArtifactDiffError(AssertionError):
    """Raised when an artifact diff is detected during testing.

    Contains the structured diff result for programmatic inspection.
    """

    def __init__(self, message: str, diff_result: Dict[str, Any]):
        super().__init__(message)
        self.diff_result = diff_result


def assert_no_diff(
    entrypoint: str,
    expected_artifact: str,
    *,
    offline: bool = True,
    script_args: Optional[List[str]] = None,
    normalize_timestamps: bool = True,
    normalize_metadata: bool = True,
) -> None:
    """Assert that running an entrypoint produces the same artifact as expected.

    Runs the entrypoint in a temp directory, normalizes the output,
    and diffs against the expected artifact. Raises ArtifactDiffError
    with a clean diff snippet if behavior changed.

    Args:
        entrypoint: Path to Python script to run.
        expected_artifact: Path to expected artifact JSON file.
        offline: If True, block all network access during the run.
        script_args: Optional arguments to pass to the script.
        normalize_timestamps: Normalize timestamps before comparison.
        normalize_metadata: Normalize platform metadata before comparison.

    Raises:
        ArtifactDiffError: If a behavioral diff is detected.
        FileNotFoundError: If entrypoint or expected_artifact doesn't exist.
    """
    if not os.path.isfile(entrypoint):
        raise FileNotFoundError(f"Entrypoint not found: {entrypoint}")
    if not os.path.isfile(expected_artifact):
        raise FileNotFoundError(f"Expected artifact not found: {expected_artifact}")

    with open(expected_artifact) as f:
        expected_raw = json.load(f)

    expected_normalized = normalize_artifact(
        expected_raw,
        normalize_timestamps=normalize_timestamps,
        normalize_metadata=normalize_metadata,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        actual_path = os.path.join(tmpdir, "actual.run.json")

        record_code = ci_record(
            entrypoint,
            actual_path,
            offline=offline,
            script_args=script_args,
        )
        if record_code != EXIT_SUCCESS:
            raise RuntimeError(
                f"Recording failed with exit code {record_code}. "
                f"Check stderr for details."
            )

        with open(actual_path) as f:
            actual_raw = json.load(f)

    actual_normalized = normalize_artifact(
        actual_raw,
        normalize_timestamps=normalize_timestamps,
        normalize_metadata=normalize_metadata,
    )

    expected_events = expected_normalized.get("events", [])
    actual_events = actual_normalized.get("events", [])

    diff_result = _diff_event_lists(expected_events, actual_events)

    if not diff_result["identical"]:
        idx = diff_result.get("first_divergent_index", 0)
        snippet = json.dumps(diff_result, indent=2, sort_keys=True, default=str)
        raise ArtifactDiffError(
            f"Behavioral diff detected at event[{idx}].\n\n"
            f"Expected artifact: {expected_artifact}\n"
            f"Diff details:\n{snippet}\n\n"
            f"To re-record the baseline:\n"
            f"  forkline ci record --entrypoint {entrypoint} --out {expected_artifact}",
            diff_result=diff_result,
        )
