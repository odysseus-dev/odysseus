"""Tests for outbound URL safety / SSRF hardening (src/url_safety.py).

A stub resolver is injected so the tests never touch real DNS.
"""

import pytest

from src.url_safety import check_outbound_url


def _resolver(mapping):
    def resolve(host):
        if host in mapping:
            return mapping[host]
        raise OSError(f"unresolvable: {host}")
    return resolve


PUBLIC = _resolver({"example.com": ["93.184.216.34"]})
LOOPBACK = _resolver({"localhost": ["127.0.0.1"]})
LAN = _resolver({"nas.local": ["192.168.1.50"]})
METADATA = _resolver({"evil.example": ["169.254.169.254"]})
MAPPED_METADATA = _resolver({"evil6.example": ["::ffff:169.254.169.254"]})


def test_non_http_scheme_blocked():
    for url in ("file:///etc/passwd", "ftp://x/y", "gopher://h", "redis://h:6379"):
        ok, reason = check_outbound_url(url, resolver=PUBLIC)
        assert ok is False, url
        assert "scheme" in reason


def test_missing_host_or_empty_blocked():
    assert check_outbound_url("", resolver=PUBLIC)[0] is False
    assert check_outbound_url("http://", resolver=PUBLIC)[0] is False


def test_public_url_allowed():
    ok, reason = check_outbound_url("https://example.com/v1/embeddings", resolver=PUBLIC)
    assert ok is True, reason


def test_cloud_metadata_blocked_even_when_private_allowed():
    # The headline SSRF vector must be blocked regardless of block_private.
    ok, reason = check_outbound_url("http://evil.example/latest/meta-data/", resolver=METADATA)
    assert ok is False
    assert "link-local" in reason


def test_ipv4_mapped_metadata_blocked():
    ok, reason = check_outbound_url("http://evil6.example/", resolver=MAPPED_METADATA)
    assert ok is False
    assert "link-local" in reason


def test_loopback_and_lan_allowed_by_default_local_first():
    # Local-first: a localhost / LAN embedding server is a legitimate target.
    assert check_outbound_url("http://localhost:8080/v1", resolver=LOOPBACK)[0] is True
    assert check_outbound_url("http://nas.local:1234/v1", resolver=LAN)[0] is True


def test_strict_mode_blocks_private_and_loopback():
    ok, reason = check_outbound_url("http://localhost:8080", block_private=True, resolver=LOOPBACK)
    assert ok is False and "private" in reason
    ok, reason = check_outbound_url("http://nas.local", block_private=True, resolver=LAN)
    assert ok is False and "private" in reason


@pytest.mark.parametrize("block_private", [False, True])
@pytest.mark.parametrize("host,ips", [
    ("127.0.0.1", ["127.0.0.1"]),
    ("[::1]", ["::1"]),
    ("[::ffff:127.0.0.1]", ["::ffff:127.0.0.1"]),
    ("localhost", ["127.0.0.1", "::1"]),
    ("localhost", ["::1", "127.0.0.1"]),
])
def test_loopback_urls_follow_private_policy(host, ips, block_private):
    ok, reason = check_outbound_url(
        f"http://{host}:8080", block_private=block_private,
        resolver=_resolver({host.strip("[]"): ips}),
    )
    assert ok is (not block_private), reason
    if block_private:
        assert "loopback address blocked" in reason


@pytest.mark.parametrize("block_private", [False, True])
@pytest.mark.parametrize("ip", [
    "169.254.169.254", "fe80::1", "0.0.0.0", "::", "224.0.0.1", "ff02::1",
    "240.0.0.1", "::2", "100::1", "::ffff:169.254.169.254",
    "::ffff:0.0.0.0", "::ffff:224.0.0.1", "::ffff:240.0.0.1",
])
def test_special_ranges_stay_blocked_alongside_loopback(ip, block_private):
    ok, reason = check_outbound_url(
        "http://localhost:8080", block_private=block_private,
        resolver=_resolver({"localhost": [ip, "127.0.0.1", "::1"]}),
    )
    assert ok is False
    assert "link-local" in reason or "disallowed address" in reason


