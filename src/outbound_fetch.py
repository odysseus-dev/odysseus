"""Single shared outbound HTTP fetcher used wherever Odysseus downloads content
from a URL supplied by an upstream response body (search snippets, gallery
results, image-generation providers, …).

Why this module exists
----------------------
Three places in the codebase used to do ``check_outbound_url(...)`` + a
plain ``httpx.get`` to fetch a URL that came back from an upstream API.
That pair has two security holes:

1. **DNS-rebinding TOCTOU.** ``check_outbound_url`` resolves the host
   once to classify it, then returns only ``(ok, reason)``. The HTTP
   client resolves the host *again* at connect time, so a low-TTL
   record can hand the guard a public IP and the socket a link-local
   one (cloud metadata, internal admin panels). The same family of
   bugs was fixed for ``execute_api_call`` in #5727 and is still open
   for the skill importer in #5609.

2. **Private-network reachability by default.** The image sites pass
   ``block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false")``,
   so at the shipped default loopback and RFC1918 are reachable. The
   operator-configured endpoint rationale in ``src/url_safety.py`` is
   about a *first* hop the admin chose, not an arbitrary URL an
   upstream server hands back.

This module provides ``fetch_public_url``, a streaming GET with:

* per-hop DNS resolution through ``_resolve_public_ips`` (rejects
  private / loopback / link-local / reserved / multicast / unspecified
  addresses, and the ``localhost`` and ``metadata.google.internal``
  hostnames),
* a TCP connect pinned to that resolved IP via ``_PinnedTransport``
  (closes the rebinding TOCTOU without changing TLS SNI or Host),
* a hard body-size ceiling that is enforced on the wire bytes (with
  ``Accept-Encoding: identity`` forced and compressed responses
  refused so the ceiling is a real memory bound),
* manual redirect handling so each hop is re-validated and re-pinned.

``services.search.content._get_public_url`` is the original home of
this code (see ``#704``); it was moved here so the three image sites
and any future caller share one implementation instead of each
copying the same drift-prone snippet.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import ssl
from typing import Iterable, cast
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

from src.constants import WEB_FETCH_HARD_MAX_BYTES, WEB_FETCH_SOFT_MAX_BYTES

logger = logging.getLogger(__name__)


_PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _is_private_address(addr: ipaddress._BaseAddress) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        or any(addr in net for net in _PRIVATE_NETWORKS)
    )


def _resolve_hostname_ips(hostname: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return []
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0]))
        except Exception:
            continue
    return out


def _resolve_public_ips(url: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise httpx.RequestError(f"Blocked non-public URL: {url}")
    host = (parsed.hostname or "").strip().lower()
    if host in ("localhost", "metadata", "metadata.google.internal"):
        raise httpx.RequestError(f"Blocked non-public hostname: {host}")
    try:
        ip = ipaddress.ip_address(host)
        if _is_private_address(ip):
            raise httpx.RequestError(f"Blocked non-public IP literal: {host}")
        return [ip]
    except httpx.RequestError:
        raise
    except ValueError:
        pass
    addrs = _resolve_hostname_ips(host)
    if not addrs or any(_is_private_address(a) for a in addrs):
        raise httpx.RequestError(f"Blocked non-public URL: {url}")
    return addrs


class _PinnedBackend(httpcore.NetworkBackend):
    """Network backend that connects to a pre-resolved IP.

    httpcore derives the TLS SNI and the ``Host`` header from the URL's
    origin, not from the host argument passed to ``connect_tcp``. So
    routing the TCP connect to a resolved IP while leaving the URL
    untouched keeps SNI / vhost behaviour correct and closes the
    DNS-rebinding TOCTOU between the SSRF check and the connect.
    """

    def __init__(self, ip: ipaddress._BaseAddress):
        self._ip = str(ip)
        self._real = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        return self._real.connect_tcp(
            self._ip, port, timeout, local_address, socket_options
        )

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return self._real.connect_unix_socket(path, timeout, socket_options)

    def sleep(self, seconds: float) -> None:
        return self._real.sleep(seconds)


# Map httpcore exception classes to their httpx equivalents. Built
# once at import time from the public exception classes; avoids any
# import of httpx's private transport machinery. httpcore's
# ``ConnectionNotAvailable`` is a pool-internal signal (the pool will
# close and retry on its own) — we never expect to see it surface to
# a transport caller, so it has no httpx counterpart here.
_HTTPCORE_TO_HTTPX_EXC = {
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.LocalProtocolError: httpx.LocalProtocolError,
    httpcore.NetworkError: httpx.NetworkError,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.ProtocolError: httpx.ProtocolError,
    httpcore.ProxyError: httpx.ProxyError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
    httpcore.TimeoutException: httpx.TimeoutException,
    httpcore.UnsupportedProtocol: httpx.UnsupportedProtocol,
    httpcore.WriteError: httpx.WriteError,
    httpcore.WriteTimeout: httpx.WriteTimeout,
}


class _PinnedTransport(httpx.BaseTransport):
    """Transport that pins every TCP connect to a pre-resolved IP.

    Uses only the public ``httpcore`` and ``httpx`` APIs — no
    subclassing of ``httpx.HTTPTransport``, no reads of private
    ``httpcore.ConnectionPool`` attributes, no imports from
    ``httpx``'s private transport internals. The URL is passed through
    unchanged so SNI / vhost work as if httpx had been given the
    hostname directly; only the TCP destination is pinned, closing the
    DNS-rebinding TOCTOU between the SSRF check and the connect.
    """

    def __init__(self, ip: ipaddress._BaseAddress, *, http2: bool = False):
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            http1=True,
            http2=http2,
            network_backend=_PinnedBackend(ip),
        )

    def __enter__(self):
        self._pool.__enter__()
        return self

    def __exit__(self, exc_type=None, exc_value=None, traceback=None) -> None:
        self._pool.__exit__(exc_type, exc_value, traceback)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        httpcore_req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            httpcore_resp = self._pool.handle_request(httpcore_req)
            # Eager materialisation matches the original
            # ``response.text`` usage in fetch_webpage_content. The
            # sync pool's stream is a plain Iterable[bytes] despite
            # the httpcore type hint unioning the async variant.
            content = b"".join(cast(Iterable[bytes], httpcore_resp.stream))
        except Exception as exc:
            mapped = _HTTPCORE_TO_HTTPX_EXC.get(type(exc))
            if mapped is not None:
                raise mapped(str(exc)) from exc
            raise

        return httpx.Response(
            status_code=httpcore_resp.status,
            headers=httpcore_resp.headers,
            content=content,
            extensions=httpcore_resp.extensions,
        )

    def close(self) -> None:
        self._pool.close()


class BodyTooLargeError(Exception):
    """The server declared a body larger than the hard fetch ceiling."""

    def __init__(self, url: str, declared_bytes: int):
        self.url = url
        self.declared_bytes = declared_bytes
        super().__init__(
            f"response body is {declared_bytes:,} bytes, over the "
            f"{WEB_FETCH_HARD_MAX_BYTES:,}-byte hard cap"
        )


class _CappedFetch:
    """Result of a size-capped streaming GET.

    Carries just what callers need from an httpx.Response plus the cap
    bookkeeping: the (possibly truncated) body, whether the cap cut it
    short, and the size the server declared via Content-Length (wire
    bytes; None when absent).
    """

    __slots__ = ("status_code", "headers", "content", "truncated",
                 "declared_bytes", "encoding", "url")

    def __init__(self, status_code, headers, content, truncated,
                 declared_bytes, encoding, url):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.truncated = truncated
        self.declared_bytes = declared_bytes
        self.encoding = encoding
        self.url = url

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", self.url)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code} for {self.url}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


def fetch_public_url(
    url: str,
    headers: dict | None = None,
    timeout: int = 30,
    max_redirects: int = 5,
    max_bytes: int | None = None,
) -> _CappedFetch:
    """Capped streaming GET with SSRF-guarded, DNS-pinned manual redirects.

    Each hop is resolved once, validated as public, and then the actual TCP
    connection is pinned to that resolved IP. The request URL is left unchanged
    so Host and TLS SNI keep the original hostname.

    ``timeout`` is seconds (int) for backward-compatibility with callers that
    pass an int; httpx also accepts a float.
    """
    cap = min(max_bytes or WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_HARD_MAX_BYTES)
    current = url
    for _ in range(max_redirects + 1):
        ips = _resolve_public_ips(current)

        # Force identity transfer-encoding. With gzip/deflate the wire bytes
        # and Content-Length can be a small fraction of the decoded body, so a
        # tiny compressed response could pass the hard-cap preflight and then
        # expand past the ceiling in one decoded chunk before the streamed cap
        # below can slice it.
        req_headers = dict(headers or {})
        req_headers["Accept-Encoding"] = "identity"

        with httpx.Client(
            headers=req_headers,
            timeout=timeout,
            follow_redirects=False,
            transport=_PinnedTransport(ips[0]),
        ) as client:
            with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        return _CappedFetch(
                            response.status_code, response.headers, b"",
                            False, None, response.encoding, str(response.url),
                        )
                    current = urljoin(str(response.url), location)
                    continue

                # A server can ignore the identity request and still return a
                # compressed body; httpx.iter_bytes would then decode it, and a
                # tiny gzip can balloon into one decoded chunk far past the cap.
                # Refuse compressed Content-Encoding so the streamed cap stays
                # a real memory bound.
                enc = (response.headers.get("content-encoding") or "").strip().lower()
                if enc and enc != "identity":
                    raise httpx.RequestError(
                        f"Refusing compressed response (Content-Encoding: {enc}) after "
                        "requesting identity: cannot bound decoded body size",
                        request=httpx.Request("GET", current),
                    )

                declared = None
                raw_len = response.headers.get("content-length")
                if raw_len and raw_len.isdigit():
                    declared = int(raw_len)

                if declared is not None and declared > WEB_FETCH_HARD_MAX_BYTES:
                    raise BodyTooLargeError(current, declared)

                chunks = []
                read = 0
                truncated = False
                for chunk in response.iter_bytes():
                    read += len(chunk)
                    if read > cap:
                        keep = cap - (read - len(chunk))
                        if keep > 0:
                            chunks.append(chunk[:keep])
                        truncated = True
                        break
                    chunks.append(chunk)

                return _CappedFetch(
                    response.status_code, response.headers,
                    b"".join(chunks), truncated, declared,
                    response.encoding, str(response.url),
                )

    raise httpx.RequestError("Too many redirects", request=httpx.Request("GET", current))