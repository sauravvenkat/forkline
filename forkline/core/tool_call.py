"""
Tool call instrumentation for Forkline.

Records tool invocations (request + response + error + timing) as first-class
events in the run artifact. Uses Option 1: single event per tool call containing
both request and response fields.

Design:
- Single ``tool_call`` event per invocation (not split into request/response)
- Deterministic invocation IDs (UUID4 hex, generated once per call)
- Timing captured via monotonic clock for duration, wall clock for timestamps
- Integrates with RunRecorder for automatic redaction at storage boundary

Usage as context manager::

    with ToolCallRecorder(recorder, run_id, "http.request") as tc:
        tc.set_request({"url": url, "headers": headers})
        response = requests.get(url, headers=headers)
        tc.set_response({"status": response.status_code, "body": response.text})

Usage as decorator::

    @record_tool_call(recorder, run_id, "bigquery.query")
    def run_query(sql):
        return client.query(sql).result()

Replay integration:
    During replay, ToolCallRecorder checks replay mode and can substitute
    recorded responses instead of executing live calls.
"""

from __future__ import annotations

import functools
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, TypeVar

from ..core.replay import guard_live_call

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class ToolCallTiming:
    """Timing information for a tool invocation."""

    started_at: str = ""
    ended_at: str = ""
    duration_ms: float = 0.0


@dataclass
class ToolCallPayload:
    """
    Canonical payload structure for a tool_call event.

    This is the single-event representation (Option 1): one event
    captures the full lifecycle of a tool invocation.
    """

    tool_name: str
    invocation_id: str
    request: Dict[str, Any] = field(default_factory=dict)
    response: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    timing: ToolCallTiming = field(default_factory=ToolCallTiming)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict. Omits None fields."""
        result: Dict[str, Any] = {
            "tool_name": self.tool_name,
            "invocation_id": self.invocation_id,
            "request": self.request,
            "timing": {
                "started_at": self.timing.started_at,
                "ended_at": self.timing.ended_at,
                "duration_ms": self.timing.duration_ms,
            },
        }
        if self.response is not None:
            result["response"] = self.response
        if self.error is not None:
            result["error"] = self.error
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCallPayload":
        """Reconstruct from a dict, ignoring unknown fields."""
        timing_data = data.get("timing", {})
        return cls(
            tool_name=data.get("tool_name", ""),
            invocation_id=data.get("invocation_id", ""),
            request=data.get("request", {}),
            response=data.get("response"),
            error=data.get("error"),
            timing=ToolCallTiming(
                started_at=timing_data.get("started_at", ""),
                ended_at=timing_data.get("ended_at", ""),
                duration_ms=timing_data.get("duration_ms", 0.0),
            ),
            metadata=data.get("metadata", {}),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolCallRecorder:
    """
    Context manager that records a tool invocation as a single event.

    Captures timing automatically. Handles errors gracefully by recording
    the error payload instead of the response. The event is written to the
    recorder on exit, after redaction is applied at the storage boundary.

    In replay mode, raises DeterminismViolationError on __enter__ unless
    ``allow_in_replay=True`` is set (for re-exec mode).

    Example::

        with ToolCallRecorder(recorder, run_id, "http.request") as tc:
            tc.set_request({"url": "https://api.example.com", "method": "GET"})
            resp = requests.get("https://api.example.com")
            tc.set_response({"status": resp.status_code, "body": resp.text})
            tc.set_metadata({"bytes_read": len(resp.content)})
    """

    def __init__(
        self,
        recorder: Any,
        run_id: str,
        tool_name: str,
        *,
        allow_in_replay: bool = False,
    ):
        self._recorder = recorder
        self._run_id = run_id
        self._tool_name = tool_name
        self._allow_in_replay = allow_in_replay
        self._invocation_id = uuid.uuid4().hex
        self._request: Dict[str, Any] = {}
        self._response: Optional[Dict[str, Any]] = None
        self._error: Optional[Dict[str, Any]] = None
        self._metadata: Dict[str, Any] = {}
        self._start_time: float = 0.0
        self._started_at: str = ""
        self._ended_at: str = ""

    def set_request(self, request: Dict[str, Any]) -> None:
        self._request = request

    def set_response(self, response: Dict[str, Any]) -> None:
        self._response = response

    def set_error(self, error: Dict[str, Any]) -> None:
        self._error = error

    def set_metadata(self, metadata: Dict[str, Any]) -> None:
        self._metadata.update(metadata)

    def __enter__(self) -> "ToolCallRecorder":
        if not self._allow_in_replay:
            guard_live_call(f"tool call: {self._tool_name}")
        self._started_at = _utc_now()
        self._start_time = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        end_time = time.monotonic()
        self._ended_at = _utc_now()
        duration_ms = round((end_time - self._start_time) * 1000, 3)

        if exc_val is not None and self._error is None:
            self._error = {
                "type": type(exc_val).__name__,
                "message": str(exc_val),
            }

        payload = ToolCallPayload(
            tool_name=self._tool_name,
            invocation_id=self._invocation_id,
            request=self._request,
            response=self._response,
            error=self._error,
            timing=ToolCallTiming(
                started_at=self._started_at,
                ended_at=self._ended_at,
                duration_ms=duration_ms,
            ),
            metadata=self._metadata,
        )

        self._recorder.log_event(self._run_id, "tool_call", payload.to_dict())

        # Don't suppress exceptions
        return None


def record_tool_call(
    recorder: Any,
    run_id: str,
    tool_name: str,
    *,
    allow_in_replay: bool = False,
) -> Callable[[F], F]:
    """
    Decorator that records a function call as a tool_call event.

    The decorated function's kwargs become the request payload.
    The return value becomes the response payload (wrapped in {"result": ...}
    if it's not already a dict).

    Args:
        recorder: RunRecorder instance
        run_id: Current run ID
        tool_name: Tool name (e.g., "bigquery.query", "http.request")
        allow_in_replay: If True, allow execution during replay mode

    Example::

        @record_tool_call(recorder, run_id, "db.query")
        def query_db(sql, params=None):
            return db.execute(sql, params)
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with ToolCallRecorder(
                recorder,
                run_id,
                tool_name,
                allow_in_replay=allow_in_replay,
            ) as tc:
                tc.set_request(
                    {"args": list(args), "kwargs": kwargs} if args else kwargs
                )
                result = fn(*args, **kwargs)
                if isinstance(result, dict):
                    tc.set_response(result)
                else:
                    tc.set_response({"result": result})
                return result

        return wrapper  # type: ignore[return-value]

    return decorator
