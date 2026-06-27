"""Behavior tests for src.app_helpers.serve_html_with_nonce.

Every caller of this helper serves a fixed, app-bundled template
(index/login/backgrounds), never a client-supplied path. So a read failure —
a missing file (broken deployment) or a permission/IO error — is a server
fault, not a client "not found", and must surface as a logged 500 rather than
hiding behind a 404 where 5xx alerting can't see it. These tests lock that
intent (raised in the PR #4637 review).
"""
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette.responses")
from fastapi import HTTPException

from src.app_helpers import serve_html_with_nonce


def _request_with_nonce(nonce: str = ""):
    """Minimal stand-in for a Starlette Request: only request.state.csp_nonce is read."""
    return types.SimpleNamespace(state=types.SimpleNamespace(csp_nonce=nonce))


def test_missing_fixed_template_returns_500_not_404(tmp_path):
    missing = tmp_path / "does_not_exist.html"
    with pytest.raises(HTTPException) as exc_info:
        serve_html_with_nonce(_request_with_nonce(), str(missing))
    assert exc_info.value.status_code == 500
    # Generic detail — no OS error string or absolute path leaked to the client.
    assert exc_info.value.detail == "Internal server error"


def test_unreadable_template_returns_500(tmp_path):
    # A directory at the path makes open() raise an OSError subtype
    # (IsADirectoryError on POSIX, PermissionError on Windows) — same branch.
    a_dir = tmp_path / "a_dir.html"
    a_dir.mkdir()
    with pytest.raises(HTTPException) as exc_info:
        serve_html_with_nonce(_request_with_nonce(), str(a_dir))
    assert exc_info.value.status_code == 500


def test_readable_template_injects_nonce(tmp_path):
    page = tmp_path / "page.html"
    page.write_text('<script nonce="{{CSP_NONCE}}">x</script>', encoding="utf-8")
    resp = serve_html_with_nonce(_request_with_nonce("nonce-abc"), str(page))
    assert resp.status_code == 200
    body = resp.body.decode("utf-8")
    assert "nonce-abc" in body
    assert "{{CSP_NONCE}}" not in body
