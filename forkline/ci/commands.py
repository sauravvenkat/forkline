"""CI command implementations.

Each function returns an exit code from the CI exit code contract.
Output goes to stdout (results) and stderr (errors/diagnostics).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

from ..artifact.schema import RunArtifact, SchemaVersionError
from ..core.json_diff import json_diff
from ..storage.recorder import RunRecorder
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


def ci_record(
    entrypoint: str,
    out_path: str,
    *,
    offline: bool = False,
    script_args: Optional[List[str]] = None,
) -> int:
    """Record a baseline artifact to a JSON file.

    Runs the entrypoint script under Forkline tracing, then exports
    the resulting artifact as a normalized JSON file suitable for
    committing to version control.

    Returns:
        CI exit code.
    """
    if not os.path.isfile(entrypoint):
        print(f"Error: entrypoint not found: {entrypoint}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "ci_record.db")
        recorder = RunRecorder(db_path=db_path)
        run_id = recorder.start_run(entrypoint=entrypoint)

        env = os.environ.copy()
        env["FORKLINE_TRACING"] = "1"
        env["FORKLINE_RUN_ID"] = run_id
        env["FORKLINE_DB"] = db_path
        if offline:
            env["FORKLINE_OFFLINE"] = "1"

        try:
            if offline:
                enable_offline_mode()
            result = subprocess.run(
                [sys.executable, entrypoint] + (script_args or []),
                env=env,
            )
            exit_code = result.returncode
        except ForklineOfflineError as e:
            print(f"Offline violation: {e}", file=sys.stderr)
            recorder.end_run(run_id, status="error")
            return EXIT_OFFLINE_VIOLATION
        except Exception as e:
            print(f"Error: failed to execute {entrypoint}: {e}", file=sys.stderr)
            recorder.end_run(run_id, status="error")
            return EXIT_REPLAY_FAILED

        status = "ok" if exit_code == 0 else "failed"
        recorder.end_run(run_id, status=status)

        if exit_code != 0:
            print(
                f"Error: script exited with code {exit_code}",
                file=sys.stderr,
            )
            return EXIT_REPLAY_FAILED

        artifact = recorder.load_artifact(run_id)
        if artifact is None:
            print("Error: failed to load recorded artifact", file=sys.stderr)
            return EXIT_INTERNAL_ERROR

        artifact_dict = artifact.to_dict()
        normalized = normalize_artifact(artifact_dict)

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(normalized, f, indent=2, sort_keys=True, default=str)
            f.write("\n")

    print(f"Recorded artifact: {out_path}", file=sys.stderr)
    return EXIT_SUCCESS


def ci_replay(
    artifact_path: str,
    *,
    strict: bool = False,
) -> int:
    """Replay/validate a recorded artifact offline.

    Loads the artifact, validates schema, and checks structural integrity.
    With --strict, also validates that all events have complete payloads.

    Returns:
        CI exit code.
    """
    if not os.path.isfile(artifact_path):
        print(f"Error: artifact not found: {artifact_path}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        with open(artifact_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: cannot parse artifact: {e}", file=sys.stderr)
        return EXIT_ARTIFACT_ERROR

    try:
        artifact = RunArtifact.from_dict(data)
    except SchemaVersionError as e:
        print(f"Error: schema version error: {e}", file=sys.stderr)
        return EXIT_ARTIFACT_ERROR
    except Exception as e:
        print(f"Error: invalid artifact structure: {e}", file=sys.stderr)
        return EXIT_ARTIFACT_ERROR

    errors = artifact.validate()
    if errors:
        for err in errors:
            print(f"Validation error: {err}", file=sys.stderr)
        return EXIT_ARTIFACT_ERROR

    if strict:
        for i, event in enumerate(artifact.events):
            if not event.payload and event.type in ("tool_call", "output"):
                print(
                    f"Strict validation: event[{i}] ({event.type}) has empty payload",
                    file=sys.stderr,
                )
                return EXIT_ARTIFACT_ERROR

    event_count = len(artifact.events)
    type_counts: Dict[str, int] = {}
    for ev in artifact.events:
        type_counts[ev.type] = type_counts.get(ev.type, 0) + 1

    print(json.dumps({
        "status": "ok",
        "run_id": artifact.run_id,
        "entrypoint": artifact.entrypoint,
        "schema_version": artifact.schema_version,
        "event_count": event_count,
        "event_types": type_counts,
    }, indent=2, sort_keys=True))

    return EXIT_SUCCESS


def ci_diff(
    expected_path: str,
    actual_path: str,
    *,
    output_format: str = "text",
    fail_on: str = "any",
) -> int:
    """Diff two artifact files and return appropriate exit code.

    Returns:
        EXIT_SUCCESS (0) if no diff, EXIT_DIFF_DETECTED (1) if diff found.
    """
    for label, path in [("expected", expected_path), ("actual", actual_path)]:
        if not os.path.isfile(path):
            print(f"Error: {label} artifact not found: {path}", file=sys.stderr)
            return EXIT_USAGE_ERROR

    try:
        with open(expected_path) as f:
            expected_raw = json.load(f)
        with open(actual_path) as f:
            actual_raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: cannot parse artifact: {e}", file=sys.stderr)
        return EXIT_ARTIFACT_ERROR

    expected = normalize_artifact(expected_raw)
    actual = normalize_artifact(actual_raw)

    expected_events = expected.get("events", [])
    actual_events = actual.get("events", [])

    diff_result = _diff_event_lists(expected_events, actual_events)

    if diff_result["identical"]:
        if output_format == "json":
            print(json.dumps({
                "identical": True,
                "expected_events": len(expected_events),
                "actual_events": len(actual_events),
            }, indent=2, sort_keys=True))
        else:
            print("No differences detected.")
        return EXIT_SUCCESS

    if output_format == "json":
        print(json.dumps(diff_result, indent=2, sort_keys=True, default=str))
    else:
        _render_diff_text(diff_result)

    return EXIT_DIFF_DETECTED


def ci_check(
    entrypoint: str,
    expected_path: str,
    *,
    offline: bool = True,
    output_format: str = "text",
    fail_on: str = "any",
    script_args: Optional[List[str]] = None,
) -> int:
    """Record actual, diff against expected. Single command for CI.

    Internally records into a temp directory, normalizes, and diffs
    against the expected artifact.

    Returns:
        CI exit code.
    """
    if not os.path.isfile(entrypoint):
        print(f"Error: entrypoint not found: {entrypoint}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    if not os.path.isfile(expected_path):
        print(f"Error: expected artifact not found: {expected_path}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    with tempfile.TemporaryDirectory() as tmpdir:
        actual_path = os.path.join(tmpdir, "actual.run.json")
        record_exit = ci_record(
            entrypoint,
            actual_path,
            offline=offline,
            script_args=script_args,
        )
        if record_exit != EXIT_SUCCESS:
            return record_exit

        return ci_diff(
            expected_path,
            actual_path,
            output_format=output_format,
            fail_on=fail_on,
        )


def ci_normalize(
    artifact_path: str,
    out_path: Optional[str] = None,
) -> int:
    """Normalize an artifact file in-place or to a new path.

    Returns:
        CI exit code.
    """
    if not os.path.isfile(artifact_path):
        print(f"Error: artifact not found: {artifact_path}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        with open(artifact_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: cannot parse artifact: {e}", file=sys.stderr)
        return EXIT_ARTIFACT_ERROR

    normalized = normalize_artifact(data)

    target = out_path or artifact_path
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w") as f:
        json.dump(normalized, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    print(f"Normalized: {target}", file=sys.stderr)
    return EXIT_SUCCESS


def _diff_event_lists(
    expected: List[Dict[str, Any]],
    actual: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare two event lists, returning a structured diff result."""
    for i, (exp, act) in enumerate(zip(expected, actual)):
        exp_comparable = {"type": exp.get("type"), "payload": exp.get("payload", {})}
        act_comparable = {"type": act.get("type"), "payload": act.get("payload", {})}

        if exp_comparable != act_comparable:
            payload_diff = json_diff(
                exp.get("payload", {}),
                act.get("payload", {}),
            )
            return {
                "identical": False,
                "first_divergent_index": i,
                "event_type": exp.get("type", "unknown"),
                "expected": exp_comparable,
                "actual": act_comparable,
                "payload_diff": payload_diff,
                "total_expected": len(expected),
                "total_actual": len(actual),
                "suggestion": "Re-record baseline: forkline ci record --entrypoint <script> --out <path>",
            }

    if len(expected) != len(actual):
        return {
            "identical": False,
            "first_divergent_index": min(len(expected), len(actual)),
            "reason": "event_count_mismatch",
            "total_expected": len(expected),
            "total_actual": len(actual),
            "suggestion": "Re-record baseline: forkline ci record --entrypoint <script> --out <path>",
        }

    return {
        "identical": True,
        "total_expected": len(expected),
        "total_actual": len(actual),
    }


