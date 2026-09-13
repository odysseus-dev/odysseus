"""Unified agent activity feed — what every agent is doing, in one timeline.

Odysseus runs work through several unrelated mechanisms: the primary agent
loop (chat tool calls), delegated Claude Code jobs (a subprocess), AI-to-AI
sessions (``send_to_session`` / ``pipeline``), detached background shell jobs,
and the worktree publish flow. Each one reports progress somewhere different
(the chat SSE stream, ``claude_code_tasks.json``, a message written into
another session, ``bg_jobs.json``, an approval file), so nothing could show the
operator "what is happening right now, across all of it".

This module is the one place they all report to:

* :func:`publish` records an **event** — who (``source``), what (``kind``), a
  one-line ``title`` and bounded ``detail``/``data`` — against the chat session
  the work belongs to, and fans it out to live subscribers.
* :func:`subscribe` is the server-sent-events feed the Workbench panel reads:
  replay from a sequence number, then live, with heartbeats.
* :func:`history` and :func:`list_runs` back the REST views (a page reload
  must show the same timeline the live stream showed).

Events are bounded and persisted as one JSONL file per session under
``<data>/agent_activity/`` so a restart keeps the timeline, and a run that
finished while nobody was watching is still there in the morning. Nothing
here is a security boundary: the routes that expose the feed apply the admin
gate; this module only stores what callers give it, clamped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from typing import Any, AsyncGenerator, Deque, Dict, Iterable, List, Optional, Set, Tuple

from src import constants

logger = logging.getLogger(__name__)

GLOBAL_FEED = "*"

# Who an event comes from. `odysseus` is the primary agent of the session
# itself; the rest are things it started.
SOURCES = ("odysseus", "claude_code", "session", "pipeline", "bg_job", "worktree", "system")
# What happened. `run_started`/`run_finished` bracket one unit of delegated
# work (a Claude Code task, a sub-session exchange, a shell job, a chat turn)
# and are what the run registry is built from.
KINDS = (
    "run_started", "run_finished", "message", "tool_start", "tool_result",
    "file_change", "commit", "status", "error", "note",
)

MAX_EVENTS_PER_SESSION = 500
MAX_RUNS = 300
MAX_TITLE_CHARS = 300
MAX_DETAIL_CHARS = 4000
MAX_DATA_CHARS = 12000
# Per-session JSONL is truncated to its newest half once it passes this, so
# a chatty coding session can never fill the data volume.
ROTATE_BYTES = 2_000_000
HEARTBEAT_S = 15.0

_lock = threading.RLock()
_events: Dict[str, Deque[dict]] = {}
_seq: Dict[str, int] = {}
_loaded: Set[str] = set()
_runs: Dict[str, dict] = {}
_runs_loaded = False
# session_id -> run_id of the chat turn currently executing there. A turn
# that ends without its own run_finished (client stop, generator closed,
# an exception) is closed by the run manager through `close_turn`.
_active_turns: Dict[str, str] = {}
# key -> {(queue, loop)}; a subscriber on GLOBAL_FEED sees every session.
_subscribers: Dict[str, Set[Tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = {}


# ── storage ──────────────────────────────────────────────────────────────────

def activity_dir() -> str:
    return os.path.join(constants.DATA_DIR, "agent_activity")


def _safe_name(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or "unknown"))[:80] or "unknown"


def _session_file(session_id: str) -> str:
    return os.path.join(activity_dir(), f"{_safe_name(session_id)}.jsonl")


def _runs_file() -> str:
    return os.path.join(activity_dir(), "runs.json")


def _clamp(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 15] + "\n…[truncated]"


def _clamp_data(data: Any) -> Any:
    """Keep `data` JSON-serialisable and bounded; drop it rather than explode."""
    if data is None:
        return None
    try:
        raw = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"repr": _clamp(repr(data), 500)}
    if len(raw) <= MAX_DATA_CHARS:
        return json.loads(raw)
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if isinstance(value, str):
                out[key] = _clamp(value, 1500)
            elif isinstance(value, (int, float, bool)) or value is None:
                out[key] = value
            else:
                out[key] = _clamp(json.dumps(value, ensure_ascii=False, default=str), 1500)
        return out
    return {"repr": _clamp(raw, 2000)}


def _load_session(session_id: str) -> None:
    """Pull the persisted tail of a session's timeline into memory once."""
    if session_id in _loaded:
        return
    _loaded.add(session_id)
    path = _session_file(session_id)
    buf: Deque[dict] = deque(maxlen=MAX_EVENTS_PER_SESSION)
    top = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and isinstance(ev.get("seq"), int):
                    buf.append(ev)
                    top = max(top, ev["seq"])
    except OSError:
        pass
    _events[session_id] = buf
    _seq[session_id] = max(_seq.get(session_id, 0), top)


