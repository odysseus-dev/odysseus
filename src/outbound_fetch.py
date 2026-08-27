"""SSRF-guarded synchronous HTTP fetching primitives.

This module owns outbound URL classification, one-resolution-per-hop DNS
pinning, redirects, and response-body budgets.  It deliberately has no search
or content-extraction dependencies so callers outside search can reuse the
same transport boundary.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from typing import Callable, Iterable, cast
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

from src.constants import WEB_FETCH_HARD_MAX_BYTES, WEB_FETCH_SOFT_MAX_BYTES


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


def _public_http_url(
    url: str,
    *,
    resolver: Callable[[str], list[ipaddress._BaseAddress]] | None = None,
) -> bool:
    resolver = resolver or _resolve_hostname_ips
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").strip()
        if not host:
            return False
        lower = host.lower()
        if lower in ("localhost", "metadata", "metadata.google.internal"):
            return False
        if lower.endswith((".local", ".localhost", ".internal", ".lan", ".intranet")):
            return False
        try:
            return not _is_private_address(ipaddress.ip_address(host))
        except ValueError:
            pass
        addrs = resolver(host)
        return bool(addrs) and not any(_is_private_address(a) for a in addrs)
    except Exception:
        return False


def _resolve_public_ips(
    url: str,
    *,
    resolver: Callable[[str], list[ipaddress._BaseAddress]] | None = None,
) -> list[ipaddress._BaseAddress]:
    resolver = resolver or _resolve_hostname_ips
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
    addrs = resolver(host)
    if not addrs or any(_is_private_address(a) for a in addrs):
        raise httpx.RequestError(f"Blocked non-public URL: {url}")
    return addrs


class _PinnedBackend(httpcore.NetworkBackend):
    """Network backend that connects to a pre-resolved IP."""

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
    """Transport that pins every TCP connect to a pre-resolved IP."""

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
    """Result of a size-capped streaming GET."""

    __slots__ = (
        "status_code",
        "headers",
        "content",
        "truncated",
        "declared_bytes",
        "encoding",
        "url",
    )

    def __init__(
        self,
        status_code,
        headers,
        content,
        truncated,
        declared_bytes,
        encoding,
        url,
    ):
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


def _get_public_url(
    url: str,
    headers: dict,
    timeout: int,
    max_redirects: int = 5,
    max_bytes: int | None = None,
    *,
    resolve_public_ips: Callable[[str], list[ipaddress._BaseAddress]] | None = None,
    transport_factory: Callable[[ipaddress._BaseAddress], httpx.BaseTransport] | None = None,
) -> _CappedFetch:
    """Capped streaming GET with SSRF-guarded, DNS-pinned redirects."""
    resolve_public_ips = resolve_public_ips or _resolve_public_ips
    transport_factory = transport_factory or _PinnedTransport
    cap = min(max_bytes or WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_HARD_MAX_BYTES)
    current = url
    for _ in range(max_redirects + 1):
        ips = resolve_public_ips(current)
        req_headers = dict(headers or {})
        req_headers["Accept-Encoding"] = "identity"

        with httpx.Client(
            headers=req_headers,
            timeout=timeout,
            follow_redirects=False,
            transport=transport_factory(ips[0]),
        ) as client:
            with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        return _CappedFetch(
                            response.status_code,
                            response.headers,
                            b"",
                            False,
                            None,
                            response.encoding,
                            str(response.url),
                        )
                    current = urljoin(str(response.url), location)
                    continue

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
                    response.status_code,
                    response.headers,
                    b"".join(chunks),
                    truncated,
                    declared,
                    response.encoding,
                    str(response.url),
                )

    raise httpx.RequestError(
        "Too many redirects", request=httpx.Request("GET", current)
    )
