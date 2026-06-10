"""Cookbook scheduler — calendar-driven model launches.

Calendar events on a designated calendar (configurable via setting
`cookbook_schedule_calendar_href`) are interpreted as serve schedules.
The reconciler ticks every ~60s, reads events whose window contains
"now", and reconciles the running serves against them:

  - Event starts in window AND no matching serve running → launch via
    existing /api/model/serve. If GPU is busy, mark event "skipped"
    with reason. No retry.
  - Event ends in window AND a scheduled serve is running → hard-kill.
  - Pre-existing manual serve matching the event's model → adopt it
    (mark as owned by the event so it gets stopped at window end).

Everything in this module is gated by setting `cookbook_scheduler_enabled`.
Setting that to False fully disables the feature without touching code.

Event description format (YAML-ish, single nested key):
  cookbook:
    preset: Qwen3.5-397B-A17B-AWQ            # or repo_id + cmd + host
    repo_id: deepseek-ai/DeepSeek-V4-Flash
    cmd: vllm serve /mnt/HADES/models/...
    host: pewds@192.168.1.12
    port: 8003

If only the title is given, the title is matched against saved preset
names (case-insensitive substring match).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


# Schedule-owned tasks are tagged with this so we can tell them apart
# from manual launches when deciding whether to hard-kill at window end.
SCHEDULE_OWNER_KEY = "_scheduledBy"
COOKBOOK_BASE_URL = "http://localhost:7000"


def _internal_headers() -> Dict[str, str]:
    """Match the in-process loopback auth path used by chat-agent tools."""
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    return {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}


def _parse_event_yaml(description: str) -> Dict[str, Any]:
    """Pull the `cookbook:` block out of an event description.

    Deliberately tolerant: we don't want a calendar-edit typo (a stray
    `>`, a tab, etc.) to silently drop the event. Returns {} on any
    error so the caller falls back to title-match against presets.
    """
    if not isinstance(description, str) or "cookbook:" not in description:
        return {}
    try:
        block_start = description.index("cookbook:")
        block = description[block_start:].split("\n")
        out: Dict[str, Any] = {}
        for line in block[1:]:
            if not line.startswith(("  ", "\t")):
                # First non-indented line ends the block.
                if line.strip() == "" and not out:
                    continue
                break
            k, _, v = line.strip().partition(":")
            v = v.strip().strip("'").strip('"')
            if k and v:
                out[k] = v
        return out
    except Exception as e:
        logger.debug(f"event yaml parse failed (ignored): {e}")
        return {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Accept both ISO with and without timezone; assume UTC if naive.
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


async def _fetch_calendar_events(calendar_href: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """List events on a single calendar in [start, end].

    Reuses /api/calendar/events. RRULE expansion happens server-side so
    we get concrete occurrences, not the master recurring event.
    """
    headers = _internal_headers()
    params = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar": calendar_href,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{COOKBOOK_BASE_URL}/api/calendar/events",
                params=params, headers=headers,
            )
            if r.status_code >= 400:
                logger.debug(f"calendar/events returned {r.status_code}: {r.text[:200]}")
                return []
            data = r.json()
        return data.get("events", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning(f"reconciler: failed to fetch calendar events: {e}")
        return []


async def _gpus_busy(host: str) -> bool:
    """Best-effort: are any GPUs on `host` already under non-trivial load?

    Used to honor "refuse to launch if GPUs busy" semantics. We don't
    block on a vllm process that's currently loading our OWN target —
    that's handled separately (idempotent registration). The check is
    "is there a foreign process holding GPU memory".
    """
    headers = _internal_headers()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            params = {"host": host} if host else {}
            r = await client.get(
                f"{COOKBOOK_BASE_URL}/api/cookbook/gpus",
                params=params, headers=headers,
            )
            if r.status_code >= 400:
                return False
            data = r.json() or {}
    except Exception:
        return False
    for gpu in data.get("gpus") or []:
        used_mb = int(gpu.get("used_mb") or 0)
        # 500 MB threshold: enough to exclude an idle display driver
        # (usually <300 MB) but catch any real allocation.
        if used_mb > 500:
            return True
    return False


def _resolve_event_payload(event: Dict[str, Any], presets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Turn a calendar event into a serve payload (or None if unschedulable).

    Tries event description's `cookbook:` block first; falls back to a
    case-insensitive preset-name match against the event title.
    """
    parsed = _parse_event_yaml(event.get("description") or "")
    if parsed.get("repo_id") or parsed.get("cmd"):
        return {
            "repo_id": parsed.get("repo_id") or parsed.get("model") or (event.get("summary") or ""),
            "cmd": parsed.get("cmd") or "",
            "remote_host": parsed.get("host") or parsed.get("remote_host") or "",
            "port": parsed.get("port"),
        }
    # Title-based preset lookup.
    title = (event.get("summary") or "").strip()
    if not title:
        return None
    preset_name = parsed.get("preset") or title
    lname = preset_name.lower()
    chosen = next(
        (p for p in presets if isinstance(p, dict) and (p.get("name") or "").lower() == lname),
        None,
    )
    if chosen is None:
        chosen = next(
            (p for p in presets if isinstance(p, dict) and lname in (p.get("name") or "").lower()),
            None,
        )
    if chosen is None:
        return None
    cmd = (chosen.get("cmd") or "").strip()
    # Adopted presets have no usable cmd — they can't be relaunched
    # from the scheduler.
    if not cmd or cmd.startswith("(adopted"):
        logger.info(f"scheduler: preset {preset_name!r} has no cmd; cannot schedule")
        return None
    return {
        "repo_id": chosen.get("model") or chosen.get("modelId") or "",
        "cmd": cmd,
        "remote_host": chosen.get("host") or chosen.get("remoteHost") or "",
        "port": chosen.get("port"),
    }


