"""Rendering helpers for the Forkline CLI.

Provides deterministic, stable formatting for CLI output in both
human-readable (pretty) and machine-readable (JSON) modes.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List


def _format_timestamp(iso_ts: str | None) -> str:
    """Format an ISO timestamp to a stable, human-readable form."""
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(iso_ts)


def _truncate(value: Any, max_len: int = 80) -> str:
    s = json.dumps(value, default=str, sort_keys=True)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def render_run_result(run_id: str) -> str:
    return f"run_id: {run_id}"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def render_list_table(runs: List[Dict[str, Any]]) -> str:
    if not runs:
        return "No runs found."

    header = f"{'ID':<36}  {'Created':<20}  {'Script':<30}  {'Status':<10}"
    separator = "-" * len(header)
    lines = [header, separator]

    for run in runs:
        run_id = run.get("run_id", "")[:36]
        created = _format_timestamp(run.get("started_at"))
        script = (run.get("entrypoint") or "")[:30]
        status = (run.get("status") or "")[:10]
        lines.append(f"{run_id:<36}  {created:<20}  {script:<30}  {status:<10}")

    return "\n".join(lines)


def render_list_json(runs: List[Dict[str, Any]]) -> str:
    clean: List[Dict[str, Any]] = []
    for run in runs:
        clean.append(
            {
                "run_id": run.get("run_id"),
                "created_at": run.get("started_at"),
                "entrypoint": run.get("entrypoint"),
                "status": run.get("status"),
                "ended_at": run.get("ended_at"),
            }
        )
    return json.dumps(clean, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def render_replay_summary(run: Dict[str, Any], events: List[Dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append(f"Run: {run['run_id']}")
    lines.append(f"Script: {run.get('entrypoint', 'unknown')}")
    lines.append(f"Status: {run.get('status', 'unknown')}")

    started = run.get("started_at")
    ended = run.get("ended_at")
    if started and ended:
        try:
            start_dt = datetime.fromisoformat(started)
            end_dt = datetime.fromisoformat(ended)
            duration = (end_dt - start_dt).total_seconds()
            lines.append(f"Duration: {duration:.2f}s")
        except (ValueError, TypeError):
            pass

    lines.append(f"Total events: {len(events)}")

    type_counts: Dict[str, int] = {}
    for event in events:
        t = event.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    if type_counts:
        lines.append("Events by type:")
        for t in sorted(type_counts.keys()):
            lines.append(f"  {t}: {type_counts[t]}")

    return "\n".join(lines)


def render_replay_json(run: Dict[str, Any], events: List[Dict[str, Any]]) -> str:
    result = {
        "run_id": run["run_id"],
        "entrypoint": run.get("entrypoint"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
        "total_events": len(events),
        "events": events,
    }
    return json.dumps(result, indent=2, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def render_diff_pretty(diff_result: Dict[str, Any], run_a: str, run_b: str) -> str:
    if diff_result["identical"]:
        return "No differences"

    lines: list[str] = []
    idx = diff_result.get("divergence_index", 0)

    reason = diff_result.get("reason")
    if reason == "different_event_count":
        lines.append(
            f"Event count differs: "
            f"{diff_result['total_events_a']} ({run_a}) vs "
            f"{diff_result['total_events_b']} ({run_b})"
        )
        lines.append(f"First divergence at event index {idx}")
    else:
        old = diff_result.get("old") or {}
        new = diff_result.get("new") or {}
        lines.append(f"Step {idx} diverged:")
        if old:
            lines.append(f"  old.type: {old.get('type', '')}")
            lines.append(f"  old.payload: {_truncate(old.get('payload', {}))}")
        if new:
            lines.append(f"  new.type: {new.get('type', '')}")
            lines.append(f"  new.payload: {_truncate(new.get('payload', {}))}")

    return "\n".join(lines)


def render_diff_json(diff_result: Dict[str, Any], run_a: str, run_b: str) -> str:
    output: Dict[str, Any] = {
        "run_a": run_a,
        "run_b": run_b,
        "identical": diff_result["identical"],
    }
    if not diff_result["identical"]:
        output["divergence_index"] = diff_result.get("divergence_index")
        if diff_result.get("old") is not None:
            output["old"] = diff_result["old"]
        if diff_result.get("new") is not None:
            output["new"] = diff_result["new"]
        if diff_result.get("reason"):
            output["reason"] = diff_result["reason"]
        output["total_events_a"] = diff_result.get("total_events_a")
        output["total_events_b"] = diff_result.get("total_events_b")

    return json.dumps(output, indent=2, sort_keys=True, default=str)