def test_strict_mode_blocks_cgnat_shared_space():
    # RFC 6598 shared/CGNAT space (100.64.0.0/10) is not globally routable.
    # A public redirect into it must be rejected under full SSRF lockdown,
    # even though ipaddress reports is_private=False for this range.
    CGNAT = _resolver({"svc.example": ["100.64.0.1"]})
    ok, reason = check_outbound_url("http://svc.example:8080", block_private=True, resolver=CGNAT)
    assert ok is False
    assert "blocked" in reason


def test_strict_mode_blocks_non_global_ranges():
    # Strict mode is a full SSRF lockdown: only globally-routable public
    # addresses may be reached. Benchmarking (198.18.0.0/15) and TEST-NET
    # documentation space (192.0.2.0/24) are not globally routable.
    for ip in ("198.18.0.1", "192.0.2.10"):
        res = _resolver({"svc.example": [ip]})
        ok, reason = check_outbound_url("http://svc.example", block_private=True, resolver=res)
        assert ok is False, ip
        assert "blocked" in reason


def test_strict_mode_still_allows_public_ip():
    # The lockdown must not reject a legitimate globally-routable target.
    ok, reason = check_outbound_url("https://example.com/v1", block_private=True, resolver=PUBLIC)
    assert ok is True, reason


def test_unresolvable_host_blocked():
    ok, reason = check_outbound_url("http://does-not-resolve.invalid", resolver=PUBLIC)
    assert ok is False
    assert "resolve" in reason


def test_resolver_values_must_include_a_parseable_ip():
    ok, reason = check_outbound_url(
        "https://example.test",
        resolver=lambda _host: [None, 123, "not-an-ip"],
    )

    assert ok is False
    assert "does not resolve to an IP" in reason


def test_resolver_skips_invalid_values_but_accepts_public_ip():
    ok, reason = check_outbound_url(
        "https://example.test",
        resolver=lambda _host: [None, "not-an-ip", "93.184.216.34"],
    )

    assert ok is True
    assert reason == "ok"


# --- RFC 6052 Well-Known Prefix (DNS64 / NAT64) ---------------------------
#
# On a DNS64/NAT64 network an IPv4-only host resolves to 64:ff9b::<v4>. The
# effective destination is the embedded IPv4 address, so that is what the
# policy must judge. RFC 6052 §3.1 permits the Well-Known Prefix to represent
# only globally-routable IPv4, so the embedded target is always held to the
# strict policy — the prefix must never tunnel past the SSRF guard.


def _nat64(v4: str) -> str:
    """Render the RFC 6052 Well-Known-Prefix form of an IPv4 address."""
    import ipaddress

    packed = int(ipaddress.IPv4Address(v4))
    return str(ipaddress.IPv6Address(int(ipaddress.IPv6Address("64:ff9b::")) | packed))


def test_nat64_well_known_prefix_allows_public_ipv4_destination():
    # The reproduced failure: export.arxiv.org resolves to 64:ff9b::924b:5b2a
    # under DNS64. The embedded 146.75.91.42 is public, so the URL is allowed.
    res = _resolver({"export.arxiv.org": [_nat64("146.75.91.42")]})
    ok, reason = check_outbound_url("https://export.arxiv.org/api/query", resolver=res)
    assert ok is True, reason
    # ...including under full lockdown, where scholarly lookups actually run.
    ok, reason = check_outbound_url(
        "https://export.arxiv.org/api/query", block_private=True, resolver=res
    )
    assert ok is True, reason


def test_nat64_well_known_prefix_blocks_embedded_loopback():
    res = _resolver({"evil.example": [_nat64("127.0.0.1")]})
    for strict in (False, True):
        ok, reason = check_outbound_url(
            "http://evil.example/", block_private=strict, resolver=res
        )
        assert ok is False, strict
        assert "NAT64" in reason and "127.0.0.1" in reason


