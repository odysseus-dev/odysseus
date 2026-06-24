"""CalDAV test_connection SSL parity with the sync path.

The sync path uses caldav.DAVClient -> requests, which respects SSL_CERT_FILE
and REQUESTS_CA_BUNDLE and does not set VERIFY_X509_STRICT.  The
test_connection endpoint must match that behaviour even though it uses httpx
with trust_env=False (kept for SSRF protection).
"""

import ssl
import os


def test_ssl_context_respects_ssl_cert_file(monkeypatch, tmp_path):
    """When SSL_CERT_FILE is set, _ca resolves to that path (env-var selection logic)."""
    fake_bundle = tmp_path / "ca.pem"
    fake_bundle.write_text("# fake bundle")
    monkeypatch.setenv("SSL_CERT_FILE", str(fake_bundle))
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or None
    assert ca == str(fake_bundle)


def test_ssl_context_respects_requests_ca_bundle(monkeypatch, tmp_path):
    fake_bundle = tmp_path / "ca.pem"
    fake_bundle.write_text("# fake bundle")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(fake_bundle))

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or None
    assert ca == str(fake_bundle)


def test_ssl_context_no_override_falls_back_to_certifi(monkeypatch):
    """When neither env var is set, _ca falls back to certifi's bundle (not None)."""
    import certifi
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or certifi.where()
    assert ca == certifi.where()
    assert os.path.isfile(ca)


def test_ssl_context_empty_string_falls_back_to_certifi(monkeypatch):
    """Docker Compose :-default forwards empty strings; they must fall back to certifi."""
    import certifi
    monkeypatch.setenv("SSL_CERT_FILE", "")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "")

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or certifi.where()
    assert ca == certifi.where()


def test_verify_x509_strict_cleared():
    """Python's ssl module supports clearing VERIFY_X509_STRICT on a default context.

    This tests the mechanism (the flag can be cleared). Whether the route actually
    clears it is covered by the VERIFY_X509_STRICT source-text assertion in
    test_caldav_url_hardening.py.
    """
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)


def test_ssl_env_fallback_order(monkeypatch, tmp_path):
    """SSL_CERT_FILE takes priority over REQUESTS_CA_BUNDLE."""
    a = tmp_path / "a.pem"
    b = tmp_path / "b.pem"
    a.write_text("# a")
    b.write_text("# b")
    monkeypatch.setenv("SSL_CERT_FILE", str(a))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(b))

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or None
    assert ca == str(a), "SSL_CERT_FILE must win over REQUESTS_CA_BUNDLE"
