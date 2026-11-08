"""Regression tests for SSRF / privilege-boundary fixes.

1. The agent web-fetch blocklist (src/outbound_fetch.py) must reject RFC 6598
   shared address space (100.64.0.0/10): Tailscale nodes and Alibaba Cloud's
   metadata endpoint 100.100.100.200 live there.
2. /api/cookbook/vllm-recipe must not let `..` segments in `repo` steer the
   raw.githubusercontent.com fetch to another repository, and its per-repo
   cache must stay bounded.
"""
import asyncio
import ipaddress
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as cookbook_routes
from src.outbound_fetch import _is_private_address, _public_http_url


# ── 1. CGNAT / shared address space in the outbound fetch blocklist ─────────

@pytest.mark.parametrize(
    "addr",
    ["100.64.1.1", "100.100.100.200", "100.64.0.0", "100.127.255.255", "::ffff:100.100.100.200"],
)
def test_shared_address_space_is_private(addr):
    assert _is_private_address(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", ["100.63.255.255", "100.128.0.0", "8.8.8.8"])
def test_neighbouring_public_addresses_stay_allowed(addr):
    assert _is_private_address(ipaddress.ip_address(addr)) is False


def test_public_http_url_rejects_host_resolving_to_alibaba_metadata():
    def resolver(_host):
        return [ipaddress.ip_address("100.100.100.200")]

    assert _public_http_url("http://innocent.example/latest/meta-data", resolver=resolver) is False


# ── 2. vllm-recipe path steering and cache bound ────────────────────────────

class _FakeHttpxClient:
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        type(self).calls.append(url)
        return SimpleNamespace(status_code=404, text="Not Found")


@pytest.fixture
def recipe_app(monkeypatch):
    _FakeHttpxClient.calls = []
    # The handler does `import httpx as _httpx` and `_httpx.Client(...)`.
    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    router = cookbook_routes.setup_cookbook_routes()
    app = FastAPI()
    app.include_router(router)
    endpoint = next(
        r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/cookbook/vllm-recipe"
    )
    closure = dict(zip(endpoint.__code__.co_freevars, (c.cell_contents for c in endpoint.__closure__)))
    return app, closure["_vllm_recipe_cache"]


async def _get_many(app, repos, after_each=None):
    transport = httpx.ASGITransport(app=app)
    out = []
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for repo in repos:
            resp = await client.get("/api/cookbook/vllm-recipe", params={"repo": repo})
            assert resp.status_code == 200
            out.append(resp.json())
            if after_each:
                after_each()
    return out


@pytest.mark.parametrize(
    "repo",
    [
        "x/../../../../../attacker/repo/main/payload",
        "org/../other",
        "org/model/../../x",
        "a/b/c",
        "../org/model",
        "org/..",
    ],
)
def test_vllm_recipe_rejects_path_steering_without_fetching(recipe_app, repo):
    app, cache = recipe_app
    (body,) = asyncio.run(_get_many(app, [repo]))
    assert body["exists"] is False
    assert _FakeHttpxClient.calls == []
    assert cache == {}


def test_vllm_recipe_valid_repo_still_fetches_upstream(recipe_app):
    app, cache = recipe_app
    (body,) = asyncio.run(_get_many(app, ["MiniMaxAI/MiniMax-M2"]))
    assert body == {"exists": False}
    assert _FakeHttpxClient.calls == [
        "https://raw.githubusercontent.com/vllm-project/recipes/main/models/MiniMaxAI/MiniMax-M2.yaml"
    ]
    assert "MiniMaxAI/MiniMax-M2" in cache


def test_vllm_recipe_cache_is_bounded(recipe_app):
    app, cache = recipe_app
    bound = 256
    repos = []
    for i in range(400):
        repos.append(f"org{i}/model-{i}")  # valid id, upstream 404 -> cached
        repos.append(f"org{i}/../evil-{i}")  # invalid id, never cached
    sizes = []
    asyncio.run(_get_many(app, repos, after_each=lambda: sizes.append(len(cache))))
    assert max(sizes) <= bound
    assert len(cache) == bound
    assert "org399/model-399" in cache  # newest kept
    assert "org0/model-0" not in cache  # oldest evicted
    assert all(".." not in key for key in cache)
    assert len(_FakeHttpxClient.calls) == 400
