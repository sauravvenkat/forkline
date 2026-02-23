"""
Ollama Qwen3 example with Forkline tracing.

Calls Ollama's qwen3 model and records the input/output as forkline events.
Run it twice via the CLI to see nondeterminism caught by diff.

Prerequisites:
    ollama pull qwen3

Usage:
    forkline run examples/ollama_qwen3.py
    forkline run examples/ollama_qwen3.py      # run again
    forkline list                               # see both runs
    forkline diff <run_a> <run_b>               # see divergence
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

from forkline.storage.recorder import RunRecorder

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen3"
PROMPT = "/no_think In exactly one sentence, what is a fork bomb?"


def call_ollama(model: str, prompt: str) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    text = data.get("response", "")
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def main() -> None:
    db_path = os.environ.get("FORKLINE_DB", "runs.db")
    run_id = os.environ.get("FORKLINE_RUN_ID")
    recorder = RunRecorder(db_path=db_path)

    own_run = run_id is None
    if own_run:
        run_id = recorder.start_run(entrypoint="examples/ollama_qwen3.py")

    recorder.log_event(run_id, "input", {"model": MODEL, "prompt": PROMPT})

    print(f"Calling {MODEL} ...")
    response = call_ollama(MODEL, PROMPT)
    print(f"Response: {response}")

    recorder.log_event(run_id, "output", {"model": MODEL, "response": response})

    if own_run:
        recorder.end_run(run_id, status="success")


if __name__ == "__main__":
    main()
