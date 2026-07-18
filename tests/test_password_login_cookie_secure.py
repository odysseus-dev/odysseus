"""Regression: the password-login session cookie must be Secure whenever
the request arrived over HTTPS, even with SECURE_COOKIES=false (the
bundled Compose default) — a stock TLS deployment must not issue a bearer
cookie eligible for cleartext transmission."""

from types import SimpleNamespace


def _fake_request(scheme="https", headers=None):
    req = SimpleNamespace()
    req.url = SimpleNamespace()
    req.url.scheme = scheme
    req.headers = headers or {}
    return req


class TestSessionCookieSecure:
    def _secure(self, request):
        from routes.auth_routes import _session_cookie_secure
        return _session_cookie_secure(request)

    def test_https_request_secure_despite_secure_cookies_false(self, monkeypatch):
        monkeypatch.setenv("SECURE_COOKIES", "false")
        monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
        assert self._secure(_fake_request("https")) is True

    def test_explicit_true_always_secure(self, monkeypatch):
        monkeypatch.setenv("SECURE_COOKIES", "true")
        assert self._secure(_fake_request("http")) is True

    def test_plain_http_not_secure(self, monkeypatch):
        monkeypatch.setenv("SECURE_COOKIES", "false")
        monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
        assert self._secure(_fake_request("http")) is False

    def test_forwarded_proto_ignored_without_trust_optin(self, monkeypatch):
        """A client-spoofed X-Forwarded-Proto must not influence policy."""
        monkeypatch.setenv("SECURE_COOKIES", "false")
        monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
        req = _fake_request("http", {"x-forwarded-proto": "https"})
        assert self._secure(req) is False

    def test_forwarded_proto_honoured_with_trust_optin(self, monkeypatch):
        monkeypatch.setenv("SECURE_COOKIES", "false")
        monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
        req = _fake_request("http", {"x-forwarded-proto": "https"})
        assert self._secure(req) is True
