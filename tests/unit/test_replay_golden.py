"""
Golden tests for Forkline replay engine.

These tests use pre-defined fixtures representing recorded runs.
No real LLM or network calls are made.

Fixtures represent:
- A run with 5 steps (init, llm_call, tool_call, process, finalize)
- At least one tool step (search tool)
- At least one LLM step (gpt-4 call)

Tests:
- test_replay_match_golden: Identical runs produce MATCH
- test_replay_diverged_first_difference: Mismatched step produces DIVERGED
- test_replay_missing_artifact_fails: Missing artifact raises MissingArtifactError
"""

import unittest
from typing import Any, Dict, List

from forkline import Event, Run, Step
from forkline.core.replay import (
    MissingArtifactError,
    ReplayEngine,
    ReplayPolicy,
    ReplayResult,
    ReplayStatus,
)


# =============================================================================
# Golden Fixtures
# =============================================================================

# Stable timestamps (deterministic, not wall-clock)
FIXTURE_TIMESTAMP = "2024-01-15T10:00:00.000000+00:00"


def make_golden_event(
    event_id: int,
    run_id: str,
    step_idx: int,
    event_type: str,
    payload: Dict[str, Any],
) -> Event:
    """Create a golden test event with stable timestamp."""
    return Event(
        event_id=event_id,
        run_id=run_id,
        step_idx=step_idx,
        type=event_type,
        created_at=FIXTURE_TIMESTAMP,
        payload=payload,
    )


def make_golden_step(
    step_id: int,
    run_id: str,
    idx: int,
    name: str,
    events: List[Event],
) -> Step:
    """Create a golden test step with stable timestamps."""
    return Step(
        step_id=step_id,
        run_id=run_id,
        idx=idx,
        name=name,
        started_at=FIXTURE_TIMESTAMP,
        ended_at=FIXTURE_TIMESTAMP,
        events=events,
    )


def create_golden_run(run_id: str = "golden-run-001") -> Run:
    """
    Create a golden run fixture with 5 steps.
    
    Steps:
    0. init - User input received
    1. llm_call - GPT-4 generates plan
    2. tool_call - Search tool executed
    3. process - Process search results
    4. finalize - Generate final output
    
    This represents a typical agentic workflow.
    """
    # Step 0: init - receives user input
    step0_events = [
        make_golden_event(1, run_id, 0, "input", {
            "prompt": "Find information about Python async programming",
            "user_id": "user-123",
        }),
    ]
    
    # Step 1: llm_call - LLM generates a plan
    step1_events = [
        make_golden_event(2, run_id, 1, "input", {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are a helpful research assistant."},
                {"role": "user", "content": "Find information about Python async programming"},
            ],
            "temperature": 0.0,  # Deterministic
        }),
        make_golden_event(3, run_id, 1, "llm_call", {
            "model": "gpt-4",
            "prompt_tokens": 42,
            "completion_tokens": 128,
            "response": {
                "role": "assistant",
                "content": "I'll search for Python async programming information.",
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"query": "Python asyncio tutorial"}',
                        },
                    }
                ],
            },
        }),
        make_golden_event(4, run_id, 1, "output", {
            "decision": "search",
            "reasoning": "Need to find relevant documentation",
        }),
    ]
    
    # Step 2: tool_call - Execute search tool
    step2_events = [
        make_golden_event(5, run_id, 2, "input", {
            "tool_name": "search",
            "tool_args": {"query": "Python asyncio tutorial"},
        }),
        make_golden_event(6, run_id, 2, "tool_call", {
            "name": "search",
            "args": {"query": "Python asyncio tutorial"},
            "result": {
                "status": "success",
                "items": [
                    {"title": "Python asyncio docs", "url": "https://docs.python.org/3/library/asyncio.html"},
                    {"title": "Real Python asyncio", "url": "https://realpython.com/async-io-python/"},
                ],
                "total_results": 2,
            },
        }),
        make_golden_event(7, run_id, 2, "output", {
            "search_complete": True,
            "result_count": 2,
        }),
    ]
    
    # Step 3: process - Process search results
    step3_events = [
        make_golden_event(8, run_id, 3, "input", {
            "action": "summarize_results",
            "results": [
                {"title": "Python asyncio docs", "url": "https://docs.python.org/3/library/asyncio.html"},
                {"title": "Real Python asyncio", "url": "https://realpython.com/async-io-python/"},
            ],
        }),
        make_golden_event(9, run_id, 3, "llm_call", {
            "model": "gpt-4",
            "prompt_tokens": 156,
            "completion_tokens": 89,
            "response": {
                "role": "assistant",
                "content": "Based on the search results, I found two excellent resources...",
            },
        }),
        make_golden_event(10, run_id, 3, "output", {
            "summary_generated": True,
        }),
    ]
    
    # Step 4: finalize - Generate final output
    step4_events = [
        make_golden_event(11, run_id, 4, "input", {
            "action": "generate_response",
        }),
        make_golden_event(12, run_id, 4, "output", {
            "final_response": "Here are the best resources for Python async programming:\n"
                            "1. Official Python asyncio documentation\n"
                            "2. Real Python's comprehensive asyncio tutorial",
            "status": "success",
        }),
    ]
    
    steps = [
        make_golden_step(1, run_id, 0, "init", step0_events),
        make_golden_step(2, run_id, 1, "llm_call", step1_events),
        make_golden_step(3, run_id, 2, "tool_call", step2_events),
        make_golden_step(4, run_id, 3, "process", step3_events),
        make_golden_step(5, run_id, 4, "finalize", step4_events),
    ]
    
    return Run(
        run_id=run_id,
        created_at=FIXTURE_TIMESTAMP,
        steps=steps,
    )


