#!/usr/bin/env python3
"""
Helper script to inspect runs.db in a human-friendly format.

Usage:
    python scripts/inspect_runs.py
    python scripts/inspect_runs.py --run-id <run_id>
"""

import argparse
import json
import sys
from pathlib import Path

from forkline.storage.recorder import RunRecorder


def format_run(run: dict) -> str:
    """Format a run record for display."""
    lines = [
        f"Run ID: {run['run_id']}",
        f"Schema: {run['schema_version']}",
        f"Entrypoint: {run['entrypoint']}",
        f"Status: {run.get('status', 'running')}",
        f"Started: {run['started_at']}",
        f"Ended: {run.get('ended_at', 'N/A')}",
        f"Python: {run['python_version'][:50]}...",
        f"Platform: {run['platform']}",
        f"CWD: {run['cwd']}",
    ]
    return "\n".join(lines)


def format_event(event: dict, index: int) -> str:
    """Format an event record for display."""
    payload_str = json.dumps(event["payload"], indent=2)
    return f"""
Event #{index} (ID: {event['event_id']})
  Type: {event['type']}
  Timestamp: {event['ts']}
  Payload:
{payload_str}
"""


def main():
    parser = argparse.ArgumentParser(
        description="Inspect runs.db in human-friendly format"
    )
    parser.add_argument(
        "--db-path",
        default="runs.db",
        help="Path to runs.db (default: runs.db)",
    )
    parser.add_argument(
        "--run-id",
        help="Show specific run ID",
    )
    args = parser.parse_args()
    
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"Error: {db_path} does not exist", file=sys.stderr)
        print(f"Run examples/minimal.py first to create it", file=sys.stderr)
        sys.exit(1)
    
    recorder = RunRecorder(db_path=str(db_path))
    
    if args.run_id:
        # Show specific run
        run = recorder.get_run(args.run_id)
        if run is None:
            print(f"Error: Run {args.run_id} not found", file=sys.stderr)
            sys.exit(1)
        
        print("=" * 70)
        print(format_run(run))
        print("=" * 70)
        
        events = recorder.get_events(args.run_id)
        print(f"\nEvents ({len(events)} total):")
        for i, event in enumerate(events, 1):
            print(format_event(event, i))
    
    else:
        # Show all runs
        import sqlite3
        
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        
        runs = conn.execute(
            "SELECT run_id, entrypoint, status, started_at FROM runs ORDER BY started_at DESC"
        ).fetchall()
        
        if not runs:
            print("No runs found in database")
            sys.exit(0)
        
        print(f"Found {len(runs)} run(s):\n")
        for i, row in enumerate(runs, 1):
            print(f"{i}. Run ID: {row['run_id']}")
            print(f"   Entrypoint: {row['entrypoint']}")
            print(f"   Status: {row['status'] or 'running'}")
            print(f"   Started: {row['started_at']}")
            print()
        
        print(f"Use --run-id <run_id> to see details")
        
        conn.close()


if __name__ == "__main__":
    main()
