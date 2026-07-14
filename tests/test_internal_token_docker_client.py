"""Regression: host→Docker published port must accept internal token auth."""

from unittest.mock import MagicMock

from core.middleware import is_trusted_internal_token_client, is_trusted_loopback


def _request(client_host: str, headers: dict | None = None):
    req = MagicMock()
    req.client.host = client_host
    req.headers = headers or {}
    return req


def test_loopback_without_proxy_headers():
    assert is_trusted_loopback(_request("127.0.0.1")) is True


def test_proxy_headers_block_loopback_trust(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_INTERNAL_TOKEN", raising=False)
    req = _request("127.0.0.1", {"x-forwarded-for": "203.0.113.1"})
    assert is_trusted_loopback(req) is False
    assert is_trusted_internal_token_client(req) is False


def test_docker_gateway_with_configured_token(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_INTERNAL_TOKEN", "test-token")
    monkeypatch.setattr("core.middleware._in_container", lambda: True)
    monkeypatch.setattr("core.middleware._docker_default_gateway_ip", lambda: "172.18.0.1")
    assert is_trusted_internal_token_client(_request("172.18.0.1")) is True


def test_docker_gateway_without_configured_token(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr("core.middleware._in_container", lambda: True)
    monkeypatch.setattr("core.middleware._docker_default_gateway_ip", lambda: "172.18.0.1")
    assert is_trusted_internal_token_client(_request("172.18.0.1")) is False


def test_docker_gateway_localhost_bypass(monkeypatch):
    from core.middleware import is_trusted_localhost_bypass_client

    monkeypatch.setattr("core.middleware._in_container", lambda: True)
    monkeypatch.setattr("core.middleware._docker_default_gateway_ip", lambda: "172.18.0.1")
    assert is_trusted_localhost_bypass_client(_request("172.18.0.1")) is True
    assert is_trusted_localhost_bypass_client(
        _request("172.18.0.1", {"x-forwarded-for": "203.0.113.1"})
    ) is False
