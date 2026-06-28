"""Detached agent-run manager.

Keeps an agent/chat stream running server-side after the SSE client disconnects
(tab close, navigate away, refresh). The streaming generator is drained by a
background asyncio task into a per-session replay buffer; SSE clients SUBSCRIBE
to that buffer (replay everything so far, then live). Closing the SSE only drops
the subscriber — the drain task keeps going.

The wrapped generator already persists the assistant message to the session on
completion, so reopening the session shows the finished result even if nobody
was connected when it finished. Reconnecting mid-run replays the buffer + streams
live (pick up where it is).

Durability scope: in-memory, survives as long as the server process runs (tab
close / navigation / refresh). It does NOT survive a server restart.
"""
import asyncio
import json
import logging
import os
import time
from typing import AsyncGenerator, Dict, Optional

from src import agent_run_records

logger = logging.getLogger(__name__)


class _Run:
    __slots__ = (
        "buffer", "subscribers", "status", "task", "evict_task",
        "watchdog_task", "started_at", "last_event_at", "stop_reason",
        "record_id", "error",
    )

    def __init__(self) -> None:
        self.buffer: list = []          # ordered SSE event strings (replay log)
        self.subscribers: set = set()   # one asyncio.Queue per connected client
        self.status: str = "running"    # running | done | error | stopped
        self.task: Optional[asyncio.Task] = None
        self.evict_task: Optional[asyncio.Task] = None
        self.watchdog_task: Optional[asyncio.Task] = None
        now = time.monotonic()
        self.started_at: float = now
        self.last_event_at: float = now
        self.stop_reason: str = ""
        self.record_id: str = ""
        self.error: str = ""


_RUNS: Dict[str, _Run] = {}

# How long a FINISHED run (and its full replay buffer) is retained after the
# last subscriber disconnects, so a reconnect within the window can still
# replay the result. After this, the run is evicted to bound memory — without
# it, every session that ever streamed kept its entire event log forever.
_EVICT_GRACE_S = 180
_WATCHDOG_POLL_S = 5.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default) or default)
    except (TypeError, ValueError):
        return default


# 0 disables either cap. Long-running tools normally emit progress heartbeats,
# so the idle timeout catches silent/wedged streams while allowing active work.
_AGENT_RUN_MAX_WALL_S = _env_int("ODYSSEUS_AGENT_RUN_MAX_WALL_SECONDS", 7200)
_AGENT_RUN_IDLE_TIMEOUT_S = _env_int("ODYSSEUS_AGENT_RUN_IDLE_TIMEOUT_SECONDS", 900)


def _publish(run: _Run, ev: str) -> None:
    """Append one SSE event and fan it out to every live subscriber."""
    run.last_event_at = time.monotonic()
    run.buffer.append(ev)
    seq = len(run.buffer) - 1
    for q in list(run.subscribers):
        try:
            q.put_nowait((seq, ev))
        except Exception:
            pass


def _last_event_type(ev: str) -> str:
    if not ev:
        return ""
    if ev.startswith("event: "):
        return ev.splitlines()[0].replace("event:", "", 1).strip()
    if ev.strip() == "data: [DONE]":
        return "done"
    if ev.startswith("data: "):
        payload = ev[6:].strip()
        try:
            data = json.loads(payload)
        except Exception:
            return "data"
        if isinstance(data, dict):
            if data.get("type"):
                return str(data.get("type"))
            if "delta" in data:
                return "delta"
        return "data"
    return ""


