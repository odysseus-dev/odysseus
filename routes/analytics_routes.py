"""Usage analytics API routes — reads from existing sessions + chat_messages tables."""

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Query

from core.database import SessionLocal, Session, ChatMessage
from src.auth_helpers import _auth_disabled, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

_DATE_FORMAT = "%Y-%m-%d"

# Per-model pricing ($ per 1M tokens). Mirrors static/js/chatRenderer.js MODEL_INFO.
_USAGE_PRICING = {
    "claude-sonnet-4-5": (3.00, 15.00), "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00), "claude-opus-4": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00), "claude-haiku-4": (0.80, 4.00),
    "claude-haiku-3-5": (0.80, 4.00), "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00), "claude-3-opus": (15.00, 75.00),
    "claude-3-sonnet": (3.00, 15.00), "claude-3-haiku": (0.25, 1.25),
    "gpt-5": (2.00, 8.00), "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00), "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00), "o1-pro": (150.00, 600.00),
    "o3": (2.00, 8.00), "o3-mini": (1.10, 4.40), "o4-mini": (1.10, 4.40),
    "deepseek-chat": (0.27, 1.10), "deepseek-coder": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19), "deepseek-r1": (0.55, 2.19),
    "deepseek-v3": (0.27, 1.10), "deepseek-v2": (0.14, 0.28),
    "gemini-2.5-pro": (1.25, 10.00), "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40), "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30), "gemma-3": (0.10, 0.10),
    "mistral-large": (2.00, 6.00), "mistral-medium": (2.00, 6.00),
    "mistral-small": (0.20, 0.60), "mistral-nemo": (0.15, 0.15),
    "mixtral": (0.24, 0.24), "codestral": (0.30, 0.90),
    "pixtral": (2.00, 6.00), "grok-4": (3.00, 15.00),
    "grok-3": (3.00, 15.00), "grok-2": (2.00, 10.00),
    "llama-4": (0.20, 0.20), "llama-3.3": (0.20, 0.20),
    "llama-3.2": (0.20, 0.20), "llama-3.1": (0.20, 0.20),
    "llama-3": (0.20, 0.20), "qwen3": (0.30, 1.20),
    "qwen2.5": (0.30, 1.20), "qwq": (0.30, 1.20),
    "command-a": (2.50, 10.00), "command-r-plus": (2.50, 10.00),
    "command-r": (0.15, 0.60), "sonar-pro": (3.00, 15.00),
    "sonar": (1.00, 1.00), "minimax": (0.70, 0.70),
    "moonshot": (1.00, 1.00), "kimi": (1.00, 1.00),
    "phi-4": (0.07, 0.14), "phi-3": (0.07, 0.14),
    "nemotron": (0.30, 1.20), "hermes": (0.20, 0.20),
}

_LOCAL_PROVIDER_PREFIXES = ("ollama", "lm-studio", "localhost", "127.0.0.1")


def _get_user(request: Request):
    user = get_current_user(request)
    if not user and not _auth_disabled():
        raise HTTPException(401, "Not authenticated")
    return user


def _is_admin(user, request: Request = None) -> bool:
    if not user:
        return False
    if request:
        am = getattr(request.app.state, "auth_manager", None)
        if am:
            return am.is_admin(user)
    return False


def _owner_filter(user, request: Request = None):
    if not user or _is_admin(user, request):
        return None
    return user


def _cutoff_from_range(range_str: str):
    if range_str == "all":
        return None
    days = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}.get(range_str, 30)
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    model_lower = model.lower().strip()
    prices = _USAGE_PRICING.get(model_lower)
    if not prices:
        return 0.0
    in_price, out_price = prices
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


def _is_local(endpoint_url: str) -> bool:
    if not endpoint_url:
        return False
    ep = endpoint_url.lower()
    return any(p in ep for p in _LOCAL_PROVIDER_PREFIXES)


def _parse_meta(meta_raw):
    """Parse assistant message metadata JSON, return dict or None."""
    if not meta_raw:
        return None
    try:
        return json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except (json.JSONDecodeError, TypeError):
        return None


def _get_assistant_messages(db, owner, cutoff):
    """Return (msg, sess_owner) for assistant messages with metadata, filtered by owner + cutoff."""
    q = db.query(ChatMessage, Session.owner).join(Session, ChatMessage.session_id == Session.id)
    q = q.filter(ChatMessage.role == "assistant")
    q = q.filter(ChatMessage.meta_data.isnot(None))
    q = q.filter(ChatMessage.meta_data != "")
    if cutoff:
        q = q.filter(ChatMessage.timestamp >= cutoff)
    rows = q.all()
    if owner:
        rows = [(m, o) for m, o in rows if o == owner or (o is None and owner == "admin")]
    return rows


