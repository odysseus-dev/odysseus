"""URL validation helpers for server-side outbound requests."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_INTERNAL_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}

_INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".intranet",
)

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _resolve_hostname_ips(hostname: str) -> list[ipaddress._BaseAddress]:
    ips: list[ipaddress._BaseAddress] = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
        if family in (socket.AF_INET, socket.AF_INET6):
            ips.append(ipaddress.ip_address(sockaddr[0]))
    return ips


def _blocked_ip(addr: ipaddress._BaseAddress) -> bool:
    return (
        any(addr in net for net in _BLOCKED_NETWORKS)
        or addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    )


def _host_resolves_publicly(hostname: str) -> bool:
    host = hostname.strip().lower()
    if host in _INTERNAL_HOSTNAMES or host.endswith(_INTERNAL_SUFFIXES):
        return False
    try:
        return not _blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        addrs = _resolve_hostname_ips(host)
    except OSError:
        return False
    return bool(addrs) and all(not _blocked_ip(addr) for addr in addrs)


def is_public_http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _host_resolves_publicly(parsed.hostname)


def validate_public_http_url(url: str, *, max_length: int = 2048) -> str:
    """Validate a user/API-token supplied server-side HTTP(S) endpoint.

    This is for untrusted outbound URLs, not admin-created model endpoints
    that are intentionally allowed to point at private model providers. DNS
    failures fail closed, and DNS checks reduce obvious private-network
    targets but do not eliminate every DNS rebinding race by themselves.
    """
    cleaned = (url or "").strip()
    if len(cleaned) > max_length:
        raise ValueError("URL is too long")
    if not is_public_http_url(cleaned):
        raise ValueError("URL must point to a public HTTP(S) endpoint")
    return cleaned