def _append_line(session_id: str, ev: dict) -> None:
    path = _session_file(session_id)
    try:
        os.makedirs(activity_dir(), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        if os.path.getsize(path) > ROTATE_BYTES:
            _rotate(path)
    except OSError as exc:
        logger.debug("activity: could not persist event for %s: %s", session_id, exc)


def _rotate(path: str) -> None:
    """Keep the newest half of a JSONL file in place."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        keep = lines[len(lines) // 2:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("activity: rotate failed for %s: %s", path, exc)


def _load_runs() -> None:
    global _runs_loaded
    if _runs_loaded:
        return
    _runs_loaded = True
    try:
        with open(_runs_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for run_id, rec in data.items():
                if isinstance(rec, dict):
                    _runs[str(run_id)] = rec
    except (OSError, ValueError):
        pass
    # A run that was "running" when the process died never finished.
    for rec in _runs.values():
        if rec.get("status") == "running":
            rec["status"] = "interrupted"
            rec["finished_at"] = rec.get("finished_at") or time.time()


def _save_runs() -> None:
    try:
        from core.atomic_io import atomic_write_json
    except Exception:  # pragma: no cover - core is always importable in the app
        atomic_write_json = None
    if len(_runs) > MAX_RUNS:
        ordered = sorted(_runs.items(), key=lambda kv: kv[1].get("started_at") or 0)
        for run_id, _ in ordered[: len(_runs) - MAX_RUNS]:
            _runs.pop(run_id, None)
    try:
        os.makedirs(activity_dir(), exist_ok=True)
        if atomic_write_json:
            atomic_write_json(_runs_file(), _runs, indent=1)
        else:
            with open(_runs_file(), "w", encoding="utf-8") as fh:
                json.dump(_runs, fh)
    except OSError as exc:
        logger.debug("activity: could not persist runs: %s", exc)


# ── publishing ───────────────────────────────────────────────────────────────

def _fanout(key: str, ev: dict) -> None:
    for queue, loop in list(_subscribers.get(key, ())):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, ev)
        except RuntimeError:
            # The subscriber's loop is gone; drop it.
            _subscribers.get(key, set()).discard((queue, loop))


def publish(
    session_id: Optional[str],
    kind: str,
    title: str,
    *,
    source: str = "odysseus",
    detail: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    owner: Optional[str] = None,
    level: str = "info",
) -> Optional[dict]:
    """Record one event and hand it to live subscribers. Never raises.

    ``session_id`` is the chat session the work belongs to; events without a
    session are filed under ``"global"`` so nothing is lost. Returns the
    stored event (with its ``seq``) or None when the store refused it.
    """
    try:
        sid = str(session_id or "global")
        kind = kind if kind in KINDS else "note"
        source = source if source in SOURCES else "system"
        level = level if level in ("info", "warning", "error") else "info"
        with _lock:
            _load_session(sid)
            _load_runs()
            seq = _seq.get(sid, 0) + 1
            _seq[sid] = seq
            ev = {
                "id": uuid.uuid4().hex[:12],
                "seq": seq,
                "ts": time.time(),
                "session_id": sid,
                "run_id": str(run_id) if run_id else None,
                "source": source,
                "kind": kind,
                "level": level,
                "title": _clamp(title, MAX_TITLE_CHARS),
                "detail": _clamp(detail, MAX_DETAIL_CHARS) if detail else None,
                "data": _clamp_data(data),
                "owner": owner,
            }
            _events[sid].append(ev)
            _append_line(sid, ev)
            if run_id and kind in ("run_started", "run_finished", "status"):
                _update_run(ev)
            _fanout(sid, ev)
            _fanout(GLOBAL_FEED, ev)
        return ev
    except Exception as exc:  # observability must never break the work it observes
        logger.debug("activity: publish failed: %s", exc, exc_info=True)
        return None


def _update_run(ev: dict) -> None:
    run_id = ev["run_id"]
    rec = _runs.get(run_id)
    data = ev.get("data") or {}
    if ev["kind"] == "run_started" or rec is None:
        rec = {
            "run_id": run_id,
            "source": ev["source"],
            "session_id": ev["session_id"],
            "owner": ev.get("owner"),
            "title": ev["title"],
            "status": "running",
            "started_at": ev["ts"],
            "finished_at": None,
            "summary": {},
        }
        _runs[run_id] = rec
    if ev["kind"] == "run_started":
        rec["summary"] = {k: v for k, v in data.items() if k in _RUN_SUMMARY_KEYS}
    elif ev["kind"] == "run_finished":
        status = str(data.get("status") or ("failed" if ev.get("level") == "error" else "completed"))
        rec["status"] = status
        rec["finished_at"] = ev["ts"]
        rec["title"] = ev["title"] or rec["title"]
        rec["summary"].update({k: v for k, v in data.items() if k in _RUN_SUMMARY_KEYS})
    elif ev["kind"] == "status" and data.get("status"):
        rec["status"] = str(data["status"])
    _save_runs()


_RUN_SUMMARY_KEYS = frozenset({
    "repository", "branch", "commit", "model", "task_id", "target_session", "target_session_name",
    "changed_files", "changes", "commits", "num_turns", "total_cost_usd", "exit_code", "error",
    "job_id", "command", "steps", "pull_request", "request_id", "result_excerpt", "mode",
})


# ── run helpers (one unit of delegated work) ─────────────────────────────────

def new_run_id(source: str) -> str:
    return f"{source}-{uuid.uuid4().hex[:10]}"


def run_started(session_id: Optional[str], source: str, title: str, *, run_id: Optional[str] = None,
                owner: Optional[str] = None, data: Optional[Dict[str, Any]] = None,
                detail: Optional[str] = None) -> str:
    rid = run_id or new_run_id(source)
    publish(session_id, "run_started", title, source=source, run_id=rid, owner=owner, data=data, detail=detail)
    if source == "odysseus" and session_id:
        with _lock:
            _active_turns[str(session_id)] = rid
    return rid


def run_finished(session_id: Optional[str], source: str, run_id: str, title: str, *,
                 status: str = "completed", owner: Optional[str] = None,
                 data: Optional[Dict[str, Any]] = None, detail: Optional[str] = None) -> None:
    payload = dict(data or {})
    payload["status"] = status
    publish(session_id, "run_finished", title, source=source, run_id=run_id, owner=owner,
            data=payload, detail=detail, level="error" if status in ("failed", "error") else "info")
    if session_id:
        with _lock:
            if _active_turns.get(str(session_id)) == run_id:
                _active_turns.pop(str(session_id), None)


def active_turn(session_id: Optional[str]) -> Optional[str]:
    """The run id of the session's live chat turn, if one is in progress."""
    with _lock:
        return _active_turns.get(str(session_id or ""))


def close_turn(session_id: Optional[str], *, status: str = "completed", title: Optional[str] = None) -> bool:
    """Finish the session's live chat turn if it never reported its own end.

    Called by the detached run manager when a stream stops, fails, or drains
    without the loop reaching its final metrics — otherwise the Workbench
    would show that turn as running until the next restart. Returns whether a
    turn was closed."""
    with _lock:
        rid = _active_turns.get(str(session_id or ""))
    if not rid:
        return False
    rec = get_run(rid) or {}
    run_finished(session_id, "odysseus", rid, title or {"cancelled": "Turn stopped", "failed": "Turn failed"}.get(status, "Turn finished"),
                 status=status, owner=rec.get("owner"))
    return True


# ── reading ──────────────────────────────────────────────────────────────────

def history(session_id: str, *, since_seq: int = 0, limit: int = 200) -> List[dict]:
    sid = str(session_id or "global")
    with _lock:
        _load_session(sid)
        rows = [ev for ev in _events.get(sid, ()) if ev.get("seq", 0) > since_seq]
    limit = max(1, min(int(limit or 200), MAX_EVENTS_PER_SESSION))
    return rows[-limit:]


def last_seq(session_id: str) -> int:
    sid = str(session_id or "global")
    with _lock:
        _load_session(sid)
        return _seq.get(sid, 0)


def list_runs(*, owner: Optional[str] = None, session_id: Optional[str] = None,
              limit: int = 50, active_only: bool = False) -> List[dict]:
    with _lock:
        _load_runs()
        rows = list(_runs.values())
    if owner is not None:
        rows = [r for r in rows if r.get("owner") in (owner, None)]
    if session_id:
        rows = [r for r in rows if r.get("session_id") == session_id]
    if active_only:
        rows = [r for r in rows if r.get("status") == "running"]
    rows.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
    return [dict(r) for r in rows[: max(1, min(int(limit or 50), MAX_RUNS))]]


def get_run(run_id: str) -> Optional[dict]:
    with _lock:
        _load_runs()
        rec = _runs.get(run_id)
        return dict(rec) if rec else None


def run_events(run_id: str, *, limit: int = 300) -> List[dict]:
    rec = get_run(run_id)
    if not rec:
        return []
    rows = [ev for ev in history(rec["session_id"], limit=MAX_EVENTS_PER_SESSION) if ev.get("run_id") == run_id]
    return rows[-max(1, limit):]


def sessions_with_activity() -> List[str]:
    try:
        names = os.listdir(activity_dir())
    except OSError:
        return []
    return sorted(n[:-6] for n in names if n.endswith(".jsonl"))


# ── live feed ────────────────────────────────────────────────────────────────

def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


async def subscribe(session_id: str, *, since_seq: int = 0, owner: Optional[str] = None,
                    heartbeat_s: float = HEARTBEAT_S) -> AsyncGenerator[str, None]:
    """SSE frames: replay everything after ``since_seq``, then live.

    ``session_id`` may be :data:`GLOBAL_FEED` for every session; ``owner``
    then filters the live frames to that owner's events (events with no owner
    are shown, they come from unattributed system work).
    """
    key = str(session_id or "global")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    with _lock:
        _subscribers.setdefault(key, set()).add((queue, loop))
        replay = [] if key == GLOBAL_FEED else history(key, since_seq=since_seq, limit=MAX_EVENTS_PER_SESSION)
    seen = since_seq
    try:
        for ev in replay:
            seen = max(seen, ev.get("seq", 0))
            yield _sse(ev)
        yield f"data: {json.dumps({'type': 'ready', 'session_id': key, 'seq': seen})}\n\n"
        beat = 0
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except asyncio.TimeoutError:
                beat += 1
                yield f": heartbeat {beat}\n\n"
                continue
            if key != GLOBAL_FEED and ev.get("seq", 0) <= seen and ev.get("session_id") == key:
                continue  # already replayed
            if owner is not None and ev.get("owner") not in (owner, None):
                continue
            seen = max(seen, ev.get("seq", 0)) if ev.get("session_id") == key else seen
            yield _sse(ev)
    finally:
        with _lock:
            _subscribers.get(key, set()).discard((queue, loop))


def _reset_for_tests() -> None:
    """Forget everything in memory (tests relocate DATA_DIR between cases)."""
    global _runs_loaded
    with _lock:
        _events.clear()
        _seq.clear()
        _loaded.clear()
        _runs.clear()
        _runs_loaded = False
        _subscribers.clear()
        _active_turns.clear()