@router.get("/api/analytics/summary")
async def analytics_summary(
    request: Request,
    range: str = Query("7d", pattern="^(7d|30d|90d|1y|all)$"),
):
    """Aggregate usage stats from sessions + chat_messages."""
    user = _get_user(request)
    owner = _owner_filter(user, request)
    cutoff = _cutoff_from_range(range)
    db = SessionLocal()
    try:
        # Session-level aggregates
        sq = db.query(Session)
        if owner:
            sq = sq.filter(Session.owner == owner)
        if cutoff:
            sq = sq.filter(Session.created_at >= cutoff)
        sessions = sq.all()

        total_in = sum(s.total_input_tokens or 0 for s in sessions)
        total_out = sum(s.total_output_tokens or 0 for s in sessions)
        total_cost = sum(
            _estimate_cost(s.model, s.total_input_tokens or 0, s.total_output_tokens or 0)
            for s in sessions if not _is_local(s.endpoint_url)
        )

        # Per-message metrics for response time + TPS
        msg_rows = _get_assistant_messages(db, owner, cutoff)
        rt_vals = []
        tps_vals = []
        for m, _ in msg_rows:
            meta = _parse_meta(m.meta_data)
            if meta:
                rt = meta.get("response_time")
                if rt:
                    rt_vals.append(int(rt * 1000))
                tps = meta.get("tokens_per_second")
                if tps:
                    tps_vals.append(tps)

        avg_rt = sum(rt_vals) / len(rt_vals) if rt_vals else 0
        avg_tps = sum(tps_vals) / len(tps_vals) if tps_vals else 0

        return {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "estimated_cost": round(total_cost, 6),
            "total_requests": len(msg_rows),
            "total_sessions": len(sessions),
            "avg_response_time_ms": round(avg_rt),
            "avg_tokens_per_second": round(avg_tps, 2),
        }
    finally:
        db.close()


@router.get("/api/analytics/timeseries")
async def analytics_timeseries(
    request: Request,
    range: str = Query("30d", pattern="^(7d|30d|90d|1y|all)$"),
):
    """Daily token usage, cost, and request count from chat_messages."""
    user = _get_user(request)
    owner = _owner_filter(user, request)
    cutoff = _cutoff_from_range(range)
    db = SessionLocal()
    try:
        msg_rows = _get_assistant_messages(db, owner, cutoff)
        daily = {}
        for m, _ in msg_rows:
            if not m.timestamp:
                continue
            day = m.timestamp.strftime(_DATE_FORMAT)
            if day not in daily:
                daily[day] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "requests": 0}
            meta = _parse_meta(m.meta_data)
            if meta:
                in_t = meta.get("input_tokens", 0) or 0
                out_t = meta.get("output_tokens", 0) or 0
                daily[day]["input_tokens"] += in_t
                daily[day]["output_tokens"] += out_t
                model = meta.get("model") or "unknown"
                daily[day]["cost"] += _estimate_cost(model, in_t, out_t)
            daily[day]["requests"] += 1

        if not daily:
            # Fallback: use session created_at dates
            sq = db.query(Session)
            if owner:
                sq = sq.filter(Session.owner == owner)
            if cutoff:
                sq = sq.filter(Session.created_at >= cutoff)
            for s in sq.all():
                if not s.created_at:
                    continue
                day = s.created_at.strftime(_DATE_FORMAT)
                if day not in daily:
                    daily[day] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "requests": 0}
                daily[day]["input_tokens"] += s.total_input_tokens or 0
                daily[day]["output_tokens"] += s.total_output_tokens or 0
                daily[day]["cost"] += _estimate_cost(s.model, s.total_input_tokens or 0, s.total_output_tokens or 0)

        days = sorted(daily.keys())
        return {
            "days": days,
            "input_tokens": [daily[d]["input_tokens"] for d in days],
            "output_tokens": [daily[d]["output_tokens"] for d in days],
            "cost": [round(daily[d]["cost"], 6) for d in days],
            "requests": [daily[d]["requests"] for d in days],
        }
    finally:
        db.close()


