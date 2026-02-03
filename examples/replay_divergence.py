#!/usr/bin/env python3
"""
Forkline v0.1.1 Replay Example: Divergence Detection

Demonstrates:
1. Recording a run with stubbed LLM and tool calls
2. Recording a second run with ONE different tool output
3. Replaying and detecting the divergence
4. Printing ReplayStatus: DIVERGED with step index and reason

No external dependencies. No real LLM calls.
"""

import os
import sys
import tempfile

# Add parent directory to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forkline import SQLiteStore, ReplayEngine, ReplayStatus


# =============================================================================
# Stubbed Adapters (no real LLM/tool calls)
# =============================================================================


def stub_llm_call(prompt: str) -> str:
    """Stubbed LLM that returns deterministic output."""
    return f"I received: {prompt}. Here is my response."


def stub_tool_call(tool_name: str, args: dict, variant: str = "original") -> dict:
    """
    Stubbed tool that returns deterministic output.
    
    The 'variant' parameter allows simulating different tool outputs
    for divergence testing.
    """
    if variant == "original":
        return {"status": "success", "tool": tool_name, "items": ["a", "b", "c"]}
    else:
        # Different output - this will cause divergence
        return {"status": "success", "tool": tool_name, "items": ["x", "y"]}


# =============================================================================
# Record Runs
# =============================================================================


def record_run(store: SQLiteStore, run_id: str, tool_variant: str = "original") -> None:
    """
    Record a 4-step run.
    
    The tool_variant parameter controls whether the tool output differs.
    """
    store.start_run(run_id)
    
    # Step 0: Input
    store.start_step(run_id, 0, "input")
    store.append_event(run_id, 0, "input", {"prompt": "Find items"})
    store.end_step(run_id, 0)
    
    # Step 1: LLM call (stubbed) - same in both runs
    store.start_step(run_id, 1, "llm_call")
    llm_response = stub_llm_call("Find items")
    store.append_event(run_id, 1, "llm_call", {
        "model": "stub-model",
        "prompt": "Find items",
        "response": llm_response,
    })
    store.end_step(run_id, 1)
    
    # Step 2: Tool call (stubbed) - THIS IS WHERE DIVERGENCE HAPPENS
    store.start_step(run_id, 2, "tool_call")
    tool_result = stub_tool_call("search", {"query": "items"}, variant=tool_variant)
    store.append_event(run_id, 2, "tool_call", {
        "name": "search",
        "args": {"query": "items"},
        "result": tool_result,  # <-- Different for variant="modified"
    })
    store.end_step(run_id, 2)
    
    # Step 3: Output
    store.start_step(run_id, 3, "output")
    store.append_event(run_id, 3, "output", {"final": "Complete"})
    store.end_step(run_id, 3)


# =============================================================================
# Main
# =============================================================================


def main():
    print("=" * 60)
    print("Forkline Replay Example: Divergence Detection")
    print("=" * 60)
    
    # Use temporary directory for this example
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "example.db")
        store = SQLiteStore(path=db_path)
        engine = ReplayEngine(store=store)
        
        # Record original run
        print("\n1. Recording run 'original'...")
        record_run(store, "original", tool_variant="original")
        print("   Done. Tool returns: ['a', 'b', 'c']")
        
        # Record modified run (different tool output at step 2)
        print("\n2. Recording run 'modified' (different tool output)...")
        record_run(store, "modified", tool_variant="modified")
        print("   Done. Tool returns: ['x', 'y']  <-- DIFFERENT!")
        
        # Compare them
        print("\n3. Comparing runs...")
        result = engine.compare_runs("original", "modified")
        
        # Print result
        print("\n" + "=" * 60)
        print(f"ReplayStatus: {result.status.value.upper()}")
        print("=" * 60)
        
        if result.is_diverged():
            div = result.divergence
            print(f"\nDivergence detected!")
            print(f"  Step index: {div.step_idx}")
            print(f"  Step name:  {div.step_name}")
            print(f"  Reason:     {div.divergence_type}")
            
            if div.event_idx is not None:
                print(f"  Event index: {div.event_idx}")
            
            print(f"\nField differences:")
            for diff in div.field_diffs[:5]:  # Show first 5
                print(f"  - {diff.path}")
                print(f"      expected: {diff.expected}")
                print(f"      actual:   {diff.actual}")
            
            print(f"\nSummary: {div.summary()}")
        else:
            print("\n✓ Runs matched (unexpected)")
            sys.exit(1)


if __name__ == "__main__":
    main()
