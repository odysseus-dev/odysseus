"""Regression: the reminder ntfy sender must run the same SSRF guard as the
webhook sender.

The webhook branch of dispatch_reminder validates its target with
src.url_safety.check_outbound_url before posting; the ntfy branch posted to
the integration's base_url with no check, so a base_url pointing at the cloud
metadata range (169.254.169.254) was fetched server-side — with the
integration's Authorization header attached — every time a reminder fired.
"""
import asyncio
from unittest.mock import MagicMock, patch

import httpx

from routes.note_routes import dispatch_reminder


def _ntfy_integration(base_url):
    return [{
        "preset": "ntfy",
        "enabled": True,
        "base_url": base_url,
        "api_key": "secret-token",
        "name": "ntfy",
    }]


def _settings(**extra):
    return {
        "reminder_channel": "ntfy",
        "reminder_llm_synthesis": False,
        "reminder_ntfy_topic": "reminders",
        **extra,
    }


class _SpyAsyncClient:
    """Stands in for httpx.AsyncClient; records posts, returns success."""
    calls = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, **kw):
        _SpyAsyncClient.calls.append(url)
        resp = MagicMock()
        resp.is_success = True
        resp.status_code = 200
        return resp


def _dispatch():
    return asyncio.run(dispatch_reminder(
        "Title", "Body", note_id="", queue_browser=True,
        settings_override=_settings(),
    ))


def test_metadata_ip_ntfy_base_url_is_rejected_and_not_fetched():
    _SpyAsyncClient.calls = []
    with (
        patch("src.integrations.load_integrations",
              return_value=_ntfy_integration("http://169.254.169.254")),
        patch.object(httpx, "AsyncClient", _SpyAsyncClient),
    ):
        result = _dispatch()

    assert _SpyAsyncClient.calls == [], "metadata address must never be fetched"
    assert result["ntfy_sent"] is False
    assert "rejected" in result["ntfy_error"].lower()


def test_public_ntfy_base_url_still_sends():
    _SpyAsyncClient.calls = []
    with (
        # 93.184.216.34 is a public literal — no DNS resolution involved.
        patch("src.integrations.load_integrations",
              return_value=_ntfy_integration("http://93.184.216.34")),
        patch.object(httpx, "AsyncClient", _SpyAsyncClient),
    ):
        result = _dispatch()

    assert _SpyAsyncClient.calls == ["http://93.184.216.34/reminders"]
    assert result["ntfy_sent"] is True
    assert result["ntfy_error"] == ""


def test_private_ntfy_base_url_blocked_only_with_env_knob(monkeypatch):
    # Default (local-first): a LAN ntfy server is a normal setup and must work.
    _SpyAsyncClient.calls = []
    monkeypatch.delenv("REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS", raising=False)
    with (
        patch("src.integrations.load_integrations",
              return_value=_ntfy_integration("http://192.168.1.50")),
        patch.object(httpx, "AsyncClient", _SpyAsyncClient),
    ):
        result = _dispatch()
    assert result["ntfy_sent"] is True

    # Locked-down deployments: the same knob the webhook branch honors.
    _SpyAsyncClient.calls = []
    monkeypatch.setenv("REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS", "true")
    with (
        patch("src.integrations.load_integrations",
              return_value=_ntfy_integration("http://192.168.1.50")),
        patch.object(httpx, "AsyncClient", _SpyAsyncClient),
    ):
        result = _dispatch()
    assert _SpyAsyncClient.calls == []
    assert result["ntfy_sent"] is False
    assert "rejected" in result["ntfy_error"].lower()
