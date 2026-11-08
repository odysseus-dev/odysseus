"""Regression tests for SSRF / privilege-boundary fixes.

1. The agent web-fetch blocklist (src/outbound_fetch.py) must reject RFC 6598
   shared address space (100.64.0.0/10): Tailscale nodes and Alibaba Cloud's
   metadata endpoint 100.100.100.200 live there.
"""
import ipaddress

import pytest

from src.outbound_fetch import _is_private_address, _public_http_url


# ── 1. CGNAT / shared address space in the outbound fetch blocklist ─────────

@pytest.mark.parametrize(
    "addr",
    ["100.64.1.1", "100.100.100.200", "100.64.0.0", "100.127.255.255", "::ffff:100.100.100.200"],
)
def test_shared_address_space_is_private(addr):
    assert _is_private_address(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", ["100.63.255.255", "100.128.0.0", "8.8.8.8"])
def test_neighbouring_public_addresses_stay_allowed(addr):
    assert _is_private_address(ipaddress.ip_address(addr)) is False


def test_public_http_url_rejects_host_resolving_to_alibaba_metadata():
    def resolver(_host):
        return [ipaddress.ip_address("100.100.100.200")]

    assert _public_http_url("http://innocent.example/latest/meta-data", resolver=resolver) is False
