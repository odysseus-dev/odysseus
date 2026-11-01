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
import asyncio
import ipaddress
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import httpcore
import httpx
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


async def _call_capturing_transport(base_url, path="/items"):
    """Drive execute_api_call and return (result, transport) where transport is
    the object passed to httpx.AsyncClient(transport=...)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = {"ok": True}
    resp.text = '{"ok": true}'

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.request = AsyncMock(return_value=resp)

    captured = {}

    def _fake_async_client(*args, **kwargs):
        captured.update(kwargs)
        return client

    with (
        patch.object(integrations, "_find_integration",
                     return_value=_integration(base_url)),
        patch("httpx.AsyncClient", side_effect=_fake_async_client),
    ):
        result = await integrations.execute_api_call("test_integ", "GET", path)
    return result, captured.get("transport"), client


@pytest.mark.asyncio
async def test_connection_is_pinned_to_the_validated_ip(monkeypatch):
    """DNS-rebinding defense: the guard resolves the host once to a benign
    public IP, and the request must be pinned to *that* IP so a host that
    rebinds to the metadata range at connect time can't be reached with the
    integration's auth headers. Static resolution passing the guard is not
    enough — a plain client would re-resolve at connect."""
    monkeypatch.setattr("src.url_safety._default_resolver",
                        lambda host: ["93.184.216.34"])
    result, transport, client = await _call_capturing_transport(
        "http://rebinding.attacker.example")

    assert result.get("exit_code") == 0
    client.request.assert_called_once()
    assert isinstance(transport, integrations._PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_pin_carries_the_whole_validated_ip_set(monkeypatch):
    """When a host resolves to several records the transport keeps all of them
    (check_outbound_url validated every one), in resolver order, so it can fall
    back past a dead first address instead of failing the whole call."""
    monkeypatch.setattr("src.url_safety._default_resolver",
                        lambda host: ["93.184.216.34", "198.51.100.7"])
    result, transport, _ = await _call_capturing_transport("http://multi.example")

    assert result.get("exit_code") == 0
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34", "198.51.100.7"]


class _FakeStream:
    """Stand-in for the connected socket the real backend returns."""


class _RecordingBackend:
    """Fake httpcore backend: connect_tcp fails for the addresses in `dead`
    and succeeds for the rest, recording the order it was asked to connect."""

    def __init__(self, dead):
        self.dead = set(dead)
        self.attempts = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        self.attempts.append((host, timeout))
        if host in self.dead:
            raise httpcore.ConnectError(f"connection refused: {host}")
        return _FakeStream()


def _pinned_backend(ips, dead):
    """A _PinnedAsyncBackend whose underlying connect is the recording fake."""
    backend = integrations._PinnedAsyncBackend(ips)
    backend._real = _RecordingBackend(dead)
    return backend


@pytest.mark.asyncio
async def test_connect_falls_back_from_dead_first_to_live_second():
    """first-dead / second-live: the pinned backend must try the next validated
    address when the first refuses, rather than surfacing the failure. It also
    ignores the `host` httpcore passes (the original hostname) and connects to
    the pinned IPs, which is what keeps TLS SNI / Host on the real hostname."""
    ips = [ipaddress.ip_address("203.0.113.10"), ipaddress.ip_address("198.51.100.7")]
    backend = _pinned_backend(ips, dead={"203.0.113.10"})

    stream = await backend.connect_tcp("original.hostname.example", 443, timeout=5.0)

    assert isinstance(stream, _FakeStream)
    # Tried the dead address first, then the live one — never the hostname.
    assert [host for host, _ in backend._real.attempts] == ["203.0.113.10", "198.51.100.7"]
    # Fallback shared one budget: the second attempt got the time left, not a fresh 5s.
    assert backend._real.attempts[1][1] <= 5.0


@pytest.mark.asyncio
async def test_connect_raises_when_every_validated_address_is_dead():
    ips = [ipaddress.ip_address("203.0.113.10"), ipaddress.ip_address("198.51.100.7")]
    backend = _pinned_backend(ips, dead={"203.0.113.10", "198.51.100.7"})

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("original.hostname.example", 443, timeout=5.0)
    assert [host for host, _ in backend._real.attempts] == ["203.0.113.10", "198.51.100.7"]


@pytest.mark.asyncio
async def test_pinned_transport_reuses_httpx_ca_trust(monkeypatch):
    """TLS trust must come from the same builder the default httpx client uses
    (certifi + SSL_CERT_FILE / SSL_CERT_DIR via trust_env), not from
    ssl.create_default_context()'s system roots — otherwise chains that verified
    under the old default client can silently stop verifying."""
    sentinel = ssl.create_default_context()
    calls = []

    def _fake_create(*args, **kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(httpx, "create_ssl_context", _fake_create)
    transport = integrations._PinnedAsyncTransport([ipaddress.ip_address("93.184.216.34")])
    try:
        assert calls, "transport did not build its context via httpx.create_ssl_context"
        assert transport._pool._ssl_context is sentinel
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_real_socket_falls_back_from_dead_first_to_live_second():
    """End-to-end over real loopback sockets: pin [127.0.0.2 (nothing
    listening), 127.0.0.1 (live)], and the request must succeed by falling back
    to the second address while the Host header stays the original hostname —
    i.e. only the socket destination moved, vhost/SNI routing did not."""
    captured = {}

    async def handle(reader, writer):
        request = await reader.read(4096)
        for line in request.split(b"\r\n"):
            if line.lower().startswith(b"host:"):
                captured["host"] = line.split(b":", 1)[1].strip().decode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        await server.start_serving()
        transport = integrations._PinnedAsyncTransport(
            [ipaddress.ip_address("127.0.0.2"), ipaddress.ip_address("127.0.0.1")]
        )
        try:
            async with httpx.AsyncClient(transport=transport) as client:
                resp = await client.get(f"http://pinned.example:{port}/health")
        finally:
            await transport.aclose()

    assert resp.status_code == 200
    assert resp.text == "hi"
    assert captured.get("host") == f"pinned.example:{port}"


@pytest.mark.asyncio
async def test_ip_literal_base_url_still_pins_and_is_not_rejected():
    """A base_url that is already an IP has nothing to rebind, but it must not
    trip the "did not resolve" guard either.

    check_outbound_url resolves even a literal (getaddrinfo returns the address
    itself), so the captured list is populated and the pin is a no-op rather
    than a rejection. Uses the real resolver on purpose — no monkeypatch — so
    this would catch the fail-closed branch firing on a literal.
    """
    result, transport, client = await _call_capturing_transport(
        "http://93.184.216.34")

    assert result.get("exit_code") == 0
    assert isinstance(transport, integrations._PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_ipv6_base_url_pins_every_validated_address(monkeypatch):
    """IPv6 goes down the same path as v4.

    Resolution is stubbed rather than using a literal so this doesn't depend on
    the runner having IPv6 configured.
    """
    v6 = "2606:2800:220:1:248:1893:25c8:1946"
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: [v6])
    result, transport, client = await _call_capturing_transport("http://v6.example")

    assert result.get("exit_code") == 0
    assert isinstance(transport, integrations._PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == [v6]


def test_validated_ips_strips_zone_id_and_drops_junk():
    """getaddrinfo can hand back a scoped v6 address like 'fe80::1%eth0'."""
    got = integrations._validated_ips(
        ["93.184.216.34", "fe80::1%eth0", "not-an-ip", None, "2001:db8::5"]
    )
    assert [str(ip) for ip in got] == ["93.184.216.34", "fe80::1", "2001:db8::5"]


def test_validated_ips_deduplicates_repeated_addresses():
    """The resolver is getaddrinfo(host, None) with no socktype filter, so glibc
    returns one record per socktype and a single-homed host arrives three times
    over. Duplicates must collapse (first-seen order kept) or the connect
    fallback wastes its shared deadline retrying one dead address."""
    got = integrations._validated_ips(
        ["93.184.216.34", "93.184.216.34", "93.184.216.34"]
    )
    assert [str(ip) for ip in got] == ["93.184.216.34"]

    # Order is first-seen, and distinct addresses all survive.
    got = integrations._validated_ips(
        ["198.51.100.7", "93.184.216.34", "198.51.100.7", "2001:db8::5"]
    )
    assert [str(ip) for ip in got] == ["198.51.100.7", "93.184.216.34", "2001:db8::5"]

    # A zone-id variant is the same address once stripped, so it collapses too.
    got = integrations._validated_ips(["fe80::1%eth0", "fe80::1%eth1", "fe80::1"])
    assert [str(ip) for ip in got] == ["fe80::1"]
