"""Tests for rollup logic, aggregate collection, and shared utility helpers (_safe_url, _classify_error)."""
import pytest

from src import service_health as sh


class _Store:
    def __init__(self, healthy):
        self.healthy = healthy


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


# ── Concurrent collection and aggregate deadline ──

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


# ── Discovery failure is not "disabled" (#6154) ──
#
# `_gather_inputs()` fails soft to an empty inventory, and every probe reads an
# empty inventory as `disabled`. Without a record of *why* it is empty, a broken
# integrations file / email store / endpoint DB is reported as an intentionally
# unconfigured subsystem — and, with the rest disabled, `overall: "ok"`.

def _boom(*_a, **_k):
    raise RuntimeError("secret-bearing detail: postgres://u:p@db/x")


def test_gather_inputs_records_each_failed_source(monkeypatch):
    import core.database
    import routes.email_helpers
    import src.integrations
    import src.settings

    monkeypatch.setattr(src.settings, "load_settings", lambda: {"a": 1})
    monkeypatch.setattr(src.integrations, "load_integrations", _boom)
    monkeypatch.setattr(routes.email_helpers, "_list_email_accounts", _boom)
    monkeypatch.setattr(core.database, "SessionLocal", _boom)

    out = sh._gather_inputs()
    assert out["settings"] == {"a": 1}
    assert set(out["failed"]) == {"integrations", "accounts", "endpoints"}
    # Controlled categories only — never the exception text.
    assert set(out["failed"].values()) <= set(sh._ERROR_DETAIL)
    assert "postgres" not in repr(out)


def test_gather_inputs_reports_no_failures_when_sources_load(monkeypatch):
    import core.database
    import routes.email_helpers
    import src.integrations
    import src.settings

    class _DB:
        def query(self, *_a):
            return self

        def filter(self, *_a):
            return self

        def all(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(src.settings, "load_settings", lambda: {})
    monkeypatch.setattr(src.integrations, "load_integrations", lambda: [])
    monkeypatch.setattr(routes.email_helpers, "_list_email_accounts", lambda: [])
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _DB())

    assert sh._gather_inputs()["failed"] == {}


@pytest.mark.parametrize("source,service", [
    ("settings", "searxng"),
    ("integrations", "ntfy"),
    ("accounts", "email"),
    ("endpoints", "providers"),
])
def test_failed_source_is_not_reported_as_disabled(monkeypatch, source, service):
    import asyncio
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {"search_provider": "disabled"},
        "integrations": [], "accounts": [], "endpoints": [],
        "failed": {source: "network_error"},
    })
    out = asyncio.run(sh.collect_service_health(None, None))
    entry = next(s for s in out["services"] if s["name"] == service)
    assert entry["status"] != sh.DISABLED
    assert entry["meta"]["error"] == sh.CONFIG_SOURCE_ERROR
    assert entry["meta"]["source"] == source
    assert entry["meta"]["source_error"] == "network_error"
    # A failed source must reach the aggregate verdict.
    assert out["overall"] != sh.OK
    assert set(out) == {"overall", "services", "timestamp"}


def test_all_sources_failing_cannot_report_overall_ok(monkeypatch):
    # The exact shape from the report: settings loads and says search is off,
    # the other three sources raise. Before the fix this was overall "ok" with
    # five "disabled" entries.
    import asyncio
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {"search_provider": "disabled"},
        "integrations": [], "accounts": [], "endpoints": [],
        "failed": {"integrations": "error", "accounts": "error",
                   "endpoints": "error"},
    })
    out = asyncio.run(sh.collect_service_health(None, None))
    by_name = {s["name"]: s for s in out["services"]}
    assert out["overall"] == sh.DEGRADED
    # Loaded-and-genuinely-empty still reads as disabled.
    assert by_name["searxng"]["status"] == sh.DISABLED
    for name in ("ntfy", "email", "providers"):
        assert by_name[name]["status"] == sh.DEGRADED


def test_empty_inventory_without_failure_stays_disabled(monkeypatch):
    import asyncio
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {"search_provider": "disabled"},
        "integrations": [], "accounts": [], "endpoints": [], "failed": {},
    })
    out = asyncio.run(sh.collect_service_health(_Store(True), _Store(True)))
    assert out["overall"] == sh.OK
    assert all(s["status"] == sh.DISABLED
               for s in out["services"] if s["name"] != "chromadb")


def test_one_failed_source_still_probes_the_others(monkeypatch):
    # A broken endpoint DB must not stop ntfy/email from being probed.
    import asyncio
    probed = []

    def _probe(name):
        def _fn(*_a, **_k):
            probed.append(name)
            return sh._svc(name, sh.OK, "probed")
        return _fn

    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {}, "integrations": [], "accounts": [], "endpoints": [],
        "failed": {"endpoints": "timeout"},
    })
    monkeypatch.setattr(sh, "searxng_health", _probe("searxng"))
    monkeypatch.setattr(sh, "ntfy_health", _probe("ntfy"))
    monkeypatch.setattr(sh, "email_health", _probe("email"))
    monkeypatch.setattr(sh, "providers_health", _probe("providers"))

    out = asyncio.run(sh.collect_service_health(None, None))
    assert sorted(probed) == ["email", "ntfy", "searxng"]
    assert "providers" not in probed
    by_name = {s["name"]: s for s in out["services"]}
    assert by_name["providers"]["status"] == sh.DEGRADED
    assert by_name["ntfy"]["status"] == sh.OK


def test_source_failure_entry_carries_no_raw_detail(monkeypatch):
    import asyncio
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {}, "integrations": [], "accounts": [], "endpoints": [],
        "failed": {"accounts": "auth_or_protocol_error"},
    })
    out = asyncio.run(sh.collect_service_health(None, None))
    entry = next(s for s in out["services"] if s["name"] == "email")
    assert entry["detail"] == (
        "Could not read configuration (configuration source could not be read).")
    assert set(entry["meta"]) == {"error", "source", "source_error"}


def test_collect_tolerates_inputs_without_a_failed_key(monkeypatch):
    # `_gather_inputs()` is monkeypatched in a number of existing tests (and by
    # anything calling the collector with a stub); a payload predating `failed`
    # must still probe normally rather than raise.
    import asyncio
    monkeypatch.setattr(sh, "_gather_inputs", lambda: {
        "settings": {"search_provider": "disabled"},
        "integrations": [], "accounts": [], "endpoints": [],
    })
    out = asyncio.run(sh.collect_service_health(_Store(True), _Store(True)))
    assert out["overall"] == sh.OK
