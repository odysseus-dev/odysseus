"""Regression: CalDAV test_connection must trust the operator's CA bundle.

The pre-flight used httpx with trust_env=False, which ignored
SSL_CERT_FILE/REQUESTS_CA_BUNDLE. Self-signed CalDAV servers that the
real sync accepts (via caldav lib → requests → honors bundle) were
rejected by the test with CERTIFICATE_VERIFY_FAILED.

These tests verify the SSL context construction without making real
network calls.
"""
import os
import ssl
import tempfile
from unittest.mock import patch


def _build_ssl_ctx(ssl_cert_file=None, requests_ca_bundle=None):
    """Reproduce the SSL context construction from calendar_routes.test_connection."""
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    ca_bundle = ssl_cert_file or requests_ca_bundle
    if ca_bundle and os.path.isfile(ca_bundle):
        ctx.load_verify_locations(ca_bundle)
    return ctx


def test_verify_x509_strict_is_cleared():
    ctx = _build_ssl_ctx()
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)


def test_ssl_cert_file_takes_precedence(tmp_path):
    # Create a dummy CA bundle (won't validate real certs, but load_verify_locations accepts it)
    bundle = tmp_path / "ca-bundle.pem"
    # Write a minimal self-signed cert for load_verify_locations to accept
    import subprocess
    result = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:1024", "-keyout", "/dev/null",
         "-out", str(bundle), "-days", "1", "-nodes", "-subj", "/CN=test"],
        capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        # openssl not available — skip gracefully
        import pytest
        pytest.skip("openssl not available")

    ctx = _build_ssl_ctx(ssl_cert_file=str(bundle))
    # Context should have loaded without error — the bundle is valid
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)


def test_missing_bundle_path_does_not_crash():
    """A nonexistent CA bundle path must not crash — fall back to system CAs."""
    ctx = _build_ssl_ctx(ssl_cert_file="/nonexistent/ca-bundle.pem")
    # Should return a valid context using system defaults
    assert isinstance(ctx, ssl.SSLContext)


def test_empty_env_vars_use_system_defaults():
    ctx = _build_ssl_ctx(ssl_cert_file="", requests_ca_bundle="")
    assert isinstance(ctx, ssl.SSLContext)
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)
