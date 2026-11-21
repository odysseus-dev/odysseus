"""Regression tests for is_trusted_loopback defense-in-depth in require_admin."""

from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from core.middleware import (
    INTERNAL_TOOL_HEADER,
    INTERNAL_TOOL_TOKEN,
    INTERNAL_TOOL_USER,
    is_trusted_loopback,
    require_admin,
)


def _req(host="127.0.0.1", headers=None, current_user=None):
    state = SimpleNamespace(current_user=current_user)
    app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
        state=state,
        app=app,
    )


def test_is_trusted_loopback_accepts_direct_loopback_and_rejects_forwarded():
    assert is_trusted_loopback(_req("127.0.0.1")) is True
    assert is_trusted_loopback(_req("::1")) is True
    assert is_trusted_loopback(_req("10.0.0.5")) is False
    assert is_trusted_loopback(_req("127.0.0.1", {"x-forwarded-for": "203.0.113.9"})) is False
    assert is_trusted_loopback(_req("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"})) is False


def test_require_admin_blocks_internal_token_when_proxy_forwarded(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    # Direct loopback with valid internal token succeeds
    require_admin(_req("127.0.0.1", {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}))

    # Tunneled/forwarded loopback with internal token or internal-tool state must be rejected
    with pytest.raises(HTTPException) as exc1:
        require_admin(_req("127.0.0.1", {
            INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN,
            "x-forwarded-for": "198.51.100.42",
        }))
    assert exc1.value.status_code == 403

    with pytest.raises(HTTPException) as exc2:
        require_admin(_req("127.0.0.1", {"cf-ray": "abc123"}, current_user=INTERNAL_TOOL_USER))
    assert exc2.value.status_code == 403