def _state_path() -> Path:
    return Path("/app/data/cookbook_state.json")


def _read_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        from core.atomic_io import atomic_write_json
        atomic_write_json(_state_path(), state)
    except Exception as e:
        logger.warning(f"scheduler: state write failed: {e}")


async def _launch_serve(payload: Dict[str, Any], event_uid: str) -> Optional[str]:
    """Hit /api/model/serve. Returns session_id on success, None on failure."""
    headers = _internal_headers()
    body = {"repo_id": payload["repo_id"], "cmd": payload["cmd"]}
    if payload.get("remote_host"):
        body["remote_host"] = payload["remote_host"]
    # Pull env/gpu/hf_token from the host's saved server entry, same as
    # the chat agent's serve_model does. Without this, vllm can't find
    # its venv binaries.
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{COOKBOOK_BASE_URL}/api/cookbook/state", headers=headers)
            st = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception:
        st = {}
    env = (st.get("env") or {}) if isinstance(st, dict) else {}
    servers = env.get("servers") or []
    target_host = payload.get("remote_host") or ""
    srv = next(
        (s for s in servers if isinstance(s, dict)
         and (s.get("host") == target_host or s.get("name") == target_host)),
        {},
    )
    if srv.get("env") in ("venv", "conda") and srv.get("envPath"):
        body["env_prefix"] = f"source {srv['envPath']}/bin/activate" if srv["env"] == "venv" else f"conda activate {srv['envPath']}"
    if srv.get("hfToken"):
        body["hf_token"] = srv["hfToken"]
    if srv.get("port"):
        body["ssh_port"] = str(srv["port"])
    if srv.get("platform"):
        body["platform"] = srv["platform"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{COOKBOOK_BASE_URL}/api/model/serve", json=body, headers=headers)
            data = r.json() if r.content else {}
    except Exception as e:
        logger.warning(f"scheduler: launch failed for event {event_uid}: {e}")
        return None
    if not data.get("ok"):
        err = data.get("error") or data.get("detail") or "unknown"
        logger.warning(f"scheduler: launch rejected for event {event_uid}: {err}")
        return None
    return data.get("session_id")


async def _stop_serve(session_id: str, host: str) -> None:
    headers = _internal_headers()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"{COOKBOOK_BASE_URL}/api/model/stop",
                              json={"session_id": session_id, "remote_host": host},
                              headers=headers)
    except Exception as e:
        logger.warning(f"scheduler: stop failed for {session_id}: {e}")


def _mark_event_status(state: Dict[str, Any], event_uid: str, status: str,
                       reason: str = "", session_id: str = "") -> None:
    """Track per-event reconciliation status in cookbook_state.scheduler.

    Schema:
      state.scheduler.events = {
        "<event_uid>": {
          "status": "running" | "skipped" | "ended" | "failed",
          "reason": "<short string>",
          "session_id": "...",
          "ts": <ms epoch>,
        },
        ...
      }
    """
    sched = state.setdefault("scheduler", {})
    events = sched.setdefault("events", {})
    events[event_uid] = {
        "status": status,
        "reason": reason,
        "session_id": session_id,
        "ts": int(time.time() * 1000),
    }


