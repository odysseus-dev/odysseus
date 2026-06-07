"""Regression test for the CORS-preflight auth bypass.

AuthMiddleware is the outermost middleware, so it used to 401 the credential-less
OPTIONS preflight before CORSMiddleware could answer it -- which blocks every
cross-origin browser/WebView client before the real request is ever sent. The
fix lets a genuine preflight through; `is_cors_preflight` is the pure predicate
it uses. Guard it so the bypass can't silently regress.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.middleware import is_cors_preflight


def test_genuine_preflight_is_detected():
    assert is_cors_preflight("OPTIONS", {"access-control-request-method": "POST"}) is True


def test_bare_options_is_not_a_preflight():
    # OPTIONS without Access-Control-Request-Method must NOT bypass auth.
    assert is_cors_preflight("OPTIONS", {}) is False


def test_real_methods_are_never_preflight():
    headers = {"access-control-request-method": "POST"}
    for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        assert is_cors_preflight(method, headers) is False
