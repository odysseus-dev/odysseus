"""Pure tests for product-aware SSRF URL policy helpers."""

from src.ssrf_guard import UrlAccessPolicy, assess_url, classify_ip, inspect_url


def _resolver(mapping):
    def resolve(host):
        return mapping[host]

    return resolve


def test_classify_metadata_service_ip():
    assert "metadata" in classify_ip("169.254.169.254")
    assert "link_local" in classify_ip("169.254.169.254")


def test_inspect_hostname_uses_injected_resolver():
    info = inspect_url(
        "https://models.example.test/v1",
        resolver=_resolver({"models.example.test": ["203.0.113.10"]}),
    )

    assert info.host == "models.example.test"
    assert info.addresses[0].address == "203.0.113.10"


def test_strict_policy_blocks_metadata_service_ip():
    decision = assess_url(
        "http://169.254.169.254/latest/meta-data/",
        UrlAccessPolicy.STRICT_UNTRUSTED_FETCH,
    )

    assert decision.allowed is False
    assert decision.reason == "metadata_service_blocked"


def test_trusted_policy_still_blocks_metadata_service_ip():
    decision = assess_url(
        "http://169.254.169.254/latest/meta-data/",
        UrlAccessPolicy.TRUSTED_USER_CONFIGURED_ENDPOINT,
    )

    assert decision.allowed is False
    assert decision.reason == "metadata_service_blocked"


def test_strict_policy_blocks_hostname_that_resolves_to_metadata_service():
    decision = assess_url(
        "http://attacker.example.test/",
        UrlAccessPolicy.STRICT_UNTRUSTED_FETCH,
        resolver=_resolver({"attacker.example.test": ["169.254.169.254"]}),
    )

    assert decision.allowed is False
    assert decision.reason == "metadata_service_blocked"


def test_strict_policy_blocks_loopback_private_lan_and_tailscale():
    cases = [
        ("http://127.0.0.1:11434/v1/models", "blocked_loopback"),
        ("http://localhost:11434/v1/models", "blocked_loopback"),
        ("http://192.168.1.20:8000/v1/models", "blocked_private"),
        ("http://10.0.0.25:8000/v1/models", "blocked_private"),
        ("http://100.64.1.2:8000/v1/models", "blocked_tailscale"),
    ]
    resolver = _resolver({"localhost": ["127.0.0.1"]})

    for url, reason in cases:
        decision = assess_url(url, UrlAccessPolicy.STRICT_UNTRUSTED_FETCH, resolver=resolver)
        assert decision.allowed is False, url
        assert decision.reason == reason


def test_trusted_policy_allows_local_first_model_server_flows():
    cases = [
        "http://127.0.0.1:11434/v1/models",
        "http://localhost:11434/v1/models",
        "http://192.168.1.20:8000/v1/models",
        "http://10.0.0.25:8000/v1/models",
        "http://100.64.1.2:8000/v1/models",
    ]
    resolver = _resolver({"localhost": ["127.0.0.1"]})

    for url in cases:
        decision = assess_url(url, UrlAccessPolicy.TRUSTED_USER_CONFIGURED_ENDPOINT, resolver=resolver)
        assert decision.allowed is True, url
        assert decision.reason == "allowed_trusted_configured_endpoint"


def test_public_https_allowed_in_strict_policy():
    decision = assess_url(
        "https://example.com/image.png",
        UrlAccessPolicy.STRICT_UNTRUSTED_FETCH,
        resolver=_resolver({"example.com": ["93.184.216.34"]}),
    )

    assert decision.allowed is True
    assert decision.reason == "allowed_public_http"


def test_non_http_scheme_is_rejected_before_policy_specific_allow():
    decision = assess_url(
        "file:///etc/passwd",
        UrlAccessPolicy.TRUSTED_USER_CONFIGURED_ENDPOINT,
    )

    assert decision.allowed is False
    assert decision.reason == "missing_host"
