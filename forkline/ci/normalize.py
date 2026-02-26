"""Artifact normalization for deterministic CI diffs.

Normalizes unstable fields (timestamps, UUIDs, platform info) so that
artifacts recorded at different times or on different machines produce
identical diffs when their behavior is the same.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Set

TIMESTAMP_FIELDS: Set[str] = frozenset({
    "ts", "started_at", "ended_at", "created_at", "timestamp",
})

UNSTABLE_METADATA_FIELDS: Set[str] = frozenset({
    "python_version", "platform", "cwd",
})

NORMALIZED_TIMESTAMP = "2000-01-01T00:00:00+00:00"


def normalize_artifact(
    artifact: Dict[str, Any],
    *,
    normalize_timestamps: bool = True,
    normalize_metadata: bool = True,
    normalize_ids: bool = False,
) -> Dict[str, Any]:
    """Normalize an artifact dict for deterministic comparison.

    Args:
        artifact: Raw artifact dict (not mutated).
        normalize_timestamps: Replace all timestamp values with a stable sentinel.
        normalize_metadata: Remove platform-specific metadata fields.
        normalize_ids: Replace run_id and event_ids with stable sequential values.

    Returns:
        A new dict with unstable fields normalized.
    """
    result = copy.deepcopy(artifact)

    if normalize_timestamps:
        _normalize_timestamps_inplace(result)

    if normalize_metadata and "metadata" in result:
        for field in UNSTABLE_METADATA_FIELDS:
            result["metadata"].pop(field, None)
        if not result["metadata"]:
            result.pop("metadata", None)

    if normalize_ids:
        result = _normalize_ids(result)

    if "events" in result:
        result["events"] = _normalize_event_ordering(result["events"])

    return result


def normalize_artifact_json(
    json_str: str,
    *,
    normalize_timestamps: bool = True,
    normalize_metadata: bool = True,
    normalize_ids: bool = False,
) -> str:
    """Normalize a JSON artifact string, returning normalized JSON."""
    artifact = json.loads(json_str)
    normalized = normalize_artifact(
        artifact,
        normalize_timestamps=normalize_timestamps,
        normalize_metadata=normalize_metadata,
        normalize_ids=normalize_ids,
    )
    return json.dumps(normalized, indent=2, sort_keys=True, default=str)


def _normalize_timestamps_inplace(obj: Any) -> None:
    """Recursively replace timestamp fields with a stable sentinel."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in TIMESTAMP_FIELDS and isinstance(obj[key], str):
                obj[key] = NORMALIZED_TIMESTAMP
            else:
                _normalize_timestamps_inplace(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _normalize_timestamps_inplace(item)


def _normalize_ids(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Replace run_id and event_ids with stable sequential values."""
    result = copy.deepcopy(artifact)
    result["run_id"] = "normalized-run-id"

    events = result.get("events", [])
    for idx, event in enumerate(events):
        event["event_id"] = idx
        event["run_id"] = "normalized-run-id"

    return result


def _normalize_event_ordering(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure event ordering is stable (already by event_id in practice)."""
    return sorted(events, key=lambda e: e.get("event_id", 0))
