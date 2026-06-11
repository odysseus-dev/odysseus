"""Tests for src.service_health — the consolidated degraded-state report.

Imports the real module (conftest.py stubs the heavy deps). Network is never
touched: HTTP probes take an injected `http_get`, and the email/provider probes
take an injected `connect` / `probe`. Asserts the ok/degraded/down/disabled
mapping per subsystem, the overall rollup, and that no secrets leak into meta.
"""
import types

import pytest

from src import service_health as sh


def _resp(status_code):
    return types.SimpleNamespace(status_code=status_code)


def _raise(*_a, **_k):
    raise RuntimeError("connection refused")


# ── chromadb_health ──

class _Store:
    def __init__(self, healthy):
        self.healthy = healthy


def test_chromadb_both_healthy_ok():
    s = sh.chromadb_health(_Store(True), _Store(True))
    assert s["status"] == sh.OK
    assert s["meta"] == {"rag": True, "memory": True}


def test_chromadb_one_down_degraded():
    s = sh.chromadb_health(_Store(True), _Store(False))
    assert s["status"] == sh.DEGRADED


def test_chromadb_both_unhealthy_down():
    s = sh.chromadb_health(_Store(False), _Store(False))
    assert s["status"] == sh.DOWN


def test_chromadb_both_absent_disabled():
    s = sh.chromadb_health(None, None)
    assert s["status"] == sh.DISABLED


def test_chromadb_one_absent_one_healthy_ok():
    # An absent store is not a failure; the present one being healthy is ok.
    s = sh.chromadb_health(_Store(True), None)
    assert s["status"] == sh.OK
    assert s["meta"]["memory"] is None


# ── searxng_health ──

def test_searxng_disabled_when_other_provider():
    s = sh.searxng_health({"search_provider": "brave"})
    assert s["status"] == sh.DISABLED


def test_searxng_ok_on_healthz():
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=lambda url, timeout: _resp(200),
    )
    assert s["status"] == sh.OK
    assert s["meta"]["probed"] == "/healthz"


def test_searxng_ok_on_root_fallback():
    def getter(url, timeout):
        return _resp(404) if url.endswith("/healthz") else _resp(200)

    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=getter,
    )
    assert s["status"] == sh.OK
    assert s["meta"]["probed"] == "/"


def test_searxng_down_on_exception():
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=_raise,
    )
    assert s["status"] == sh.DOWN


def test_searxng_down_on_5xx():
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=lambda url, timeout: _resp(502),
    )
    assert s["status"] == sh.DOWN


# ── ntfy_health ──

def _ntfy_intg():
    return [{"preset": "ntfy", "enabled": True, "base_url": "http://ntfy:80"}]


def test_ntfy_disabled_without_integration():
    s = sh.ntfy_health([], {"reminder_channel": "ntfy"})
    assert s["status"] == sh.DISABLED


def test_ntfy_ok():
    s = sh.ntfy_health(_ntfy_intg(), {"reminder_channel": "ntfy"},
                       http_get=lambda url, timeout: _resp(200))
    assert s["status"] == sh.OK
    assert s["meta"]["base"] == "http://ntfy:80"


def test_ntfy_probes_v1_health_not_a_topic():
    seen = {}

    def getter(url, timeout):
        seen["url"] = url
        return _resp(200)

    sh.ntfy_health(_ntfy_intg(), {"reminder_channel": "ntfy"}, http_get=getter)
    # Non-intrusive: hits /v1/health, never publishes to a topic.
    assert seen["url"].endswith("/v1/health")


def test_ntfy_down_on_exception():
    s = sh.ntfy_health(_ntfy_intg(), {"reminder_channel": "ntfy"},
                       http_get=_raise)
    assert s["status"] == sh.DOWN


# ── email_health ──

def _acct(name, host="imap.example.com"):
    return {"account_id": name, "account_name": name, "imap_host": host,
            "imap_password": "hunter2"}


class _Conn:
    def logout(self):
        pass


def test_email_disabled_without_accounts():
    assert sh.email_health([])["status"] == sh.DISABLED


