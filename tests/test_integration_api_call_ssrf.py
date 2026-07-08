"""Regression: execute_api_call must run the outbound SSRF guard.

The api_call agent tool lets the LLM drive HTTP requests against a
user-configured integration base_url. Before this guard, a base_url (or a
hostname resolving) to the cloud metadata range was requested server-side
with the integration's auth headers attached. execute_api_call now validates
the joined URL with src.url_safety.check_outbound_url before connecting:
link-local/metadata is always rejected; RFC-1918/loopback only when
INTEGRATION_API_BLOCK_PRIVATE_IPS=true (LAN integrations are the primary
use case, so private stays allowed by default).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import integrations


def _integration(base_url):
    return {
        "id": "test_integ",
        "name": "TestInteg",
        "enabled": True,
        "base_url": base_url,
        "auth_type": "bearer",
        "api_key": "secret-token",
        "auth_header": "",
        "auth_param": "",
        "description": "",
        "preset": "",
    }


async def _call(base_url, path="/items"):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = {"ok": True}
    resp.text = '{"ok": true}'

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.request = AsyncMock(return_value=resp)

    with (
        patch.object(integrations, "_find_integration",
                     return_value=_integration(base_url)),
        patch("httpx.AsyncClient", return_value=client),
    ):
        result = await integrations.execute_api_call("test_integ", "GET", path)
    return result, client


@pytest.mark.asyncio
async def test_metadata_ip_base_url_is_rejected_without_requesting():
    result, client = await _call("http://169.254.169.254")

    assert result["exit_code"] == 1
    assert "rejected" in result["error"].lower()
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_hostname_resolving_to_metadata_ip_is_rejected(monkeypatch):
    """DNS-based variant: an innocuous-looking hostname that resolves into
    the link-local range must be caught by the resolver check."""
    monkeypatch.setattr("src.url_safety._default_resolver",
                        lambda host: ["169.254.169.254"])
    result, client = await _call("http://internal.attacker.example")

    assert result["exit_code"] == 1
    assert "rejected" in result["error"].lower()
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_public_ip_base_url_still_requests():
    # Public literal — no DNS involved.
    result, client = await _call("http://93.184.216.34")

    assert result.get("exit_code") == 0
    client.request.assert_called_once()


@pytest.mark.asyncio
async def test_private_base_url_allowed_by_default_blocked_with_knob(monkeypatch):
    # Local-first default: LAN integrations (Home Assistant etc.) must work.
    monkeypatch.delenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", raising=False)
    result, client = await _call("http://192.168.1.50")
    assert result.get("exit_code") == 0
    client.request.assert_called_once()

    # Locked-down deployments opt in to a full private/loopback block.
    monkeypatch.setenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", "true")
    result, client = await _call("http://192.168.1.50")
    assert result["exit_code"] == 1
    assert "rejected" in result["error"].lower()
    client.request.assert_not_called()
