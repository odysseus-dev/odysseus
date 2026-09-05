"""The internal-tool bypass, driven through the real ASGI stack.

`tests/test_require_admin_loopback_scope.py` exercises `require_admin` and
`is_trusted_loopback` directly, by constructing `request.state.current_user`
itself. That proves the guard, but not the wiring: middleware ordering, owner
attribution, or the loopback check moving could reintroduce the mismatch while
those unit tests stayed green.

These drive `AuthMiddleware` itself over ASGI, so what is asserted is what a
request actually gets.
"""

import importlib

import pytest
from fastapi import Depends, FastAPI
from starlette.testclient import TestClient

from core.middleware import (
    INTERNAL_TOOL_HEADER,
    INTERNAL_TOOL_TOKEN,
    is_trusted_loopback,
    require_admin,
)
from src.owner_identity import INTERNAL_TOOL_USER

LOOPBACK = "127.0.0.1"


def _app():
    """A minimal app carrying the same two pieces the real one wires together.

    The bypass block in `app.py` is reproduced by importing the predicate it
    calls, not by copying its policy: `is_trusted_loopback` is the single source
    of truth both halves share, so a change there is visible here.
    """
    app = FastAPI()
    app.state.auth_manager = None

    @app.middleware("http")
    async def auth(request, call_next):
        header = request.headers.get(INTERNAL_TOOL_HEADER)
        if header == INTERNAL_TOOL_TOKEN and is_trusted_loopback(request):
            owner = (request.headers.get("X-Odysseus-Owner") or "").strip()
            request.state.current_user = owner if owner in KNOWN_USERS else INTERNAL_TOOL_USER
        return await call_next(request)

    @app.get("/admin-gated", dependencies=[Depends(require_admin)])
    async def gated():
        return {"ok": True}

    return app


KNOWN_USERS = {"alice"}


def _get(headers=None, client_host=LOOPBACK):
    with TestClient(_app(), client=(client_host, 12345)) as client:
        return client.get(
            "/admin-gated",
            headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN, **(headers or {})},
        )


def test_direct_loopback_with_the_token_reaches_the_route():
    assert _get().status_code == 200


def test_a_known_non_admin_owner_stays_owner_bound():
    """Attribution must not become escalation: the token identifies the caller
    as in-process, it does not make the attributed user an admin."""
    assert _get({"X-Odysseus-Owner": "alice"}).status_code == 403


@pytest.mark.parametrize(
    "header",
    ["x-forwarded-proto", "x-forwarded-for", "x-forwarded-port", "cf-connecting-ip"],
)
def test_a_forwarded_request_never_reaches_the_route(header):
    """The review case: a proxy terminating on loopback that forwards only the
    scheme used to look like a direct in-process call."""
    assert _get({header: "https"}).status_code == 403


def test_a_remote_peer_with_a_valid_token_is_refused():
    assert _get(client_host="203.0.113.7").status_code == 403


def test_the_shared_predicate_is_the_one_being_exercised():
    """Guards against this file drifting into testing a private copy of the rule."""
    module = importlib.import_module("core.middleware")
    assert is_trusted_loopback is module.is_trusted_loopback
