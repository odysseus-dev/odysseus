from core.log_safety import redact_url


def test_strips_userinfo():
    assert redact_url("https://user:pass@host.example/v1/models") == "https://host.example/v1/models"


def test_strips_query_and_fragment():
    assert redact_url("https://host.example/v1?api_key=secret#frag") == "https://host.example/v1"


def test_keeps_port_and_path():
    assert redact_url("http://host.example:8080/api/tags") == "http://host.example:8080/api/tags"


def test_ipv6_host_keeps_brackets():
    assert redact_url("https://user:pass@[2001:db8::1]:8443/v1") == "https://[2001:db8::1]:8443/v1"
    assert redact_url("https://[2001:db8::1]/v1") == "https://[2001:db8::1]/v1"


def test_no_credentials_passthrough():
    assert redact_url("https://host.example/v1/models") == "https://host.example/v1/models"


def test_empty_and_none():
    assert redact_url("") == ""
    assert redact_url(None) == ""


def test_garbage_does_not_raise():
    # urlparse is lenient; just assert no credential-looking userinfo survives.
    assert "@" not in redact_url("::::not a url::::")


# Scheme-less endpoint URLs (``user:pass@host/path``) made urlparse read the
# username as the scheme, leaving the credentials in the path where
# redact_url passed them straight through to the log line.
_SCHEMELESS_CREDENTIAL_URLS = (
    "admin:hunter2@10.0.0.5:11434/v1",
    "admin:hunter2@host.example/v1/models",
    "admin:hunter2@[2001:db8::1]:8443/v1",
    "admin@10.0.0.5/v1",
    "admin:hunter2@host.example/v1?api_key=hunter2#frag",
)


def test_schemeless_userinfo_is_stripped():
    assert redact_url("admin:hunter2@10.0.0.5:11434/v1") == "10.0.0.5:11434/v1"
    assert redact_url("admin:hunter2@host.example/v1/models") == "host.example/v1/models"


def test_schemeless_username_only_is_stripped():
    assert redact_url("admin@10.0.0.5/v1") == "10.0.0.5/v1"


def test_schemeless_ipv6_userinfo_is_stripped():
    assert redact_url("admin:hunter2@[2001:db8::1]:8443/v1") == "[2001:db8::1]:8443/v1"


def test_schemeless_query_and_fragment_are_stripped():
    assert redact_url("admin:hunter2@host.example/v1?api_key=hunter2#frag") == "host.example/v1"


def test_no_credential_survives_any_schemeless_shape():
    for url in _SCHEMELESS_CREDENTIAL_URLS:
        out = redact_url(url)
        assert "hunter2" not in out, (url, out)
        assert "admin" not in out, (url, out)
        assert "@" not in out, (url, out)


def test_schemeless_without_credentials_passthrough():
    assert redact_url("10.0.0.5:11434/v1") == "10.0.0.5:11434/v1"
    assert redact_url("host.example/v1/models") == "host.example/v1/models"


def test_network_path_reference_keeps_leading_slashes():
    assert redact_url("//user:pass@host.example/v1") == "//host.example/v1"