def create_diverged_run(
    run_id: str = "diverged-run-001",
    diverge_at_step: int = 2,
) -> Run:
    """
    Create a run that diverges from golden at the specified step.
    
    By default, diverges at step 2 (tool_call) with different search results.
    """
    golden = create_golden_run(run_id)
    
    # Create modified steps list
    modified_steps = []
    
    for step in golden.steps:
        if step.idx < diverge_at_step:
            # Keep steps before divergence unchanged
            modified_steps.append(step)
        elif step.idx == diverge_at_step:
            # Modify the divergent step (tool_call step)
            modified_events = []
            for event in step.events:
                if event.type == "tool_call":
                    # Different search results!
                    modified_payload = {
                        "name": "search",
                        "args": {"query": "Python asyncio tutorial"},
                        "result": {
                            "status": "success",
                            "items": [
                                # Different results - this is the divergence
                                {"title": "DIFFERENT RESULT", "url": "https://example.com/different"},
                            ],
                            "total_results": 1,  # Different count
                        },
                    }
                    modified_events.append(make_golden_event(
                        event.event_id,
                        run_id,
                        step.idx,
                        event.type,
                        modified_payload,
                    ))
                else:
                    modified_events.append(Event(
                        event_id=event.event_id,
                        run_id=run_id,
                        step_idx=event.step_idx,
                        type=event.type,
                        created_at=event.created_at,
                        payload=event.payload,
                    ))
            
            modified_steps.append(make_golden_step(
                step.step_id,
                run_id,
                step.idx,
                step.name,
                modified_events,
            ))
        else:
            # Keep steps after divergence (though in practice we'd halt)
            modified_steps.append(Step(
                step_id=step.step_id,
                run_id=run_id,
                idx=step.idx,
                name=step.name,
                started_at=step.started_at,
                ended_at=step.ended_at,
                events=[Event(
                    event_id=e.event_id,
                    run_id=run_id,
                    step_idx=e.step_idx,
                    type=e.type,
                    created_at=e.created_at,
                    payload=e.payload,
                ) for e in step.events],
            ))
    
    return Run(
        run_id=run_id,
        created_at=FIXTURE_TIMESTAMP,
        steps=modified_steps,
    )


