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


def fake_fetch(provider, **kw):
    calls.append(provider)
    value = behaviour.get(provider)
    if isinstance(value, Exception):
        raise value
    if value is not None and value.windows:
        cache.remember_account_usage(provider, value)
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
with mock.patch.object(au, "fetch_account_usage", fake_fetch), mock.patch.object(ua, "_current_provider", lambda: ""), \
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
    # hung provider does not block the default call
    reset()
    def slow(provider, **kw):
        time.sleep(3); return None
    with mock.patch.object(au, "fetch_account_usage", slow):
        t0 = time.monotonic(); ua.accounts_view(); out["default_call_nonblocking"] = (time.monotonic() - t0) < 1.0
    time.sleep(3.5)
    # model-scoped windows keep their scope
    reset(); behaviour["openai-codex"] = Snap(provider="x", source="s", fetched_at=now, windows=(Win("Opus week", 80.0, None, None, "model"),))
    ua.accounts_view(); run(); out["model_scope"] = view("openai-codex")["windows"][0]["scope"]
# the built-in Anthropic fetcher (presents itself as Claude Code) must never reach the network
def boom(*a, **k):
    raise AssertionError("network used")
with mock.patch.object(au, "_get_json", boom), mock.patch.object(au, "resolve_anthropic_token", boom):
    snap = au.fetch_account_usage("anthropic", api_key="sk-ant-oat-TEST")
out["anthropic_builtin"] = {"windows": len(snap.windows), "reason": snap.unavailable_reason, "source": snap.source}
print("@@" + json.dumps(out, default=str))
