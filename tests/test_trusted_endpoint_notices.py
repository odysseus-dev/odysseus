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


def test_caldav_routes_surface_trusted_security_notice():
    text = Path("routes/calendar_routes.py").read_text(encoding="utf-8")

    assert "from src.ssrf_guard import trusted_endpoint_notice" in text
    assert "def _trusted_endpoint_notice_or_error(url: str)" in text
    assert 'security_notice = _trusted_endpoint_notice_or_error(cfg["url"])' in text
    assert "security_notice = _trusted_endpoint_notice_or_error(url)" in text
    assert '"security_notice": trusted_endpoint_notice(url) if url else None' in text
    assert '"security_notice": security_notice' in text


def test_carddav_routes_surface_trusted_security_notice():
    text = Path("routes/contacts_routes.py").read_text(encoding="utf-8")

    assert "from src.ssrf_guard import trusted_endpoint_notice" in text
    assert "def _trusted_endpoint_notice_or_error(url: str)" in text
    assert 'security_notice = _trusted_endpoint_notice_or_error((data.get("carddav_url") or "").strip())' in text
    assert 'cfg["security_notice"] = trusted_endpoint_notice(url) if url else None' in text
    assert '"security_notice": security_notice' in text


def test_ntfy_integration_routes_surface_trusted_security_notice():
    text = Path("routes/auth_routes.py").read_text(encoding="utf-8")

    assert "from src.ssrf_guard import trusted_endpoint_notice" in text
    assert "def _integration_security_notice(item: dict)" in text
    assert "def _ntfy_notice_or_error(item: dict)" in text
    assert "safe = [_mask_integration_with_notice(item) for item in items]" in text
    assert "_ntfy_notice_or_error(body)" in text
    assert "_ntfy_notice_or_error(candidate)" in text
    assert '"security_notice": security_notice' in text


def test_ntfy_reminder_delivery_checks_saved_endpoint_notice():
    text = Path("routes/note_routes.py").read_text(encoding="utf-8")

    assert "from src.ssrf_guard import trusted_endpoint_notice" in text
    assert "notice = trusted_endpoint_notice(base)" in text
    assert 'if not notice.get("allowed")' in text
    assert "raise RuntimeError(ntfy_error)" in text
