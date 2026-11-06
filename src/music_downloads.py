"""Descargas Soulseek: el servidor sondea slskd y emite un evento SSE al terminar.

Un task por descarga, con tope de 15 min. El navegador no sondea: solo escucha
/api/music/events, donde llegan los eventos de tipo "download".
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Set

from services.music import slskd_source

logger = logging.getLogger(__name__)

POLL_SEC = 5.0
MAX_SEC = 15 * 60

_subscribers: Set[asyncio.Queue] = set()
_tasks: Set[asyncio.Task] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _emit(evt: Dict[str, Any]) -> None:
    for q in list(_subscribers):
        q.put_nowait(evt)


def publish(evt: Dict[str, Any]) -> None:
    """Emite un evento de descarga a todos los suscriptores de /api/music/events."""
    _emit(evt)


def _find_state(transfers: Any, username: str, filename: str) -> Optional[str]:
    for user in transfers or []:
        if user.get("username") != username:
            continue
        for d in user.get("directories") or []:
            for f in d.get("files") or []:
                if f.get("filename") == filename:
                    return f.get("state") or ""
    return None


async def _watch(username: str, filename: str) -> None:
    state = "failed"
    deadline = time.monotonic() + MAX_SEC
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_SEC)
            try:
                transfers = await asyncio.to_thread(slskd_source._api, "GET", "/api/v0/transfers/downloads")
            except Exception as exc:
                logger.debug("slskd transfers poll failed: %s", exc)
                continue
            st = _find_state(transfers, username, filename)
            if st and st.startswith("Completed"):
                state = "done" if "Succeeded" in st else "failed"
                break
    finally:
        _emit({"type": "download", "state": state, "username": username, "file": filename})


def start(username: str, filename: str) -> None:
    task = asyncio.get_running_loop().create_task(_watch(username, filename))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
