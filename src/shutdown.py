"""Process-wide shutdown signalling for graceful restarts.

Server-Sent-Events / streaming responses never complete on their own, so
uvicorn's graceful shutdown would wait for an open browser stream up to the
full ``timeout_graceful_shutdown`` window before exiting. A SIGTERM/SIGINT
handler flips this flag and tells uvicorn to exit; every streaming generator
checks the flag on each loop and ends promptly, so a restart completes in
seconds while ordinary in-flight requests still finish.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_SHUTTING_DOWN = threading.Event()


def is_shutting_down() -> bool:
    """True once a shutdown (SIGTERM/SIGINT) has been requested."""
    return _SHUTTING_DOWN.is_set()


def request_shutdown() -> None:
    """Mark the process as shutting down (idempotent)."""
    if not _SHUTTING_DOWN.is_set():
        _SHUTTING_DOWN.set()
        logger.info("shutdown requested; streaming responses will end promptly")


def reset() -> None:
    """Clear the flag (for tests)."""
    _SHUTTING_DOWN.clear()
