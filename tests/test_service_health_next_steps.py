"""Tests for actionable next-step hints on degraded/down service entries.

ROADMAP: "clear next steps instead of just 'crashed'". Each degraded/down
entry should carry ``meta.next_step`` with a concrete, secret-free action;
ok/disabled entries must NOT carry one.
"""
import types

from src import service_health as sh


def _raise(*_a, **_k):
    raise RuntimeError("connection refused")


def _ep(name):
    return {"name": name, "base_url": f"http://{name}:8000/v1", "api_key": "sk-secret"}


def test_searxng_down_gets_next_step():
    s = sh._enrich_next_step(sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=_raise,
    ))
    assert s["status"] == sh.DOWN
    assert s["meta"]["next_step"]


def test_searxng_ok_has_no_next_step():
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=lambda url, timeout: types.SimpleNamespace(status_code=200),
    )
    assert s["status"] == sh.OK
    assert "next_step" not in s["meta"]


def test_searxng_disabled_has_no_next_step():
    s = sh.searxng_health({"search_provider": "brave"})
    assert s["status"] == sh.DISABLED
    assert "next_step" not in s["meta"]


def test_provider_endpoint_down_item_has_next_step():
    s = sh.providers_health([_ep("a")], probe=_raise)
    item = s["meta"]["endpoints"][0]
    assert item["ok"] is False
    assert item["error"] == "error"
    assert item["next_step"]


def test_provider_endpoint_no_models_has_next_step():
    s = sh.providers_health([_ep("a")], probe=lambda base, key, timeout: [])
    item = s["meta"]["endpoints"][0]
    assert item["ok"] is False
    assert item["error"] == "no_models"
    assert item["next_step"]


def test_email_no_host_item_has_next_step():
    s = sh.email_health([{"account_name": "a", "imap_host": ""}])
    item = s["meta"]["accounts"][0]
    assert item["ok"] is False
    assert item["error"] == "no_host"
    assert item["next_step"]


def test_email_timeout_item_has_next_step(monkeypatch):
    import time
    monkeypatch.setattr(sh, "_FANOUT_BUDGET", 1)

    def slow(_aid):
        time.sleep(10)
    s = sh.email_health([{"account_name": "slow", "imap_host": "imap.x"}],
                        connect=slow)
    item = s["meta"]["accounts"][0]
    assert item["ok"] is False and item["error"] == "timeout"
    assert item["next_step"]


def test_collect_enriches_down_services(monkeypatch):
    import asyncio

    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {}, "integrations": [], "accounts": [], "endpoints": [],
    })
    monkeypatch.setattr(sh, "searxng_health", lambda *a, **k: sh._svc(
        "searxng", sh.DOWN, "down", error="connection_refused"))
    monkeypatch.setattr(sh, "ntfy_health", lambda *a, **k: sh._svc(
        "ntfy", sh.OK, "ok"))
    out = asyncio.run(sh.collect_service_health(None, None))
    by = {s["name"]: s for s in out["services"]}
    assert by["searxng"]["meta"]["next_step"]
    assert by["ntfy"]["meta"].get("next_step") is None


def test_enrich_is_idempotent_and_secret_free():
    s = {"name": "providers", "status": sh.DOWN, "detail": "",
         "meta": {"error": "connection_refused"}}
    first = sh._enrich_next_step(s)["meta"]["next_step"]
    second = sh._enrich_next_step(s)["meta"]["next_step"]
    assert first == second
    assert "user:" not in first and "http://" not in first