def _render_diff_text(diff_result: Dict[str, Any]) -> None:
    """Render diff result as concise text for CI output."""
    idx = diff_result.get("first_divergent_index", 0)

    reason = diff_result.get("reason")
    if reason == "event_count_mismatch":
        print(
            f"DIFF: Event count mismatch at index {idx}\n"
            f"  expected: {diff_result['total_expected']} events\n"
            f"  actual:   {diff_result['total_actual']} events"
        )
    else:
        event_type = diff_result.get("event_type", "unknown")
        print(f"DIFF: First divergence at event[{idx}] (type: {event_type})")

        payload_diff = diff_result.get("payload_diff", [])
        for op in payload_diff[:5]:
            op_type = op.get("op", "?")
            path = op.get("path", "?")
            if op_type == "replace":
                print(f"  {path}: {_trunc(op.get('old'))} -> {_trunc(op.get('new'))}")
            elif op_type == "add":
                print(f"  {path}: <missing> -> {_trunc(op.get('value'))}")
            elif op_type == "remove":
                print(f"  {path}: {_trunc(op.get('old'))} -> <removed>")
        if len(payload_diff) > 5:
            print(f"  ... and {len(payload_diff) - 5} more changes")

    suggestion = diff_result.get("suggestion")
    if suggestion:
        print(f"\nSuggested fix: {suggestion}")


def _trunc(val: Any, max_len: int = 60) -> str:
    s = json.dumps(val, default=str, sort_keys=True)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s
