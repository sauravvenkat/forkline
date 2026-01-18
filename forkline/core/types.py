from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Event:
    event_id: Optional[int]
    run_id: str
    step_idx: int
    type: str
    created_at: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Step:
    step_id: Optional[int]
    run_id: str
    idx: int
    name: str
    started_at: str
    ended_at: Optional[str] = None
    events: List[Event] = field(default_factory=list)


@dataclass(frozen=True)
class Run:
    run_id: str
    created_at: str
    steps: List[Step] = field(default_factory=list)