def _schedule_evict(session_id: str) -> None:
    """(Re)arm a grace-period eviction for a terminal run with no subscribers.
    Identity-checked so a run that gets replaced/reused is never evicted by a
    stale timer."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    if run.evict_task and not run.evict_task.done():
        run.evict_task.cancel()

    async def _evict(run_ref: _Run) -> None:
        try:
            await asyncio.sleep(_EVICT_GRACE_S)
        except asyncio.CancelledError:
            return
        cur = _RUNS.get(session_id)
        if cur is run_ref and cur.status != "running" and not cur.subscribers:
            _RUNS.pop(session_id, None)

    run.evict_task = asyncio.create_task(_evict(run))


async def _watchdog(session_id: str, run_ref: _Run) -> None:
    """Cancel a detached run that exceeds wall-clock or no-progress limits."""
    while True:
        try:
            await asyncio.sleep(_WATCHDOG_POLL_S)
        except asyncio.CancelledError:
            return
        cur = _RUNS.get(session_id)
        if cur is not run_ref or cur.status != "running":
            return

        now = time.monotonic()
        reason = ""
        if _AGENT_RUN_MAX_WALL_S > 0 and (now - cur.started_at) >= _AGENT_RUN_MAX_WALL_S:
            reason = "wall_clock_timeout"
        elif _AGENT_RUN_IDLE_TIMEOUT_S > 0 and (now - cur.last_event_at) >= _AGENT_RUN_IDLE_TIMEOUT_S:
            reason = "idle_timeout"
        if not reason:
            continue

        cur.stop_reason = reason
        logger.warning("[agent-run] %s stopped by %s watchdog", session_id, reason)
        _publish(
            cur,
            "data: "
            + json.dumps({
                "type": "run_timeout",
                "reason": reason,
                "message": (
                    "Agent run stopped by the server watchdog after exceeding "
                    "the wall-clock limit." if reason == "wall_clock_timeout"
                    else "Agent run stopped by the server watchdog after no stream progress."
                ),
                "wall_seconds": round(now - cur.started_at, 3),
                "idle_seconds": round(now - cur.last_event_at, 3),
            })
            + "\n\n",
        )
        if cur.task and not cur.task.done():
            cur.task.cancel()
        return


def is_active(session_id: str) -> bool:
    r = _RUNS.get(session_id)
    return bool(r and r.status == "running")


def get_status(session_id: str) -> Optional[str]:
    r = _RUNS.get(session_id)
    return r.status if r else None


def get_stop_reason(session_id: str) -> str:
    r = _RUNS.get(session_id)
    return r.stop_reason if r else ""


async def _drain(session_id: str, agen: AsyncGenerator[str, None],
                 prev_task: Optional[asyncio.Task] = None) -> None:
    """Pull every event from the wrapped generator into the run buffer, fanning
    each out to live subscribers. Runs to completion regardless of subscribers."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    # If this run replaced an in-flight one (rapid double-send), wait for that
    # one to fully finish first. Its CancelledError handler calls aclose(), which
    # persists its partial response — letting it complete before we start writing
    # keeps the two runs' session saves sequential instead of interleaved.
    if prev_task is not None and not prev_task.done():
        try:
            await asyncio.wait({prev_task})
        except asyncio.CancelledError:
            raise            # our own cancellation — propagate
        except Exception:
            pass
    try:
        async for ev in agen:
            _publish(run, ev)
        if run.status == "running":
            run.status = "done"
    except asyncio.CancelledError:
        run.status = "stopped"
        # Let the wrapped generator's own CancelledError handler run (it saves
        # the partial response to the session).
        try:
            await agen.aclose()
        except Exception:
            pass
    except Exception as e:
        logger.error("[agent-run] %s failed: %s", session_id, e, exc_info=True)
        run.status = "error"
        run.stop_reason = "error"
        run.error = str(e)
        _publish(
            run,
            "event: error\n"
            f"data: {json.dumps({'error': 'Agent run failed before completion.', 'status': 500})}\n\n",
        )
        _publish(run, "data: [DONE]\n\n")
    finally:
        if run.record_id:
            agent_run_records.finish(
                run.record_id,
                status=run.status,
                stop_reason=run.stop_reason,
                error=run.error,
                event_count=len(run.buffer),
                last_event_type=_last_event_type(run.buffer[-1]) if run.buffer else "",
            )
        if run.watchdog_task and not run.watchdog_task.done():
            run.watchdog_task.cancel()
        # Wake every subscriber with the end sentinel so their SSE closes.
        for q in list(run.subscribers):
            try:
                q.put_nowait((None, None))
            except Exception:
                pass
        # Run is terminal — arm the grace timer so it (and its buffer) is
        # eventually freed even if nobody ever reconnects. subscribe() cancels
        # this on connect and re-arms on disconnect.
        _schedule_evict(session_id)


def start(session_id: str, agen: AsyncGenerator[str, None], *, record_id: str = "") -> _Run:
    """Start a detached run draining `agen` for a session. If a run is already in
    flight for this session (e.g. a rapid double-send), it's cancelled first."""
    prev = _RUNS.get(session_id)
    prev_task: Optional[asyncio.Task] = None
    if prev:
        if prev.task and not prev.task.done():
            prev.stop_reason = "replaced"
            prev.task.cancel()
            prev_task = prev.task   # new run awaits this before it starts writing
        if prev.evict_task and not prev.evict_task.done():
            prev.evict_task.cancel()
        if prev.watchdog_task and not prev.watchdog_task.done():
            prev.watchdog_task.cancel()
    run = _Run()
    run.record_id = record_id or ""
    _RUNS[session_id] = run
    run.task = asyncio.create_task(_drain(session_id, agen, prev_task))
    run.watchdog_task = asyncio.create_task(_watchdog(session_id, run))
    return run


async def subscribe(session_id: str) -> AsyncGenerator[str, None]:
    """Replay the run's buffer from the start, then stream live until it ends.
    Safe to call repeatedly (reconnect) and from multiple clients at once."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    q: asyncio.Queue = asyncio.Queue()
    run.subscribers.add(q)            # register BEFORE replaying so nothing is missed
    # A live subscriber is connected — don't let a pending grace timer evict
    # the run out from under it mid-replay.
    if run.evict_task and not run.evict_task.done():
        run.evict_task.cancel()
    try:
        next_seq = 0
        while next_seq < len(run.buffer):
            yield run.buffer[next_seq]
            next_seq += 1
        if run.status != "running":
            return
        heartbeat_idx = 0
        while True:
            try:
                seq, ev = await asyncio.wait_for(q.get(), timeout=10.0)
            except asyncio.TimeoutError:
                # Keep slow local models/proxies alive while they prefill before
                # the first token. SSE comments are ignored by the UI but reset
                # browser/proxy idle timers, which prevents "empty response"
                # disconnects on llama.cpp first-token latencies of 30s+.
                if run.status == "running":
                    heartbeat_idx += 1
                    yield f": heartbeat {heartbeat_idx}\n\n"
                    continue
                seq, ev = (None, None)
            if seq is None:            # end sentinel
                while next_seq < len(run.buffer):   # flush any tail the sentinel raced
                    yield run.buffer[next_seq]
                    next_seq += 1
                break
            if seq >= next_seq:        # skip events already replayed from the buffer
                yield ev
                next_seq = seq + 1
    finally:
        run.subscribers.discard(q)
        # Last subscriber gone on a finished run — (re)arm eviction so the
        # buffer doesn't linger indefinitely.
        if not run.subscribers and run.status != "running":
            _schedule_evict(session_id)


def stop(session_id: str) -> bool:
    """Cancel an in-flight run (the wrapped generator saves its partial)."""
    run = _RUNS.get(session_id)
    if run and run.task and not run.task.done():
        run.stop_reason = "user_stop"
        run.task.cancel()
        return True
    return False