def test_email_ok_all_connect():
    s = sh.email_health([_acct("a"), _acct("b")], connect=lambda _id: _Conn())
    assert s["status"] == sh.OK


def test_email_degraded_some_fail():
    def connect(account_id):
        if account_id == "bad":
            raise RuntimeError("auth failed")
        return _Conn()

    s = sh.email_health([_acct("good"), _acct("bad")], connect=connect)
    assert s["status"] == sh.DEGRADED


def test_email_down_all_fail():
    s = sh.email_health([_acct("a")], connect=_raise)
    assert s["status"] == sh.DOWN


def test_email_account_without_host_marked_failed():
    s = sh.email_health([_acct("a", host="")], connect=lambda _id: _Conn())
    assert s["status"] == sh.DOWN


def test_email_meta_never_leaks_password():
    s = sh.email_health([_acct("a")], connect=lambda _id: _Conn())
    assert "hunter2" not in repr(s)


# ── providers_health ──

def _ep(name):
    return {"name": name, "base_url": f"http://{name}:8000/v1", "api_key": "sk-secret"}


def test_providers_disabled_without_endpoints():
    assert sh.providers_health([])["status"] == sh.DISABLED


def test_providers_ok_all_reachable():
    s = sh.providers_health([_ep("a")],
                            probe=lambda base, key, timeout: ["m1", "m2"])
    assert s["status"] == sh.OK
    assert s["meta"]["endpoints"][0]["model_count"] == 2


def test_providers_degraded_some_empty():
    def probe(base, key, timeout):
        return ["m1"] if "good" in base else []

    s = sh.providers_health([_ep("good"), _ep("bad")], probe=probe)
    assert s["status"] == sh.DEGRADED


def test_providers_down_all_fail():
    s = sh.providers_health([_ep("a")], probe=_raise)
    assert s["status"] == sh.DOWN


def test_providers_meta_never_leaks_api_key():
    s = sh.providers_health([_ep("a")],
                            probe=lambda base, key, timeout: ["m1"])
    assert "sk-secret" not in repr(s)


# ── rollup ──

def test_rollup_picks_worst_non_disabled():
    services = [
        {"status": sh.OK}, {"status": sh.DISABLED},
        {"status": sh.DEGRADED}, {"status": sh.OK},
    ]
    assert sh._rollup(services) == sh.DEGRADED


def test_rollup_down_beats_degraded():
    assert sh._rollup([{"status": sh.DEGRADED}, {"status": sh.DOWN}]) == sh.DOWN


def test_rollup_all_disabled_is_ok():
    assert sh._rollup([{"status": sh.DISABLED}, {"status": sh.DISABLED}]) == sh.OK


# ── collect_service_health (async aggregate) ──

def test_collect_service_health_shape(monkeypatch):
    import asyncio

    # Avoid touching real data sources / network.
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {"search_provider": "disabled"},
        "integrations": [],
        "accounts": [],
        "endpoints": [],
    })
    out = asyncio.run(sh.collect_service_health(_Store(True), _Store(True)))
    assert set(out) == {"overall", "services", "timestamp"}
    names = {s["name"] for s in out["services"]}
    assert names == {"chromadb", "searxng", "ntfy", "email", "providers"}
    # Chroma healthy, everything else disabled → overall ok.
    assert out["overall"] == sh.OK


# ── _safe_url: strip userinfo / query / fragment ──

@pytest.mark.parametrize("raw,expected", [
    ("http://user:pass@host:8080/path?api_key=secret#frag", "http://host:8080/path"),
    ("https://admin:hunter2@searx.example.com/", "https://searx.example.com"),
    ("http://ntfy.local:80?token=abc", "http://ntfy.local:80"),
    ("host:8080", "host:8080"),
    ("", ""),
    (None, ""),
])
def test_safe_url_strips_secrets(raw, expected):
    out = sh._safe_url(raw)
    assert out == expected
    for bad in ("pass", "secret", "hunter2", "abc", "token", "@"):
        if raw and bad in raw and bad not in expected:
            assert bad not in out


# ── _classify_error: controlled categories, never raw text ──