def create_incomplete_run(run_id: str = "incomplete-run-001") -> Run:
    """
    Create a run with missing artifacts (empty events in a step).
    """
    golden = create_golden_run(run_id)
    
    # Modify step 2 to have no events (missing artifact)
    modified_steps = []
    for step in golden.steps:
        if step.idx == 2:
            # Empty events - missing artifact
            modified_steps.append(make_golden_step(
                step.step_id,
                run_id,
                step.idx,
                step.name,
                [],  # No events!
            ))
        else:
            modified_steps.append(Step(
                step_id=step.step_id,
                run_id=run_id,
                idx=step.idx,
                name=step.name,
                started_at=step.started_at,
                ended_at=step.ended_at,
                events=[Event(
                    event_id=e.event_id,
                    run_id=run_id,
                    step_idx=e.step_idx,
                    type=e.type,
                    created_at=e.created_at,
                    payload=e.payload,
                ) for e in step.events],
            ))
    
    return Run(
        run_id=run_id,
        created_at=FIXTURE_TIMESTAMP,
        steps=modified_steps,
    )


# =============================================================================
# Golden Tests
# =============================================================================


class TestReplayGolden(unittest.TestCase):
    """Golden tests for replay engine using pre-defined fixtures."""

    def setUp(self):
        """Set up replay engine (no storage needed for loaded runs)."""
        self.engine = ReplayEngine()

    def test_replay_match_golden(self):
        """
        Golden test: Identical runs should produce MATCH status.
        
        Creates two identical copies of the golden run and compares them.
        All 5 steps and 12 events should match.
        """
        # Arrange: Create two identical runs
        original = create_golden_run("original-001")
        replay = create_golden_run("replay-001")
        
        # Act: Compare the runs
        result = self.engine.compare_loaded_runs(original, replay)
        
        # Assert: Should match completely
        self.assertEqual(result.status, ReplayStatus.MATCH)
        self.assertTrue(result.is_match())
        self.assertIsNone(result.divergence)
        
        # Verify all steps were compared
        self.assertEqual(result.steps_compared, 5)
        self.assertEqual(len(result.step_results), 5)
        
        # Verify each step matched
        for step_result in result.step_results:
            self.assertTrue(step_result.matched, 
                f"Step {step_result.step_idx} ({step_result.step_name}) should match")
            self.assertIsNone(step_result.divergence)
        
        # Verify total events compared
        # init: 1, llm_call: 3, tool_call: 3, process: 3, finalize: 2 = 12
        self.assertEqual(result.total_events_compared, 12)

    def test_replay_diverged_first_difference(self):
        """
        Golden test: First mismatched step should produce DIVERGED status.
        
        Creates a diverged run where step 2 (tool_call) has different results.
        Should detect divergence at step 2 and halt.
        """
        # Arrange: Create original and diverged runs
        original = create_golden_run("original-002")
        diverged = create_diverged_run("diverged-002", diverge_at_step=2)
        
        # Act: Compare the runs
        result = self.engine.compare_loaded_runs(original, diverged)
        
        # Assert: Should be DIVERGED
        self.assertEqual(result.status, ReplayStatus.DIVERGED)
        self.assertFalse(result.is_match())
        self.assertTrue(result.is_diverged())
        
        # Verify divergence was detected at step 2
        self.assertIsNotNone(result.divergence)
        self.assertEqual(result.divergence.step_idx, 2)
        self.assertEqual(result.divergence.step_name, "tool_call")
        
        # Verify divergence type
        self.assertEqual(result.divergence.divergence_type, "event_payload_mismatch")
        
        # Verify we stopped at step 2 (didn't compare steps 3 and 4)
        self.assertEqual(result.steps_compared, 3)  # 0, 1, 2
        
        # Steps 0 and 1 should have matched
        self.assertTrue(result.step_results[0].matched)
        self.assertTrue(result.step_results[1].matched)
        self.assertFalse(result.step_results[2].matched)
        
        # Verify field_diffs contain useful information
        self.assertGreater(len(result.divergence.field_diffs), 0)
        
        # Check that divergence summary is readable
        summary = result.divergence.summary()
        self.assertIn("tool_call", summary)
        self.assertIn("step[2]", summary)

    def test_replay_missing_artifact_fails(self):
        """
        Golden test: Missing artifact should raise MissingArtifactError.
        
        Creates a run with empty events in step 2 and uses replay() method
        with strict policy to trigger the error.
        """
        # Arrange: Create incomplete run (step 2 has no events)
        incomplete = create_incomplete_run("incomplete-003")
        
        # Create an in-memory "store" by using the engine's replay() method
        # which validates artifacts when policy requires it
        
        # We need to use replay() with an executor to trigger artifact validation
        # But replay() requires the run to be in storage. Instead, let's test
        # the validation logic directly.
        
        # Actually, let's use _validate_recorded_run which is what replay() uses
        policy = ReplayPolicy(fail_on_missing_artifact=True)
        
        # Act & Assert: Should raise MissingArtifactError
        with self.assertRaises(MissingArtifactError) as ctx:
            self.engine._validate_recorded_run(incomplete, policy)
        
        # Verify error contains useful context
        error = ctx.exception
        self.assertEqual(error.run_id, "incomplete-003")
        self.assertEqual(error.step_idx, 2)
        self.assertEqual(error.artifact_type, "events")
        
        error_str = str(error)
        self.assertIn("incomplete-003", error_str)
        self.assertIn("step=2", error_str)

    def test_replay_match_stable_serialization(self):
        """
        Verify that fixture serialization is stable (sorted keys).
        
        This ensures deterministic comparison across runs.
        """
        import json
        
        run1 = create_golden_run("test-stable-1")
        run2 = create_golden_run("test-stable-2")
        
        # Serialize events to JSON with sorted keys
        for step1, step2 in zip(run1.steps, run2.steps):
            for e1, e2 in zip(step1.events, step2.events):
                json1 = json.dumps(e1.payload, sort_keys=True)
                json2 = json.dumps(e2.payload, sort_keys=True)
                self.assertEqual(json1, json2, 
                    f"Payloads should be identical when serialized with sorted keys")

    def test_replay_diverged_at_llm_step(self):
        """
        Test divergence detection at an LLM step.
        
        Modifies the LLM response in step 1 to verify LLM divergence is caught.
        """
        original = create_golden_run("original-llm")
        
        # Create a run with modified LLM response at step 1
        modified = create_golden_run("modified-llm")
        
        # Manually modify the LLM response in step 1
        modified_step1_events = []
        for event in modified.steps[1].events:
            if event.type == "llm_call":
                modified_payload = dict(event.payload)
                modified_payload["response"] = {
                    "role": "assistant",
                    "content": "COMPLETELY DIFFERENT RESPONSE",  # Changed!
                    "tool_calls": [],  # Different tool calls
                }
                modified_step1_events.append(make_golden_event(
                    event.event_id,
                    "modified-llm",
                    event.step_idx,
                    event.type,
                    modified_payload,
                ))
            else:
                modified_step1_events.append(event)
        
        modified_steps = [modified.steps[0]]
        modified_steps.append(make_golden_step(
            modified.steps[1].step_id,
            "modified-llm",
            1,
            "llm_call",
            modified_step1_events,
        ))
        modified_steps.extend(modified.steps[2:])
        
        modified = Run(
            run_id="modified-llm",
            created_at=FIXTURE_TIMESTAMP,
            steps=modified_steps,
        )
        
        # Compare
        result = self.engine.compare_loaded_runs(original, modified)
        
        # Should diverge at step 1 (llm_call)
        self.assertEqual(result.status, ReplayStatus.DIVERGED)
        self.assertEqual(result.divergence.step_idx, 1)
        self.assertEqual(result.divergence.step_name, "llm_call")
        
        # Should have compared step 0 successfully
        self.assertTrue(result.step_results[0].matched)

    def test_replay_diverged_at_tool_step(self):
        """
        Test divergence detection specifically at a tool step.
        
        This is the canonical test for tool output mismatch.
        """
        original = create_golden_run("original-tool")
        diverged = create_diverged_run("diverged-tool", diverge_at_step=2)
        
        result = self.engine.compare_loaded_runs(original, diverged)
        
        # Should diverge at step 2 (tool_call)
        self.assertEqual(result.status, ReplayStatus.DIVERGED)
        self.assertEqual(result.divergence.step_idx, 2)
        self.assertEqual(result.divergence.step_name, "tool_call")
        
        # Verify the specific event that diverged
        self.assertIsNotNone(result.divergence.event_idx)
        
        # The divergence should be in the tool_call event (event index 1 in step 2)
        self.assertEqual(result.divergence.event_idx, 1)


