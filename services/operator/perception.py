"""Screen perception — live and recent screen OCR via Screenpipe.

Backs the `screen_look` agent tool. Screen-only: never enables Screenpipe
audio capture (the mic belongs to Clicky / Odysseus voice via the mic lease).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from services.operator.core import (
    CAP_SCREEN_PERCEPTION,
    degraded_envelope,
    envelope,
    require_capability,
    screenpipe_url,
)

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_MINUTES = 5
MAX_LOOKBACK_MINUTES = 120
LIVE_LOOKBACK_SECONDS = 60  # no-query default: "what's on screen right now"
FRAME_LIMIT = 50
REQUEST_TIMEOUT = 15.0


def _char_budget() -> int:
    try:
        return int(os.environ.get("OPERATOR_PERCEPTION_CHAR_BUDGET") or 8000)
    except ValueError:
        return 8000


def _parse_frames(body: Any) -> List[Dict[str, Any]]:
    """Normalize Screenpipe /search items to {timestamp, app, window, text}."""
    frames: List[Dict[str, Any]] = []
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return frames
    for item in items:
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, dict):
            continue
        text = (content.get("text") or "").strip()
        if not text:
            continue
        frames.append({
            "timestamp": content.get("timestamp"),
            "app": content.get("app_name"),
            "window": content.get("window_name") or content.get("window_title"),
            "text": text,
        })
    # Newest first; Screenpipe timestamps are ISO strings so string sort works.
    frames.sort(key=lambda f: str(f.get("timestamp") or ""), reverse=True)
    return frames


def _apply_budget(frames: List[Dict[str, Any]], budget: int) -> Dict[str, Any]:
    """Cut at a frame boundary once the character budget is exhausted."""
    kept: List[Dict[str, Any]] = []
    used = 0
    for frame in frames:
        cost = len(frame["text"])
        if kept and used + cost > budget:
            break
        kept.append(frame)
        used += cost
        if used >= budget:
            break
    omitted = len(frames) - len(kept)
    out: Dict[str, Any] = {"frames": kept, "truncated": omitted > 0}
    if omitted > 0:
        out["omitted_frames"] = omitted
        out["truncation_note"] = (
            f"[truncated at frame boundary — {omitted} older frame(s) omitted; "
            "narrow with a query or shorter lookback]"
        )
    return out


def screen_look(query: Optional[str] = None, minutes: Optional[int] = None) -> Dict[str, Any]:
    """Return recent OCR frames, optionally filtered by a text query.

    No arguments → frames from the last 60 seconds ("what's on screen now").
    With a query → default 5-minute lookback, capped at 120 minutes.
    """
    gate = require_capability(CAP_SCREEN_PERCEPTION)
    if gate:
        return gate

    if minutes is not None:
        lookback = timedelta(minutes=max(1, min(int(minutes), MAX_LOOKBACK_MINUTES)))
    elif query:
        lookback = timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)
    else:
        lookback = timedelta(seconds=LIVE_LOOKBACK_SECONDS)

    start_time = (datetime.now(timezone.utc) - lookback).isoformat().replace("+00:00", "Z")
    params: Dict[str, str] = {
        "content_type": "ocr",
        "limit": str(FRAME_LIMIT),
        "start_time": start_time,
    }
    if query:
        params["q"] = query

    url = f"{screenpipe_url()}/search?{parse.urlencode(params)}"
    req = request.Request(url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (error.URLError, TimeoutError, OSError) as exc:
        return degraded_envelope(CAP_SCREEN_PERCEPTION, f"screenpipe_error: {exc}")
    except (json.JSONDecodeError, ValueError):
        return degraded_envelope(CAP_SCREEN_PERCEPTION, "screenpipe_bad_response")

    frames = _parse_frames(body)
    data = _apply_budget(frames, _char_budget())
    data["query"] = query
    data["lookback_minutes"] = round(lookback.total_seconds() / 60, 2)
    data["window_count"] = len({(f.get("app"), f.get("window")) for f in data["frames"]})
    return envelope(CAP_SCREEN_PERCEPTION, True, data=data)
