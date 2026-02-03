#!/usr/bin/env python3
"""
Forkline v0.1.1 Replay Example: Happy Path

Demonstrates:
1. Recording a run with stubbed LLM and tool calls
2. Replaying the same run
3. Verifying ReplayStatus: MATCH

No external dependencies. No real LLM calls.
"""

import os
import sys
import tempfile

# Add parent directory to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forkline import ReplayEngine, SQLiteStore


# =============================================================================
# Stubbed Adapters (no real LLM/tool calls)
# =============================================================================


def stub_llm_call(prompt: str) -> str:
    """Stubbed LLM that returns deterministic output."""
    return f"I received: {prompt}. Here is my response."


def stub_tool_call(tool_name: str, args: dict) -> dict:
    """Stubbed tool that returns deterministic output."""
    return {"status": "success", "tool": tool_name, "result": f"executed with {args}"}


# =============================================================================
# Record a Run
# =============================================================================


def record_run(store: SQLiteStore, run_id: str) -> None:
    """Record a simple 3-step run."""
    store.start_run(run_id)

    # Step 0: Input
    store.start_step(run_id, 0, "input")
    store.append_event(run_id, 0, "input", {"prompt": "Hello, world!"})
    store.end_step(run_id, 0)

    # Step 1: LLM call (stubbed)
    store.start_step(run_id, 1, "llm_call")
    llm_response = stub_llm_call("Hello, world!")
    store.append_event(
        run_id,
        1,
        "llm_call",
        {
            "model": "stub-model",
            "prompt": "Hello, world!",
            "response": llm_response,
        },
    )
    store.end_step(run_id, 1)

    # Step 2: Tool call (stubbed)
    store.start_step(run_id, 2, "tool_call")
    tool_result = stub_tool_call("search", {"query": "test"})
    store.append_event(
        run_id,
        2,
        "tool_call",
        {
            "name": "search",
            "args": {"query": "test"},
            "result": tool_result,
        },
    )
    store.end_step(run_id, 2)

    # Step 3: Output
    store.start_step(run_id, 3, "output")
    store.append_event(run_id, 3, "output", {"final": "Done!"})
    store.end_step(run_id, 3)


# =============================================================================
# Main
# =============================================================================


def main():
    print("=" * 60)
    print("Forkline Replay Example: Happy Path")
    print("=" * 60)

    # Use temporary directory for this example
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "example.db")
        store = SQLiteStore(path=db_path)
        engine = ReplayEngine(store=store)

        # Record two identical runs
        print("\n1. Recording run 'original'...")
        record_run(store, "original")
        print("   Done. 4 steps recorded.")

        print("\n2. Recording run 'replay' (identical)...")
        record_run(store, "replay")
        print("   Done. 4 steps recorded.")

        # Compare them
        print("\n3. Comparing runs...")
        result = engine.compare_runs("original", "replay")

        # Print result
        print("\n" + "=" * 60)
        print(f"ReplayStatus: {result.status.value.upper()}")
        print(f"Steps compared: {result.steps_compared}")
        print(f"Events compared: {result.total_events_compared}")
        print("=" * 60)

        if result.is_match():
            print("\n✓ Runs are identical. Replay successful.")
        else:
            print(f"\n✗ Runs diverged: {result.divergence.summary()}")
            sys.exit(1)


if __name__ == "__main__":
    main()
