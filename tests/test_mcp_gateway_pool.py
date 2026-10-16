"""Tests for the MCP gateway round-robin pool (src/mcp_gateway.py).

Filename contains ``mcp``, so these collect under ``area_services``.

The SSRF cases here are deliberate: gateway URLs are operator-supplied and are
fetched server-side, which is the same shape as the gap recorded in
THREAT_MODEL.md (#1039). These tests pin the guard so it cannot regress.
"""

import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    """Import src.mcp_gateway with the app dependency chain stubbed out.

    The real module imports core.atomic_io and src.constants, which pull in
    httpx/sqlalchemy via the package __init__ chain. These tests only exercise
    pure routing state, so a light stub keeps them runnable standalone.
    """
    state = tmp_path / "gateway_state.json"

    core = types.ModuleType("core")
    core.__path__ = []
    atomic = types.ModuleType("core.atomic_io")

    def atomic_write_json(path, data, *, indent=None):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent or 1)
        os.replace(tmp, path)

    atomic.atomic_write_json = atomic_write_json
    constants = types.ModuleType("src.constants")
    constants.__path__ = []
    constants.DATA_DIR = str(tmp_path)
    # Point the stubbed package at the real directory so ``src.mcp_gateway``
    # resolves, while ``src.constants`` stays stubbed.
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "src")]

    for name, mod in (
        ("core", core),
        ("core.atomic_io", atomic),
        ("src", src_pkg),
        ("src.constants", constants),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    sys.modules.pop("src.mcp_gateway", None)
    from src import mcp_gateway as mod

    monkeypatch.setattr(mod, "ALLOW_PRIVATE", True)
    monkeypatch.setattr(mod, "_DEFAULT", None, raising=False)
    return mod, str(state)


# -- round robin ----------------------------------------------------------

def test_pool_acquire_is_round_robin(gw):
    mod, state = gw
    pool = mod.GatewayPool(
        [f"https://{n}.example.com/ai" for n in "abc"], state_path=state
    )
    assert [pool.acquire().base_url[8:9] for _ in range(6)] == ["a", "b", "c", "a", "b", "c"]


def test_single_endpoint_pool_repeats(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    assert all(pool.acquire().base_url.endswith("a.example.com/ai") for _ in range(4))


def test_empty_pool_is_exhausted(gw):
    mod, state = gw
    pool = mod.GatewayPool([], state_path=state)
    with pytest.raises(mod.GatewayExhausted):
        pool.acquire()


def test_disabled_endpoint_is_skipped(gw):
    mod, state = gw
    pool = mod.GatewayPool(
        ["https://a.example.com/ai", "https://b.example.com/ai"], state_path=state
    )
    pool.endpoints[0].enabled = False
    assert pool.acquire().base_url.endswith("b.example.com/ai")


# -- cooldown / failover --------------------------------------------------

def test_cooling_endpoint_is_skipped(gw):
    mod, state = gw
    pool = mod.GatewayPool(
        ["https://a.example.com/ai", "https://b.example.com/ai"], state_path=state
    )
    pool.endpoints[0].cooldown_until = time.time() + 60
    assert {pool.acquire().base_url for _ in range(6)} == {"https://b.example.com/ai"}


def test_all_cooling_raises_exhausted(gw):
    mod, state = gw
    pool = mod.GatewayPool(
        ["https://a.example.com/ai", "https://b.example.com/ai"], state_path=state
    )
    for ep in pool.endpoints:
        ep.cooldown_until = time.time() + 60
    with pytest.raises(mod.GatewayExhausted):
        pool.acquire()


def test_dead_endpoint_is_skipped(gw):
    mod, state = gw
    pool = mod.GatewayPool(
        ["https://a.example.com/ai", "https://b.example.com/ai"], state_path=state
    )
    pool.endpoints[0].dead = True
    assert pool.acquire().base_url.endswith("b.example.com/ai")


def test_cooldown_backs_off_exponentially_and_is_capped(gw):
    mod, state = gw
    monkey = getattr(mod, "BASE_COOLDOWN", 60)
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    ep = pool.endpoints[0]

    delays = []
    for _ in range(6):
        pool._record_fail(ep, "boom")
        delays.append(ep.cooldown_until - time.time())
        ep.cooldown_until = 0.0

    assert delays[0] < delays[1] < delays[2]
    assert max(delays) <= getattr(mod, "MAX_COOLDOWN", 3600) + 1


def test_terminal_failure_marks_dead_without_cooldown(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    ep = pool.endpoints[0]
    pool._record_fail(ep, "HTTP 502", terminal=True)
    assert ep.dead is True
    assert ep.cooling() is False


def test_success_clears_cooldown_and_strikes(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    ep = pool.endpoints[0]
    for _ in range(3):
        pool._record_fail(ep, "boom")
    assert ep.strikes == 3
    pool._record_ok(ep, 0.05)
    assert ep.strikes == 0
    assert ep.cooling() is False
    assert ep.dead is False


def test_success_revives_a_dead_endpoint(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    ep = pool.endpoints[0]
    pool._record_fail(ep, "HTTP 502", terminal=True)
    assert ep.dead is True
    pool._record_ok(ep, 0.05)
    assert ep.dead is False


def test_avg_latency_is_ewma(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    ep = pool.endpoints[0]
    for lat in (0.1, 0.3, 0.2):
        pool._record_ok(ep, lat)
    assert ep.avg_latency == pytest.approx(0.2, abs=1e-6)


# -- persistence ----------------------------------------------------------

def test_state_round_trips_across_instances(gw):
    """Regression: _mutate() must merge on entry, not reload over the write.

    Reloading on exit restored stale on-disk values and silently discarded
    every recorded failure.
    """
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    with pool._mutate():
        pool._record_fail(pool.endpoints[0], "boom")
        pool._record_fail(pool.endpoints[0], "boom")

    reloaded = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    reloaded.load()
    assert reloaded.endpoints[0].strikes == 2
    assert reloaded.endpoints[0].fail_count == 2


def test_repeated_failures_accumulate_on_disk(gw):
    """Each request failure records one _mutate() call, so they must compound.

    Guards the same reload-on-exit bug as above: with the file already present,
    every write after the first was being clobbered by a stale re-read, so
    exponential backoff never actually escalated past its first step.
    """
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)

    delays = []
    for _ in range(5):
        with pool._mutate():
            pool._record_fail(pool.endpoints[0], "boom")
        delays.append(pool.endpoints[0].cooldown_until - time.time())
        pool.endpoints[0].cooldown_until = 0.0

    reloaded = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    reloaded.load()
    assert reloaded.endpoints[0].strikes == 5, "strike count was discarded"
    assert reloaded.endpoints[0].fail_count == 5
    assert delays == sorted(delays), "backoff must escalate across writes"
    assert delays[-1] > delays[0]


def test_revive_persists(gw):
    """Regression: revive() returned True while leaving dead=True on disk."""
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    with pool._mutate():
        pool._record_fail(pool.endpoints[0], "HTTP 502", terminal=True)
    assert pool.revive("https://a.example.com/ai") is True
    assert pool.endpoints[0].dead is False

    reloaded = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    reloaded.load()
    assert reloaded.endpoints[0].dead is False


def test_revive_unknown_endpoint_returns_false(gw):
    mod, state = gw
    pool = mod.GatewayPool([], state_path=state)
    assert pool.revive("https://nope.example.com/ai") is False


def test_load_tolerates_missing_file(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    pool.load()  # must not raise
    assert pool.endpoints[0].strikes == 0


def test_load_tolerates_corrupt_file(gw):
    mod, state = gw
    with open(state, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    pool.load()  # must not raise
    assert pool.endpoints[0].strikes == 0


def test_unwritable_state_path_still_routes(gw):
    mod, _ = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path="/proc/nope/x.json")
    pool.save()  # best-effort, must not raise
    assert pool.acquire().base_url.endswith("a.example.com/ai")


def test_remove_endpoint(gw):
    mod, state = gw
    pool = mod.GatewayPool(
        ["https://a.example.com/ai", "https://b.example.com/ai"], state_path=state
    )
    assert pool.remove("https://a.example.com/ai") is True
    assert len(pool.endpoints) == 1
    assert pool.remove("https://gone.example.com/ai") is False


def test_add_normalizes_trailing_slash(gw):
    mod, state = gw
    pool = mod.GatewayPool([], state_path=state)
    ep = pool.add("https://a.example.com/ai/", persist=False)
    assert ep.base_url == "https://a.example.com/ai"


def test_add_is_idempotent(gw):
    mod, state = gw
    pool = mod.GatewayPool([], state_path=state)
    pool.add("https://a.example.com/ai", persist=False)
    pool.add("https://a.example.com/ai", persist=False)
    assert len(pool.endpoints) == 1


# -- SSRF guard -----------------------------------------------------------

@pytest.mark.parametrize(
    "bad_url",
    [
        "http://a.example.com/ai",        # plaintext
        "https://127.0.0.1/ai",           # loopback
        "https://localhost/ai",           # loopback by name
        "https://192.168.1.5/ai",         # RFC1918
        "https://10.0.0.1/ai",            # RFC1918
        "https://169.254.169.254/ai",     # link-local metadata
    ],
)
def test_rejects_non_public_and_plaintext(gw, bad_url, monkeypatch):
    mod, state = gw
    monkeypatch.setattr(mod, "ALLOW_PRIVATE", False)
    with pytest.raises(mod.GatewayRejected):
        mod.GatewayPool([bad_url], state_path=state)


def test_rejects_url_without_host(gw, monkeypatch):
    mod, state = gw
    monkeypatch.setattr(mod, "ALLOW_PRIVATE", False)
    with pytest.raises(mod.GatewayRejected):
        mod.GatewayPool(["https:///ai"], state_path=state)


def test_allows_public_https_host(gw, monkeypatch):
    mod, state = gw
    monkeypatch.setattr(mod, "ALLOW_PRIVATE", False)
    pool = mod.GatewayPool(["https://htp.sh/ai"], state_path=state)
    assert pool.endpoints[0].base_url == "https://htp.sh/ai"


def test_private_allowed_when_opted_in(gw, monkeypatch):
    mod, state = gw
    monkeypatch.setattr(mod, "ALLOW_PRIVATE", True)
    pool = mod.GatewayPool(["https://192.168.1.5:7000/ai"], state_path=state)
    assert pool.endpoints[0].base_url == "https://192.168.1.5:7000/ai"


# -- status ---------------------------------------------------------------

def test_status_counts_buckets(gw):
    mod, state = gw
    pool = mod.GatewayPool(
        [f"https://{n}.example.com/ai" for n in "abcd"], state_path=state
    )
    a, b, c, _d = pool.endpoints
    a.cooldown_until = time.time() + 60
    b.dead = True
    c.enabled = False
    st = pool.status()
    assert st["count"] == 4
    assert st["cooling"] == 1
    assert st["dead"] == 1
    assert st["available"] == 1


def test_status_endpoint_records_are_serializable(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    pool._record_fail(pool.endpoints[0], "boom")
    json.dumps(pool.status())  # must not raise


def test_peek_returns_none_when_exhausted(gw):
    mod, state = gw
    pool = mod.GatewayPool([], state_path=state)
    assert pool.peek() is None


# -- manifest -------------------------------------------------------------

def test_resolve_path_returns_none_for_unknown(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    assert pool.resolve_path("https://a.example.com/ai", "tools") is None
    assert pool.resolve_path("https://nope.example.com/ai", "tools") is None


def test_resolve_path_uses_cached_manifest(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    pool.endpoints[0].manifest = {"tools": "/ai/tools"}
    assert pool.resolve_path("https://a.example.com/ai", "tools") == "/ai/tools"


def test_manifest_keys_cover_htp_gateway_shape(gw):
    """htp.sh/ai advertises these keys; keep the allowlist in sync with it."""
    mod, _ = gw
    for key in ("models", "chat", "tools", "route", "health", "mcp_resources"):
        assert key in mod.MANIFEST_KEYS


def test_manifest_keys_cover_live_vitals_route(gw):
    """htp.sh/ai advertises /api/vitals; it must not be dropped."""
    mod, _ = gw
    assert "vitals" in mod.MANIFEST_KEYS


def test_manifest_parse_keeps_known_keys_only(gw):
    mod, state = gw
    pool = mod.GatewayPool(["https://a.example.com/ai"], state_path=state)
    # Shape returned by htp.sh/ai at time of writing.
    raw = {
        "status": "local",
        "endpoints": {
            "tools": "/ai/tools", "route": "/ai/route",
            "vitals": "/api/vitals", "made_up": "/nope",
        },
    }
    pool.endpoints[0].manifest = {
        k: raw["endpoints"][k] for k in mod.MANIFEST_KEYS if k in raw["endpoints"]
    }
    assert pool.resolve_path("https://a.example.com/ai", "tools") == "/ai/tools"
    assert pool.resolve_path("https://a.example.com/ai", "vitals") == "/api/vitals"
    assert "made_up" not in pool.endpoints[0].manifest