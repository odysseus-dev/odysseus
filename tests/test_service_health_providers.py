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
