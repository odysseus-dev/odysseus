"""URL classification and SSRF policy helpers.

This module is intentionally policy-driven. Odysseus has local-first features
that legitimately call loopback, LAN, and Tailscale services, so callers should
choose a policy that matches the endpoint class instead of applying one global
private-IP denylist.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlparse


METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
}

METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
}

TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")

Resolver = Callable[[str], Iterable[str]]


class UrlAccessPolicy(str, Enum):
    """Product-aware URL access policies."""

    STRICT_UNTRUSTED_FETCH = "strict_untrusted_fetch"
    TRUSTED_USER_CONFIGURED_ENDPOINT = "trusted_user_configured_endpoint"


@dataclass(frozen=True)
class ResolvedAddress:
    address: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class UrlInspection:
    url: str
    scheme: str
    host: str
    port: int | None
    addresses: tuple[ResolvedAddress, ...]
    errors: tuple[str, ...] = ()

    @property
    def categories(self) -> frozenset[str]:
        values: set[str] = set()
        for addr in self.addresses:
            values.update(addr.categories)
        return frozenset(values)


@dataclass(frozen=True)
class UrlAccessDecision:
    allowed: bool
    reason: str
    inspection: UrlInspection


def _default_resolver(host: str) -> Iterable[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    seen: set[str] = set()
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.add(ip)
            yield ip


def classify_ip(value: str) -> tuple[str, ...]:
    """Return stable security categories for an IP address."""
    ip = ipaddress.ip_address(value)
    categories: list[str] = []
    if ip in METADATA_IPS:
        categories.append("metadata")
    if ip.is_loopback:
        categories.append("loopback")
    if ip.is_link_local:
        categories.append("link_local")
    if ip.version == 4 and ip in TAILSCALE_NETWORK:
        categories.append("tailscale")
    if ip.is_private:
        categories.append("private")
    if ip.is_unspecified:
        categories.append("unspecified")
    if ip.is_multicast:
        categories.append("multicast")
    if ip.is_reserved:
        categories.append("reserved")
    if not categories:
        categories.append("public")
    return tuple(categories)


def inspect_url(url: str, resolver: Resolver | None = None) -> UrlInspection:
    """Parse and classify a URL without deciding whether it is allowed."""
    parsed = urlparse((url or "").strip())
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    errors: list[str] = []
    addresses: list[ResolvedAddress] = []

    if not scheme:
        errors.append("missing_scheme")
    if not host:
        errors.append("missing_host")

    if host in METADATA_HOSTS:
        addresses.append(ResolvedAddress(host, ("metadata",)))
    elif host:
        try:
            ip = ipaddress.ip_address(host)
            addresses.append(ResolvedAddress(str(ip), classify_ip(str(ip))))
        except ValueError:
            resolve = resolver or _default_resolver
            try:
                for resolved in resolve(host):
                    addresses.append(ResolvedAddress(resolved, classify_ip(resolved)))
            except Exception:
                errors.append("dns_resolution_failed")

    return UrlInspection(
        url=url,
        scheme=scheme,
        host=host,
        port=parsed.port,
        addresses=tuple(addresses),
        errors=tuple(errors),
    )


def assess_url(
    url: str,
    policy: UrlAccessPolicy,
    resolver: Resolver | None = None,
) -> UrlAccessDecision:
    """Assess a URL under the selected product policy."""
    inspection = inspect_url(url, resolver=resolver)
    if inspection.errors:
        return UrlAccessDecision(False, inspection.errors[0], inspection)
    if inspection.scheme not in {"http", "https"}:
        return UrlAccessDecision(False, "unsupported_scheme", inspection)
    if "metadata" in inspection.categories:
        return UrlAccessDecision(False, "metadata_service_blocked", inspection)

    local_categories = {
        "loopback",
        "private",
        "link_local",
        "tailscale",
        "unspecified",
        "multicast",
        "reserved",
    }
    if policy == UrlAccessPolicy.STRICT_UNTRUSTED_FETCH:
        blocked = sorted(inspection.categories.intersection(local_categories))
        if blocked:
            return UrlAccessDecision(False, f"blocked_{blocked[0]}", inspection)
        return UrlAccessDecision(True, "allowed_public_http", inspection)

    if policy == UrlAccessPolicy.TRUSTED_USER_CONFIGURED_ENDPOINT:
        return UrlAccessDecision(True, "allowed_trusted_configured_endpoint", inspection)

    return UrlAccessDecision(False, "unknown_policy", inspection)
