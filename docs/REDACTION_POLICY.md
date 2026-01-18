# Forkline Redaction Policy

## 1. Purpose

Forkline captures **single executions of agentic workflows** as portable artifacts for
replay, diffing, and forensic debugging.

This document defines **what data Forkline is allowed to record by default** and
**how sensitive data is handled** across environments.

This is a **design contract**, not a compliance guarantee.

---

## 2. Core Invariant

> **By default, Forkline artifacts MUST NOT contain recoverable sensitive user,
customer, or proprietary data.**

This invariant applies to:
- local disk
- temporary files
- uploaded artifacts (e.g., object storage)
- any medium intended for replay or sharing

All Forkline features must preserve this invariant unless an **explicit opt-in
redaction mode** is enabled.

---

## 3. Definition of Sensitive Data

Forkline treats the following categories as **sensitive by default**.

### 3.1 Personally Identifiable Information (PII)
Examples include (non-exhaustive):
- Names
- Email addresses
- Phone numbers
- Physical addresses
- Government identifiers
- IP addresses (configurable)

### 3.2 Secrets and Credentials
- API keys
- OAuth tokens
- Cookies
- Authorization headers
- Session identifiers
- Private keys

**Secrets MUST NEVER be written to disk in any mode.**

---

### 3.3 Customer and Proprietary Data
- Retrieved documents
- Internal knowledge base content
- Source code
- Business metrics
- Contracts, invoices, financial records
- Internal datasets

---

### 3.4 Free-Text Risk
The following are **assumed sensitive unless explicitly allowed**:
- LLM prompts
- LLM responses
- Tool inputs containing natural language
- Tool outputs containing natural language

Forkline does not attempt semantic classification of free text.

---

## 4. Default Redaction Behavior (SAFE Mode)

Forkline operates in **SAFE mode by default**, suitable for production environments.

In SAFE mode, Forkline:

### Does NOT persist:
- Raw LLM prompts or responses
- Raw tool inputs or outputs
- Retrieved documents
- HTTP headers or authentication material
- Secrets of any kind

### DOES persist:
- Step ordering and control flow
- Tool and model identifiers
- Timestamps and durations
- Execution metadata
- Environment fingerprints (code version, prompt version, config version)
- **Stable cryptographic hashes** of redacted values
- Structural shape of inputs/outputs (where applicable)

This allows **diffing, correlation, and replay control** without data exposure.

---

## 5. Redaction Mechanisms

Forkline applies redaction **at capture time**, before data is written to disk.

Mechanisms include:
- Field-based redaction (deny-by-default)
- Type-based redaction (strings, blobs)
- Stable hashing for comparison and diffing
- Structured preservation of explicitly non-sensitive fields
- Never-write rules for secrets

> **Disk is treated as a hostile surface.**
> Redaction MUST occur before persistence.

---

## 6. Escalation Modes (Explicit Opt-In)

Forkline supports explicit redaction modes for development and debugging.

### 6.1 SAFE (default)
- Production-safe
- Redaction enforced
- No recoverable sensitive data

### 6.2 DEBUG
- Intended for local development or controlled staging
- May persist raw values
- MUST NOT be enabled in production by default

### 6.3 ENCRYPTED_DEBUG
- Raw payloads encrypted before persistence
- Requires explicit key configuration
- Intended for break-glass production debugging
- Short retention expected
- Access must be restricted and auditable

Escalation modes are **explicit configuration choices** and never implicit.

---

## 7. Non-Goals

Forkline explicitly does NOT:
- Perform semantic PII detection
- Guarantee zero data leakage if misconfigured
- Replace organizational data governance or compliance tooling
- Automatically classify business sensitivity

Forkline provides **mechanisms and defaults**, not guarantees.

---

## 8. Summary

Forkline prioritizes **safe, replayable debugging artifacts** over raw data capture.

- Redaction is default
- Escalation is explicit
- Secrets are never persisted
- Structure and behavior matter more than payloads

This policy is foundational and must be upheld by all Forkline components.