def test_classify_error_categories():
    import socket
    assert sh._classify_error(TimeoutError()) == "timeout"
    assert sh._classify_error(socket.timeout()) == "timeout"
    assert sh._classify_error(socket.gaierror()) == "dns_error"
    assert sh._classify_error(ConnectionRefusedError()) == "connection_refused"
    assert sh._classify_error(OSError("boom")) == "network_error"
    assert sh._classify_error(ValueError("x")) == "error"


# ── Sanitization in subsystem output (blocker #2) ──

def test_searxng_meta_redacts_instance_url():
    s = sh.searxng_health(
        {"search_provider": "searxng",
         "search_url": "http://user:s3cr3t@searx.local:8080/?token=zzz"},
        http_get=lambda url, timeout: _resp(200),
    )
    blob = repr(s)
    assert "s3cr3t" not in blob and "zzz" not in blob and "user:" not in blob
    assert s["meta"]["instance"] == "http://searx.local:8080"


def test_searxng_down_uses_error_category_not_raw_exception():
    def boom(url, timeout):
        raise RuntimeError("failed connecting to http://user:pw@searx.local secret-token")
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://searx.local"},
        http_get=boom,
    )
    assert s["status"] == sh.DOWN
    assert s["meta"]["error"] == "error"           # controlled category token
    assert "secret-token" not in repr(s) and "pw@" not in repr(s)


def test_ntfy_meta_redacts_userinfo_in_base():
    intg = [{"preset": "ntfy", "enabled": True,
             "base_url": "https://user:topsecret@ntfy.example.com"}]
    seen = {}

    def getter(url, timeout):
        seen["url"] = url          # the probe itself may keep credentials
        return _resp(200)

    s = sh.ntfy_health(intg, {"reminder_channel": "ntfy"}, http_get=getter)
    assert s["meta"]["base"] == "https://ntfy.example.com"
    assert "topsecret" not in repr(s)


def test_providers_name_fallback_is_sanitized():
    # No display name → falls back to the base_url, which must be sanitized.
    ep = {"base_url": "http://user:k3y@prov.local:9000/v1?api_key=zzz", "api_key": "sk-x"}
    s = sh.providers_health([ep], probe=lambda b, k, t: ["m1"])
    entry = s["meta"]["endpoints"][0]
    assert entry["name"] == "http://prov.local:9000/v1"
    assert "k3y" not in repr(s) and "zzz" not in repr(s) and "sk-x" not in repr(s)


def test_providers_probe_exception_maps_to_category():
    def boom(base, key, timeout):
        raise RuntimeError(f"500 from {base} with key {key}")  # would leak base+key
    s = sh.providers_health([_ep("a")], probe=boom)
    assert s["status"] == sh.DOWN
    assert s["meta"]["endpoints"][0]["error"] == "error"
    assert "sk-secret" not in repr(s) and "http://a" not in repr(s)


def test_email_connect_exception_maps_to_category():
    def boom(account_id):
        raise RuntimeError("login failed for user bob with password hunter2")
    s = sh.email_health([_acct("a")], connect=boom)
    assert s["status"] == sh.DOWN
    assert s["meta"]["accounts"][0]["error"] == "error"
    assert "hunter2" not in repr(s)


# ── Bounded wall-clock (blocker #1) ──

def test_providers_bounded_marks_slow_as_timeout(monkeypatch):
    import time
    monkeypatch.setattr(sh, "_FANOUT_BUDGET", 1)

    def probe(base, key, timeout):
        if "slow" in base:
            time.sleep(10)          # would blow the budget if unbounded
        return ["m1"]

    eps = [{"name": "fast", "base_url": "http://fast", "api_key": "k"},
           {"name": "slow", "base_url": "http://slow", "api_key": "k"}]
    t0 = time.monotonic()
    out = sh.providers_health(eps, probe=probe)
    elapsed = time.monotonic() - t0
    assert elapsed < 4, f"providers_health not bounded: took {elapsed:.1f}s"
    by = {e["name"]: e for e in out["meta"]["endpoints"]}
    assert by["fast"]["ok"] is True
    assert by["slow"]["ok"] is False and by["slow"]["error"] == "timeout"
    assert out["status"] == sh.DEGRADED


