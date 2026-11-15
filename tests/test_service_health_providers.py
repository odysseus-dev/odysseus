"""Tests for providers_health — probe logic, status classification, sanitization, and bounded timeout."""
import pytest

from src import service_health as sh


def _raise(*_a, **_k):
    raise RuntimeError("connection refused")


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


def test_providers_invalid_or_missing_base_url_fails_fast_without_probing():
    called = []

    def probe(base, key, timeout):
        called.append(base)
        return ["m1"]

    eps = [
        {"name": "empty", "base_url": "   ", "api_key": "sk-1"},
        {"name": "bad-scheme", "base_url": "ftp://local:8000/v1", "api_key": "sk-2"},
        {"name": "no-host", "base_url": "http://", "api_key": "sk-3"},
    ]
    out = sh.providers_health(eps, probe=probe)
    assert called == []
    assert out["status"] == sh.DOWN
    for entry in out["meta"]["endpoints"]:
        assert entry["ok"] is False
        assert entry["error"] == "invalid_url"
        assert entry["latency_ms"] == 0
    assert "sk-1" not in repr(out) and "sk-2" not in repr(out) and "sk-3" not in repr(out)


def test_providers_records_latency_ms_and_classifies_http_auth_errors():
    def probe(base, key, timeout):
        if "unauth" in base:
            raise ValueError(f"401 Unauthorized for {key}")
        return ["llama-3"]

    eps = [
        {"name": "ok-ep", "base_url": "http://ok-ep:8000/v1", "api_key": "k1"},
        {"name": "unauth-ep", "base_url": "http://unauth-ep:8000/v1", "api_key": "super-secret"},
    ]
    out = sh.providers_health(eps, probe=probe)
    assert out["status"] == sh.DEGRADED
    by = {e["name"]: e for e in out["meta"]["endpoints"]}
    assert by["ok-ep"]["ok"] is True
    assert isinstance(by["ok-ep"]["latency_ms"], int) and by["ok-ep"]["latency_ms"] >= 0
    assert by["unauth-ep"]["ok"] is False
    assert by["unauth-ep"]["error"] in {"auth_or_protocol_error", "error"}
    assert "super-secret" not in repr(out)


@pytest.mark.asyncio
async def test_collect_service_health_reports_degraded_when_endpoint_inventory_fails(monkeypatch):
    monkeypatch.setattr(
        sh,
        "_gather_inputs",
        lambda: {
            "settings": {},
            "integrations": [],
            "accounts": [],
            "endpoints": [],
            "failed": {"endpoints": "error"},
        },
    )
    report = await sh.collect_service_health(rag_manager=None, memory_vector=None)
    by_svc = {s["name"]: s for s in report["services"]}
    assert by_svc["providers"]["status"] == sh.DEGRADED
    assert by_svc["providers"]["meta"]["error"] == sh.CONFIG_SOURCE_ERROR
    assert report["overall"] in {sh.DEGRADED, sh.DOWN}
