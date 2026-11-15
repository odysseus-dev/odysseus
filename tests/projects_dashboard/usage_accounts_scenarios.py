"""usage.accounts behaviour with mocked provider fetchers (no network, no credentials). argv[1] = patched Hermes source, argv[2] = scratch HERMES_HOME."""
import json, os, sys, time
sys.path.insert(0, sys.argv[1]); os.environ["HERMES_HOME"] = sys.argv[2]
from datetime import datetime, timedelta, timezone
from unittest import mock
from agent import account_usage as au, account_usage_cache as cache
from agent.account_usage import AccountUsageSnapshot as Snap, AccountUsageWindow as Win
from gateway import usage_accounts as ua
from tui_gateway.contracts.usage_accounts import UsageAccountsResult

SECRET = "sk-live-SECRET-TOKEN-123"
calls = []
now = datetime.now(timezone.utc)
codex = Snap(provider="openai-codex", source="codex_usage_api", fetched_at=now, plan="Plus",
             windows=(Win("Session", 31.0, now + timedelta(hours=2)), Win("Weekly", 62.5, now + timedelta(days=3))),
             raw={"access_token": SECRET}, identity="codex:acct:user")
openrouter = Snap(provider="openrouter", source="credits_api", fetched_at=now, windows=(Win("API key quota", 10.0, None, "$9.00 of $10.00 remaining"),),
                  details=("Credits balance: $4.20",))
behaviour = {"openai-codex": codex, "openrouter": openrouter,
             "claude-subscription-directsdk-experimental": Snap(provider="c", source="claude_code_get_usage", fetched_at=now, unavailable_reason="Claude Code gave no usage answer (needs a version with get_usage)")}


def fake_fetch(provider, *a, **kw):
    calls.append(provider)
    value = behaviour.get(provider)
    if isinstance(value, Exception):
        raise value
    return value


def reset():
    ua._state.clear(); cache._snapshots.clear(); cache._started.clear(); cache._last_try.clear(); calls.clear()


def view(slug):
    return ua.provider_view(slug)


def run(refresh=None):
    for t in list(__import__("threading").enumerate()):
        if t.name == "hermes-usage-accounts":
            t.join(5)
    return ua.accounts_view(refresh=refresh)


out = {}
with mock.patch.object(ua, "_call_fetcher", fake_fetch), mock.patch.object(ua, "_current_provider", lambda: ""), \
        mock.patch.object(ua, "_pool_slugs", lambda: []), mock.patch.object(ua, "_nous_snapshot", lambda: None), \
        mock.patch.object(ua, "_display_name", lambda s: s):
    reset()
    first = ua.accounts_view()
    first = run()                                   # background refreshes landed
    by = {p["provider"]: p for p in first["providers"]}
    out["order"] = [p["provider"] for p in first["providers"]]
    out["codex"] = by["openai-codex"]
    out["openrouter"] = by["openrouter"]
    out["claude_plugin"] = by["claude-subscription-directsdk-experimental"]
    out["anthropic"] = by["anthropic"]
    out["nous"] = by["nous"]
    out["anthropic_fetched"] = "anthropic" in calls
    out["secret_leak"] = SECRET in json.dumps(first) or "codex:acct" in json.dumps(first)
    UsageAccountsResult(**first)                    # the frozen contract accepts the real payload
    n = len(calls); ua.accounts_view(); out["no_refetch_within_cadence"] = len(calls) == n
    # manual refresh is throttled, then allowed
    ua._state["openai-codex"]["tried"] -= 5
    n = len(calls); ua.accounts_view(refresh="openai-codex"); out["manual_throttled"] = calls[n:] == []
    ua._state["openai-codex"]["tried"] -= 30
    ua.accounts_view(refresh="openai-codex"); out["manual_allowed"] = calls.count("openai-codex") == 2
    # failure keeps last good reading, marked stale, with backoff
    behaviour["openai-codex"] = RuntimeError("boom " + SECRET)
    ua._state["openai-codex"]["tried"] -= 40
    after = ua.accounts_view(refresh="openai-codex")
    row = {p["provider"]: p for p in after["providers"]}["openai-codex"]
    out["failed_refresh"] = {"state": row["state"], "windows": [w["used_percent"] for w in row["windows"]], "reason": row["reason"], "retry_in_s": row["retry_in_s"]}
    out["failure_hides_secret"] = SECRET not in json.dumps(after)
    # very old snapshot is stale even without a failure
    reset(); behaviour["openai-codex"] = codex
    ua.accounts_view(); run()
    old = Snap(provider="openai-codex", source="codex_usage_api", fetched_at=now - timedelta(minutes=30), windows=codex.windows)
    cache._snapshots[cache._key("openai-codex")] = old
    ua._state["openai-codex"]["ok"] = True
    out["old_is_stale"] = view("openai-codex")["state"]
    # signed out
    reset(); behaviour["openrouter"] = None
    ua.accounts_view(); run()
    out["signed_out"] = view("openrouter")["state"]
    behaviour["openrouter"] = RuntimeError("outage"); ua._state["openrouter"]["tried"] -= 500; ua.accounts_view(refresh="openrouter")
    out["outage_is_not_signed_out"] = (view("openrouter")["state"], view("openrouter")["reason"])
    # hung provider does not block the default call
    reset()
    def slow(provider, **kw):
        time.sleep(3); return None
    with mock.patch.object(ua, "_call_fetcher", slow):
        t0 = time.monotonic(); ua.accounts_view(); out["default_call_nonblocking"] = (time.monotonic() - t0) < 1.0
    time.sleep(3.5)
    # model-scoped windows keep their scope
    reset(); behaviour["openai-codex"] = Snap(provider="x", source="s", fetched_at=now, windows=(Win("Opus week", 80.0, None, None, "model"),))
    ua.accounts_view(); run(); out["model_scope"] = view("openai-codex")["windows"][0]["scope"]
