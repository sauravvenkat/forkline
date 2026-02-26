#!/usr/bin/env python3
"""
CI example: Python test helper (assert_no_diff).

Demonstrates how to use Forkline's test helper for snapshot-style
testing of agentic workflows in pytest or unittest.

The helper:
1. Runs the entrypoint in a temp directory
2. Records and normalizes the artifact
3. Diffs against the expected baseline
4. Raises ArtifactDiffError with a clean diff snippet on failure

Run:
    python examples/ci_test_helper.py
"""

import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forkline.ci.commands import ci_record
from forkline.ci.exitcodes import EXIT_SUCCESS
from forkline.testing import ArtifactDiffError, assert_no_diff

_INSERT = "INSERT INTO events" " (run_id, ts, type, payload)" " VALUES (?, ?, ?, ?)"


def _write_qa_flow(path: str, answer: str) -> None:
    """Write a script that logs a Q&A exchange."""
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
                    json.dumps({{"question": "Capital of France?"}}),
                ))
                c.execute(SQL, (
                    rid, "2026-01-01T00:00:01+00:00",
                    "output",
                    json.dumps({{"answer": "{answer}"}}),
                ))
                c.commit()
                c.close()
        """
            )
        )


def main() -> None:
    print("=" * 60)
    print("Forkline CI: Python Test Helper (assert_no_diff)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        script = os.path.join(tmpdir, "my_flow.py")
        baseline = os.path.join(tmpdir, "baseline.run.json")

        # Record the baseline
        print("\n1. Recording baseline...")
        _write_qa_flow(script, answer="Paris")
        code = ci_record(script, baseline, offline=False)
        assert code == EXIT_SUCCESS
        print(f"   Saved: {baseline}")

        # Passing test
        print("\n2. assert_no_diff (same behavior)...")
        assert_no_diff(
            entrypoint=script,
            expected_artifact=baseline,
            offline=False,
        )
        print("   PASSED — no behavioral diff")

        # Change the script
        print("\n3. Modifying script behavior...")
        _write_qa_flow(script, answer="London")

        # Failing test
        print("\n4. assert_no_diff (changed behavior)...")
        try:
            assert_no_diff(
                entrypoint=script,
                expected_artifact=baseline,
                offline=False,
            )
            print("   ERROR: Should not reach here!")
        except ArtifactDiffError as e:
            idx = e.diff_result["first_divergent_index"]
            print(f"   FAILED — {type(e).__name__} raised")
            print(f"   Divergence index: {idx}")
            print("   Message preview:")
            for line in str(e).split("\n")[:4]:
                print(f"     {line}")

        # Show the real test pattern
        print("\n5. In a real pytest test, you'd write:")
        print(
            textwrap.dedent(
                """\
           from forkline.testing import assert_no_diff

           def test_my_flow():
               assert_no_diff(
                   entrypoint="examples/my_flow.py",
                   expected_artifact="tests/testdata/my_flow.run.json",
                   offline=True,
               )"""
            )
        )

    print("=" * 60)
    print("Test helper demo complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
