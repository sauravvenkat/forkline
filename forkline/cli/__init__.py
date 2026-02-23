"""Forkline CLI.

Entry point for the ``forkline`` command-line tool.

Usage::

    forkline run <path/to/script.py> [-- <script args...>]
    forkline list [--limit N] [--json]
    forkline replay <run_id> [--json]
    forkline diff <run_id_a> <run_id_b> [--format pretty|json] [--first-divergence]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Any, Dict, List

from ..storage.recorder import RunRecorder
from .render import (
    render_diff_json,
    render_diff_pretty,
    render_list_json,
    render_list_table,
    render_replay_json,
    render_replay_summary,
    render_run_result,
)


def _get_recorder(args: argparse.Namespace) -> RunRecorder:
    redact_config = getattr(args, "redact_config", None)
    if redact_config:
        return RunRecorder.with_config(
            db_path=args.db, redact_config_path=redact_config
        )
    return RunRecorder(db_path=args.db)


# ---------------------------------------------------------------------------
# Event-level diff engine
# ---------------------------------------------------------------------------


def _diff_events(
    events_a: List[Dict[str, Any]], events_b: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compare two event sequences and return the first divergence."""
    for i, (ea, eb) in enumerate(zip(events_a, events_b)):
        if ea["type"] != eb["type"] or ea["payload"] != eb["payload"]:
            return {
                "identical": False,
                "divergence_index": i,
                "old": {"type": ea["type"], "payload": ea["payload"]},
                "new": {"type": eb["type"], "payload": eb["payload"]},
                "total_events_a": len(events_a),
                "total_events_b": len(events_b),
            }
    if len(events_a) != len(events_b):
        return {
            "identical": False,
            "divergence_index": min(len(events_a), len(events_b)),
            "reason": "different_event_count",
            "old": None,
            "new": None,
            "total_events_a": len(events_a),
            "total_events_b": len(events_b),
        }
    return {
        "identical": True,
        "total_events_a": len(events_a),
        "total_events_b": len(events_b),
    }


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> None:
    script = args.script
    script_args: list[str] = args.script_args or []
    # Strip the leading '--' separator if present
    if script_args and script_args[0] == "--":
        script_args = script_args[1:]

    if not os.path.isfile(script):
        print(f"Error: file not found: {script}", file=sys.stderr)
        sys.exit(2)

    recorder = _get_recorder(args)
    run_id = recorder.start_run(entrypoint=script)

    env = os.environ.copy()
    env["FORKLINE_TRACING"] = "1"
    env["FORKLINE_RUN_ID"] = run_id
    env["FORKLINE_DB"] = args.db
    if getattr(args, "redact_config", None):
        env["FORKLINE_REDACT_CONFIG"] = args.redact_config

    try:
        result = subprocess.run(
            [sys.executable, script] + script_args,
            env=env,
        )
        exit_code = result.returncode
    except Exception as e:
        print(f"Error: failed to execute {script}: {e}", file=sys.stderr)
        recorder.end_run(run_id, status="error")
        sys.exit(1)

    status = "ok" if exit_code == 0 else "failed"
    recorder.end_run(run_id, status=status)

    print(render_run_result(run_id))

    if exit_code != 0:
        sys.exit(exit_code)


def _cmd_list(args: argparse.Namespace) -> None:
    recorder = _get_recorder(args)
    runs = recorder.list_runs(limit=args.limit)

    if args.json:
        print(render_list_json(runs))
    else:
        print(render_list_table(runs))


def _cmd_replay(args: argparse.Namespace) -> None:
    recorder = _get_recorder(args)
    run = recorder.get_run(args.run_id)

    if run is None:
        print(f"Error: run '{args.run_id}' not found", file=sys.stderr)
        sys.exit(2)

    events = recorder.get_events(args.run_id)

    if args.json:
        print(render_replay_json(run, events))
    else:
        print(render_replay_summary(run, events))


def _cmd_diff(args: argparse.Namespace) -> None:
    recorder = _get_recorder(args)

    run_a = recorder.get_run(args.run_a)
    if run_a is None:
        print(f"Error: run '{args.run_a}' not found", file=sys.stderr)
        sys.exit(2)

    run_b = recorder.get_run(args.run_b)
    if run_b is None:
        print(f"Error: run '{args.run_b}' not found", file=sys.stderr)
        sys.exit(2)

    events_a = recorder.get_events(args.run_a)
    events_b = recorder.get_events(args.run_b)

    diff_result = _diff_events(events_a, events_b)

    fmt = getattr(args, "format", "pretty")
    if fmt == "json":
        print(render_diff_json(diff_result, args.run_a, args.run_b))
    else:
        print(render_diff_pretty(diff_result, args.run_a, args.run_b))

    if not diff_result["identical"]:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_DB_HELP = "Path to SQLite database (default: runs.db)"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="forkline",
        description="Forkline: replay-first tracing and diffing for agentic workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Shared arguments via parent parser
    db_parser = argparse.ArgumentParser(add_help=False)
    db_parser.add_argument("--db", default="runs.db", help=_DB_HELP)

    # --- run ---
    run_parser = subparsers.add_parser(
        "run",
        parents=[db_parser],
        help="Execute a script under forkline tracing",
        description=(
            "Run a Python script with forkline tracing enabled. "
            "Records run metadata (script path, timestamps, exit code) "
            "and prints the assigned run_id."
        ),
    )
    run_parser.add_argument("script", help="Path to Python script to execute")
    run_parser.add_argument(
        "script_args",
        nargs=argparse.REMAINDER,
        help="Arguments to pass to the script (use -- to separate)",
    )
    run_parser.add_argument(
        "--redact-config",
        default=None,
        help="Path to redaction config file (YAML or JSON)",
    )
    run_parser.set_defaults(func=_cmd_run)

    # --- list ---
    list_parser = subparsers.add_parser(
        "list",
        parents=[db_parser],
        help="List stored runs",
        description="Show all recorded runs, newest first.",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of runs to show",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON array",
    )
    list_parser.set_defaults(func=_cmd_list)

    # --- replay ---
    replay_parser = subparsers.add_parser(
        "replay",
        parents=[db_parser],
        help="Replay a recorded run",
        description=(
            "Load a recorded run by ID and print a summary of its events, "
            "tools invoked, duration, and status."
        ),
    )
    replay_parser.add_argument("run_id", help="Run ID to replay")
    replay_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )
    replay_parser.set_defaults(func=_cmd_replay)

    # --- diff ---
    diff_parser = subparsers.add_parser(
        "diff",
        parents=[db_parser],
        help="Diff two recorded runs",
        description=(
            "Compare two recorded runs event-by-event and show "
            "the first point of divergence."
        ),
    )
    diff_parser.add_argument("run_a", help="Run ID for baseline")
    diff_parser.add_argument("run_b", help="Run ID for comparison")
    diff_parser.add_argument(
        "--format",
        choices=["pretty", "json"],
        default="pretty",
        help="Output format (default: pretty)",
    )
    diff_parser.add_argument(
        "--first-divergence",
        action="store_true",
        default=True,
        help="Stop at first divergence (default behavior)",
    )
    diff_parser.set_defaults(func=_cmd_diff)

    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
