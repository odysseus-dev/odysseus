"""ssrf_guard.py — reusable SSRF protection for user-supplied URLs.

Provides URL validation that blocks private, loopback, link-local, reserved,
and cloud metadata addresses. Can be used standalone or as an httpx transport
wrapper for defense-in-depth against DNS rebinding.
"""

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Hostname patterns that resolve to cloud metadata endpoints
_BLOCKED_HOSTNAME_PATTERNS = (
    "metadata.google.internal",
    "169.254.169.254",
    "metadata.internal",
    "metadata",
)

# Allowed URL schemes
_ALLOWED_SCHEMES = ("http", "https")


class SSRFBlockedException(Exception):
    """Raised when a URL is blocked by the SSRF guard."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"SSRF blocked: {reason}")


def validate_url_for_safety(url: str) -> None:
    """Validate a URL is safe to request.

    Raises SSRFBlockedException if the URL points to a private, loopback,
    link-local, reserved, or metadata service address.

    Args:
        url: The URL to validate.

    Raises:
        SSRFBlockedException: If the URL is unsafe.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFBlockedException(
            "disallowed_scheme",
            f"Only http/https URLs are allowed, got: {parsed.scheme}",
        )

    hostname = parsed.hostname or ""
    if not hostname:
        raise SSRFBlockedException("no_hostname", "URL has no hostname")

    # Check blocked hostname patterns first (before DNS resolution)
    for pattern in _BLOCKED_HOSTNAME_PATTERNS:
        if pattern in hostname.lower():
            raise SSRFBlockedException(
                "blocked_hostname",
                f"URL hostname matches blocked pattern: {pattern}",
            )

    # Resolve DNS and check IP
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise SSRFBlockedException(
            "dns_resolution_failed",
            f"Could not resolve hostname: {hostname}",
        )

    for family, type_, proto, canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if ip.is_private:
            raise SSRFBlockedException("private_ip", f"Private IP: {ip}")
        if ip.is_loopback:
            raise SSRFBlockedException("loopback_ip", f"Loopback IP: {ip}")
        if ip.is_link_local:
            raise SSRFBlockedException("link_local_ip", f"Link-local IP: {ip}")
        if ip.is_reserved:
            raise SSRFBlockedException("reserved_ip", f"Reserved IP: {ip}")
        if ip.is_multicast:
            raise SSRFBlockedException("multicast_ip", f"Multicast IP: {ip}")

        # Block Tailscale CGNAT range (100.64.0.0/10)
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            raise SSRFBlockedException("tailscale_cgnat", f"Tailscale CGNAT IP: {ip}")


def safe_httpx_request(method: str, url: str, **kwargs):
    """Make an httpx request with SSRF validation.

    Validates the URL before making the request. All kwargs are passed
    through to httpx.request().

    Args:
        method: HTTP method (GET, POST, etc.)
        url: The URL to request.
        **kwargs: Additional arguments passed to httpx.request().

    Returns:
        httpx.Response

    Raises:
        SSRFBlockedException: If the URL is unsafe.
    """
    import httpx

    validate_url_for_safety(url)
    return httpx.request(method, url, **kwargs)


def safe_httpx_client(**kwargs):
    """Create an httpx.AsyncClient with SSRF validation.

    All kwargs are passed through to httpx.AsyncClient().
    The client will validate URLs before making requests.

    Args:
        **kwargs: Additional arguments passed to httpx.AsyncClient().

    Returns:
        httpx.AsyncClient
    """
    import httpx

    validate_url_for_safety(kwargs.get("base_url", ""))
    return httpx.AsyncClient(**kwargs)


def ssrf_error_response(exc: SSRFBlockedException) -> dict:
    """Convert an SSRFBlockedException to a safe error response dict.

    Does not leak internal details like resolved IPs.

    Args:
        exc: The SSRFBlockedException to convert.

    Returns:
        dict with 'error' and 'exit_code' keys.
    """
    reason_messages = {
        "disallowed_scheme": "Only http/https URLs are allowed",
        "no_hostname": "Invalid URL: no hostname",
        "blocked_hostname": "URL hostname is not allowed",
        "dns_resolution_failed": "Could not resolve URL hostname",
        "private_ip": "Private/internal network addresses are not allowed",
        "loopback_ip": "Loopback addresses are not allowed",
        "link_local_ip": "Link-local addresses are not allowed",
        "reserved_ip": "Reserved IP addresses are not allowed",
        "multicast_ip": "Multicast addresses are not allowed",
        "tailscale_cgnat": "Tailscale CGNAT addresses are not allowed",
    }
    return {
        "error": reason_messages.get(exc.reason, "URL is not allowed"),
        "exit_code": 1,
    }
