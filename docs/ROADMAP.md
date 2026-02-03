# Forkline Roadmap & Design Contract

This document defines the **design principles, scope, and execution roadmap** for Forkline.

It is the **canonical source of truth** for what Forkline is, what it is not, and how it evolves.
All feature work and design decisions should align with this document.

---

## What Forkline is

Forkline is a **local-first, replay-first tracing and diffing library for agentic AI workflows**.

Its core goal is to make agent runs:

- reproducible
- inspectable
- diffable

Forkline treats nondeterminism as a **bug to be controlled**, not a mystery to be observed.

---

## What Forkline is NOT

Forkline explicitly does **not** aim to be:

- an evaluation or benchmarking framework
- prompt engineering or prompt optimization tooling
- a hosted SaaS or dashboard product
- a generic “AI observability” platform
- a metrics-first analytics system

If a feature does not help **reproduce, replay, or diff an agent run**, it does not belong in Forkline.

---

## Core design principles

These principles are non-negotiable and inform every design decision.

### 1. Replay-first
Forkline prioritizes **deterministic replay** over real-time inspection.
A run that cannot be replayed is considered incomplete.

---

### 2. Determinism over probabilistic insight
Forkline prefers correctness and reproducibility over aggregated metrics or trends.
Understanding *why* something changed matters more than observing *that* it changed.

---

### 3. Local-first by default
All artifacts are stored locally.
Replay must work offline.
No hidden remote state is allowed in the core workflow.

---

### 4. Diff over dashboards
Forkline treats agent behavior like code.
Changes are understood through **diffs**, not charts.

---

### 5. Explicit schemas and contracts
Run artifacts, tool calls, and metadata use explicit, versioned schemas.
Implicit behavior is avoided.

---

### 6. CLI-first
Forkline is CLI-first by design.

The CLI defines the real API surface and semantic contract.
Any future UI must be a thin layer on top of the CLI, never the source of truth.

---

## Versioning philosophy

Forkline follows semantic versioning with **strict guarantees**.

- **v0.x**
  - APIs may change
  - Focus is on correctness, determinism, and core semantics
- **v1.0**
  - Artifact formats stabilized
  - Replay and diff semantics locked
  - Backward compatibility guaranteed

Breaking changes after v1.0 are strongly discouraged.

---

## v0 roadmap (execution order)

The v0 series is intentionally narrow.
The goal is to establish a **trustworthy core**, not a feature-rich platform.

### v0.1 — Deterministic recording ✅
- Deterministic recording of agent runs
- Self-contained run artifacts
- Local storage (file-based or SQLite)
- No replay yet, record-only

---

### v0.2 — Replay engine ✅
- Replay runs without re-invoking the LLM
- No network calls during replay
- Identical behavior guaranteed
- Step-by-step comparison with first-divergence semantics
- ReplayContext for injecting recorded outputs

---

### v0.3 — Diffing
- Step-by-step comparison of two runs
- Detection of first divergence
- Clear presentation of old vs new state

---

### v0.4 — Minimal CLI
- `forkline run`
- `forkline replay`
- `forkline diff`
- Human-readable output
- Scriptable exit codes

---

### v0.5 — CI-friendly mode
- Deterministic execution for tests
- Fail CI on unexpected diffs
- Zero network dependency

---

## Out-of-scope for v0

The following are explicitly out of scope for the v0 series:

- Web dashboards or UIs
- Hosted services
- Model evaluation or scoring
- Prompt experimentation tooling
- Visualization-heavy workflows

These may be reconsidered only after v1.0.

---

## Final constraint

> If Forkline cannot explain *why* an agent changed behavior, it has failed its purpose.
