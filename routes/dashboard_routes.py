"""Dashboard routes — /api/admin/dashboard

Admin-only analytics endpoint that aggregates token usage, model distribution,
cost breakdown, and local-LLM savings from the sessions table.
"""

import ipaddress
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Query

from core.middleware import require_admin

logger = logging.getLogger(__name__)

_PRICING_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "data", "model_pricing.json"
)

def _load_pricing() -> Dict[str, Dict[str, float]]:
    try:
        with open(_PRICING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        logger.warning("Failed to load model pricing: %s", e)
        return {}

MODEL_PRICING: Dict[str, Dict[str, float]] = _load_pricing()

_FALLBACK_PRICING = {"input": 1.00, "output": 4.00}

_TAILSCALE_RE = re.compile(r"^100\.(\d+)\.")


def is_local_endpoint(url: str) -> bool:
    """Server-side equivalent of chatRenderer.js ``isLocalEndpoint``.

    Returns True when the endpoint is local/self-hosted (loopback, LAN,
    Tailscale, Docker internal, single-label hostname, etc.).
    """
    if not url:
        return True
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return True
    if not host:
        return True

    if host in ("localhost", "0.0.0.0", "host.docker.internal"):
        return True
    if host.endswith(".local"):
        return True

    if "." not in host:
        return True

    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback or addr.is_private:
            return True
        m = _TAILSCALE_RE.match(host)
        if m and 64 <= int(m.group(1)) <= 127:
            return True
        return False
    except ValueError:
        pass

    return False


def _lookup_pricing(model_name: str) -> Dict[str, float]:
    """Find pricing for a model by substring match (same logic as frontend)."""
    if not model_name:
        return _FALLBACK_PRICING
    lower = model_name.lower()
    best_key = ""
    best_pricing = _FALLBACK_PRICING
    for key, pricing in MODEL_PRICING.items():
        if key in lower and len(key) > len(best_key):
            best_key = key
            best_pricing = pricing
    return best_pricing


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute dollar cost for a given model and token counts."""
    pricing = _lookup_pricing(model)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def _date_key(dt) -> str:
    """Extract YYYY-MM-DD from a datetime."""
    if dt is None:
        return "unknown"
    if isinstance(dt, str):
        return dt[:10]
    return dt.strftime("%Y-%m-%d")


def setup_dashboard_routes() -> APIRouter:
    router = APIRouter(tags=["dashboard"])

    @router.get("/api/admin/dashboard")
    async def get_dashboard(
        request: Request,
        days: int = Query(30, ge=1, le=365),
    ) -> Dict[str, Any]:
        """Aggregated AI usage analytics for the admin dashboard."""
        require_admin(request)

        from core.database import get_db_session, Session as DBSession

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

        with get_db_session() as db:
            sessions = (
                db.query(
                    DBSession.model,
                    DBSession.endpoint_url,
                    DBSession.total_input_tokens,
                    DBSession.total_output_tokens,
                    DBSession.mode,
                    DBSession.created_at,
                    DBSession.message_count,
                )
                .filter(DBSession.created_at >= cutoff)
                .all()
            )

        daily_usage: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"input_tokens": 0, "output_tokens": 0, "sessions": 0}
        )
        model_dist: Dict[str, int] = defaultdict(int)
        mode_dist: Dict[str, int] = defaultdict(int)
        cost_by_model: Dict[str, float] = defaultdict(float)
        local_savings_by_model: Dict[str, float] = defaultdict(float)

        total_input = 0
        total_output = 0
        total_cost = 0.0
        total_local_savings = 0.0
        total_sessions = 0
        total_messages = 0
        local_sessions = 0
        cloud_sessions = 0

        for row in sessions:
            model = row.model or "unknown"
            endpoint = row.endpoint_url or ""
            inp = row.total_input_tokens or 0
            out = row.total_output_tokens or 0
            mode = row.mode or "chat"
            msgs = row.message_count or 0

            if inp == 0 and out == 0 and msgs == 0:
                continue
            day = _date_key(row.created_at)

            total_input += inp
            total_output += out
            total_sessions += 1
            total_messages += msgs

            daily_usage[day]["input_tokens"] += inp
            daily_usage[day]["output_tokens"] += out
            daily_usage[day]["sessions"] += 1

            model_dist[model] += 1
            mode_dist[mode] += 1

            local = is_local_endpoint(endpoint)
            if local:
                local_sessions += 1
                hypothetical = _compute_cost(model, inp, out)
                total_local_savings += hypothetical
                local_savings_by_model[model] += hypothetical
            else:
                cloud_sessions += 1
                cost = _compute_cost(model, inp, out)
                total_cost += cost
                cost_by_model[model] += cost

        sorted_daily: List[Dict[str, Any]] = []
        for day in sorted(daily_usage.keys()):
            entry = daily_usage[day]
            sorted_daily.append({
                "date": day,
                "input_tokens": entry["input_tokens"],
                "output_tokens": entry["output_tokens"],
                "sessions": entry["sessions"],
            })

        sorted_models = sorted(model_dist.items(), key=lambda x: x[1], reverse=True)
        sorted_costs = sorted(cost_by_model.items(), key=lambda x: x[1], reverse=True)
        sorted_savings = sorted(
            local_savings_by_model.items(), key=lambda x: x[1], reverse=True
        )

        return {
            "period_days": days,
            "summary": {
                "total_sessions": total_sessions,
                "total_messages": total_messages,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "total_cost_usd": round(total_cost, 4),
                "total_local_savings_usd": round(total_local_savings, 4),
                "local_sessions": local_sessions,
                "cloud_sessions": cloud_sessions,
            },
            "daily_usage": sorted_daily,
            "model_distribution": [
                {"model": m, "sessions": c} for m, c in sorted_models
            ],
            "mode_distribution": dict(mode_dist),
            "cost_by_model": [
                {"model": m, "cost_usd": round(c, 4)} for m, c in sorted_costs
            ],
            "local_savings_by_model": [
                {"model": m, "savings_usd": round(s, 4)} for m, s in sorted_savings
            ],
        }

    return router
