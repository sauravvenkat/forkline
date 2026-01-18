"""
Minimal example demonstrating deterministic run recording v0.

Setup:
    source dev.env

Run this with:
    python examples/minimal.py

Then inspect the recording with:
    sqlite3 runs.db "SELECT * FROM runs;"
    sqlite3 runs.db "SELECT * FROM events;"
    python scripts/inspect_runs.py
"""

from forkline.storage.recorder import RunRecorder


def main() -> None:
    # Create recorder
    recorder = RunRecorder()

    # Start run
    run_id = recorder.start_run(entrypoint="examples/minimal.py")
    print(f"Started run: {run_id}")

    # Log events in order
    recorder.log_event(
        run_id,
        event_type="input",
        payload={"prompt": "hello world"},
    )

    recorder.log_event(
        run_id,
        event_type="tool_call",
        payload={
            "name": "search",
            "args": {"query": "python best practices"},
            "result": {"status": "ok", "items": 5},
        },
    )

    recorder.log_event(
        run_id,
        event_type="output",
        payload={"result": "world"},
    )

    recorder.log_event(
        run_id,
        event_type="artifact_ref",
        payload={"path": "/tmp/output.txt", "size": 1024},
    )

    # End run
    recorder.end_run(run_id, status="success")
    print(f"Ended run: {run_id}")

    # Verify recording
    run = recorder.get_run(run_id)
    events = recorder.get_events(run_id)

    print(f"\nRecorded {len(events)} events")
    print(f"Run status: {run['status']}")
    print(f"Schema version: {run['schema_version']}")


if __name__ == "__main__":
    main()