def test_nat64_well_known_prefix_blocks_embedded_metadata_address():
    # The headline SSRF vector must not become reachable through DNS64.
    res = _resolver({"evil.example": [_nat64("169.254.169.254")]})
    for strict in (False, True):
        ok, reason = check_outbound_url(
            "http://evil.example/latest/meta-data/", block_private=strict, resolver=res
        )
        assert ok is False, strict
        assert "link-local" in reason and "169.254.169.254" in reason


def test_nat64_well_known_prefix_blocks_embedded_private_ipv4_under_strict():
    res = _resolver({"evil.example": [_nat64("192.168.1.50")]})
    ok, reason = check_outbound_url(
        "http://evil.example/", block_private=True, resolver=res
    )
    assert ok is False
    assert "NAT64" in reason and "192.168.1.50" in reason


def test_nat64_well_known_prefix_blocks_embedded_shared_space_under_strict():
    # RFC 6598 shared/CGNAT space is not globally routable, so the Well-Known
    # Prefix may not represent it.
    res = _resolver({"evil.example": [_nat64("100.64.0.1")]})
    ok, reason = check_outbound_url(
        "http://evil.example/", block_private=True, resolver=res
    )
    assert ok is False
    assert "NAT64" in reason and "100.64.0.1" in reason


def test_nat64_well_known_prefix_blocks_embedded_non_global_ipv4():
    # Multicast, unspecified and other non-global space must not be reachable
    # through the Well-Known Prefix, whatever block_private says.
    for v4 in ("224.0.0.1", "0.0.0.0", "198.18.0.1", "192.0.2.10", "240.0.0.1"):
        res = _resolver({"evil.example": [_nat64(v4)]})
        for strict in (False, True):
            ok, reason = check_outbound_url(
                "http://evil.example/", block_private=strict, resolver=res
            )
            assert ok is False, (v4, strict)
            assert v4 in reason, (v4, reason)


def test_network_specific_nat64_prefixes_are_not_decoded():
    # RFC 8215 64:ff9b:1::/48 and arbitrary network-specific prefixes carry
    # locally assigned semantics, so no embedded IPv4 may be inferred from them.
    # 64:ff9b:1::8ef:5b2a would decode to the *public* 8.239.91.42; it stays
    # rejected on the outer IPv6 address, which proves no decoding happened.
    for addr in ("64:ff9b:1::8ef:5b2a", "64:ff9b:1::7f00:1"):
        res = _resolver({"svc.example": [addr]})
        ok, reason = check_outbound_url("http://svc.example/", resolver=res)
        assert ok is False, addr
        assert "NAT64" not in reason, addr
        assert addr in reason, addr

    # A documentation-range prefix is judged as an ordinary IPv6 address under
    # the existing policy (local-first by default, blocked under lockdown) and
    # is never reinterpreted as carrying an IPv4 destination.
    res = _resolver({"svc.example": ["2001:db8:1::8ef:5b2a"]})
    ok, reason = check_outbound_url("http://svc.example/", resolver=res)
    assert ok is True, reason
    ok, reason = check_outbound_url(
        "http://svc.example/", block_private=True, resolver=res
    )
    assert ok is False
    assert "NAT64" not in reason and "8.239.91.42" not in reason


def test_ordinary_ipv6_behaviour_is_unchanged():
    # A global unicast IPv6 host is allowed in both modes.
    GLOBAL6 = _resolver({"v6.example": ["2606:2800:220:1:248:1893:25c8:1946"]})
    assert check_outbound_url("https://v6.example/", resolver=GLOBAL6)[0] is True
    assert (
        check_outbound_url("https://v6.example/", block_private=True, resolver=GLOBAL6)[0]
        is True
    )

    # ULA is local-first allowed by default, blocked under lockdown.
    ULA = _resolver({"ula.example": ["fd00::1"]})
    assert check_outbound_url("http://ula.example/", resolver=ULA)[0] is True
    ok, reason = check_outbound_url(
        "http://ula.example/", block_private=True, resolver=ULA
    )
    assert ok is False and "private" in reason

    # fe80::/10 is always blocked.
    LL6 = _resolver({"ll.example": ["fe80::1"]})
    ok, reason = check_outbound_url("http://ll.example/", resolver=LL6)
    assert ok is False and "link-local" in reason
