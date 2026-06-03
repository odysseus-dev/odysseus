"""Tests for the app_api tool executor (do_app_api).

Covers:
  1. action="endpoints" returns endpoint list from internal route registry
  2. action="endpoints" with filter returns filtered list
  3. action="call" with GET path succeeds (mocked backend)
  4. Implicit action="call" when path present but no action
  5. Rejects non-/api/ paths
  6. Rejects external URLs (not applicable directly, but /api/ prefix guard)
  7. Failure output includes reason with attempted URL
  8. 401 returns structured error with attempted_url and reason
"""

import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'sqlalchemy.sql.sqltypes', 'sqlalchemy.types',
    'bcrypt', 'pyotp',
    'fastapi', 'fastapi.responses', 'fastapi.routing',
    'starlette', 'starlette.responses', 'starlette.middleware',
    'starlette.middleware.base',
    'pydantic',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

for mod in ['core.database', 'core.models', 'core.auth', 'core.session_manager',
            'core.constants', 'core.exceptions']:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

if 'core.middleware' not in sys.modules:
    _middleware_stub = MagicMock()
    _middleware_stub.INTERNAL_TOOL_HEADER = "X-Juniperus-Internal-Token"
    _middleware_stub.INTERNAL_TOOL_TOKEN = "test-internal-token"
    sys.modules['core.middleware'] = _middleware_stub

for mod in ['src.settings', 'src.prompt_security', 'src.tool_security',
            'src.context_compactor', 'src.model_context',
            'src.tool_index', 'src.integrations', 'src.llm_core',
            'src.memory', 'src.rag_manager', 'src.rag_singleton', 'src.rag_vector',
            'src.deep_research', 'src.research_handler', 'src.teacher_escalation',
            'src.event_bus', 'src.task_scheduler', 'src.webhook_manager',
            'src.mcp_manager', 'src.builtin_mcp']:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

if 'httpx' not in sys.modules:
    sys.modules['httpx'] = MagicMock()

from src.tool_implementations import do_app_api, _APP_API_BASE


