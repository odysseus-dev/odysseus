"""Tests for the degraded-state service summary in routes/diagnostics_routes.py.

Covers the pure roll-up/classification helpers and the per-service probe
branches that don't require a live network (disabled / unconfigured / TCP
connect). Network-reachable branches are intentionally not asserted here — the
container driver verifies those live. Async tests rely on pytest-asyncio's
auto mode (configured in pyproject.toml).
"""

import asyncio

import routes.diagnostics_routes as diag
from routes.diagnostics_routes import (
    STATUS_OK,
    STATUS_DOWN,
    STATUS_UNCONFIGURED,
    STATUS_DISABLED,
    STATUS_UNKNOWN,
    classify_searxng_url,
    summarize,
)


# ── classify_searxng_url (pure) ──

def test_classify_prefers_admin_url_over_env():
    assert classify_searxng_url("http://admin:8080/", "http://env:8080") == "http://admin:8080"


def test_classify_falls_back_to_env_when_admin_blank():
    assert classify_searxng_url("   ", "http://env:8080/") == "http://env:8080"


def test_classify_empty_when_nothing_set():
    assert classify_searxng_url("", "") == ""


# ── summarize (pure) ──

def test_summarize_healthy_when_all_ok_or_deliberate():
    services = [
        {"service": "a", "status": STATUS_OK},
        {"service": "b", "status": STATUS_UNCONFIGURED},
        {"service": "c", "status": STATUS_DISABLED},
    ]
    out = summarize(services)
    assert out["overall"] == "healthy"
    assert out["counts"][STATUS_OK] == 1


def test_summarize_degraded_when_any_down():
    services = [
        {"service": "a", "status": STATUS_OK},
        {"service": "b", "status": STATUS_DOWN},
    ]
    assert summarize(services)["overall"] == "degraded"


def test_summarize_partial_when_unknown_but_no_down():
    services = [
        {"service": "a", "status": STATUS_OK},
        {"service": "b", "status": STATUS_UNKNOWN},
    ]
    assert summarize(services)["overall"] == "partial"


def test_summarize_unconfigured_does_not_degrade():
    services = [{"service": "a", "status": STATUS_UNCONFIGURED}]
    assert summarize(services)["overall"] == "healthy"


# ── chromadb probe ──

async def test_chromadb_disabled_when_rag_unavailable():
    out = await diag._probe_chromadb(None, False)
    assert out["service"] == "chromadb"
    assert out["status"] == STATUS_DISABLED


async def test_chromadb_down_when_stats_report_error():
    class _RM:
        def get_stats(self):
            return {"error": "collection missing"}

    out = await diag._probe_chromadb(_RM(), True)
    assert out["status"] == STATUS_DOWN
    assert "collection missing" in out["detail"]


async def test_chromadb_ok_when_stats_clean():
    class _RM:
        def get_stats(self):
            return {"document_count": 7}

    out = await diag._probe_chromadb(_RM(), True)
    assert out["status"] == STATUS_OK
    assert out["stats"] == {"document_count": 7}


async def test_chromadb_down_when_stats_raises():
    class _RM:
        def get_stats(self):
            raise RuntimeError("disk gone")

    out = await diag._probe_chromadb(_RM(), True)
    assert out["status"] == STATUS_DOWN
    assert "disk gone" in out["detail"]


# ── email probe (TCP connect, no creds) ──

async def test_email_unconfigured_when_no_imap_host(monkeypatch):
    import routes.email_helpers as eh

    monkeypatch.setattr(eh, "_get_email_config", lambda *a, **k: {"imap_host": "", "imap_port": 993})
    out = await diag._probe_email()
    assert out["status"] == STATUS_UNCONFIGURED


async def test_email_down_when_host_unreachable(monkeypatch):
    import routes.email_helpers as eh

    # RFC 5737 TEST-NET-1 address — guaranteed non-routable, so the connect
    # fails fast within the probe timeout rather than hitting a real server.
    monkeypatch.setattr(
        eh,
        "_get_email_config",
        lambda *a, **k: {"imap_host": "192.0.2.1", "imap_port": 1},
    )
    monkeypatch.setattr(diag, "_PROBE_TIMEOUT_SECONDS", 0.5)
    out = await diag._probe_email()
    assert out["status"] == STATUS_DOWN
    assert out["host"] == "192.0.2.1"


async def test_email_ok_against_local_listener(monkeypatch):
    """A live TCP listener should report ok (proves the connect-only probe works
    without sending credentials)."""
    import routes.email_helpers as eh

    srv = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        monkeypatch.setattr(
            eh,
            "_get_email_config",
            lambda *a, **k: {"imap_host": "127.0.0.1", "imap_port": port},
        )
        out = await diag._probe_email()
        assert out["status"] == STATUS_OK
        assert out["port"] == port
    finally:
        srv.close()
        await srv.wait_closed()


# ── ntfy probe ──

async def test_ntfy_unconfigured_when_no_integration(monkeypatch):
    import src.integrations as integrations

    monkeypatch.setattr(integrations, "load_integrations", lambda: [])
    out = await diag._probe_ntfy()
    assert out["status"] == STATUS_UNCONFIGURED


# ── never-crash wrapper ──

async def test_run_probe_swallows_exceptions():
    async def _boom():
        raise ValueError("kaboom")

    out = await diag._run_probe(_boom())
    assert out["status"] == STATUS_DOWN
    assert "kaboom" in out["detail"]
