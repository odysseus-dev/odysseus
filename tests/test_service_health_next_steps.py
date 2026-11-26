"""Tests for actionable next-step hints on degraded/down service entries.

ROADMAP: "clear next steps instead of just 'crashed'". Each degraded/down
entry should carry ``meta.next_step`` with a concrete, secret-free action;
ok/disabled entries must NOT carry one.
"""
import asyncio
import types
import pytest

from src import service_health as sh


def _raise(*_a, **_k):
    raise RuntimeError("connection refused")


def _ep(name):
    return {"name": name, "base_url": f"http://{name}:8000/v1", "api_key": "sk-secret"}


# ── Failure category coverage & fallback ──

@pytest.mark.parametrize("category", list(sh._NEXT_STEP.keys()))
def test_all_supported_failure_categories_have_actionable_hints(category):
    hint = sh._next_step_for(category)
    assert isinstance(hint, str) and len(hint) > 15
    # Hints must guide the operator on where to look or what to check.
    assert any(term in hint.lower() for term in ("check", "see", "settings", "logs"))
    # Hints must never include raw URLs with credentials or secrets.
    assert not any(sec in hint for sec in ("http://", "https://", "user:", "password", "sk-"))


def test_unknown_category_uses_safe_fallback():
    fallback = sh._next_step_for("unexpected_unknown_category_xyz")
    assert fallback == sh._DEFAULT_NEXT_STEP
    assert "Settings" in fallback and "/api/diagnostics/logs" in fallback


def test_none_category_defaults_to_error_hint():
    hint = sh._next_step_for(None)
    assert hint == sh._NEXT_STEP["error"]


# ── ok / disabled entries must NOT carry next_step ──

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
    # Even after explicit enrichment call, ok services stay hint-free.
    enriched = sh._enrich_next_step(s)
    assert "next_step" not in enriched["meta"]


def test_searxng_disabled_has_no_next_step():
    s = sh.searxng_health({"search_provider": "brave"})
    assert s["status"] == sh.DISABLED
    assert "next_step" not in s["meta"]
    enriched = sh._enrich_next_step(s)
    assert "next_step" not in enriched["meta"]


# ── Per-item probe results: success has no hint, failure does ──

def test_provider_endpoint_success_item_has_no_next_step():
    s = sh.providers_health([_ep("a")], probe=lambda base, key, timeout: ["gpt-4o"])
    item = s["meta"]["endpoints"][0]
    assert item["ok"] is True
    assert item["error"] is None
    assert "next_step" not in item


def test_provider_endpoint_down_item_has_next_step():
    s = sh.providers_health([_ep("a")], probe=_raise)
    item = s["meta"]["endpoints"][0]
    assert item["ok"] is False
    assert item["error"] == "error"
    assert item["next_step"] == sh._NEXT_STEP["error"]


def test_provider_endpoint_no_models_has_next_step():
    s = sh.providers_health([_ep("a")], probe=lambda base, key, timeout: [])
    item = s["meta"]["endpoints"][0]
    assert item["ok"] is False
    assert item["error"] == "no_models"
    assert item["next_step"] == sh._NEXT_STEP["no_models"]


def test_email_success_item_has_no_next_step():
    mock_conn = types.SimpleNamespace(logout=lambda: None)
    s = sh.email_health([{"account_name": "a", "imap_host": "imap.example.com"}],
                        connect=lambda aid: mock_conn)
    item = s["meta"]["accounts"][0]
    assert item["ok"] is True
    assert item["error"] is None
    assert "next_step" not in item


def test_email_no_host_item_has_next_step():
    s = sh.email_health([{"account_name": "a", "imap_host": ""}])
    item = s["meta"]["accounts"][0]
    assert item["ok"] is False
    assert item["error"] == "no_host"
    assert item["next_step"] == sh._NEXT_STEP["no_host"]


def test_email_timeout_item_has_next_step(monkeypatch):
    import time
    monkeypatch.setattr(sh, "_FANOUT_BUDGET", 1)

    def slow(_aid):
        time.sleep(10)
    s = sh.email_health([{"account_name": "slow", "imap_host": "imap.x"}],
                        connect=slow)
    item = s["meta"]["accounts"][0]
    assert item["ok"] is False and item["error"] == "timeout"
    assert item["next_step"] == sh._NEXT_STEP["timeout"]


# ── Secret-safety & Idempotency ──

def test_enrich_is_idempotent_and_secret_free():
    s = {"name": "providers", "status": sh.DOWN, "detail": "",
         "meta": {"error": "connection_refused"}}
    first = sh._enrich_next_step(s)["meta"]["next_step"]
    second = sh._enrich_next_step(s)["meta"]["next_step"]
    assert first == second
    assert "user:" not in first and "http://" not in first


def test_hints_do_not_leak_secrets_from_exceptions_or_urls():
    secret_url = "http://superadmin:supersecret_pwd_98765@internal.corp:8080/v1"
    secret_key = "sk-live-supersecretkey999"

    def sensitive_raise(*_a, **_k):
        raise RuntimeError(f"Failed connecting to {secret_url} with key {secret_key}")

    s = sh.providers_health([{"name": "test_ep", "base_url": secret_url, "api_key": secret_key}],
                            probe=sensitive_raise)
    item = s["meta"]["endpoints"][0]
    assert item["ok"] is False
    assert "supersecret_pwd" not in item["next_step"]
    assert "sk-live" not in item["next_step"]
    assert "superadmin" not in item["next_step"]


# ── Collector integration: timeouts and configuration-source failures ──

def test_collect_enriches_down_services(monkeypatch):
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {}, "integrations": [], "accounts": [], "endpoints": [],
    })
    monkeypatch.setattr(sh, "searxng_health", lambda *a, **k: sh._svc(
        "searxng", sh.DOWN, "down", error="connection_refused"))
    monkeypatch.setattr(sh, "ntfy_health", lambda *a, **k: sh._svc(
        "ntfy", sh.OK, "ok"))
    out = asyncio.run(sh.collect_service_health(None, None))
    by = {s["name"]: s for s in out["services"]}
    assert by["searxng"]["meta"]["next_step"] == sh._NEXT_STEP["connection_refused"]
    assert by["ntfy"]["meta"].get("next_step") is None


def test_collect_enriches_config_source_failures_and_subsystem_timeouts(monkeypatch):
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {}, "integrations": [], "accounts": [], "endpoints": [],
        "failed": {"accounts": "auth_or_protocol_error"},
    })

    def hang_probe(*_a, **_k):
        import time
        time.sleep(10)

    monkeypatch.setattr(sh, "_SUBSYSTEM_DEADLINE", 0.05)
    monkeypatch.setattr(sh, "searxng_health", hang_probe)
    monkeypatch.setattr(sh, "ntfy_health", lambda *a, **k: sh._svc("ntfy", sh.DISABLED, "disabled"))

    out = asyncio.run(sh.collect_service_health(None, None))
    by = {s["name"]: s for s in out["services"]}

    # Configuration source failure path
    email = by["email"]
    assert email["status"] == sh.DEGRADED
    assert email["meta"]["error"] == sh.CONFIG_SOURCE_ERROR
    assert email["meta"]["next_step"] == sh._NEXT_STEP["config_source_error"]

    # Subsystem timeout path
    searxng = by["searxng"]
    assert searxng["status"] == sh.DOWN
    assert searxng["meta"]["error"] == "timeout"
    assert searxng["meta"]["next_step"] == sh._NEXT_STEP["timeout"]

    # Disabled subsystem has no hint
    assert "next_step" not in by["ntfy"]["meta"]
