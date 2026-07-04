"""Tests for the SSRF guard in execute_api_call (#5143).

The api_call agent tool lets the LLM drive requests against a
user-configured integration base_url. The joined URL must pass
src.url_safety.check_outbound_url before httpx connects:

  (a) link-local / metadata addresses are rejected always
  (b) private / loopback addresses pass by default (local-first)
  (c) private / loopback addresses are rejected when
      INTEGRATION_API_BLOCK_PRIVATE_IPS=true
  (d) rejected URLs never reach the HTTP client

All URLs use IP literals so no test depends on real DNS.
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so src.integrations can be imported without heavy deps
# ---------------------------------------------------------------------------

for mod_name in ("core", "core.atomic_io", "core.platform_compat"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

core_atomic = sys.modules["core.atomic_io"]
if not hasattr(core_atomic, "atomic_write_json"):
    core_atomic.atomic_write_json = lambda *a, **kw: None  # type: ignore

core_compat = sys.modules["core.platform_compat"]
if not hasattr(core_compat, "safe_chmod"):
    core_compat.safe_chmod = lambda *a, **kw: None  # type: ignore

if "src.secret_storage" not in sys.modules:
    stub = types.ModuleType("src.secret_storage")
    stub.encrypt = lambda s: s  # type: ignore
    stub.decrypt = lambda s: s  # type: ignore
    stub.is_encrypted = lambda s: False  # type: ignore
    sys.modules["src.secret_storage"] = stub

if "src.constants" not in sys.modules:
    stub_c = types.ModuleType("src.constants")
    stub_c.DATA_DIR = "/tmp"  # type: ignore
    stub_c.INTEGRATIONS_FILE = "/tmp/integrations_test.json"  # type: ignore
    stub_c.SETTINGS_FILE = "/tmp/settings_test.json"  # type: ignore
    sys.modules["src.constants"] = stub_c

from src import integrations  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _integration(base_url):
    return {
        "id": "ssrf_test",
        "name": "SsrfTest",
        "enabled": True,
        "base_url": base_url,
        "auth_type": "none",
        "api_key": "",
        "auth_header": "",
        "auth_param": "",
        "description": "",
        "preset": "",
    }


async def _call(base_url, path="/status"):
    """Run execute_api_call against a mocked httpx client.

    Returns (result, request_mock) so tests can assert both the outcome and
    whether an outbound request was attempted at all.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/plain"}
    mock_resp.text = "ok"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=mock_resp)

    with (
        patch.object(integrations, "_find_integration", return_value=_integration(base_url)),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await integrations.execute_api_call("ssrf_test", "GET", path)
    return result, mock_client.request


# ---------------------------------------------------------------------------
# (a) link-local / metadata rejected always
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254",  # cloud instance metadata
        "http://169.254.170.2/v2",  # ECS task metadata
    ],
)
async def test_link_local_rejected_by_default(base_url):
    result, request = await _call(base_url)
    assert result.get("exit_code") == 1
    assert "rejected" in result.get("error", "").lower()
    request.assert_not_called()


# ---------------------------------------------------------------------------
# (b) local-first default: private / loopback pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8123",  # loopback (local Home Assistant)
        "http://192.168.1.50:8080",  # RFC-1918 LAN host
        "http://10.0.0.5",  # RFC-1918
    ],
)
async def test_private_allowed_by_default(base_url):
    result, request = await _call(base_url)
    assert result.get("exit_code") == 0
    request.assert_called_once()


@pytest.mark.asyncio
async def test_public_ip_allowed_even_under_lockdown(monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", "true")
    result, request = await _call("http://8.8.8.8")  # unambiguously public
    assert result.get("exit_code") == 0
    request.assert_called_once()


# ---------------------------------------------------------------------------
# (c) lockdown knob: private / loopback rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8123",
        "http://192.168.1.50:8080",
    ],
)
async def test_private_rejected_with_lockdown_knob(monkeypatch, base_url):
    monkeypatch.setenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", "true")
    result, request = await _call(base_url)
    assert result.get("exit_code") == 1
    assert "rejected" in result.get("error", "").lower()
    request.assert_not_called()


# ---------------------------------------------------------------------------
# (d) guard runs on the joined URL, after path validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejection_covers_llm_supplied_path(monkeypatch):
    # The path is LLM-influenced; the host still comes from base_url, so a
    # metadata base must be rejected regardless of what path is requested.
    result, request = await _call("http://169.254.169.254", path="/latest/meta-data/")
    assert result.get("exit_code") == 1
    request.assert_not_called()
