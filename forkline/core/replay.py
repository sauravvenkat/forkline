from __future__ import annotations

from typing import Optional

from .types import Run
from ..storage.store import SQLiteStore


def replay(run_id: str, store: Optional[SQLiteStore] = None) -> Optional[Run]:
    active_store = store or SQLiteStore()
    return active_store.load_run(run_id)
