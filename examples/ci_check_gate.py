#!/usr/bin/env python3
"""
CI example: Build gating with `ci check`.

Demonstrates the all-in-one CI command that records actual behavior
and diffs it against an expected baseline in a single call.

This is the command you'd put in your CI pipeline:
    forkline ci check --entrypoint my_flow.py --expected baseline.run.json

Here we show the full lifecycle:
1. Record a baseline
2. ci_check passes when behavior is unchanged
3. ci_check fails (exit 1) when behavior changes

No external dependencies. No network calls.

Run:
    python examples/ci_check_gate.py
"""

import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forkline.ci.commands import ci_check, ci_record
from forkline.ci.exitcodes import EXIT_DIFF_DETECTED, EXIT_SUCCESS

_INSERT = "INSERT INTO events" " (run_id, ts, type, payload)" " VALUES (?, ?, ?, ?)"


def _write_agent(path: str, tool: str, chunks: int, summary: str) -> None:
    """Write a script that logs input, tool_call, and output."""
    with open(path, "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
            import os, json, sqlite3
            db = os.environ.get("FORKLINE_DB", "runs.db")
            rid = os.environ.get("FORKLINE_RUN_ID", "")
            SQL = "{_INSERT}"
            if db and rid:
                c = sqlite3.connect(db)
                c.execute(SQL, (
                    rid, "2026-01-01T00:00:00+00:00",
                    "input",
                    json.dumps({{"query": "summarize document"}}),
                ))
                c.execute(SQL, (
                    rid, "2026-01-01T00:00:00+00:00",
                    "tool_call",
                    json.dumps({{"tool": "{tool}", "chunks": {chunks}}}),
                ))
                c.execute(SQL, (
                    rid, "2026-01-01T00:00:01+00:00",
                    "output",
                    json.dumps({{"summary": "{summary}"}}),
                ))
                c.commit()
                c.close()
        """
            )
        )


def main() -> None:
    print("=" * 60)
    print("Forkline CI: Build Gate with ci_check")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        script = os.path.join(tmpdir, "agent.py")
        baseline = os.path.join(tmpdir, "baseline.run.json")

        # Step 1: Record baseline
        print("\n1. Recording baseline...")
        _write_agent(
            script,
            tool="retriever",
            chunks=3,
            summary="The document discusses X, Y, Z.",
        )
        code = ci_record(script, baseline, offline=False)
        assert code == EXIT_SUCCESS
        print("   Baseline saved.")

        # Step 2: Check with same script — should pass
        print("\n2. Running ci_check (same behavior)...")
        code = ci_check(script, baseline, offline=False)
        print(f"   Exit code: {code}")
        assert code == EXIT_SUCCESS
        print("   BUILD PASS")

        # Step 3: Change the script behavior
        print("\n3. Modifying agent behavior...")
        _write_agent(
            script,
            tool="retriever_v2",
            chunks=5,
            summary="The document covers A, B, C, D.",
        )

        # Step 4: Check again — should fail
        print("\n4. Running ci_check (changed behavior)...")
        code = ci_check(script, baseline, offline=False, output_format="text")
        print(f"   Exit code: {code}")
        assert code == EXIT_DIFF_DETECTED
        print("   BUILD FAIL — behavioral diff detected")

    print("\n" + "=" * 60)
    print("Build gate demo complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
