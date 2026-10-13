"""Tests for Featherless provider detection, setup, lazy discovery, and catalog search."""

import asyncio
import json
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from tests.helpers.import_state import clear_fake_endpoint_resolver_modules, preserve_import_state

with preserve_import_state("core.database", "src.database", "core.session_manager", "routes.model_routes"):
    clear_fake_endpoint_resolver_modules()

    if "core.database" not in sys.modules:
        _core_db = types.ModuleType("core.database")
        for _name in [
            "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
            "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
            "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun",
            "McpServer", "ProviderAuthSession", "Base",
        ]:
            setattr(_core_db, _name, MagicMock())
        _core_db.utcnow_naive = MagicMock()
        sys.modules["core.database"] = _core_db

    import routes.model_routes as model_routes
    import src.llm_core as llm_core
    from routes.model_routes import (
        _effective_endpoint_kind,
        _probe_endpoint,
        _ping_endpoint,
        _picker_requires_pinning,
        _has_explicit_pinned_models,
        _picker_models_for_endpoint,
        _featherless_search_cache,
        _featherless_search_cache_lock,
    )
    from src.llm_core import (
        _detect_provider,
        _provider_label,
        _is_self_hosted_openai_compatible,
    )

_REPO = Path(__file__).resolve().parent.parent
_ADMIN_JS = _REPO / "static" / "js" / "admin.js"
_ROUTER = model_routes.setup_model_routes(model_discovery=None)
_should_refresh_endpoint = _ROUTER._should_refresh_endpoint
search_endpoint_catalog = _ROUTER._search_endpoint_catalog


def _route_endpoint(router, path, method="GET"):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


# ============================================================
# 1. Provider Detection & Identification
# ============================================================

def test_featherless_provider_detection():
    url = "https://api.featherless.ai/v1"
    assert _detect_provider(url) == "featherless"
    assert _provider_label(url) == "Featherless.ai"
    assert _is_self_hosted_openai_compatible(url) is False

    subdomain_url = "https://eu.featherless.ai/v1"
    assert _detect_provider(subdomain_url) == "featherless"
    assert _provider_label(subdomain_url) == "Featherless.ai"
    assert _is_self_hosted_openai_compatible(subdomain_url) is False


def test_featherless_endpoint_kind_is_api_not_proxy():
    ep = SimpleNamespace(endpoint_kind="auto", api_key="sk-test-key")
    url = "https://api.featherless.ai/v1"
    # Keyed /v1 URLs normally resolve to 'proxy', but Featherless must resolve to 'api'
    assert _effective_endpoint_kind(ep, url) == "api"


# ============================================================
# 2. Probing & Setup Validation
# ============================================================

def test_featherless_probe_endpoint_bypasses_full_catalog():
    with patch("httpx.get") as mock_get:
        models = _probe_endpoint("https://api.featherless.ai/v1", api_key="sk-test-key")
        # Probe must immediately return [] without making any HTTP request to fetch 20k+ models
        assert models == []
        mock_get.assert_not_called()


def test_featherless_ping_endpoint_plan_success():
    resp_plan = MagicMock()
    resp_plan.status_code = 200
    resp_plan.text = '{"plan": "pro"}'

    with patch("httpx.get", return_value=resp_plan) as mock_get:
        res = _ping_endpoint("https://api.featherless.ai/v1", api_key="sk-test-key")
        assert res["reachable"] is True
        assert res["status_code"] == 200
        assert res["error"] is None
        mock_get.assert_called_once()
        assert "plan" in mock_get.call_args[0][0]


def test_featherless_ping_endpoint_plan_fallback_to_models():
    # If /v1/plan returns 404, fallback to /v1/models with per_page=1
    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.text = "Not found"

    resp_models = MagicMock()
    resp_models.status_code = 200
    resp_models.text = '{"data": [{"id": "model1"}]}'

    with patch("httpx.get", side_effect=[resp_404, resp_models]) as mock_get:
        res = _ping_endpoint("https://api.featherless.ai/v1", api_key="sk-test-key")
        assert res["reachable"] is True
        assert res["status_code"] == 200
        assert mock_get.call_count == 2
        assert "available_on_current_plan=true" in mock_get.call_args_list[1][0][0]
        assert "per_page=1" in mock_get.call_args_list[1][0][0]


def test_featherless_ping_endpoint_auth_failure():
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.text = "Unauthorized"

    with patch("httpx.get", return_value=resp_401):
        res = _ping_endpoint("https://api.featherless.ai/v1", api_key="bad-key")
        assert res["reachable"] is False
        assert res["status_code"] == 401
        assert "Featherless API key invalid or unauthorized" in res["error"]


