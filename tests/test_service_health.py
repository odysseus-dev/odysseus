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
