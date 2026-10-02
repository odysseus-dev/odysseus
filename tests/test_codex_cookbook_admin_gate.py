"""Fail-closed boundary tests for the duplicate Codex Cookbook routes."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

import core.middleware as middleware
from core.middleware import (
    CodexCookbookBoundaryMiddleware,
    INTERNAL_TOOL_HEADER,
    INTERNAL_TOOL_TOKEN,
    INTERNAL_TOOL_USER,
    is_codex_cookbook_path,
    is_odysseus_bearer_authorization,
)
import routes.codex_routes as codex_routes


COOKBOOK_ROUTES = [
    pytest.param("GET", "/api/codex/cookbook/tasks", None, id="tasks"),
    pytest.param("GET", "/api/codex/cookbook/servers", None, id="servers"),
    pytest.param(
        "GET",
        "/api/codex/cookbook/output/serve-test?tail=40",
        None,
        id="output",
    ),
    pytest.param("GET", "/api/codex/cookbook/cached?host=gpu", None, id="cached"),
    pytest.param("GET", "/api/codex/cookbook/presets", None, id="presets"),
    pytest.param(
        "POST",
        "/api/codex/cookbook/serve",
        {"repo_id": "org/model", "cmd": "vllm serve org/model"},
        id="serve",
    ),
    pytest.param(
        "POST",
        "/api/codex/cookbook/preset/saved",
        None,
        id="preset",
    ),
    pytest.param(
        "POST",
        "/api/codex/cookbook/adopt",
        {"tmux_session": "serve-test", "model": "org/model", "host": "gpu"},
        id="adopt",
    ),
    pytest.param(
        "POST",
        "/api/codex/cookbook/stop/serve-test",
        None,
        id="stop",
    ),
]

BLOCKED_IDENTITIES = [
    pytest.param({"x-test-identity": "api-token"}, id="api-token"),
    pytest.param({"x-test-identity": "internal-user"}, id="internal-user"),
    pytest.param(
        {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
        id="internal-header",
    ),
]

PRE_BODY_CREDENTIALS = [
    pytest.param(
        {"authorization": "Bearer ody_valid_format_token"},
        id="valid-format-bearer",
    ),
    pytest.param(
        {"authorization": "bEaReR \t ody_x"},
        id="invalid-short-bearer",
    ),
    pytest.param(
        {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
        id="internal-header",
    ),
]

_INTERNAL_HEADER_BYTES = INTERNAL_TOOL_HEADER.lower().encode("ascii")
_INTERNAL_TOKEN_BYTES = INTERNAL_TOOL_TOKEN.encode("utf-8")
DUPLICATE_PRE_BODY_HEADERS = [
    pytest.param(
        [
            (b"authorization", b"Basic placeholder"),
            (b"authorization", b"Bearer ody_second_value"),
        ],
        id="bearer-second",
    ),
    pytest.param(
        [
            (b"authorization", b"Bearer ody_first_value"),
            (b"authorization", b"Basic placeholder"),
        ],
        id="bearer-first",
    ),
    pytest.param(
        [
            (_INTERNAL_HEADER_BYTES, b"invalid"),
            (_INTERNAL_HEADER_BYTES, _INTERNAL_TOKEN_BYTES),
        ],
        id="internal-second",
    ),
    pytest.param(
        [
            (_INTERNAL_HEADER_BYTES, _INTERNAL_TOKEN_BYTES),
            (_INTERNAL_HEADER_BYTES, b"invalid"),
        ],
        id="internal-first",
    ),
    pytest.param(
        [(b"authorization", b"Basic placeholder, Bearer ody_combined")],
        id="bearer-proxy-combined",
    ),
    pytest.param(
        [(_INTERNAL_HEADER_BYTES, b"invalid, " + _INTERNAL_TOKEN_BYTES)],
        id="internal-proxy-combined",
    ),
    pytest.param(
        [(_INTERNAL_HEADER_BYTES, b" " + _INTERNAL_TOKEN_BYTES)],
        id="internal-leading-sp",
    ),
    pytest.param(
        [(_INTERNAL_HEADER_BYTES, _INTERNAL_TOKEN_BYTES + b" ")],
        id="internal-trailing-sp",
    ),
    pytest.param(
        [(_INTERNAL_HEADER_BYTES, b" " + _INTERNAL_TOKEN_BYTES + b" ")],
        id="internal-both-sp",
    ),
    pytest.param(
        [(_INTERNAL_HEADER_BYTES, b"\t" + _INTERNAL_TOKEN_BYTES + b"\t")],
        id="internal-both-htab",
    ),
    pytest.param(
        [
            (_INTERNAL_HEADER_BYTES, b"\xff"),
            (b"authorization", b"Bearer ody_after_obs_text"),
        ],
        id="non-ascii-before-bearer",
    ),
    pytest.param(
        [
            (_INTERNAL_HEADER_BYTES, b"\xff"),
            (_INTERNAL_HEADER_BYTES, _INTERNAL_TOKEN_BYTES),
        ],
        id="non-ascii-before-internal",
    ),
]


class _PoisonPath:
    def __fspath__(self):
        raise AssertionError("Cookbook state must not be resolved before the gate")


def _build_app(side_effects: list[str]) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda username: username == "alice",
    )

    @app.middleware("http")
    async def stamp_test_identity(request: Request, call_next):
        identity = request.headers.get("x-test-identity")
        if identity == "api-token":
            request.state.current_user = "api"
            request.state.api_token = True
            request.state.api_token_owner = "alice"
            # Legacy rows can still carry these retired strings. They grant no
            # access and are intentionally not migrated by this change.
            request.state.api_token_scopes = ["cookbook:read", "cookbook:launch"]
        elif identity == "internal-user":
            request.state.current_user = INTERNAL_TOOL_USER
            request.state.api_token = False
        else:
            request.state.current_user = "alice"
            request.state.api_token = False
        return await call_next(request)

    # Registered last, matching app.py: this boundary is outermost and rejects
    # raw Odysseus credentials before the inner auth/identity middleware.
    app.add_middleware(CodexCookbookBoundaryMiddleware)

    @app.post("/api/model/serve")
    async def model_serve_stub(request: Request, body: dict):
        side_effects.append("model-serve")
        return {"ok": True}

    @app.get("/api/model/cached")
    async def model_cached_stub(request: Request):
        side_effects.append("model-cached")
        return {"models": []}

    app.include_router(codex_routes.setup_codex_routes())
    return app


@pytest.fixture
def blocked_client(monkeypatch):
    side_effects: list[str] = []
    monkeypatch.setattr(codex_routes, "COOKBOOK_STATE_FILE", _PoisonPath())

    async def unexpected_process(*args, **kwargs):
        side_effects.append("process")
        raise AssertionError("process launch must not happen before the gate")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", unexpected_process)

    import core.atomic_io as atomic_io
    import routes.cookbook_helpers as cookbook_helpers

    def unexpected_write(*args, **kwargs):
        side_effects.append("state-write")
        raise AssertionError("state write must not happen before the gate")

    def unexpected_serve_request(*args, **kwargs):
        side_effects.append("serve-body")
        raise AssertionError("serve body must not be evaluated before the gate")

    monkeypatch.setattr(atomic_io, "atomic_write_json", unexpected_write)
    monkeypatch.setattr(cookbook_helpers, "ServeRequest", unexpected_serve_request)

    with TestClient(_build_app(side_effects)) as client:
        yield client, side_effects


@pytest.mark.parametrize("headers", BLOCKED_IDENTITIES)
@pytest.mark.parametrize("method,path,body", COOKBOOK_ROUTES)
def test_duplicate_cookbook_routes_fail_closed_before_side_effects(
    blocked_client,
    headers,
    method,
    path,
    body,
):
    client, side_effects = blocked_client
    response = client.request(method, path, headers=headers, json=body)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert side_effects == []


@pytest.mark.parametrize("headers", PRE_BODY_CREDENTIALS)
def test_gate_precedes_json_body_validation(blocked_client, headers):
    client, side_effects = blocked_client
    headers = {**headers, "content-type": "application/json"}

    response = client.post(
        "/api/codex/cookbook/serve",
        headers=headers,
        content=b"{not-json",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert side_effects == []


@pytest.mark.parametrize("raw_headers", DUPLICATE_PRE_BODY_HEADERS)
def test_all_duplicate_and_obs_text_credentials_precede_body_validation(
    blocked_client,
    raw_headers,
):
    client, side_effects = blocked_client

    response = client.post(
        "/api/codex/cookbook/serve",
        headers=[*raw_headers, (b"content-type", b"application/json")],
        content=b"{not-json",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert side_effects == []


@pytest.mark.parametrize(
    "value",
    [
        "Bearer ody_token",
        "bearer ody_token",
        "BEARER   ody_token",
        "BeArEr\tody_token",
        "  bearer \t  ody_token  ",
        "Basic placeholder, Bearer ody_token",
    ],
)
def test_odysseus_bearer_parser_accepts_scheme_case_and_sp_htab(value):
    assert is_odysseus_bearer_authorization(value) is True


@pytest.mark.parametrize(
    "value",
    [None, "", "ody_token", "Basic ody_token", "Bearer", "Bearer other"],
)
def test_odysseus_bearer_parser_rejects_other_credentials(value):
    assert is_odysseus_bearer_authorization(value) is False


def test_internal_header_match_preserves_an_exact_whitespace_token(monkeypatch):
    configured_token = "\t configured token \t"
    monkeypatch.setattr(middleware, "INTERNAL_TOOL_TOKEN", configured_token)

    assert middleware._internal_header_matches(configured_token) is True


def test_mounted_root_path_gate_precedes_json_validation(monkeypatch):
    side_effects: list[str] = []
    monkeypatch.setattr(codex_routes, "COOKBOOK_STATE_FILE", _PoisonPath())
    child = _build_app(side_effects)
    parent = FastAPI()
    parent.mount("/odysseus", child)

    with TestClient(parent) as client:
        response = client.post(
            "/odysseus/api/codex/cookbook/serve",
            headers={
                "authorization": "bEaReR   ody_legacy",
                "content-type": "application/json",
            },
            content=b"{not-json",
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert side_effects == []


def test_auth_disabled_shape_rejects_raw_bearer_before_body_parsing():
    """The boundary remains installed when app.AuthMiddleware is absent."""
    app = FastAPI()
    app.add_middleware(CodexCookbookBoundaryMiddleware)

    @app.post("/api/codex/cookbook/serve")
    async def unreachable(body: dict):
        return body

    with TestClient(app) as client:
        response = client.post(
            "/api/codex/cookbook/serve",
            headers={
                "authorization": "BEARER \t ody_legacy",
                "content-type": "application/json",
            },
            content=b"{not-json",
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_localhost_bypass_stack_still_runs_outer_boundary_first():
    app = FastAPI()

    class LocalhostBypassMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Matches app.AuthMiddleware's accepted loopback branch: continue
            # without stamping an authenticated cookie/API principal.
            return await call_next(request)

    app.add_middleware(LocalhostBypassMiddleware)
    app.add_middleware(CodexCookbookBoundaryMiddleware)

    @app.post("/api/codex/cookbook/serve")
    async def unreachable(body: dict):
        return body

    with TestClient(app) as client:
        response = client.post(
            "/api/codex/cookbook/serve",
            headers={
                "authorization": "bEaReR   ody_legacy",
                "content-type": "application/json",
            },
            content=b"{not-json",
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/codex/cookbook", True),
        ("/api/codex/cookbook/tasks", True),
        ("/api/codex/cookbookish", False),
        ("/api/codex/cookbooks/tasks", False),
        ("/api/cookbook/state", False),
        ("/api/model/serve", False),
    ],
)
def test_boundary_path_match_is_exact(path, expected):
    assert is_codex_cookbook_path(path) is expected


@pytest.mark.parametrize(
    "path",
    ["/api/cookbook/state", "/api/model/serve", "/api/codex/cookbookish"],
)
def test_boundary_does_not_intercept_canonical_or_neighbor_routes(path):
    app = FastAPI()
    app.add_middleware(CodexCookbookBoundaryMiddleware)

    @app.api_route(path, methods=["GET", "POST"])
    async def unaffected_route():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post(
            path,
            headers={"authorization": "BeArEr\tody_token"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_cors_preflight_reaches_cors_middleware():
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://client.example"],
        allow_methods=["POST"],
        allow_headers=["authorization", "content-type"],
    )
    app.add_middleware(CodexCookbookBoundaryMiddleware)

    @app.post("/api/codex/cookbook/serve")
    async def unused_route(body: dict):
        return body

    with TestClient(app) as client:
        response = client.options(
            "/api/codex/cookbook/serve",
            headers={
                "origin": "https://client.example",
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.example"


def test_cookie_admin_retains_duplicate_read_access(monkeypatch, tmp_path):
    state_path = tmp_path / "cookbook_state.json"
    state_path.write_text(
        json.dumps({"tasks": [{"sessionId": "serve-test", "status": "running"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_routes, "COOKBOOK_STATE_FILE", state_path)
    side_effects: list[str] = []

    with TestClient(_build_app(side_effects)) as client:
        response = client.get("/api/codex/cookbook/tasks")

    assert response.status_code == 200
    assert response.json() == {
        "tasks": [{"sessionId": "serve-test", "status": "running"}]
    }
    assert side_effects == []


def test_cookie_admin_retains_duplicate_serve_compatibility():
    side_effects: list[str] = []

    with TestClient(_build_app(side_effects)) as client:
        response = client.post(
            "/api/codex/cookbook/serve",
            json={"repo_id": "org/model", "cmd": "vllm serve org/model"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert side_effects == ["model-serve"]


def test_non_admin_cookie_session_is_still_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    request = SimpleNamespace(
        state=SimpleNamespace(current_user="bob", api_token=False),
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=SimpleNamespace(
                    is_configured=True,
                    is_admin=lambda username: False,
                )
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        codex_routes._require_cookbook_admin(request)

    assert exc_info.value.status_code == 403


def test_capabilities_hide_retired_cookbook_surface():
    side_effects: list[str] = []
    with TestClient(_build_app(side_effects)) as client:
        response = client.get(
            "/api/codex/capabilities",
            headers={"x-test-identity": "api-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "cookbook" not in payload["tools"]
    assert "cookbook" not in json.dumps(payload).lower()
    assert payload["tools"]["todos"]["read"] is False


def test_shipped_agent_surfaces_do_not_offer_cookbook_bearer_actions():
    root = Path(__file__).resolve().parents[1]
    helper_paths = [
        root / "integrations/codex/scripts/odysseus_api.py",
        root / "integrations/claude/skills/odysseus/scripts/odysseus_api.py",
    ]
    skill_paths = [
        root / "integrations/codex/skills/odysseus/SKILL.md",
        root / "integrations/claude/skills/odysseus/SKILL.md",
    ]

    for path in helper_paths:
        source = path.read_text(encoding="utf-8")
        assert 'command == "cookbook"' not in source
        assert "/api/codex/cookbook" not in source

    for path in skill_paths:
        source = path.read_text(encoding="utf-8").lower()
        assert "## cookbook serve" not in source
        assert "cookbook:read" not in source
        assert "cookbook:launch" not in source
        assert "cookbook/model deployment is intentionally operator-controlled" in source
        assert "/api/codex/cookbook/*" in source

    for relative in ("static/js/admin.js", "static/js/settings.js"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "cookbook:read" not in source
        assert "cookbook:launch" not in source


def test_real_app_places_pre_body_boundary_outside_auth():
    """Starlette's last-added middleware is outermost in the request stack."""
    app_source = (
        Path(__file__).resolve().parents[1] / "app.py"
    ).read_text(encoding="utf-8")
    boundary_registration = app_source.index(
        "app.add_middleware(CodexCookbookBoundaryMiddleware)"
    )
    auth_registration = app_source.index("app.add_middleware(AuthMiddleware)")

    # Registering the boundary later makes it outermost, so raw Odysseus bearer
    # and internal credentials are rejected before token-cache/last-used work.
    # Handler/dependency gates remain the backstop for stamped identities.
    assert boundary_registration > auth_registration


def test_every_duplicate_handler_starts_with_the_direct_call_backstop():
    source = (
        Path(__file__).resolve().parents[1] / "routes/codex_routes.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    expected = {
        "codex_cookbook_tasks",
        "codex_cookbook_servers",
        "codex_cookbook_output",
        "codex_cookbook_serve",
        "codex_cookbook_stop",
        "codex_cookbook_cached",
        "codex_cookbook_presets",
        "codex_cookbook_serve_preset",
        "codex_cookbook_adopt",
    }
    found = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name not in expected:
            continue
        found.add(node.name)
        statements = list(node.body)
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
        ):
            statements = statements[1:]
        first = statements[0]
        assert isinstance(first, ast.Expr), node.name
        assert isinstance(first.value, ast.Call), node.name
        assert isinstance(first.value.func, ast.Name), node.name
        assert first.value.func.id == "_require_cookbook_admin", node.name

    assert found == expected
