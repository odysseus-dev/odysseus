"""Tests for the gateway MCP transport (src/mcp_gateway_transport.py).

Filename contains ``mcp``, so these collect under ``area_services``.
"""

import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def gt(tmp_path, monkeypatch):
    """Load mcp_gateway_transport with the dependency chain stubbed."""
    core = types.ModuleType("core")
    core.__path__ = []
    atomic = types.ModuleType("core.atomic_io")
    atomic.atomic_write_json = lambda p, d, **k: None
    constants = types.ModuleType("src.constants")
    constants.__path__ = []
    constants.DATA_DIR = str(tmp_path)
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "src")]

    for name, mod in (
        ("core", core),
        ("core.atomic_io", atomic),
        ("src.constants", constants),
        ("src", src_pkg),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    import importlib

    sys.modules.pop("src.mcp_gateway_transport", None)
    transport = importlib.import_module("src.mcp_gateway_transport")

    # The SSRF guard resolves DNS and rejects anything that is not a public
    # address. Test hosts like a.example.com do not resolve here, so opt into
    # private addressing for the duration of the test — the guard itself is
    # covered in test_mcp_gateway_pool.py.
    gateway = importlib.import_module("src.mcp_gateway")
    monkeypatch.setattr(gateway, "ALLOW_PRIVATE", True)
    return transport


MANIFEST = {
    "tools": "/ai/tools",
    "route": "/ai/route",
    "chat": "/chat/api/ask",
    "osint_analyze": "/osint/api/analyze",
    "models": "/ai/models",
    "health": "/ai/health",
    "metrics": "/ai/metrics",
    "vitals": "/api/vitals",
    "mcp_resources": "/ai/mcp/resources",
}


# -- tool synthesis -------------------------------------------------------

def test_builds_one_tool_per_callable_route(gt):
    tools = {t["name"]: t for t in gt.build_tools(MANIFEST)}
    assert set(tools) == {"tools", "route", "chat", "osint_analyze", "models"}


def test_excludes_observability_and_infra_routes(gt):
    names = {t["name"] for t in gt.build_tools(MANIFEST)}
    for excluded in ("health", "metrics", "vitals", "mcp_resources",
                     "mcp_read", "abilities", "capabilities"):
        assert excluded not in names, f"{excluded} must not become an agent tool"


def test_tools_carry_a_description(gt):
    for tool in gt.build_tools(MANIFEST):
        assert tool["description"].strip()


def test_tools_use_a_freeform_schema(gt):
    # A fabricated schema would be a guess about what the gateway accepts.
    for tool in gt.build_tools(MANIFEST):
        assert tool["input_schema"]["type"] == "object"
        assert tool["input_schema"]["additionalProperties"] is True


def test_tools_are_not_marked_readonly(gt):
    # route/compose can mutate upstream state; plan mode must be able to block.
    for tool in gt.build_tools(MANIFEST):
        assert tool["annotations"] is None


def test_empty_manifest_yields_no_tools(gt):
    assert gt.build_tools({}) == []


# -- sessions -------------------------------------------------------------

def test_session_keeps_the_full_manifest(gt):
    """Excluded routes are still resolved, just not advertised."""
    s = gt.GatewaySession("gw", "gateway", MANIFEST)
    assert s.manifest["health"] == "/ai/health"
    assert "health" not in {t["name"] for t in s.tools}


def test_session_copies_the_manifest(gt):
    m = dict(MANIFEST)
    s = gt.GatewaySession("gw", "gateway", m)
    m["tools"] = "/mutated"
    assert s.manifest["tools"] == "/ai/tools"


@pytest.mark.asyncio
async def test_call_unknown_route_errors_without_network(gt):
    s = gt.GatewaySession("gw", "gateway", MANIFEST)
    result = await s.call_tool("nonexistent", {})
    assert result.isError is True
    assert "no route named" in result.content[0].text


@pytest.mark.asyncio
async def test_call_with_no_pool_configured_errors(gt):
    from src.mcp_gateway import GatewayPool, set_pool

    set_pool(GatewayPool([]))
    s = gt.GatewaySession("gw", "gateway", MANIFEST)
    result = await s.call_tool("tools", {})
    assert result.isError is True
    assert "ODYSSEUS_GATEWAY_URLS" in result.content[0].text


@pytest.mark.asyncio
async def test_call_dispatches_through_the_pool(gt, monkeypatch):
    """The POST must go to the manifest path on a pooled endpoint."""
    from src.mcp_gateway import GatewayPool, set_pool

    pool = GatewayPool(["https://a.example.com", "https://b.example.com"])
    set_pool(pool)

    seen = {}

    async def fake_request(path, *, method="GET", json_body=None, **kw):
        seen["path"] = path
        seen["method"] = method
        seen["body"] = json_body
        return {"ok": True}

    monkeypatch.setattr(pool, "request", fake_request)
    s = gt.GatewaySession("gw", "gateway", MANIFEST)
    result = await s.call_tool("route", {"q": "hello"})

    assert result.isError is False
    assert seen["path"] == "/ai/route"
    assert seen["method"] == "POST"
    assert seen["body"] == {"q": "hello"}
    assert json.loads(result.content[0].text) == {"ok": True}


@pytest.mark.asyncio
async def test_pool_exhaustion_becomes_a_tool_error(gt, monkeypatch):
    from src.mcp_gateway import GatewayExhausted, GatewayPool, set_pool

    pool = GatewayPool(["https://a.example.com"])
    set_pool(pool)

    async def boom(*a, **k):
        raise GatewayExhausted("all cooling")

    monkeypatch.setattr(pool, "request", boom)
    s = gt.GatewaySession("gw", "gateway", MANIFEST)
    result = await s.call_tool("tools", {})
    assert result.isError is True
    assert "Gateway unavailable" in result.content[0].text


@pytest.mark.asyncio
async def test_unexpected_error_never_raises_into_the_loop(gt, monkeypatch):
    from src.mcp_gateway import GatewayPool, set_pool

    pool = GatewayPool(["https://a.example.com"])
    set_pool(pool)

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(pool, "request", boom)
    s = gt.GatewaySession("gw", "gateway", MANIFEST)
    result = await s.call_tool("tools", {})
    assert result.isError is True
    assert "kaboom" in result.content[0].text


# -- rendering ------------------------------------------------------------

def test_render_truncates_large_payloads(gt):
    out = gt._render({"k": "x" * 20000})
    assert "truncated" in out
    assert len(out) < 9000


def test_render_handles_plain_text(gt):
    assert gt._render("hello") == "hello"


def test_render_handles_none(gt):
    assert gt._render(None) == "None"


def test_render_short_payload_is_not_truncated(gt):
    assert "truncated" not in gt._render({"a": 1})


# -- discovery ------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_returns_none_when_no_endpoints(gt):
    from src.mcp_gateway import GatewayPool, set_pool

    set_pool(GatewayPool([]))
    assert not await gt.connect_gateway("gw", "gateway")


@pytest.mark.asyncio
async def test_connect_returns_none_when_manifest_unusable(gt, monkeypatch):
    from src.mcp_gateway import GatewayPool, set_pool

    pool = GatewayPool(["https://a.example.com"])
    set_pool(pool)

    async def bad_discover(url):
        return {"status": "local"}  # no endpoints map

    monkeypatch.setattr(pool, "discover", bad_discover)
    assert not await gt.connect_gateway("gw", "gateway")


@pytest.mark.asyncio
async def test_connect_returns_none_when_only_health_routes(gt, monkeypatch):
    """A gateway offering only observability is a failed connect, not a
    connected server with zero tools."""
    from src.mcp_gateway import GatewayPool, set_pool

    pool = GatewayPool(["https://a.example.com"])
    set_pool(pool)

    async def health_only(url):
        return {"endpoints": {"health": "/ai/health", "metrics": "/ai/metrics"}}

    monkeypatch.setattr(pool, "discover", health_only)
    assert not await gt.connect_gateway("gw", "gateway")


@pytest.mark.asyncio
async def test_connect_succeeds_and_returns_a_session(gt, monkeypatch):
    from src.mcp_gateway import GatewayPool, set_pool

    pool = GatewayPool(["https://a.example.com"])
    set_pool(pool)

    async def ok(url):
        return {"endpoints": dict(MANIFEST)}

    monkeypatch.setattr(pool, "discover", ok)
    session = await gt.connect_gateway("gw", "gateway")
    assert session is not None
    assert session.manifest["tools"] == "/ai/tools"
    assert {t["name"] for t in session.tools} >= {"tools", "route"}


@pytest.mark.asyncio
async def test_connect_skips_a_failing_endpoint(gt, monkeypatch):
    """One dead gateway must not prevent discovery on a healthy one."""
    from src.mcp_gateway import GatewayPool, set_pool

    pool = GatewayPool(["https://dead.example.com", "https://ok.example.com"])
    set_pool(pool)

    async def maybe(url):
        if "dead" in url:
            raise RuntimeError("gateway down")
        return {"endpoints": {"tools": "/ai/tools"}}

    monkeypatch.setattr(pool, "discover", maybe)
    session = await gt.connect_gateway("gw", "gateway")
    assert session is not None
    assert session.manifest["tools"] == "/ai/tools"


@pytest.mark.asyncio
async def test_connect_discovery_uses_the_pool_per_endpoint(gt, monkeypatch):
    from src.mcp_gateway import GatewayPool, set_pool

    pool = GatewayPool(["https://a.example.com"])
    set_pool(pool)
    probed = []

    async def ok(url):
        probed.append(url)
        return {"endpoints": {"tools": "/ai/tools"}}

    monkeypatch.setattr(pool, "discover", ok)
    await gt.connect_gateway("gw", "gateway")
    assert probed == ["https://a.example.com"]