from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .types import Run


@dataclass(frozen=True)
class DiffResult:
    same: bool
    notes: List[str]


def diff_runs(left: Run, right: Run) -> DiffResult:
    notes: List[str] = []
    if len(left.steps) != len(right.steps):
        notes.append("step_count_mismatch")
    for left_step, right_step in zip(left.steps, right.steps):
        if left_step.name != right_step.name:
            notes.append(f"step_name_mismatch:{left_step.name}:{right_step.name}")
        if len(left_step.events) != len(right_step.events):
            notes.append(f"event_count_mismatch:{left_step.step_id}")
    return DiffResult(same=not notes, notes=notes)
