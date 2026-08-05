"""Tests for the shared outbound fetcher in ``src.outbound_fetch``.

The fetcher is the single SSRF-guarded, DNS-pinned HTTP downloader that the
gallery, image-generation and image-edit paths now route through (#5888).
These tests pin its public contract independently of any caller:

* it rejects URLs whose host is private / loopback / link-local / reserved
  / unspecified before opening a socket,
* it rejects the cloud-metadata hostnames by name even before the resolver
  runs,
* it raises on a Content-Encoding that would defeat the size cap,
* it raises BodyTooLargeError on a server that declared an oversize body.
"""
import ipaddress

import pytest

import src.outbound_fetch as outbound_fetch
from src.outbound_fetch import (
    BodyTooLargeError,
    _is_private_address,
    _resolve_public_ips,
    fetch_public_url,
)


# ---------------------------------------------------------------------------
# Pure unit tests — no HTTP, no resolver.
# ---------------------------------------------------------------------------


class TestIsPrivateAddress:
    @pytest.mark.parametrize(
        "addr",
        [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",  # cloud metadata
            "0.0.0.0",
            "::1",
            "fc00::1",
            "fe80::1",
        ],
    )
    def test_rejects_private_addresses(self, addr):
        assert _is_private_address(ipaddress.ip_address(addr)) is True, addr

    @pytest.mark.parametrize(
        "addr",
        [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
            "2606:4700:4700::1111",
        ],
    )
    def test_allows_public_addresses(self, addr):
        assert _is_private_address(ipaddress.ip_address(addr)) is False, addr

    def test_ipv4_mapped_ipv6_is_classified_by_v4(self):
        # An IPv4-mapped IPv6 address like ``::ffff:127.0.0.1`` must be
        # classified as loopback, not as public IPv6.
        addr = ipaddress.ip_address("::ffff:127.0.0.1")
        assert _is_private_address(addr) is True


class TestResolvePublicIps:
    def test_rejects_metadata_hostname_without_resolver(self):
        # The hostname check fires before DNS, so the resolver does not even
        # need to be running. No patch needed — the function raises before
        # touching socket.getaddrinfo.
        with pytest.raises(Exception) as exc:
            _resolve_public_ips("http://metadata.google.internal/")
        assert "metadata" in str(exc.value).lower() or "public" in str(exc.value).lower()

    def test_rejects_localhost_without_resolver(self):
        with pytest.raises(Exception):
            _resolve_public_ips("http://localhost/secret")

    def test_rejects_non_http_scheme(self):
        with pytest.raises(Exception):
            _resolve_public_ips("file:///etc/passwd")

        with pytest.raises(Exception):
            _resolve_public_ips("ftp://example.com/x")

    def test_rejects_link_local_ip_literal(self):
        with pytest.raises(Exception):
            _resolve_public_ips("http://169.254.169.254/latest/meta-data")

    def test_rejects_loopback_ip_literal(self):
        with pytest.raises(Exception):
            _resolve_public_ips("http://127.0.0.1/admin")

    def test_accepts_public_ip_literal(self):
        # An IP literal that is not private resolves to itself.
        ips = _resolve_public_ips("http://93.184.216.34/example.com")
        assert len(ips) == 1
        assert ips[0] == ipaddress.ip_address("93.184.216.34")


# ---------------------------------------------------------------------------
# HTTP-level tests — monkeypatch the underlying httpx.Client to avoid real
# network calls and assert how the shared fetcher actually behaves at the
# socket boundary.
# ---------------------------------------------------------------------------


class _FakeStream:
    """Mimics the streaming response context manager returned by
    ``httpx.Client.stream(...)``. Must support ``iter_bytes`` because the
    shared fetcher reads the body through it."""

    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size=None):
        yield self._body


