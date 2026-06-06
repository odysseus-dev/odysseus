from src.api_token_capabilities import (
    authorize_api_token_route,
    find_api_token_route_capability,
)


def test_api_token_route_capability_allows_declared_scope():
    decision = authorize_api_token_route("POST", "/api/v1/chat", ["chat"])

    assert decision.allowed is True
    assert decision.error is None


def test_api_token_route_capability_rejects_missing_scope():
    decision = authorize_api_token_route("POST", "/api/v1/chat", ["documents:read"])

    assert decision.allowed is False
    assert decision.error == "API token missing required scope: chat"
    assert decision.required_scopes == ("chat",)


def test_api_token_route_capability_rejects_unregistered_routes():
    for method, path in [
        ("GET", "/api/tokens"),
        ("POST", "/api/tokens"),
        ("GET", "/api/companion/pair"),
        ("POST", "/api/companion/pair"),
        ("GET", "/api/codex/todos/export"),
    ]:
        decision = authorize_api_token_route(method, path, ["chat", "todos:write"])
        assert decision.allowed is False
        assert decision.error == "API token is not allowed for this endpoint"


def test_api_token_route_capability_allows_valid_token_only_bootstrap_routes():
    for method, path in [
        ("GET", "/api/companion/ping"),
        ("GET", "/api/companion/info"),
        ("GET", "/api/companion/models"),
        ("GET", "/api/codex/capabilities"),
        ("GET", "/api/codex/plugin.zip"),
        ("GET", "/api/claude/plugin.zip"),
    ]:
        decision = authorize_api_token_route(method, path, [])
        assert decision.allowed is True


def test_api_token_route_capability_matches_path_templates():
    assert (
        find_api_token_route_capability("GET", "/api/codex/emails/abc123")
        is not None
    )
    assert (
        find_api_token_route_capability(
            "DELETE",
            "/api/codex/calendar/events/event-1",
        )
        is not None
    )
    assert (
        find_api_token_route_capability("GET", "/api/codex/emails/abc123/extra")
        is None
    )


def test_api_token_route_capability_preserves_codex_fine_grained_checks():
    assert (
        authorize_api_token_route("GET", "/api/codex/todos", ["todos:write"]).allowed
        is True
    )
    assert (
        authorize_api_token_route("POST", "/api/codex/todos", ["todos:read"]).allowed
        is True
    )
    assert (
        authorize_api_token_route(
            "POST",
            "/api/codex/emails/send",
            ["email:draft"],
        ).allowed
        is False
    )
    assert (
        authorize_api_token_route(
            "POST",
            "/api/codex/emails/send",
            ["email:send"],
        ).allowed
        is True
    )
    assert (
        authorize_api_token_route(
            "DELETE",
            "/api/codex/documents/doc-1",
            ["documents:read"],
        ).allowed
        is False
    )
    assert (
        authorize_api_token_route(
            "DELETE",
            "/api/codex/documents/doc-1",
            ["documents:write"],
        ).allowed
        is True
    )