with mock.patch.object(ua, "_call_fetcher", fake_fetch), mock.patch.object(ua, "_current_provider", lambda: "deepseek"), \
        mock.patch.object(ua, "_pool_slugs", lambda: ["xai"]), mock.patch.object(ua, "_display_name", lambda s: s):
    reset(); extra = {p["provider"]: p for p in ua.accounts_view()["providers"]}
    out["unsupported_configured"] = {k: (extra[k]["state"], extra[k]["can_refresh"]) for k in ("deepseek", "xai")}
    out["current_provider"] = ua.accounts_view()["current_provider"]
# the built-in Anthropic fetcher (presents itself as Claude Code) must never reach the network
def boom(*a, **k):
    raise AssertionError("network used")
with mock.patch.object(au, "_get_json", boom), mock.patch.object(au, "resolve_anthropic_token", boom):
    snap = au.fetch_account_usage("anthropic", api_key="sk-ant-oat-TEST")
out["anthropic_builtin"] = {"windows": len(snap.windows), "reason": snap.unavailable_reason, "source": snap.source}

# credential problems read as "sign in", not as an outage; 401 is a rejected sign-in
class AuthError(Exception):
    pass
class Resp:
    status_code = 401
class Http(Exception):
    response = Resp()
out["classify"] = {"authError": ua._classify_failure(AuthError("x")), "pool": ua._classify_failure(RuntimeError("No available openai-codex credential in credential pool")),
                   "http401": ua._classify_failure(Http("401")), "outage": ua._classify_failure(RuntimeError("boom"))}
# Codex parser: synthetic payloads only (no credential store, no network)
def codex(payload):
    with mock.patch.object(au, "_resolve_codex_usage_credentials", lambda *a, **k: ("tok-" + SECRET, "https://chatgpt.com/backend-api/codex", None)), \
            mock.patch.object(au, "_get_json", lambda *a, **k: payload), mock.patch.object(au, "_codex_snapshot_identity", lambda t: "codex:a:b"):
        s = au._fetch_codex_account_usage_impl()
    return [(w.label, w.used_percent, w.reset_at.isoformat() if w.reset_at else None) for w in s.windows], s.plan, list(s.details)
iso = "2026-11-15T15:00:32+00:00"
out["codex_zero_used"] = codex({"plan_type": "plus", "rate_limit": {"primary_window": {"used_percent": 0, "limit_window_seconds": 18000, "reset_at": iso}, "secondary_window": {"used_percent": 99, "limit_window_seconds": 604800, "reset_at": iso}}})
out["codex_weekly_only"] = codex({"plan_type": "pro", "rate_limit": {"primary_window": {"used_percent": 12.5, "limit_window_seconds": 604800, "reset_at": iso}}})
out["codex_fraction_not_scaled"] = codex({"rate_limit": {"primary_window": {"used_percent": 1, "limit_window_seconds": 18000, "reset_at": iso}}})
out["codex_missing_used"] = codex({"rate_limit": {"primary_window": {"limit_window_seconds": 18000, "reset_at": iso}}})

# OpenRouter: mocked HTTP and mocked runtime resolution (no key, no network)
class FakeClient:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, url, headers=None):
        assert "SECRET" not in url
        body = {"data": {"total_credits": 25.0, "total_usage": 20.75}} if url.endswith("/credits") else {"data": {"limit": 10.0, "limit_remaining": 4.0, "usage": 6.0, "usage_daily": 0.5, "limit_reset": "monthly"}}
        class R:
            def raise_for_status(self): pass
            def json(self): return body
        return R()
with mock.patch.object(au.httpx, "Client", FakeClient), mock.patch.object(au, "resolve_runtime_provider", lambda **k: {"api_key": "sk-or-" + SECRET, "base_url": "https://openrouter.ai/api/v1"}):
    s = au._fetch_openrouter_account_usage(None, None)
out["openrouter_mock"] = {"windows": [(w.label, w.used_percent, w.detail) for w in s.windows], "details": list(s.details)}
out["openrouter_mock_has_key"] = SECRET in json.dumps({"w": [w.detail for w in s.windows], "d": list(s.details)})
print("@@" + json.dumps(out, default=str))