# ============================================================
# 3. Background Refresh & Catalog Protection
# ============================================================

def test_featherless_should_refresh_endpoint_returns_false():
    ep = SimpleNamespace(
        id="ep-fl",
        base_url="https://api.featherless.ai/v1",
        api_key="sk-test",
        provider_auth_id=None,
        cached_models=None,
        pinned_models="[]",
    )
    should_refresh, info = _should_refresh_endpoint(ep, time.time())
    assert should_refresh is False
    assert info["base"] == "https://api.featherless.ai/v1"


# ============================================================
# 4. Pinning & Chat Picker Isolation
# ============================================================

def test_featherless_picker_models_initially_empty():
    url = "https://api.featherless.ai/v1"
    kind = "api"
    assert _picker_requires_pinning(url, kind) is True

    ep = SimpleNamespace(
        base_url=url,
        endpoint_kind=kind,
        pinned_models="[]",
        cached_models=None,
        hidden_models=None,
    )
    assert _has_explicit_pinned_models(ep) is True
    visible, pinned = _picker_models_for_endpoint(ep, url, kind)
    # Default state has 0 models enabled
    assert visible == []
    assert pinned == []


def test_featherless_picker_models_reflects_pinned_only():
    url = "https://api.featherless.ai/v1"
    kind = "api"
    ep = SimpleNamespace(
        base_url=url,
        endpoint_kind=kind,
        pinned_models=json.dumps(["mistralai/Mistral-7B-Instruct-v0.2", "meta-llama/Llama-3-8B-Instruct"]),
        cached_models=None,
        hidden_models=None,
    )
    visible, pinned = _picker_models_for_endpoint(ep, url, kind)
    assert visible == ["mistralai/Mistral-7B-Instruct-v0.2", "meta-llama/Llama-3-8B-Instruct"]
    assert pinned == ["mistralai/Mistral-7B-Instruct-v0.2", "meta-llama/Llama-3-8B-Instruct"]


# ============================================================
# 5. Catalog Search Route
# ============================================================

@pytest.mark.asyncio
async def test_featherless_catalog_search_validation():
    # q < 2 chars raises HTTPException(400)
    req = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        await search_endpoint_catalog("ep-1", req, q="a")
    assert exc_info.value.status_code == 400
    assert "at least 2 characters" in exc_info.value.detail


class _FakeQuery:
    def __init__(self, ep):
        self.ep = ep

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return [self.ep] if self.ep else []

    def first(self):
        return self.ep


class _FakeDb:
    def __init__(self, ep):
        self.ep = ep

    def query(self, *args, **kwargs):
        return _FakeQuery(self.ep)

    def close(self):
        pass


