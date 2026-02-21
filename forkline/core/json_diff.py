"""Deterministic JSON diff patch generation for Forkline.

Produces a stable, ordered list of diff operations for JSON-like values.

Ordering guarantees:
- dict: removed keys (sorted), then added keys (sorted), then common keys (sorted, recursed)
- list: by index; removes at tail, then adds at tail
- Type mismatch: replace whole node
- int vs float treated as compatible numeric type

Output patch format:
    [{"op":"remove","path":"$.a.b","old":...},
     {"op":"add","path":"$.x","value":...},
     {"op":"replace","path":"$.k","old":...,"new":...}]
"""
from __future__ import annotations

from typing import Any, Dict, List


def json_diff(old: Any, new: Any, path: str = "$") -> List[Dict[str, Any]]:
    """Produce a deterministic JSON diff patch between two values."""
    ops: List[Dict[str, Any]] = []

    if old is None and new is None:
        return ops

    if type(old) is not type(new):
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            if old != new:
                ops.append({"op": "replace", "path": path, "old": old, "new": new})
            return ops
        ops.append({"op": "replace", "path": path, "old": old, "new": new})
        return ops

    if isinstance(old, dict):
        old_keys = set(old.keys())
        new_keys = set(new.keys())
        for k in sorted(old_keys - new_keys):
            ops.append({"op": "remove", "path": f"{path}.{k}", "old": old[k]})
        for k in sorted(new_keys - old_keys):
            ops.append({"op": "add", "path": f"{path}.{k}", "value": new[k]})
        for k in sorted(old_keys & new_keys):
            ops.extend(json_diff(old[k], new[k], f"{path}.{k}"))
        return ops

    if isinstance(old, list):
        min_len = min(len(old), len(new))
        for i in range(min_len):
            ops.extend(json_diff(old[i], new[i], f"{path}[{i}]"))
        if len(old) > len(new):
            for i in range(len(new), len(old)):
                ops.append({"op": "remove", "path": f"{path}[{i}]", "old": old[i]})
        elif len(new) > len(old):
            for i in range(len(old), len(new)):
                ops.append({"op": "add", "path": f"{path}[{i}]", "value": new[i]})
        return ops

    if old != new:
        ops.append({"op": "replace", "path": path, "old": old, "new": new})
    return ops
