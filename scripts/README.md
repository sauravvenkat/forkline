# Forkline Scripts

Helper scripts for working with Forkline runs.

## inspect_runs.py

Human-friendly inspection of `runs.db`.

### Usage

List all runs:
```bash
python scripts/inspect_runs.py
```

Show details for a specific run:
```bash
python scripts/inspect_runs.py --run-id <run_id>
```

Use a different database:
```bash
python scripts/inspect_runs.py --db-path path/to/runs.db
```

### Examples

```bash
# List all recorded runs
$ python scripts/inspect_runs.py
Found 2 run(s):

1. Run ID: b817c2bac38b4268aa63ffc5a663d1f9
   Entrypoint: examples/minimal.py
   Status: success
   Started: 2026-01-18T04:00:06.523405+00:00

2. Run ID: 59e41fabc564499cbea20d319bce03c1
   Entrypoint: examples/minimal.py
   Status: success
   Started: 2026-01-18T03:59:54.592902+00:00

# Inspect a specific run with all events
$ python scripts/inspect_runs.py --run-id 59e41fabc564499cbea20d319bce03c1
======================================================================
Run ID: 59e41fabc564499cbea20d319bce03c1
Schema: 0.1
Entrypoint: examples/minimal.py
Status: success
Started: 2026-01-18T03:59:54.592902+00:00
Ended: 2026-01-18T03:59:54.604925+00:00
...

Events (4 total):

Event #1 (ID: 1)
  Type: input
  Timestamp: 2026-01-18T03:59:54.603511+00:00
  Payload:
{
  "prompt": "hello world"
}
...
```

## Direct SQLite inspection

You can also inspect `runs.db` directly with sqlite3:

```bash
# Show all runs
sqlite3 runs.db "SELECT * FROM runs;"

# Show all events
sqlite3 runs.db "SELECT * FROM events;"

# Show schema
sqlite3 runs.db ".schema"

# Interactive mode
sqlite3 runs.db
```
