"""Forkline CLI.

Entry point for the ``forkline`` command-line tool.

Usage:
    forkline diff --first <run_a> <run_b> [--window N] [--format json|text]
                  [--show input|output|both] [--canon strict] [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import sys

from .core.first_divergence import DivergenceType, find_first_divergence
from .storage.store import SQLiteStore

# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------


def _compact_value(op: dict) -> str:
    if op["op"] == "replace":
        old = json.dumps(op.get("old"), default=str)
        new = json.dumps(op.get("new"), default=str)
        if len(old) > 40:
            old = old[:37] + "..."
        if len(new) > 40:
            new = new[:37] + "..."
        return f"{old} -> {new}"
    if op["op"] == "add":
        val = json.dumps(op.get("value"), default=str)
        if len(val) > 40:
            val = val[:37] + "..."
        return val
    if op["op"] == "remove":
        val = json.dumps(op.get("old"), default=str)
        if len(val) > 40:
            val = val[:37] + "..."
        return val
    return ""


def _format_text(result) -> str:
    lines: list[str] = []
    lines.append(f"First divergence: {result.status}")
    lines.append(f"  {result.explanation}")
    lines.append("")

    if result.old_step:
        s = result.old_step
        lines.append(f"  Run A step {s.idx} '{s.name}':")
        lines.append(f"    input_hash:  {s.input_hash[:16]}...")
        lines.append(f"    output_hash: {s.output_hash[:16]}...")
        lines.append(f"    events: {s.event_count}")
        lines.append(f"    has_error: {s.has_error}")
        lines.append("")

    if result.new_step:
        s = result.new_step
        lines.append(f"  Run B step {s.idx} '{s.name}':")
        lines.append(f"    input_hash:  {s.input_hash[:16]}...")
        lines.append(f"    output_hash: {s.output_hash[:16]}...")
        lines.append(f"    events: {s.event_count}")
        lines.append(f"    has_error: {s.has_error}")
        lines.append("")

    if result.input_diff:
        lines.append("  Input diff:")
        for op in result.input_diff[:10]:
            lines.append(f"    {op['op']} {op['path']}: {_compact_value(op)}")
        if len(result.input_diff) > 10:
            lines.append(f"    ... and {len(result.input_diff) - 10} more operations")
        lines.append("")

    if result.output_diff:
        lines.append("  Output diff:")
        for op in result.output_diff[:10]:
            lines.append(f"    {op['op']} {op['path']}: {_compact_value(op)}")
        if len(result.output_diff) > 10:
            lines.append(f"    ... and {len(result.output_diff) - 10} more operations")
        lines.append("")

    lines.append(f"  Last equal: step {result.last_equal_idx}")

    if result.context_a:
        ctx = ", ".join(f"step {s.idx} '{s.name}'" for s in result.context_a)
        lines.append(f"  Context A: [{ctx}]")
    if result.context_b:
        ctx = ", ".join(f"step {s.idx} '{s.name}'" for s in result.context_b)
        lines.append(f"  Context B: [{ctx}]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _cmd_diff(args: argparse.Namespace) -> None:
    store = SQLiteStore(path=args.db)

    run_a = store.load_run(args.run_a)
    if run_a is None:
        print(f"Error: run '{args.run_a}' not found in {args.db}", file=sys.stderr)
        sys.exit(1)

    run_b = store.load_run(args.run_b)
    if run_b is None:
        print(f"Error: run '{args.run_b}' not found in {args.db}", file=sys.stderr)
        sys.exit(1)

    result = find_first_divergence(
        run_a,
        run_b,
        window=args.window,
        show=args.show,
    )

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(_format_text(result))

    if result.status != DivergenceType.EXACT_MATCH:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="forkline",
        description="Forkline: replay-first tracing and diffing for agentic workflows",
    )
    subparsers = parser.add_subparsers(dest="command")

    diff_parser = subparsers.add_parser("diff", help="Compare two recorded runs")
    diff_parser.add_argument(
        "--first",
        action="store_true",
        default=True,
        help="Show first divergence only (default)",
    )
    diff_parser.add_argument("run_a", help="Run ID for baseline")
    diff_parser.add_argument("run_b", help="Run ID for comparison")
    diff_parser.add_argument(
        "--window", type=int, default=10, help="Resync window size (default: 10)"
    )
    diff_parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )
    diff_parser.add_argument(
        "--show",
        choices=["input", "output", "both"],
        default="both",
        help="Which diffs to show (default: both)",
    )
    diff_parser.add_argument(
        "--canon",
        choices=["strict"],
        default="strict",
        help="Canonicalization profile (default: strict)",
    )
    diff_parser.add_argument(
        "--db",
        default="forkline.db",
        help="Path to SQLite database (default: forkline.db)",
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