@router.get("/api/analytics/models")
async def analytics_models(
    request: Request,
    range: str = Query("30d", pattern="^(7d|30d|90d|1y|all)$"),
):
    """Per-model breakdown from sessions."""
    user = _get_user(request)
    owner = _owner_filter(user, request)
    cutoff = _cutoff_from_range(range)
    db = SessionLocal()
    try:
        sq = db.query(Session)
        if owner:
            sq = sq.filter(Session.owner == owner)
        if cutoff:
            sq = sq.filter(Session.created_at >= cutoff)
        sessions = sq.all()

        models = {}
        for s in sessions:
            m = s.model or "unknown"
            if m not in models:
                models[m] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "sessions": 0, "requests": 0}
            models[m]["input_tokens"] += s.total_input_tokens or 0
            models[m]["output_tokens"] += s.total_output_tokens or 0
            models[m]["cost"] += _estimate_cost(s.model, s.total_input_tokens or 0, s.total_output_tokens or 0)
            models[m]["sessions"] += 1

        # Per-message model data for request count + TPS
        msg_rows = _get_assistant_messages(db, owner, cutoff)
        for m, _ in msg_rows:
            meta = _parse_meta(m.meta_data)
            if meta:
                model_name = meta.get("model") or "unknown"
                if model_name not in models:
                    models[model_name] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "sessions": 0, "requests": 0}
                models[model_name]["requests"] += 1
                in_t = meta.get("input_tokens", 0) or 0
                out_t = meta.get("output_tokens", 0) or 0
                models[model_name]["input_tokens"] += in_t
                models[model_name]["output_tokens"] += out_t

        rows = []
        for name, d in sorted(models.items(), key=lambda x: x[1]["requests"] + x[1]["sessions"], reverse=True):
            total_tok = d["input_tokens"] + d["output_tokens"]
            rows.append({
                "model": name,
                "input_tokens": d["input_tokens"],
                "output_tokens": d["output_tokens"],
                "total_tokens": total_tok,
                "estimated_cost": round(d["cost"], 6),
                "requests": d["requests"] or d["sessions"] * 5,
                "avg_tokens_per_second": 0,
                "avg_response_time_ms": 0,
            })
        return {"models": rows}
    finally:
        db.close()


@router.get("/api/analytics/heatmap")
async def analytics_heatmap(
    request: Request,
    range: str = Query("7d", pattern="^(7d|30d|90d|1y|all)$"),
):
    """Hourly activity matrix from chat_messages timestamps."""
    user = _get_user(request)
    owner = _owner_filter(user, request)
    cutoff = _cutoff_from_range(range)
    db = SessionLocal()
    try:
        q = db.query(ChatMessage, Session.owner).join(Session, ChatMessage.session_id == Session.id)
        if cutoff:
            q = q.filter(ChatMessage.timestamp >= cutoff)
        rows = q.all()
        if owner:
            rows = [(m, o) for m, o in rows if o == owner or (o is None and owner == "admin")]

        grid = {}
        for m, _ in rows:
            if not m.timestamp:
                continue
            day = m.timestamp.strftime(_DATE_FORMAT)
            hour = m.timestamp.hour
            key = (day, hour)
            grid[key] = grid.get(key, 0) + 1

        days_in = {}
        for (day, hour), count in grid.items():
            if day not in days_in:
                days_in[day] = [0] * 24
            days_in[day][hour] = count

        days_sorted = sorted(days_in.keys())
        hours = list(range(24))
        return {
            "days": days_sorted,
            "hours": hours,
            "data": [days_in[d] for d in days_sorted],
        }
    finally:
        db.close()


@router.get("/api/analytics/sessions")
async def analytics_top_sessions(
    request: Request,
    range: str = Query("30d", pattern="^(7d|30d|90d|1y|all)$"),
    limit: int = Query(20, ge=1, le=100),
):
    """Top sessions by token usage, from sessions table directly."""
    user = _get_user(request)
    owner = _owner_filter(user, request)
    cutoff = _cutoff_from_range(range)
    db = SessionLocal()
    try:
        q = db.query(Session)
        if owner:
            q = q.filter(Session.owner == owner)
        if cutoff:
            q = q.filter(Session.created_at >= cutoff)
        q = q.order_by(
            (Session.total_input_tokens + Session.total_output_tokens).desc()
        )
        q = q.limit(limit)

        rows = []
        for s in q.all():
            total = (s.total_input_tokens or 0) + (s.total_output_tokens or 0)
            cost = _estimate_cost(s.model, s.total_input_tokens or 0, s.total_output_tokens or 0)
            rows.append({
                "session_id": s.id,
                "session_name": s.name,
                "input_tokens": s.total_input_tokens or 0,
                "output_tokens": s.total_output_tokens or 0,
                "total_tokens": total,
                "estimated_cost": round(cost, 6),
                "requests": s.message_count or 0,
                "last_used": s.last_message_at.isoformat() if s.last_message_at else None,
            })
        return {"sessions": rows}
    finally:
        db.close()