def test_create_featherless_endpoint(monkeypatch):
    create = _route_endpoint(_ROUTER, "/api/model-endpoints", "POST")
    added = []
    class FakeDb:
        def __init__(self):
            self.added = added
        def query(self, *args, **kwargs):
            return _FakeQuery(None)
        def add(self, row):
            self.added.append(row)
        def commit(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(model_routes, "SessionLocal", FakeDb)
    monkeypatch.setattr(model_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(model_routes, "_ping_endpoint", lambda *a, **kw: {"reachable": True, "error": None})
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "_save_settings", lambda s: None)

    req = MagicMock()
    result = create(
        req,
        base_url="https://api.featherless.ai/v1",
        name="",
        api_key="sk-test",
        skip_probe="false",
        require_models="false",
        model_type="llm",
        endpoint_kind="auto",
        model_refresh_mode="",
        model_refresh_interval="",
        model_refresh_timeout="",
        supports_tools="",
        pinned_models="",
        container_local="false",
        shared="true",
    )

    assert result["name"] == "Featherless.ai"
    assert result["endpoint_kind"] == "api"
    assert result["pinned_models"] == []
    assert result["models"] == []
    assert result["online"] is True
    assert result["status"] == "online"

    assert len(added) == 1
    ep = added[0]
    assert ep.name == "Featherless.ai"
    assert ep.endpoint_kind == "api"
    assert ep.pinned_models == "[]"
    assert ep.cached_models is None


def test_list_featherless_endpoint(monkeypatch):
    list_ep = _route_endpoint(_ROUTER, "/api/model-endpoints", "GET")
    ep = SimpleNamespace(
        id="ep-fl",
        name="Featherless.ai",
        base_url="https://api.featherless.ai/v1",
        api_key="sk-test",
        is_enabled=True,
        cached_models=None,
        pinned_models="[]",
        hidden_models=None,
        endpoint_kind="api",
        model_type="llm",
        supports_tools=None,
        model_refresh_mode="auto",
        model_refresh_interval=None,
        model_refresh_timeout=None,
        owner=None,
        created_at=None,
        updated_at=None,
    )
    class FakeDb:
        def query(self, *args, **kwargs):
            m = MagicMock()
            m.order_by.return_value.all.return_value = [ep]
            return m
        def close(self):
            pass

    monkeypatch.setattr(model_routes, "SessionLocal", FakeDb)
    monkeypatch.setattr(model_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(model_routes, "_disable_stale_cookbook_local_endpoints", lambda db: False)

    req = MagicMock()
    results = list_ep(req)
    assert len(results) == 1
    r = results[0]
    assert r["name"] == "Featherless.ai"
    assert r["status"] == "online"
    assert r["online"] is True
    assert r["model_count"] == 0
    assert r["models"] == []
    assert r["pinned_models"] == []


@pytest.mark.asyncio
async def test_featherless_catalog_search_non_featherless_endpoint(monkeypatch):
    req = MagicMock()
    ep_mock = SimpleNamespace(
        id="ep-openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
    )
    monkeypatch.setattr(model_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(model_routes, "_chatgpt_endpoint_visible", lambda ep, req: True)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb(ep_mock))

    with pytest.raises(HTTPException) as exc_info:
        await search_endpoint_catalog("ep-openai", req, q="gpt")
    assert exc_info.value.status_code == 400
    assert "only supported for Featherless" in exc_info.value.detail


@pytest.mark.asyncio
async def test_featherless_catalog_search_success_and_caching(monkeypatch):
    req = MagicMock()
    ep_mock = SimpleNamespace(
        id="ep-fl",
        base_url="https://api.featherless.ai/v1",
        api_key="sk-test-key",
    )

    upstream_data = {
        "data": [
            {
                "id": "mistralai/Mistral-7B-Instruct-v0.2",
                "name": "Mistral 7B Instruct v0.2",
                "context_length": 32768,
                "max_completion_tokens": 8192,
                "is_gated": False,
                "available_on_current_plan": True,
            },
            {
                "id": "meta-llama/Meta-Llama-3-8B-Instruct",
                "context_length": 8192,
            },
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = upstream_data

    # Clear cache before test
    with _featherless_search_cache_lock:
        _featherless_search_cache.clear()

    monkeypatch.setattr(model_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(model_routes, "_chatgpt_endpoint_visible", lambda ep, req: True)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb(ep_mock))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as mock_async_get:
        res1 = await search_endpoint_catalog("ep-fl", req, q="mistral", page=1, per_page=50)
        assert len(res1["items"]) == 2
        assert res1["items"][0]["id"] == "mistralai/Mistral-7B-Instruct-v0.2"
        assert res1["items"][0]["context_length"] == 32768
        assert res1["items"][1]["name"] == "meta-llama/Meta-Llama-3-8B-Instruct"
        assert res1["page"] == 1
        assert res1["per_page"] == 50
        assert mock_async_get.call_count == 1

        # Check upstream call parameters
        call_kwargs = mock_async_get.call_args[1]
        assert call_kwargs["params"]["q"] == "mistral"
        assert "search" not in call_kwargs["params"]
        assert call_kwargs["params"]["available_on_current_plan"] == "true"
        assert call_kwargs["params"]["status"] == "active"
        assert call_kwargs["params"]["conversational"] == "true"
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-key"

        # Second call with same query should hit in-memory cache without calling upstream
        res2 = await search_endpoint_catalog("ep-fl", req, q="mistral", page=1, per_page=50)
        assert res2 == res1
        assert mock_async_get.call_count == 1  # Not incremented!


@pytest.mark.asyncio
async def test_featherless_catalog_search_exact_upstream_params(monkeypatch):
    """Proves exact upstream query parameters: q (not search), filters, page, bounded per_page."""
    req = MagicMock()
    ep_mock = SimpleNamespace(
        id="ep-fl",
        base_url="https://api.featherless.ai/v1",
        api_key="sk-test-key",
    )

    with _featherless_search_cache_lock:
        _featherless_search_cache.clear()

    monkeypatch.setattr(model_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(model_routes, "_chatgpt_endpoint_visible", lambda ep, req: True)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb(ep_mock))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "deepseek-ai/DeepSeek-V3"}]}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as mock_async_get:
        # Standard query
        await search_endpoint_catalog("ep-fl", req, q="deepseek", page=2, per_page=50)
        assert mock_async_get.call_count == 1
        call_kwargs = mock_async_get.call_args[1]
        params = call_kwargs["params"]

        assert params["q"] == "deepseek"
        assert "search" not in params
        assert params["available_on_current_plan"] == "true"
        assert params["status"] == "active"
        assert params["conversational"] == "true"
        assert params["page"] == 2
        assert params["per_page"] == 50

        # Bounded per_page: upper bound (500 -> 100)
        await search_endpoint_catalog("ep-fl", req, q="deepseek-high", page=1, per_page=500)
        params_upper = mock_async_get.call_args[1]["params"]
        assert params_upper["per_page"] == 100

        # Bounded page and per_page: lower bound (page 0 -> 1, per_page -5 -> 1)
        await search_endpoint_catalog("ep-fl", req, q="deepseek-low", page=0, per_page=-5)
        params_lower = mock_async_get.call_args[1]["params"]
        assert params_lower["page"] == 1
        assert params_lower["per_page"] == 1


@pytest.mark.asyncio
async def test_featherless_catalog_pagination_defensive_behavior(monkeypatch):
    """Tests defensive pagination: total/count metadata vs data-only fallback."""
    req = MagicMock()
    ep_mock = SimpleNamespace(
        id="ep-fl",
        base_url="https://api.featherless.ai/v1",
        api_key="sk-test-key",
    )

    with _featherless_search_cache_lock:
        _featherless_search_cache.clear()

    monkeypatch.setattr(model_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(model_routes, "_chatgpt_endpoint_visible", lambda ep, req: True)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb(ep_mock))

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as mock_async_get:
        # Case 1: Response with total metadata (page 1 * 50 = 50 < 120 => has_more=True)
        mock_resp.json.return_value = {
            "data": [{"id": f"model-{i}"} for i in range(50)],
            "total": 120,
        }
        res1 = await search_endpoint_catalog("ep-fl", req, q="query1", page=1, per_page=50)
        assert res1["has_more"] is True
        assert res1["total"] == 120
        assert len(res1["items"]) == 50

        # Case 2: Response with total metadata reached (page 1 * 50 = 50 >= 50 => has_more=False)
        mock_resp.json.return_value = {
            "data": [{"id": f"model-{i}"} for i in range(50)],
            "total": 50,
        }
        res2 = await search_endpoint_catalog("ep-fl", req, q="query2", page=1, per_page=50)
        assert res2["has_more"] is False
        assert res2["total"] == 50

        # Case 3: Response with count metadata reached (page 2 * 50 = 100 >= 80 => has_more=False)
        mock_resp.json.return_value = {
            "data": [{"id": f"model-{i}"} for i in range(30)],
            "count": 80,
        }
        res3 = await search_endpoint_catalog("ep-fl", req, q="query3", page=2, per_page=50)
        assert res3["has_more"] is False
        assert res3["total"] == 80

        # Case 4: Response containing ONLY {"data": [...]} with exactly per_page items => conservative has_more=True
        mock_resp.json.return_value = {
            "data": [{"id": f"model-{i}"} for i in range(50)],
        }
        res4 = await search_endpoint_catalog("ep-fl", req, q="query4", page=1, per_page=50)
        assert res4["has_more"] is True
        assert "total" not in res4
        assert len(res4["items"]) == 50

        # Case 5: Response containing ONLY {"data": [...]} with fewer than per_page items => has_more=False
        mock_resp.json.return_value = {
            "data": [{"id": f"model-{i}"} for i in range(49)],
        }
        res5 = await search_endpoint_catalog("ep-fl", req, q="query5", page=1, per_page=50)
        assert res5["has_more"] is False
        assert "total" not in res5
        assert len(res5["items"]) == 49

        # Case 6: Response containing ONLY {"data": []} => has_more=False
        mock_resp.json.return_value = {
            "data": [],
        }
        res6 = await search_endpoint_catalog("ep-fl", req, q="query6", page=1, per_page=50)
        assert res6["has_more"] is False
        assert "total" not in res6
        assert len(res6["items"]) == 0


@pytest.mark.asyncio
async def test_featherless_catalog_search_error_handling(monkeypatch):
    req = MagicMock()
    ep_mock = SimpleNamespace(
        id="ep-fl",
        base_url="https://api.featherless.ai/v1",
        api_key="sk-test-key",
    )

    monkeypatch.setattr(model_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(model_routes, "_chatgpt_endpoint_visible", lambda ep, req: True)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb(ep_mock))

    # 401 Unauthorized
    resp_401 = MagicMock()
    resp_401.status_code = 401
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=resp_401):
        with pytest.raises(HTTPException) as exc_401:
            await search_endpoint_catalog("ep-fl", req, q="llama")
        assert exc_401.value.status_code == 401
        assert "API key invalid" in exc_401.value.detail

    # 429 Rate Limit
    resp_429 = MagicMock()
    resp_429.status_code = 429
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=resp_429):
        with pytest.raises(HTTPException) as exc_429:
            await search_endpoint_catalog("ep-fl", req, q="llama")
        assert exc_429.value.status_code == 429
        assert "rate limit" in exc_429.value.detail.lower()

    # 504 Timeout
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.TimeoutException("Timeout")):
        with pytest.raises(HTTPException) as exc_504:
            await search_endpoint_catalog("ep-fl", req, q="llama")
        assert exc_504.value.status_code == 504


