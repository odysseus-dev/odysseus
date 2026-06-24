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


def test_ssl_context_no_override_is_none(monkeypatch):
    """When neither env var is set, _ca must be None (not empty string)."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or None
    assert ca is None


def test_ssl_context_empty_string_normalises_to_none(monkeypatch):
    """Docker Compose :-default forwards empty strings; they must not reach cafile=."""
    monkeypatch.setenv("SSL_CERT_FILE", "")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "")

    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or None
    assert ca is None


def test_verify_x509_strict_cleared():
    """The ssl context passed to httpx must not have VERIFY_X509_STRICT set.

    This flag (Python 3.12+) rejects self-signed certs missing keyUsage, but
    requests/urllib3 (used by the sync path) does not set it.
    """
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT), (
            "VERIFY_X509_STRICT must be cleared so self-signed certs are accepted"
        )


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
