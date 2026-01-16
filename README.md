<p align="center">
  <img src="docs/assets/forkline-wordmark.svg" alt="Forkline" width="420"/>
</p>

**Forkline** is a **local-first, replay-first tracing and diffing library for agentic AI workflows**.

Its purpose is simple and strict:

> **Make agent runs reproducible, inspectable, and diffable.**

Forkline treats nondeterminism as something to be **controlled**, not merely observed.

---

## Why Forkline exists

Modern agentic systems fail in a frustrating way:

- The same prompt behaves differently on different days
- Tool calls change silently
- Debugging becomes guesswork
- CI becomes flaky or meaningless

Logs and dashboards tell you *that* something changed.  
Forkline is built to tell you **where**, **when**, and **why**.

---

## What Forkline does

Forkline allows you to:

- **Record** an agent run as a deterministic, local artifact
- **Replay** that run without re-invoking the LLM
- **Diff** two runs and detect the **first point of divergence**
- **Capture tool calls** safely with deterministic redaction
- **Use agent workflows in CI** without network calls or flakiness

This turns agent behavior into something you can reason about like code.

---

## Design principles

Forkline is intentionally opinionated.

- **Replay-first, not dashboards-first**
- **Determinism over probabilistic insight**
- **Local-first artifacts**
- **Diff over metrics**
- **Explicit schemas over implicit behavior**

If a feature does not help reproduce, replay, or diff an agent run, it does not belong in Forkline.

---

## Why CLI-first

Forkline is **CLI-first by design**, not by convenience.

Agent debugging and reproducibility are **developer workflows**.  
They live in terminals, CI pipelines, local machines, and code reviews — not dashboards.

### Determinism and scriptability
CLI commands are composable, automatable, and repeatable.

This makes Forkline usable in:
- CI pipelines
- test suites
- local debugging loops
- regression checks

If it can’t be scripted, it can’t be trusted as infrastructure.

---

### Local-first by default
A CLI enforces Forkline’s local-first philosophy:
- artifacts live on disk
- runs replay offline
- no hidden network dependencies
- no opaque browser state

This keeps behavior inspectable and failure modes obvious.

---

### Diff is terminal-native
Diffing is already how developers reason about change:
- `git diff`
- `pytest` failures
- compiler diagnostics
- performance regressions

Forkline extends this mental model to agent behavior.

A CLI makes Forkline additive to existing tooling, not a replacement.

---

### Avoiding dashboard gravity
Dashboards optimize for:
- aggregation over root cause
- real-time metrics over replayability
- visualization over determinism

Forkline explicitly avoids this gravity.

If a feature requires a UI to be understandable, it is usually hiding complexity rather than exposing truth.

---

### UIs can come later — CLIs must come first
Forkline does not reject UIs.  
It rejects **UI-first design**.

The CLI defines the real API surface and semantic contract.
Any future UI must be a thin layer on top — never the other way around.

> Forkline is CLI-first because reproducibility, diffing, and trust are terminal-native problems.

---

## What Forkline is NOT

Forkline explicitly does **not** aim to be:

- An evaluation or benchmarking framework
- Prompt engineering or prompt optimization tooling
- A hosted SaaS or dashboard product
- A generic “AI observability” platform

Forkline is a debugging and reproducibility tool, not an analytics product.

---

## Roadmap

Forkline follows a disciplined, execution-first roadmap.

The v0 series focuses on **correctness and determinism**, not polish.

1. Deterministic run recording  
2. Offline replay engine  
3. First-divergence diffing  
4. Minimal CLI (`run`, `replay`, `diff`)  
5. CI-friendly deterministic mode  

The canonical roadmap and design contract live here:

👉 `docs/ROADMAP.md`

---

## Status

Forkline is **early-stage and under active development**.

APIs are expected to change until `v1.0`.  
Feedback is welcome, especially around replay semantics and diffing behavior.

---

## License

Forkline is licensed under the **Apache 2.0 License**.

---

## Philosophy (one sentence)

> Forkline exists because “it changed” is not a useful debugging answer.