async def _reconcile_once() -> Dict[str, Any]:
    """One reconciliation pass. Returns a dict for diagnostics + UI.

    Idempotent: running this twice in a row with no event changes
    should produce the same state without double-launching or
    double-killing.
    """
    from src.settings import get_setting
    if not get_setting("cookbook_scheduler_enabled", False):
        return {"skipped": "disabled"}
    calendar_href = get_setting("cookbook_schedule_calendar_href", "") or ""
    if not calendar_href:
        return {"skipped": "no_calendar_configured"}

    now = _now_utc()
    # Look ±90s around now so a 60s tick still picks up events that
    # started 30s ago but haven't been reconciled.
    window_start = now - timedelta(seconds=90)
    window_end = now + timedelta(seconds=90)
    events = await _fetch_calendar_events(calendar_href, window_start, window_end)
    state = _read_state()
    presets = state.get("presets") or []
    sched = state.get("scheduler") or {}
    tracked = sched.get("events") or {}

    out: Dict[str, Any] = {"events": []}
    state_dirty = False

    # Classify each event by where `now` falls relative to its window.
    for ev in events:
        uid = ev.get("uid") or ev.get("id") or ""
        if not uid:
            continue
        ev_start = _parse_iso(ev.get("dtstart") or ev.get("start") or "")
        ev_end = _parse_iso(ev.get("dtend") or ev.get("end") or "")
        if ev_start is None or ev_end is None:
            continue
        in_window = ev_start <= now < ev_end
        just_ended = (ev_end <= now) and (now - ev_end) < timedelta(seconds=90)
        ev_status = (tracked.get(uid) or {}).get("status")
        ev_session = (tracked.get(uid) or {}).get("session_id")

        if just_ended and ev_session and ev_status in {"running", "adopted"}:
            # Window closed → hard-kill (per user choice).
            payload = _resolve_event_payload(ev, presets) or {}
            host = payload.get("remote_host") or ""
            await _stop_serve(ev_session, host)
            _mark_event_status(state, uid, "ended", session_id=ev_session)
            state_dirty = True
            out["events"].append({"uid": uid, "status": "ended", "session_id": ev_session})
            continue

        if not in_window:
            continue

        # In window. Determine whether a serve already exists for this event.
        if ev_status == "running" and ev_session:
            out["events"].append({"uid": uid, "status": "running", "session_id": ev_session})
            continue
        if ev_status == "skipped":
            # User chose: no retry within the window.
            out["events"].append({"uid": uid, "status": "skipped",
                                  "reason": (tracked.get(uid) or {}).get("reason", "")})
            continue

        payload = _resolve_event_payload(ev, presets)
        if payload is None:
            _mark_event_status(state, uid, "failed",
                               reason="no preset or cmd resolvable from event")
            state_dirty = True
            out["events"].append({"uid": uid, "status": "failed", "reason": "no preset"})
            continue

        # Adoption pass: is a non-scheduled serve already running this model?
        target_host = payload.get("remote_host") or ""
        for t in state.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            if t.get("type") != "serve":
                continue
            if (t.get("status") or "").lower() not in {"running", "ready", "loading", "warming"}:
                continue
            if t.get("remoteHost") != target_host:
                continue
            t_model = (t.get("payload") or {}).get("repo_id") or t.get("name") or ""
            if t_model.split("/")[-1] == (payload["repo_id"] or "").split("/")[-1]:
                t[SCHEDULE_OWNER_KEY] = uid
                _mark_event_status(state, uid, "adopted",
                                   reason="pre-existing serve adopted",
                                   session_id=t.get("sessionId") or t.get("id") or "")
                state_dirty = True
                out["events"].append({"uid": uid, "status": "adopted",
                                      "session_id": t.get("sessionId")})
                break
        else:
            # No matching pre-existing serve → fresh launch path.
            if await _gpus_busy(target_host):
                _mark_event_status(state, uid, "skipped",
                                   reason="GPUs busy at launch time")
                state_dirty = True
                out["events"].append({"uid": uid, "status": "skipped",
                                      "reason": "GPUs busy"})
                continue
            sid = await _launch_serve(payload, uid)
            if sid:
                _mark_event_status(state, uid, "running",
                                   reason="launched by scheduler",
                                   session_id=sid)
                state_dirty = True
                # Tag the new task with the schedule owner so window-end
                # cleanup knows this is ours, not a manual launch.
                fresh_state = _read_state()
                for t in fresh_state.get("tasks") or []:
                    if isinstance(t, dict) and t.get("sessionId") == sid:
                        t[SCHEDULE_OWNER_KEY] = uid
                        break
                _write_state(fresh_state)
                state_dirty = False  # we just wrote
                out["events"].append({"uid": uid, "status": "running",
                                      "session_id": sid})
            else:
                _mark_event_status(state, uid, "skipped",
                                   reason="serve_model rejected launch")
                state_dirty = True
                out["events"].append({"uid": uid, "status": "skipped",
                                      "reason": "launch rejected"})

    if state_dirty:
        _write_state(state)
    out["tick_at"] = now.isoformat()
    return out


async def reconcile_loop() -> None:
    """Forever-loop reconciler. Registered as a startup task in app.py."""
    # Stagger the first tick so we don't fight the rest of startup for
    # CPU + I/O.
    await asyncio.sleep(15)
    while True:
        try:
            result = await _reconcile_once()
            if result.get("events"):
                logger.info(f"scheduler tick: {result}")
        except Exception as e:
            logger.warning(f"scheduler tick failed: {e}")
        await asyncio.sleep(60)
