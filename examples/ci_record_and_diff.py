#!/usr/bin/env python3
"""
CI example: Record a baseline, then diff against a changed version.

Demonstrates the core CI workflow:
1. Record a baseline artifact from a deterministic script
2. Diff two artifacts to confirm identical behavior
3. Introduce a behavior change
4. Diff again to see the change caught with exit code 1

No external dependencies. No network calls.

Run:
    python examples/ci_record_and_diff.py
"""

import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forkline.ci.commands import ci_diff, ci_record
from forkline.ci.exitcodes import EXIT_DIFF_DETECTED, EXIT_SUCCESS

_INSERT = "INSERT INTO events" " (run_id, ts, type, payload)" " VALUES (?, ?, ?, ?)"


def _write_flow(path: str, answer: str, extra: str = "") -> None:
    """Write a small flow script that logs input + output events."""
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
                    json.dumps({{"prompt": "What is 2+2?"}}),
                ))
                c.execute(SQL, (
                    rid, "2026-01-01T00:00:01+00:00",
                    "output",
                    json.dumps({{"answer": "{answer}"{extra}}}),
                ))
                c.commit()
                c.close()
        """
            )
        )


def main() -> None:
    print("=" * 60)
    print("Forkline CI: Record and Diff")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        v1 = os.path.join(tmpdir, "flow_v1.py")
        v2 = os.path.join(tmpdir, "flow_v2.py")
        _write_flow(v1, answer="4")
        _write_flow(v2, answer="5", extra=', "note": "wrong!"')

        baseline = os.path.join(tmpdir, "baseline.run.json")
        actual_same = os.path.join(tmpdir, "actual_same.run.json")
        actual_changed = os.path.join(tmpdir, "actual_changed.run.json")

        # Step 1: Record baseline
        print("\n1. Recording baseline from v1...")
        code = ci_record(v1, baseline)
        assert code == EXIT_SUCCESS, f"Expected 0, got {code}"
        print("   Baseline recorded.")

        # Step 2: Record v1 again — should be identical
        print("\n2. Recording v1 again...")
        code = ci_record(v1, actual_same)
        assert code == EXIT_SUCCESS

        # Step 3: Diff baseline vs same — should pass
        print("\n3. Diffing baseline vs v1 (same behavior)...")
        code = ci_diff(baseline, actual_same)
        assert code == EXIT_SUCCESS, f"Expected 0, got {code}"
        print("   No differences. Exit code: 0")

        # Step 4: Record v2 — changed behavior
        print("\n4. Recording v2 (changed behavior)...")
        code = ci_record(v2, actual_changed)
        assert code == EXIT_SUCCESS

        # Step 5: Diff baseline vs changed — should fail
        print("\n5. Diffing baseline vs v2 (changed behavior)...")
        code = ci_diff(baseline, actual_changed, output_format="text")
        assert code == EXIT_DIFF_DETECTED, f"Expected 1, got {code}"
        print(f"   Exit code: {code} (build would fail)")

        # Step 6: Show JSON diff
        print("\n6. JSON diff output:")
        ci_diff(baseline, actual_changed, output_format="json")

    print("\n" + "=" * 60)
    print("CI workflow complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
