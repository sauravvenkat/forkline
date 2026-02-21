"""Deterministic canonicalization for Forkline values.

Provides stable byte representations and cryptographic hashes for
deterministic comparison of run data.

Guarantees:
- canon(v) is deterministic: same input always yields identical bytes
- Dict key order is irrelevant (sorted internally)
- Unicode is NFC-normalized
- Newlines are normalized to LF
- Floats use repr-level precision; -0.0 collapses to 0.0
- Bytes pass through unchanged
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any


def canon(value: Any, profile: str = "strict") -> bytes:
    """Canonicalize a value to bytes for deterministic comparison.

    Args:
        value: bytes (returned as-is), str (NFC + newline normalized, UTF-8),
               or JSON-like (sorted keys, stable floats, compact JSON, UTF-8).
        profile: Canonicalization profile. Currently only "strict".

    Returns:
        Canonical byte representation.
    """
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return _canon_str(value).encode("utf-8")
    return _canon_json(value).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Compute SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def bytes_preview(data: bytes, max_len: int = 16) -> str:
    """Human-readable preview: sha256 hash + hex prefix."""
    prefix = data[:max_len].hex()
    return f"sha256:{sha256_hex(data)}:{prefix}"


def _canon_str(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s


def _canon_json(value: Any) -> str:
    return json.dumps(
        _normalize_value(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        normalized = float(f"{value:.17g}")
        if normalized == 0.0:
            normalized = 0.0
        return normalized
    if isinstance(value, str):
        return _canon_str(value)
    if isinstance(value, bytes):
        return {"__bytes__": True, "sha256": sha256_hex(value), "length": len(value)}
    if isinstance(value, dict):
        return {
            str(k): _normalize_value(v)
            for k, v in sorted(value.items(), key=lambda x: str(x[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_value(v) for v in value]
    return str(value)
