"""Optional DataFog text redaction adapter.

This module deliberately avoids importing DataFog at import time.
The dependency is resolved lazily so existing Forkline default behavior
and dependency set are untouched unless callers opt-in.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional


class DataFogConfigurationError(RuntimeError):
    """Raised when DataFog is unavailable or misconfigured."""


def _coerce_datafog_result(result: Any) -> Optional[str]:
    """Normalize DataFog output into a string when possible."""
    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        for key in ("text", "redacted_text", "output", "result"):
            value = result.get(key)
            if isinstance(value, str):
                return value

    if isinstance(result, (list, tuple)) and result:
        for value in result:
            if isinstance(value, str):
                return value

    return None


def _invoke_datafog_function(
    fn: Callable[..., Any],
    value: str,
    mode: str,
    entity_types: Optional[list[str]],
) -> str:
    """Call a DataFog function with best-effort argument shapes."""
    attempts = [
        {"text": value, "mode": mode, "entity_types": entity_types},
        {"text": value, "mode": mode, "entities": entity_types},
        {"text": value, "mode": mode},
        {"text": value, "entity_types": entity_types},
        {"text": value, "entities": entity_types},
        {"text": value},
        {"text": value, "mode": mode, "regex": False},
        {"text": value, "entity_types": entity_types, "mode": mode},
        {"value": value, "mode": mode, "entity_types": entity_types},
        {"input_text": value, "mode": mode, "entity_types": entity_types},
        {"text_to_scan": value, "mode": mode, "entity_types": entity_types},
    ]

    # Try explicit keyword signatures first, then positional.
    for kwargs in attempts:
        if "entity_types" in kwargs and kwargs["entity_types"] is None:
            kwargs = dict(kwargs)
            kwargs.pop("entity_types")
        if "entities" in kwargs and kwargs["entities"] is None:
            kwargs = dict(kwargs)
            kwargs.pop("entities")

        try:
            result = fn(**kwargs)
        except TypeError:
            continue

        redacted = _coerce_datafog_result(result)
        if redacted is not None:
            return redacted

    # Last fallback: positional only call
    try:
        redacted = _coerce_datafog_result(fn(value))
        if redacted is not None:
            return redacted
    except TypeError:
        pass

    raise DataFogConfigurationError(
        f"DataFog function {fn.__name__} returned an unsupported payload shape."
    )


def _build_datafog_redactor_function(mode: str, entity_types: Optional[list[str]]) -> Callable[[str], str]:
    """Build a callable that redacts a text input with DataFog."""
    try:
        import datafog  # type: ignore
    except ImportError as exc:
        raise DataFogConfigurationError(
            "DataFog is not installed. Install with `pip install datafog` to enable "
            "RunRecorder DataFog mode."
        ) from exc

    # Try the stable helper names used by DataFog.
    candidates = []
    for name in ("sanitize", "scan_text", "anonymize_text", "process"):
        if hasattr(datafog, name):
            candidates.append(getattr(datafog, name))

    # Optional guardrail API: may return a callable.
    if hasattr(datafog, "create_guardrail"):
        guardrail_factory = getattr(datafog, "create_guardrail")
        for kwargs in (
            {"entity_types": entity_types, "mode": mode},
            {"entities": entity_types, "mode": mode},
            {"mode": mode, "entity_types": entity_types},
            {"mode": mode},
            {"entity_types": entity_types},
            {},
        ):
            if "entity_types" in kwargs and kwargs["entity_types"] is None:
                kwargs = dict(kwargs)
                kwargs.pop("entity_types")
            if "entities" in kwargs and kwargs["entities"] is None:
                kwargs = dict(kwargs)
                kwargs.pop("entities")

            try:
                guardrail = guardrail_factory(**kwargs)
            except TypeError:
                continue

            if callable(guardrail):
                candidates.insert(0, guardrail)
                break

    if not candidates:
        raise DataFogConfigurationError(
            "No usable DataFog redaction API was found (sanitize/scan_text/anonymize_text/process)."
        )

    candidate_signature_seen = set()
    for candidate in candidates:
        try:
            candidate_signature = inspect.signature(candidate)
        except (TypeError, ValueError):
            candidate_signature = id(candidate)
        else:
            if candidate_signature in candidate_signature_seen:
                continue

        candidate_signature_seen.add(candidate_signature)

        try:
            # Smoke-test signature by redacting an empty string.
            def redactor(value: str, _candidate=candidate) -> str:
                return _invoke_datafog_function(_candidate, value, mode, entity_types)

            redactor("")
            return redactor
        except Exception:
            continue

    raise DataFogConfigurationError(
        "Unable to invoke any DataFog API with supported argument signatures."
    )


def apply_datafog_redaction(payload: Any, redactor: Callable[[str], str]) -> Any:
    """Recursively apply string redaction to all string leaves in a payload."""
    if isinstance(payload, str):
        return redactor(payload)
    if isinstance(payload, list):
        return [apply_datafog_redaction(item, redactor) for item in payload]
    if isinstance(payload, tuple):
        return tuple(apply_datafog_redaction(item, redactor) for item in payload)
    if isinstance(payload, dict):
        return {
            key: apply_datafog_redaction(value, redactor)
            for key, value in payload.items()
        }

    return payload