def _make_openapi_response(paths: dict) -> MagicMock:
    """Build a mock httpx.Response whose .json() returns an OpenAPI doc."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"paths": paths}
    resp.text = json.dumps({"paths": paths})
    return resp


def _make_json_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


# ── _APP_API_BASE tests ─────────────────────────────────────────────────────


class TestAppApiBase:
    """_APP_API_BASE should read from environment and default to 7010."""

    def test_default_base_url(self, monkeypatch):
        monkeypatch.delenv("JUNIPERUS_APP_PORT", raising=False)
        monkeypatch.delenv("JUNIPERUS_APP_BIND", raising=False)
        import importlib
        import src.tool_implementations as tim
        importlib.reload(tim)
        assert tim._APP_API_BASE == "http://127.0.0.1:7010"

    def test_custom_port_via_env(self, monkeypatch):
        monkeypatch.setenv("JUNIPERUS_APP_PORT", "8080")
        monkeypatch.setenv("JUNIPERUS_APP_BIND", "127.0.0.1")
        import importlib
        import src.tool_implementations as tim
        importlib.reload(tim)
        assert tim._APP_API_BASE == "http://127.0.0.1:8080"

    def test_custom_bind_via_env(self, monkeypatch):
        monkeypatch.setenv("JUNIPERUS_APP_PORT", "7010")
        monkeypatch.setenv("JUNIPERUS_APP_BIND", "0.0.0.0")
        import importlib
        import src.tool_implementations as tim
        importlib.reload(tim)
        assert tim._APP_API_BASE == "http://0.0.0.0:7010"


# ── action="endpoints" tests ────────────────────────────────────────────────


class TestAppApiEndpoints:
    """Test the action=endpoints discovery path."""

    @pytest.mark.asyncio
    async def test_endpoints_uses_internal_route_registry(self):
        """action=endpoints should call _get_app_endpoints, not HTTP /openapi.json."""
        mock_endpoints = [
            {"method": "GET", "path": "/api/cookbook/gpus", "name": "list_gpus", "summary": "List GPUs", "tags": ["cookbook"]},
            {"method": "GET", "path": "/api/gallery/list", "name": "list_gallery", "summary": "List gallery images", "tags": ["gallery"]},
        ]
        with patch('src.tool_implementations._get_app_endpoints', return_value=mock_endpoints):
            result = await do_app_api('{"action":"endpoints"}')

        assert result["exit_code"] == 0
        assert "output" in result
        assert "endpoints" in result
        assert len(result["endpoints"]) == 2
        paths = {e["path"]: e for e in result["endpoints"]}
        assert "/api/cookbook/gpus" in paths
        assert "/api/gallery/list" in paths
        assert "2 endpoint(s)" in result["output"]

    @pytest.mark.asyncio
    async def test_endpoints_with_filter(self):
        """action=endpoints with filter should only return matching endpoints."""
        mock_endpoints = [
            {"method": "GET", "path": "/api/cookbook/gpus", "name": "list_gpus", "summary": "List GPUs", "tags": ["cookbook"]},
            {"method": "GET", "path": "/api/gallery/list", "name": "list_gallery", "summary": "List gallery images", "tags": ["gallery"]},
        ]
        with patch('src.tool_implementations._get_app_endpoints', return_value=mock_endpoints):
            result = await do_app_api('{"action":"endpoints","filter":"cookbook"}')

        assert result["exit_code"] == 0
        assert len(result["endpoints"]) == 1
        assert result["endpoints"][0]["path"] == "/api/cookbook/gpus"
        assert "matching 'cookbook'" in result["output"]

    @pytest.mark.asyncio
    async def test_endpoints_filter_matches_description(self):
        """Filter should match summary/description and tags."""
        mock_endpoints = [
            {"method": "GET", "path": "/api/cookbook/gpus", "name": "list_gpus", "summary": "List GPUs", "tags": ["cookbook"]},
            {"method": "GET", "path": "/api/gallery/list", "name": "list_gallery", "summary": "List gallery images", "tags": ["gallery"]},
        ]
        with patch('src.tool_implementations._get_app_endpoints', return_value=mock_endpoints):
            result = await do_app_api('{"action":"endpoints","filter":"gallery"}')

        assert result["exit_code"] == 0
        assert len(result["endpoints"]) == 1
        assert result["endpoints"][0]["path"] == "/api/gallery/list"

    @pytest.mark.asyncio
    async def test_endpoints_empty_filter_returns_all(self):
        """Empty or missing filter should return all endpoints."""
        mock_endpoints = [
            {"method": "GET", "path": "/api/cookbook/gpus", "name": "list_gpus", "summary": "List GPUs", "tags": ["cookbook"]},
        ]
        with patch('src.tool_implementations._get_app_endpoints', return_value=mock_endpoints):
            result = await do_app_api('{"action":"endpoints","filter":""}')

        assert result["exit_code"] == 0
        assert len(result["endpoints"]) == 1

    @pytest.mark.asyncio
    async def test_endpoints_discovery_failure(self):
        """If _get_app_endpoints raises, should return a descriptive error."""
        with patch('src.tool_implementations._get_app_endpoints', side_effect=RuntimeError("route registry unavailable")):
            result = await do_app_api('{"action":"endpoints"}')

        assert result["exit_code"] == 1
        assert "error" in result
        assert "Endpoint discovery failed" in result["error"]
        assert "route registry unavailable" in result["error"]

    @pytest.mark.asyncio
    async def test_endpoints_blocklisted_prefixes_excluded(self):
        """Endpoints with blocklisted prefixes should be excluded."""
        mock_endpoints = [
            {"method": "GET", "path": "/api/auth/login", "name": "login", "summary": "Login", "tags": []},
            {"method": "GET", "path": "/api/cookbook/gpus", "name": "list_gpus", "summary": "List GPUs", "tags": ["cookbook"]},
        ]
        with patch('src.tool_implementations._get_app_endpoints', return_value=mock_endpoints):
            result = await do_app_api('{"action":"endpoints"}')

        assert result["exit_code"] == 0
        paths_returned = [e["path"] for e in result["endpoints"]]
        assert "/api/auth/login" not in paths_returned
        assert "/api/cookbook/gpus" in paths_returned

    @pytest.mark.asyncio
    async def test_endpoints_blocks_specific_methods(self):
        """Blocklisted method/path combinations should be excluded."""
        mock_endpoints = [
            {"method": "POST", "path": "/api/cookbook/state", "name": "save_state", "summary": "Save state", "tags": []},
            {"method": "GET", "path": "/api/cookbook/state", "name": "get_state", "summary": "Get state", "tags": []},
        ]
        with patch('src.tool_implementations._get_app_endpoints', return_value=mock_endpoints):
            result = await do_app_api('{"action":"endpoints"}')

        assert result["exit_code"] == 0
        items = [(e["path"], e["method"]) for e in result["endpoints"]]
        assert ("/api/cookbook/state", "POST") not in items
        assert ("/api/cookbook/state", "GET") in items


# ── action="call" tests ─────────────────────────────────────────────────────


class TestAppApiCall:
    """Test the action=call path."""

    @pytest.mark.asyncio
    async def test_call_get_succeeds(self):
        """action=call with method=GET and a valid /api/ path should succeed."""
        mock_resp = _make_json_response(200, {"gpus": []})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            result = await do_app_api('{"action":"call","method":"GET","path":"/api/cookbook/gpus"}')

        assert result["exit_code"] == 0
        assert "output" in result
        assert "200" in result["output"]
        # Verify the request was made to the correct URL
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert "/api/cookbook/gpus" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_call_uses_app_api_base_url(self):
        """app_api calls must go to the Juniperus app (7010), not cookbook (7000)."""
        mock_resp = _make_json_response(200, {})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            result = await do_app_api('{"action":"call","method":"GET","path":"/api/cookbook/gpus"}')

        call_args = mock_client.request.call_args
        called_url = call_args[0][1]
        # Must contain 7010, not 7000
        assert ":7010" in called_url
        assert "localhost:7000" not in called_url

    @pytest.mark.asyncio
    async def test_call_with_implicit_action(self):
        """When path is present but action is not, should default to action=call."""
        mock_resp = _make_json_response(200, {"data": []})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            result = await do_app_api('{"method":"GET","path":"/api/cookbook/gpus"}')

        assert result["exit_code"] == 0
        mock_client.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_with_body(self):
        """action=call with method=POST and body should send JSON."""
        mock_resp = _make_json_response(201, {"id": "123"})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            result = await do_app_api('{"action":"call","method":"POST","path":"/api/example","body":{"key":"value"}}')

        assert result["exit_code"] == 0
        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["json"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_call_400_returns_error(self):
        """A 4xx response should return an error with status_code and body."""
        mock_resp = _make_json_response(404, {"detail": "Not Found"})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            result = await do_app_api('{"action":"call","method":"GET","path":"/api/nonexistent"}')

        assert result["exit_code"] == 1
        assert "error" in result
        assert "404" in result["error"]
        assert "status_code" in result
        assert "attempted_url" in result

    @pytest.mark.asyncio
    async def test_call_connection_error_includes_url(self):
        """Connection errors should include the full attempted URL."""
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=ConnectionError("Connection refused"))

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            result = await do_app_api('{"action":"call","method":"GET","path":"/api/cookbook/gpus"}')

        assert result["exit_code"] == 1
        assert "error" in result
        # Should contain the URL and the error reason
        assert "connection refused" in result["error"].lower() or "Connection refused" in result["error"]
        assert "/api/cookbook/gpus" in result["error"]
        assert "attempted_url" in result

    @pytest.mark.asyncio
    async def test_call_401_returns_structured_error(self):
        """A 401 response should return attempted_url and reason fields."""
        mock_resp = _make_json_response(401, {"error": "Not authenticated"})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            result = await do_app_api('{"action":"call","method":"GET","path":"/api/cookbook/gpus"}')

        assert result["exit_code"] == 1
        assert result.get("ok") is False
        assert "401" in result["error"]
        assert "/api/cookbook/gpus" in result["attempted_url"]
        assert result["attempted_url"].startswith("http://")
        assert "app_api does not yet receive" in result["reason"]
        assert result["status_code"] == 401


# ── Safety tests ─────────────────────────────────────────────────────────────


class TestAppApiSafety:
    """Test that app_api enforces path safety constraints."""

    @pytest.mark.asyncio
    async def test_rejects_non_api_paths(self):
        """Paths not starting with /api/ must be rejected."""
        result = await do_app_api('{"action":"call","method":"GET","path":"/etc/passwd"}')

        assert result["exit_code"] == 1
        assert "error" in result
        assert "/api/" in result["error"]  # error message mentions /api/ requirement

    @pytest.mark.asyncio
    async def test_rejects_root_path(self):
        """Root path should be rejected — not an /api/ path."""
        result = await do_app_api('{"action":"call","method":"GET","path":"/"}')

        assert result["exit_code"] == 1
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_external_url_attempt(self):
        """A path like http://evil.com should be caught by /api/ prefix check."""
        result = await do_app_api('{"action":"call","method":"GET","path":"http://evil.com/api/foo"}')

        assert result["exit_code"] == 1
        assert "error" in result

    @pytest.mark.asyncio
    async def test_blocklisted_prefix_auth(self):
        """Auth endpoints should be blocklisted."""
        result = await do_app_api('{"action":"call","method":"GET","path":"/api/auth/login"}')

        assert result["exit_code"] == 1
        assert "error" in result

    @pytest.mark.asyncio
    async def test_blocklisted_prefix_users(self):
        """Users endpoints should be blocklisted."""
        result = await do_app_api('{"action":"call","method":"GET","path":"/api/users/1"}')

        assert result["exit_code"] == 1
        assert "error" in result

    @pytest.mark.asyncio
    async def test_blocklisted_prefix_admin(self):
        """Admin endpoints should be blocklisted."""
        result = await do_app_api('{"action":"call","method":"GET","path":"/api/admin/wipe"}')

        assert result["exit_code"] == 1
        assert "error" in result

    @pytest.mark.asyncio
    async def test_blocklisted_email_accounts(self):
        """GET /api/email/accounts is blocklisted."""
        result = await do_app_api('{"action":"call","method":"GET","path":"/api/email/accounts"}')

        assert result["exit_code"] == 1
        assert "error" in result
        assert "list_email_accounts" in result["error"]

    @pytest.mark.asyncio
    async def test_call_requires_path(self):
        """action=call without path should return error."""
        result = await do_app_api('{"action":"call","method":"GET"}')

        assert result["exit_code"] == 1
        assert "error" in result
        assert "path" in result["error"]


# ── Invalid input tests ─────────────────────────────────────────────────────


class TestAppApiInput:
    """Test error handling for malformed input."""

    @pytest.mark.asyncio
    async def test_invalid_json(self):
        """Invalid JSON should return a clear error."""
        result = await do_app_api('not valid json{{{')

        assert result["exit_code"] == 1
        assert "error" in result
        assert "Invalid JSON" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_content(self):
        """Empty content with no args should require path for call."""
        result = await do_app_api('')

        assert result["exit_code"] == 1
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unsupported_method(self):
        """Unsupported HTTP method should return error."""
        result = await do_app_api('{"action":"call","method":"TRACE","path":"/api/test"}')

        assert result["exit_code"] == 1
        assert "error" in result
        assert "Unsupported method" in result["error"]
