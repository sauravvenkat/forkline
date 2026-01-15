from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .store import SQLiteStore
from .types import Event


@dataclass
class Tracer:
    store: SQLiteStore = field(default_factory=SQLiteStore)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _active_step_idx: Optional[int] = field(default=None, init=False, repr=False)
    _next_step_idx: int = field(default=0, init=False, repr=False)

    def __enter__(self) -> "Tracer":
        self.store.start_run(self.run_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def step(self, name: str) -> "StepScope":
        return StepScope(tracer=self, name=name)

    def record_event(
        self, name: str, payload: Optional[Dict[str, Any]] = None
    ) -> Event:
        if self._active_step_idx is None:
            raise RuntimeError("No active step. Use tracer.step(...) context.")
        payload_dict = payload or {}
        return self.store.append_event(
            run_id=self.run_id,
            step_idx=self._active_step_idx,
            type=name,
            payload_dict=payload_dict,
        )


@dataclass
class StepScope:
    tracer: Tracer
    name: str
    idx: int = field(default=0)
    _previous_step_idx: Optional[int] = field(default=None, init=False, repr=False)

    def __enter__(self) -> "StepScope":
        self._previous_step_idx = self.tracer._active_step_idx
        self.idx = self.tracer._next_step_idx
        self.tracer._next_step_idx += 1
        self.tracer._active_step_idx = self.idx
        self.tracer.store.start_step(self.tracer.run_id, self.idx, self.name)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.tracer.store.end_step(self.tracer.run_id, self.idx)
        self.tracer._active_step_idx = self._previous_step_idx