# ============================================================
# 6. Frontend JS Tests (Node)
# ============================================================

@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
class TestFeatherlessFrontend:
    def test_featherless_js_panel_and_helpers(self):
        js = f"""
          import fs from 'node:fs';
          import {{ isChatgptSubscriptionEndpoint }} from '{(_REPO / 'static' / 'js' / 'chatgptSubscriptionUsage.js').as_posix()}';
          const source = fs.readFileSync('{_ADMIN_JS.as_posix()}', 'utf8');
          const fnStart = source.indexOf('function shouldDisplayEndpointBaseUrl');
          const fnEnd = source.indexOf('// ChatGPT per-endpoint usage panel', fnStart);
          const fnCode = source.slice(fnStart, fnEnd);
          const fns = new Function('isChatgptSubscriptionEndpoint', 'esc',
            fnCode + '; return {{ shouldDisplayEndpointBaseUrl, isFeatherlessEndpoint, renderFeatherlessPanel }};'
          )(isChatgptSubscriptionEndpoint, x => String(x));

          const ep = {{
            id: 'fl-1',
            base_url: 'https://api.featherless.ai/v1',
            provider: 'featherless',
            pinned_models: ['mistralai/Mistral-7B-Instruct-v0.2']
          }};

          const isFl = fns.isFeatherlessEndpoint(ep);
          const showUrl = fns.shouldDisplayEndpointBaseUrl(ep);

          // Test renderFeatherlessPanel DOM construction
          const mockPanel = {{
            dataset: {{}},
            innerHTML: '',
            querySelector: function(sel) {{
              if (sel === '.featherless-search-input') return {{ addEventListener: () => {{}}, value: '' }};
              if (sel === '.featherless-enabled-list') return {{ innerHTML: '', querySelectorAll: () => [] }};
              if (sel === '.featherless-enabled-count') return {{ textContent: '' }};
              if (sel === '.featherless-results-list') return {{ innerHTML: '', querySelectorAll: () => [] }};
              if (sel === '.featherless-pagination') return {{ style: {{}} }};
              if (sel === '.featherless-load-more') return {{ addEventListener: () => {{}} }};
              if (sel === '.featherless-spinner-host') return {{ style: {{}} }};
              return null;
            }},
            querySelectorAll: function() {{ return []; }}
          }};

          const mockRow = {{
            querySelector: function() {{ return {{ textContent: '' }}; }}
          }};

          fns.renderFeatherlessPanel(mockPanel, ep, mockRow);

          console.log(JSON.stringify({{
            isFl,
            showUrl,
            pickerMode: mockPanel.dataset.pickerMode,
            hasHeader: mockPanel.innerHTML.includes('Featherless Catalog'),
            hasSearchBar: mockPanel.innerHTML.includes('featherless-search-bar'),
            hasEnabledSection: mockPanel.innerHTML.includes('featherless-enabled-section'),
            hasResultsSection: mockPanel.innerHTML.includes('featherless-results-section'),
          }}));
        """
        proc = subprocess.run(
            ["node", "--input-type=module"],
            input=js,
            capture_output=True,
            text=True,
            cwd=str(_REPO),
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout.strip())
        assert data["isFl"] is True
        assert data["showUrl"] is False
        assert data["pickerMode"] == "pinned"
        assert data["hasHeader"] is True
        assert data["hasSearchBar"] is True
        assert data["hasEnabledSection"] is True
        assert data["hasResultsSection"] is True