def test_providers_bounded_with_many_slow_endpoints(monkeypatch):
    import time
    monkeypatch.setattr(sh, "_FANOUT_BUDGET", 1)

    def probe(base, key, timeout):
        time.sleep(10)
        return ["m1"]

    eps = [{"name": f"ep{i}", "base_url": f"http://ep{i}", "api_key": "k"}
           for i in range(25)]
    t0 = time.monotonic()
    out = sh.providers_health(eps, probe=probe)
    elapsed = time.monotonic() - t0
    # 25 endpoints * sleep would be huge if sequential; bounded keeps it ~budget.
    assert elapsed < 4, f"not bounded with many endpoints: {elapsed:.1f}s"
    assert out["status"] == sh.DOWN
    assert all(e["error"] == "timeout" for e in out["meta"]["endpoints"])


def test_email_bounded_marks_slow_as_timeout(monkeypatch):
    import time
    monkeypatch.setattr(sh, "_FANOUT_BUDGET", 1)

    def connect(account_id):
        if account_id == "slow":
            time.sleep(10)
        return _Conn()

    accts = [_acct("fast"), _acct("slow")]
    accts[1]["account_id"] = "slow"
    t0 = time.monotonic()
    out = sh.email_health(accts, connect=connect)
    elapsed = time.monotonic() - t0
    assert elapsed < 4, f"email_health not bounded: took {elapsed:.1f}s"
    by = {a["name"]: a for a in out["meta"]["accounts"]}
    assert by["slow"]["error"] == "timeout"


def test_collect_runs_subsystems_concurrently(monkeypatch):
    # The aggregate is bounded by running the (internally-bounded) subsystems
    # concurrently, so total wall-clock ≈ max(subsystem), not the sum. Each of
    # the four network subsystems here sleeps ~0.6s; sequential would be ~2.4s.
    import asyncio
    import time
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {}, "integrations": [], "accounts": [], "endpoints": [],
    })

    def slow(name):
        def _fn(*_a, **_k):
            time.sleep(0.6)
            return {"name": name, "status": sh.OK, "detail": "", "meta": {}}
        return _fn

    monkeypatch.setattr(sh, "searxng_health", slow("searxng"))
    monkeypatch.setattr(sh, "ntfy_health", slow("ntfy"))
    monkeypatch.setattr(sh, "email_health", slow("email"))
    monkeypatch.setattr(sh, "providers_health", slow("providers"))

    t0 = time.monotonic()
    out = asyncio.run(sh.collect_service_health(None, None))
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, f"subsystems not concurrent: took {elapsed:.1f}s"
    assert {s["name"] for s in out["services"]} == {
        "chromadb", "searxng", "ntfy", "email", "providers"}


def test_collect_aggregate_deadline_yields_controlled_result(monkeypatch):
    # If the gather overruns the aggregate ceiling, the response is still a
    # controlled {overall, services, timestamp} with each network subsystem
    # marked down/timeout — never a hang or a raised exception.
    import asyncio
    import time
    monkeypatch.setattr(sh, "_AGGREGATE_DEADLINE", 0.5)
    monkeypatch.setattr(sh, "_SUBSYSTEM_DEADLINE", 0.4)
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {}, "integrations": [], "accounts": [], "endpoints": [],
    })

    async def _slow_gather(*coros, **_k):
        for c in coros:                 # close unawaited coros to avoid warnings
            close = getattr(c, "close", None)
            if close:
                close()
        await asyncio.sleep(5)

    # Force the outer wait_for to trip by making gather itself slow.
    monkeypatch.setattr(sh.asyncio, "gather", _slow_gather)
    t0 = time.monotonic()
    out = asyncio.run(sh.collect_service_health(None, None))
    elapsed = time.monotonic() - t0
    assert elapsed < 2, f"aggregate deadline did not bound: {elapsed:.1f}s"
    assert set(out) == {"overall", "services", "timestamp"}
    net = [s for s in out["services"] if s["name"] != "chromadb"]
    assert all(s["status"] == sh.DOWN and s["meta"].get("error") == "timeout"
               for s in net)
