"""`require_admin` must scope the internal-tool loopback the way AuthMiddleware does.

THREAT_MODEL.md describes the loopback as an *in-process* mechanism: the agent's
tool calls can't carry the admin's session cookie, so they ride a per-process
token instead. `app.AuthMiddleware` honours that token only for a DIRECT
loopback connection carrying no proxy/tunnel forwarding headers, and — when the
call names a session owner via `X-Odysseus-Owner` — attributes the request to
that real user rather than to the `internal-tool` sentinel.

`core.middleware.require_admin` is the second half of that contract. These tests
pin the two properties it must preserve:

1. The token is not an admin credential on its own. It only counts from the same
   direct-loopback origin AuthMiddleware demands, so a caller that reaches a
   route from an arbitrary address can't present the token and be admin.
2. Impersonation is not an escalation. When the loopback ran on behalf of a
   NON-admin session owner, that owner's own admin status decides — the shared
   per-process token must not lift them to admin.
"""

import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from core.middleware import (
    INTERNAL_TOOL_HEADER,
    INTERNAL_TOOL_TOKEN,
    is_trusted_loopback,
    require_admin,
)
from src.owner_identity import INTERNAL_TOOL_USER


LOOPBACK = "127.0.0.1"
REMOTE = "192.0.2.10"


def _req(
    current_user=None,
    *,
    client_host=LOOPBACK,
    headers=None,
    is_admin=False,
    configured=True,
):
    """A request stub shaped like the attributes require_admin actually reads."""
    return SimpleNamespace(
        state=SimpleNamespace(current_user=current_user, api_token=False),
        headers=dict(headers or {}),
        client=SimpleNamespace(host=client_host),
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=SimpleNamespace(
                    is_admin=lambda u: is_admin,
                    is_configured=configured,
                )
            )
        ),
    )


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")


# --- is_trusted_loopback: the shared origin rule ---------------------------

def test_direct_loopback_without_forwarding_headers_is_trusted():
    assert is_trusted_loopback(_req(client_host=LOOPBACK)) is True
    assert is_trusted_loopback(_req(client_host="::1")) is True


def test_remote_client_is_never_trusted_loopback():
    assert is_trusted_loopback(_req(client_host=REMOTE)) is False


@pytest.mark.parametrize(
    "header",
    [
        "x-forwarded-for",
        "x-forwarded-host",
        "x-real-ip",
        "forwarded",
        "cf-connecting-ip",
        "cf-ray",
        "cf-visitor",
        # Review found this one missing: a proxy terminating on loopback that
        # forwards only the scheme still read as a direct in-process call.
        "x-forwarded-proto",
        # The rest of the family, which an enumeration of spellings kept missing.
        "x-forwarded-port",
        "x-forwarded-prefix",
        "x-forwarded-server",
        "true-client-ip",
        "x-client-ip",
        "x-cluster-client-ip",
        "cdn-loop",
    ],
)
def test_forwarded_request_is_not_trusted_loopback(header):
    # cloudflared / nginx / Caddy connect to the app FROM 127.0.0.1, so the
    # peer address alone can't distinguish a tunnelled visitor from the app
    # calling itself. The forwarding header is what gives it away.
    assert is_trusted_loopback(_req(client_host=LOOPBACK, headers={header: "1"})) is False


# --- the token is not a portable admin credential --------------------------

def test_internal_token_grants_admin_from_a_direct_loopback_call():
    # The real in-process path: no session user, token present, direct loopback.
    require_admin(_req(None, headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}))


def test_internal_token_from_a_remote_client_does_not_grant_admin():
    with pytest.raises(HTTPException) as exc:
        require_admin(
            _req(
                None,
                client_host=REMOTE,
                headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
            )
        )
    assert exc.value.status_code == 403


def test_internal_token_behind_a_tunnel_does_not_grant_admin():
    with pytest.raises(HTTPException) as exc:
        require_admin(
            _req(
                None,
                client_host=LOOPBACK,
                headers={
                    INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN,
                    "cf-connecting-ip": "203.0.113.7",
                },
            )
        )
    assert exc.value.status_code == 403


def test_wrong_internal_token_does_not_grant_admin():
    with pytest.raises(HTTPException) as exc:
        require_admin(_req(None, headers={INTERNAL_TOOL_HEADER: "not-the-token"}))
    assert exc.value.status_code == 403


# --- impersonation must not escalate ---------------------------------------

def test_sentinel_user_stamped_by_the_middleware_still_passes():
    # AuthMiddleware already checked token + origin before stamping this.
    require_admin(_req(INTERNAL_TOOL_USER))


def test_impersonated_admin_owner_passes():
    require_admin(
        _req(
            "alice",
            is_admin=True,
            headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
        )
    )


def test_impersonated_non_admin_owner_is_not_escalated_by_the_token():
    # A tool call made on behalf of a non-admin session owner. AuthMiddleware
    # attributed it to "bob"; bob is not an admin, so the admin-gated route
    # must stay closed even though the loopback token is present.
    with pytest.raises(HTTPException) as exc:
        require_admin(
            _req(
                "bob",
                is_admin=False,
                headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
            )
        )
    assert exc.value.status_code == 403


def test_bearer_pseudo_user_is_not_escalated_by_the_token():
    with pytest.raises(HTTPException) as exc:
        require_admin(
            _req("api", is_admin=False, headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN})
        )
    assert exc.value.status_code == 403


# --- unchanged behaviour ---------------------------------------------------

def test_admin_session_user_passes_without_any_token():
    require_admin(_req("alice", is_admin=True))


def test_non_admin_session_user_is_rejected():
    with pytest.raises(HTTPException) as exc:
        require_admin(_req("bob", is_admin=False))
    assert exc.value.status_code == 403


def test_auth_disabled_allows_everything(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    require_admin(_req(None, client_host=REMOTE))


def test_pre_setup_is_closed_not_open():
    with pytest.raises(HTTPException) as exc:
        require_admin(_req("alice", is_admin=True, configured=False))
    assert exc.value.status_code == 403


# --- single source of truth ------------------------------------------------

def test_app_middleware_reuses_the_shared_loopback_helper():
    """app.py must not keep its own copy of the origin rule.

    The bug this file fixes was exactly a second, diverging implementation of
    "is this the in-process loopback?". Reading the source keeps the check
    honest without importing app.py (which builds the whole application).
    """
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[1] / "app.py"
    text = source.read_text(encoding="utf-8")
    assert "is_trusted_loopback" in text
    # No locally-redefined copy of the rule.
    assert "def _is_trusted_loopback" not in text
    assert "_PROXY_FWD_HEADERS = (" not in text