class _FakeClient:
    """Records every ``stream("GET", url)`` call so tests can assert the
    helper refused the URL *before* opening the socket.

    The transport is inspected here purely for the pin assertion — the
    fetcher's ``_PinnedTransport`` exposes its pinned IP via
    ``transport._pool._network_backend._ip``.
    """

    instances: list = []

    def __init__(self, *args, **kwargs):
        self.calls: list = []
        self.pinned_ip: str | None = None
        transport = kwargs.get("transport")
        if transport is not None:
            pool = getattr(transport, "_pool", None)
            backend = getattr(pool, "_network_backend", None) if pool else None
            if backend is not None and hasattr(backend, "_ip"):
                self.pinned_ip = backend._ip
        _FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url):
        self.calls.append((method, url))
        return _FakeStream(200, {}, b"PNGDATA")


@pytest.fixture
def stub_httpx(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(outbound_fetch.httpx, "Client", _FakeClient)
    yield _FakeClient


def test_public_url_is_pinned_to_first_resolved_ip(stub_httpx, monkeypatch):
    # The helper must call httpx.Client with a ``transport`` whose pinned IP
    # equals the first address returned by the resolver. A monkeypatched
    # resolver returning a single public IP gives us a deterministic target.
    monkeypatch.setattr(
        outbound_fetch,
        "_resolve_public_ips",
        lambda url: [ipaddress.ip_address("93.184.216.34")],
    )
    fetch_public_url("http://example.com/x.png", headers=None, timeout=10)

    assert stub_httpx.calls == [("GET", "http://example.com/x.png")]
    assert stub_httpx.pinned_ip == "93.184.216.34"


def test_loopback_url_never_opens_a_socket(stub_httpx):
    # The whole point of #5888: a URL pointing at loopback must be refused
    # before httpx.Client is constructed at all.
    with pytest.raises(Exception):
        fetch_public_url("http://127.0.0.1/diffusion/result.png", headers=None, timeout=10)
    assert stub_httpx.calls == [], "loopback URL must not reach httpx.Client"


def test_link_local_url_never_opens_a_socket(stub_httpx):
    with pytest.raises(Exception):
        fetch_public_url("http://169.254.169.254/latest/meta-data", headers=None, timeout=10)
    assert stub_httpx.calls == []


def test_metadata_hostname_never_opens_a_socket(stub_httpx):
    with pytest.raises(Exception):
        fetch_public_url(
            "http://metadata.google.internal/computeMetadata/v1/",
            headers=None,
            timeout=10,
        )
    assert stub_httpx.calls == []


def test_compressed_body_is_refused(stub_httpx):
    # A gzip body would defeat the size cap (a tiny compressed payload can
    # balloon into one decoded chunk far past the cap). The helper must
    # reject it instead of decoding.

    class _CompressedStream(_FakeStream):
        def __init__(self):
            super().__init__(
                200,
                {"content-encoding": "gzip"},
                b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03",
            )

    class _CompressedClient(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        def stream(self, method, url):
            self.calls.append((method, url))
            return _CompressedStream()

    _CompressedClient.instances = []
    outbound_fetch.httpx.Client = _CompressedClient  # type: ignore

    with pytest.raises(Exception) as exc:
        fetch_public_url("http://example.com/x", headers=None, timeout=10)
    assert "encoding" in str(exc.value).lower() or "compress" in str(exc.value).lower()


def test_oversize_declared_body_raises_body_too_large(stub_httpx, monkeypatch):
    # A server that declares a Content-Length above the hard cap must be
    # refused up-front rather than streamed. We exercise that branch by
    # monkeypatching the cap to a tiny number and feeding in a response
    # whose declared size sits above it.
    class _OversizeClient(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        def stream(self, method, url):
            self.calls.append((method, url))
            return _FakeStream(200, {"content-length": "99999999"}, b"")

    _OversizeClient.instances = []
    outbound_fetch.httpx.Client = _OversizeClient  # type: ignore
    monkeypatch.setattr(outbound_fetch, "WEB_FETCH_HARD_MAX_BYTES", 1000)

    with pytest.raises(BodyTooLargeError):
        fetch_public_url("http://example.com/x", headers=None, timeout=10)