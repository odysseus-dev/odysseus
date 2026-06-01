"""Usage analytics routes."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from core.database import ChatMessage as DbChatMessage
from core.database import Session as DbSession
from core.database import SessionLocal
from src.auth_helpers import require_user


ALL_USERS = "__all__"


def _parse_ymd(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except Exception:
        raise HTTPException(400, f"Invalid {field_name}; expected YYYY-MM-DD")


def _date_range(start: date, end: date) -> list[str]:
    days = (end - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(days + 1)]


def _local_bounds_to_utc(start: date, end: date, tz_offset_minutes: int) -> tuple[datetime, datetime]:
    """Convert an inclusive local date range into UTC-naive DB bounds."""
    offset = timedelta(minutes=tz_offset_minutes)
    start_local = datetime.combine(start, time.min)
    end_local_exclusive = datetime.combine(end + timedelta(days=1), time.min)
    return start_local + offset, end_local_exclusive + offset


def _timestamp_to_local_day(ts: datetime, tz_offset_minutes: int) -> str:
    return (ts - timedelta(minutes=tz_offset_minutes)).date().isoformat()


def _coerce_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except Exception:
        return 0


def _parse_message_metrics(meta_data: Any) -> Optional[dict]:
    if not meta_data:
        return None
    if isinstance(meta_data, str):
        try:
            meta = json.loads(meta_data)
        except Exception:
            return None
    elif isinstance(meta_data, dict):
        meta = meta_data
    else:
        return None
    input_tokens = _coerce_int(meta.get("input_tokens"))
    output_tokens = _coerce_int(meta.get("output_tokens"))
    if not (input_tokens or output_tokens):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usage_source": meta.get("usage_source") or "unknown",
        "model": meta.get("model") or "",
    }


def _empty_daily_buckets(start: date, end: date) -> dict[str, dict]:
    return {
        day: {
            "date": day,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "message_count": 0,
            "estimated_count": 0,
            "real_count": 0,
        }
        for day in _date_range(start, end)
    }


def _empty_user_daily_buckets(start: date, end: date) -> dict[str, dict]:
    return {
        day: {
            "date": day,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "message_count": 0,
        }
        for day in _date_range(start, end)
    }


def _aggregate_usage_rows(
    rows: Iterable[tuple[datetime, Any, Optional[str], str]],
    *,
    start: date,
    end: date,
    tz_offset_minutes: int,
) -> dict:
    daily = _empty_daily_buckets(start, end)
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "message_count": 0,
        "estimated_count": 0,
        "real_count": 0,
    }
    by_user: dict[str, dict[str, int]] = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "message_count": 0,
    })
    daily_by_user: dict[str, dict[str, dict]] = defaultdict(lambda: _empty_user_daily_buckets(start, end))

    for timestamp, meta_data, owner, role in rows:
        if not timestamp:
            continue
        day = _timestamp_to_local_day(timestamp, tz_offset_minutes)
        if day not in daily:
            continue
        owner_key = owner or "unassigned"
        bucket = daily[day]
        bucket["message_count"] += 1
        totals["message_count"] += 1
        by_user[owner_key]["message_count"] += 1
        daily_by_user[owner_key][day]["message_count"] += 1

        if role != "assistant":
            continue
        metrics = _parse_message_metrics(meta_data)
        if not metrics:
            continue

        input_tokens = metrics["input_tokens"]
        output_tokens = metrics["output_tokens"]
        total_tokens = input_tokens + output_tokens
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += total_tokens
        user_bucket = daily_by_user[owner_key][day]
        user_bucket["input_tokens"] += input_tokens
        user_bucket["output_tokens"] += output_tokens
        user_bucket["total_tokens"] += total_tokens

        source = metrics.get("usage_source")
        if source == "estimated":
            bucket["estimated_count"] += 1
            totals["estimated_count"] += 1
        elif source == "real":
            bucket["real_count"] += 1
            totals["real_count"] += 1

        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += total_tokens

        by_user[owner_key]["input_tokens"] += input_tokens
        by_user[owner_key]["output_tokens"] += output_tokens
        by_user[owner_key]["total_tokens"] += total_tokens

    return {
        "daily": [daily[day] for day in sorted(daily)],
        "totals": totals,
        "by_user": [
            {"user": user, **values}
            for user, values in sorted(by_user.items(), key=lambda item: item[0])
        ],
        "daily_by_user": [
            {"user": user, "daily": [values[day] for day in sorted(values)]}
            for user, values in sorted(daily_by_user.items(), key=lambda item: item[0])
        ],
    }


def _is_admin(request: Request, user: str) -> bool:
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if not user or not auth_mgr:
        return False
    try:
        return bool(auth_mgr.is_admin(user))
    except Exception:
        return False


def _user_options(request: Request, include: bool) -> list[dict]:
    if not include:
        return []
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if not auth_mgr:
        return []
    try:
        return [
            {"username": u.get("username"), "is_admin": bool(u.get("is_admin"))}
            for u in auth_mgr.list_users()
            if u.get("username")
        ]
    except Exception:
        return []


def _resolve_owner_scope(current_user: str, admin: bool, requested_user: Optional[str]) -> tuple[Optional[str], str]:
    selected_user = (requested_user or "").strip()
    if current_user:
        if admin:
            if selected_user and selected_user != ALL_USERS:
                return selected_user, selected_user
            return None, ALL_USERS
        return current_user, current_user
    return None, selected_user or ALL_USERS


def setup_usage_routes() -> APIRouter:
    router = APIRouter(prefix="/api/usage", tags=["usage"])

    @router.get("/tokens")
    async def token_usage(
        request: Request,
        start: str = Query(..., description="Start date, YYYY-MM-DD, inclusive"),
        end: str = Query(..., description="End date, YYYY-MM-DD, inclusive"),
        user: Optional[str] = Query(None, description="Admin-only username filter"),
        tz_offset_minutes: int = Query(0, ge=-840, le=840),
    ) -> Dict[str, Any]:
        current_user = require_user(request)
        admin = _is_admin(request, current_user)

        start_date = _parse_ymd(start, "start")
        end_date = _parse_ymd(end, "end")
        if end_date < start_date:
            raise HTTPException(400, "end must be on or after start")
        if (end_date - start_date).days > 370:
            raise HTTPException(400, "Date range cannot exceed 371 days")

        owner_scope, selected_user = _resolve_owner_scope(current_user, admin, user)

        start_utc, end_utc = _local_bounds_to_utc(start_date, end_date, tz_offset_minutes)

        db = SessionLocal()
        try:
            q = (
                db.query(DbChatMessage.timestamp, DbChatMessage.meta_data, DbSession.owner, DbChatMessage.role)
                .join(DbSession, DbChatMessage.session_id == DbSession.id)
                .filter(DbChatMessage.timestamp >= start_utc)
                .filter(DbChatMessage.timestamp < end_utc)
            )
            if owner_scope is not None:
                q = q.filter(DbSession.owner == owner_scope)
            rows = q.all()
        finally:
            db.close()

        result = _aggregate_usage_rows(
            rows,
            start=start_date,
            end=end_date,
            tz_offset_minutes=tz_offset_minutes,
        )
        result.update({
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "user": selected_user,
            "is_admin": admin,
            "users": _user_options(request, admin),
        })
        return result

    return router
