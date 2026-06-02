"""Regression tests for trusted configured endpoint SSRF notices.

These source-level checks avoid importing the FastAPI route modules, which can
boot database-heavy dependencies in this test environment.
"""

from pathlib import Path


def test_model_endpoint_routes_surface_trusted_security_notice():
    text = Path("routes/model_routes.py").read_text(encoding="utf-8")

    assert "from src.ssrf_guard import trusted_endpoint_notice" in text
    assert "def _trusted_endpoint_notice_or_error(base_url: str)" in text
    assert "security_notice = _trusted_endpoint_notice_or_error(base_url)" in text
    assert "_trusted_endpoint_notice_or_error(_new_base)" in text
    assert '"security_notice": security_notice' in text
    assert '"security_notice": trusted_endpoint_notice(r.base_url)' in text


def test_embedding_endpoint_routes_surface_trusted_security_notice():
    text = Path("routes/embedding_routes.py").read_text(encoding="utf-8")

    assert "from src.ssrf_guard import trusted_endpoint_notice" in text
    assert "def _trusted_endpoint_notice_or_error(url: str)" in text
    assert "security_notice = _trusted_endpoint_notice_or_error(url)" in text
    assert '"security_notice": trusted_endpoint_notice(url) if url else None' in text
    assert '"security_notice": security_notice' in text
