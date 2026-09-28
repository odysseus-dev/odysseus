"""Monthly USD spend caps for Claude models, one per payment channel.

Every LLM call whose model id contains "claude" is priced at Anthropic list
rates and added to a per-calendar-month (UTC) ledger in DATA_DIR, under the
channel that pays for it:

- ``timeweb``   — the Timeweb AI gateway (api.timeweb.ai), paid from the
                  Timeweb balance; setting ``claude_timeweb_monthly_budget_usd``
- ``anthropic`` — everything else (Anthropic direct, the anthropic-proxy relay,
                  ClawRouter, ...); setting ``claude_monthly_budget_usd``

Both default to $7; 0 or negative disables that cap. Before each Claude call,
``check_or_raise`` refuses it when the channel's month spend plus a rough
estimate of this call's input would reach the limit; llm_core turns that into
an ordinary upstream error, so the configured fallback chain answers instead.

Timeweb bills in rubles with a markup over list price, so for that channel the
count here is a lower bound on the real charge.
"""
import json
import logging
import os
import threading
import time
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_MONTHLY_LIMIT_USD = 7.0
CHANNEL_SETTINGS = {
    "anthropic": "claude_monthly_budget_usd",
    "timeweb": "claude_timeweb_monthly_budget_usd",
}
SETTING_KEY = CHANNEL_SETTINGS["anthropic"]

# USD per 1M tokens: (input, output). Cache writes bill at 1.25x input, cache
# reads at 0.1x input. First match wins, so more specific ids come first.
_PRICES = (
    ("fable", (10.0, 50.0)),
    ("mythos", (10.0, 50.0)),
    ("opus-5-5", (4.0, 20.0)),
    ("opus", (5.0, 25.0)),
    ("sonnet-5", (2.0, 10.0)),
    ("sonnet", (3.0, 15.0)),
    ("haiku", (1.0, 5.0)),
)
# Unknown Claude ids are priced at the most expensive tier so the cap errs safe.
_FALLBACK_PRICE = (10.0, 50.0)

_lock = threading.Lock()


def is_claude(model: Optional[str]) -> bool:
    return bool(model) and "claude" in model.lower()


def channel(url: Optional[str]) -> str:
    host = (urlparse(url or "").hostname or "").lower().rstrip(".")
    if host == "timeweb.ai" or host.endswith(".timeweb.ai"):
        return "timeweb"
    return "anthropic"


def _price(model: str):
    m = model.lower()
    for key, price in _PRICES:
        if key in m:
            return price
    return _FALLBACK_PRICE


def cost_usd(model: str, input_tokens: int = 0, output_tokens: int = 0,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    pin, pout = _price(model)
    return (
        (input_tokens or 0) * pin
        + (cache_write_tokens or 0) * pin * 1.25
        + (cache_read_tokens or 0) * pin * 0.1
        + (output_tokens or 0) * pout
    ) / 1_000_000


def _ledger_path() -> str:
    from src.constants import DATA_DIR
    return os.path.join(DATA_DIR, "claude_spend.json")


def _month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def _load() -> dict:
    try:
        with open(_ledger_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("[claude-budget] unreadable ledger, treating as empty: %s", e)
        return {}


def limit_usd(chan: str = "anthropic") -> float:
    key = CHANNEL_SETTINGS.get(chan, SETTING_KEY)
    try:
        from src.settings import get_setting
        value = get_setting(key, DEFAULT_MONTHLY_LIMIT_USD)
        return float(DEFAULT_MONTHLY_LIMIT_USD if value in (None, "") else value)
    except Exception:
        return DEFAULT_MONTHLY_LIMIT_USD


def spent_usd(chan: str = "anthropic", month: Optional[str] = None) -> float:
    entry = (_load().get(month or _month()) or {}).get(chan) or {}
    try:
        return float(entry.get("usd", 0.0))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def record(model: str, input_tokens: int = 0, output_tokens: int = 0,
           cache_write_tokens: int = 0, cache_read_tokens: int = 0,
           estimated: bool = False, url: Optional[str] = None) -> float:
    """Add one call's cost to this month's ledger. Returns the call's cost."""
    if not is_claude(model):
        return 0.0
    usd = cost_usd(model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens)
    if usd <= 0:
        return 0.0
    from core.atomic_io import atomic_write_json
    chan = channel(url)
    month = _month()
    with _lock:
        data = _load()
        entry = data.setdefault(month, {}).setdefault(chan, {"usd": 0.0, "calls": 0, "by_model": {}})
        entry["usd"] = round(float(entry.get("usd", 0.0)) + usd, 6)
        entry["calls"] = int(entry.get("calls", 0)) + 1
        if estimated:
            entry["estimated_calls"] = int(entry.get("estimated_calls", 0)) + 1
        by_model = entry.setdefault("by_model", {})
        by_model[model] = round(float(by_model.get(model, 0.0)) + usd, 6)
        atomic_write_json(_ledger_path(), data, indent=2)
        total = entry["usd"]
    logger.info("[claude-budget] %s via %s +$%.4f%s → $%.4f this month (limit $%.2f)",
                model, chan, usd, " (estimated)" if estimated else "", total, limit_usd(chan))
    return usd


def estimate_input_tokens(messages) -> int:
    try:
        return len(json.dumps(messages, ensure_ascii=False, default=str)) // 4
    except Exception:
        return 0


def block_reason(model: str, messages=None, url: Optional[str] = None) -> Optional[str]:
    """Why a call to ``model`` must be refused right now, or None to allow it."""
    if not is_claude(model):
        return None
    chan = channel(url)
    limit = limit_usd(chan)
    if limit <= 0:
        return None
    spent = spent_usd(chan)
    projected = cost_usd(model, input_tokens=estimate_input_tokens(messages)) if messages else 0.0
    if spent + projected < limit:
        return None
    return (
        f"Monthly Claude budget reached ({chan}): ${spent:.2f} spent in {_month()} "
        f"(limit ${limit:.2f}, this request ≈ ${projected:.2f}). "
        f"Raise '{CHANNEL_SETTINGS[chan]}' in data/settings.json to allow more."
    )


def check_or_raise(model: str, messages=None, url: Optional[str] = None) -> None:
    reason = block_reason(model, messages, url)
    if reason:
        from fastapi import HTTPException
        logger.warning("[claude-budget] blocked %s: %s", model, reason)
        raise HTTPException(402, reason)
