"""IPv6 forms that carry an IPv4 destination must be judged by that destination.

``src.url_security._blocked_ip`` guards untrusted outbound URLs (webhooks and,
via ``resolve_public_http_url``, an API token's pinned ``base_url``). An
IPv4-mapped address such as ``::ffff:100.64.1.1`` connects to the IPv4 host
``100.64.1.1``; it must be blocked exactly when that IPv4 address would be.
NAT64 Well-Known-Prefix addresses are decoded the same way, as
``src.url_safety`` already does.
"""

import ipaddress

import pytest

from src.url_security import _blocked_ip


@pytest.mark.parametrize(
    "address",
    [
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:100.64.1.1",  # RFC 6598 shared space (CGNAT / Tailscale)
        "::ffff:100.100.100.200",  # Alibaba Cloud metadata, inside 100.64/10
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b::6440:101",  # NAT64 of 100.64.1.1
    ],
)
def test_ipv6_wrapped_internal_ipv4_is_blocked(address):
    assert _blocked_ip(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize(
    "address",
    [
        "::ffff:93.184.216.34",
        "64:ff9b::5db8:d822",  # NAT64 of 93.184.216.34
    ],
)
def test_ipv6_wrapped_public_ipv4_is_allowed(address):
    assert _blocked_ip(ipaddress.ip_address(address)) is False
