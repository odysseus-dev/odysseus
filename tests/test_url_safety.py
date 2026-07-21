"""Tests for outbound URL safety / SSRF hardening (src/url_safety.py).

A stub resolver is injected so the tests never touch real DNS.
"""

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
