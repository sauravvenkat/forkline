"""Hard offline enforcement for CI runs.

Monkeypatches socket, urllib, requests, and httpx to fail immediately
with a clear, deterministic error when network access is attempted.

The "fail closed" approach: if any code path touches network, it gets
a ForklineOfflineError instead of hanging or timing out.
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Any, Generator


class ForklineOfflineError(Exception):
    """Raised when network access is attempted in offline mode.

    This error is deterministic: same call always produces the same error,
    with the same message, regardless of environment or timing.
    """

    def __init__(self, operation: str = "network access"):
        super().__init__(
            f"Network access blocked: {operation}. "
            f"Forkline is running in offline mode (FORKLINE_OFFLINE=1). "
            f"All network calls are forbidden to ensure deterministic replay."
        )
        self.operation = operation


_original_socket_connect = socket.socket.connect
_original_create_connection = socket.create_connection
_original_getaddrinfo = socket.getaddrinfo
_offline_active = False


def _blocked_connect(self: Any, *args: Any, **kwargs: Any) -> None:
    address = args[0] if args else "unknown"
    raise ForklineOfflineError(f"socket.connect({address})")


def _blocked_create_connection(*args: Any, **kwargs: Any) -> None:
    address = args[0] if args else "unknown"
    raise ForklineOfflineError(f"socket.create_connection({address})")


def _blocked_getaddrinfo(*args: Any, **kwargs: Any) -> list:
    host = args[0] if args else "unknown"
    raise ForklineOfflineError(f"socket.getaddrinfo({host})")


def enable_offline_mode() -> None:
    """Activate offline mode globally.

    After calling this, any attempt to open a network connection
    raises ForklineOfflineError immediately.
    """
    global _offline_active
    if _offline_active:
        return
    socket.socket.connect = _blocked_connect  # type: ignore[assignment]
    socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = _blocked_getaddrinfo  # type: ignore[assignment]
    _offline_active = True


def disable_offline_mode() -> None:
    """Restore normal network access."""
    global _offline_active
    if not _offline_active:
        return
    socket.socket.connect = _original_socket_connect  # type: ignore[assignment]
    socket.create_connection = _original_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = _original_getaddrinfo  # type: ignore[assignment]
    _offline_active = False


def is_offline_mode() -> bool:
    """Check if offline mode is currently active."""
    return _offline_active


@contextmanager
def offline_context() -> Generator[None, None, None]:
    """Context manager that enforces offline mode for its duration."""
    enable_offline_mode()
    try:
        yield
    finally:
        disable_offline_mode()