class TestReplayGoldenEdgeCases(unittest.TestCase):
    """Edge case tests for golden replay scenarios."""

    def setUp(self):
        self.engine = ReplayEngine()

    def test_empty_runs_match(self):
        """Two empty runs should match."""
        run1 = Run(run_id="empty-1", created_at=FIXTURE_TIMESTAMP, steps=[])
        run2 = Run(run_id="empty-2", created_at=FIXTURE_TIMESTAMP, steps=[])
        
        result = self.engine.compare_loaded_runs(run1, run2)
        
        self.assertEqual(result.status, ReplayStatus.MATCH)
        self.assertEqual(result.steps_compared, 0)

    def test_step_count_mismatch_diverges(self):
        """Runs with different step counts should diverge."""
        original = create_golden_run("original-steps")
        
        # Create a run with fewer steps
        truncated = Run(
            run_id="truncated",
            created_at=FIXTURE_TIMESTAMP,
            steps=original.steps[:3],  # Only first 3 steps
        )
        
        result = self.engine.compare_loaded_runs(original, truncated)
        
        # Should be INCOMPLETE (replay has fewer steps)
        self.assertEqual(result.status, ReplayStatus.INCOMPLETE)

    def test_extra_steps_diverges(self):
        """Replay with extra steps should diverge."""
        original = create_golden_run("original-extra")
        
        # Create a run with extra step
        extra_step = make_golden_step(
            99, "extra", 5, "unexpected_step",
            [make_golden_event(99, "extra", 5, "output", {"unexpected": True})]
        )
        extended = Run(
            run_id="extended",
            created_at=FIXTURE_TIMESTAMP,
            steps=list(original.steps) + [extra_step],
        )
        
        result = self.engine.compare_loaded_runs(original, extended)
        
        # Should be DIVERGED (extra steps)
        self.assertEqual(result.status, ReplayStatus.DIVERGED)
        self.assertEqual(result.divergence.divergence_type, "extra_steps_in_replay")

    def test_step_name_mismatch_diverges(self):
        """Steps with different names should diverge."""
        original = create_golden_run("original-name")
        
        # Create a run with different step name at step 2
        modified_steps = list(original.steps)
        modified_steps[2] = make_golden_step(
            original.steps[2].step_id,
            "modified-name",
            2,
            "WRONG_STEP_NAME",  # Different name!
            original.steps[2].events,
        )
        modified = Run(
            run_id="modified-name",
            created_at=FIXTURE_TIMESTAMP,
            steps=modified_steps,
        )
        
        result = self.engine.compare_loaded_runs(original, modified)
        
        self.assertEqual(result.status, ReplayStatus.DIVERGED)
        self.assertEqual(result.divergence.step_idx, 2)
        self.assertEqual(result.divergence.divergence_type, "step_name_mismatch")


if __name__ == "__main__":
    unittest.main()
