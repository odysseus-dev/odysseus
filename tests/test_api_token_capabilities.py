import pytest

from src.api_token_capabilities import (
    ALL_API_TOKEN_SCOPES,
    API_TOKEN_FORBIDDEN_ERROR,
    API_TOKEN_ROUTE_CAPABILITIES,
    authorize_api_token_request,
    authorize_api_token_route,
    find_api_token_route_capability,
)


def _allowed(method, path, scopes):
    return authorize_api_token_route(method, path, scopes).allowed


def test_retained_public_chat_and_model_inventory_require_chat_scope():
    for method, path in [
        ("POST", "/api/v1/chat"),
        ("GET", "/api/models"),
    ]:
        assert _allowed(method, path, ["chat"]) is True
        assert _allowed(method, path, ["documents:read"]) is False
        assert _allowed(method, path, []) is False


def test_companion_bearer_reads_all_require_chat_scope():
    for path in [
        "/api/companion/ping",
        "/api/companion/info",
        "/api/companion/models",
    ]:
        assert _allowed("GET", path, ["chat"]) is True
        assert _allowed("GET", path, ["todos:read"]) is False
        assert _allowed("GET", path, []) is False


@pytest.mark.parametrize(
    ("method", "path", "accepted_scope", "rejected_scope"),
    [
        ("GET", "/api/codex/todos", "todos:read", "email:read"),
        ("POST", "/api/codex/todos", "todos:write", "email:read"),
        ("GET", "/api/codex/emails", "email:read", "todos:read"),
        ("GET", "/api/codex/emails/abc123", "email:send", "chat"),
        ("POST", "/api/codex/emails/draft", "email:draft", "email:read"),
        ("POST", "/api/codex/emails/send", "email:send", "email:draft"),
        ("GET", "/api/codex/memory", "memory:read", "calendar:read"),
        ("POST", "/api/codex/memory", "memory:write", "memory:read"),
        ("DELETE", "/api/codex/memory/mem-1", "memory:write", "memory:read"),
        ("GET", "/api/codex/calendar/events", "calendar:read", "memory:read"),
        ("POST", "/api/codex/calendar/events", "calendar:write", "calendar:read"),
        (
            "DELETE",
            "/api/codex/calendar/events/event-1",
            "calendar:write",
            "calendar:read",
        ),
        ("GET", "/api/codex/documents", "documents:read", "todos:read"),
        ("GET", "/api/codex/documents/doc-1", "documents:write", "chat"),
        ("POST", "/api/codex/documents", "documents:write", "documents:read"),
        (
            "DELETE",
            "/api/codex/documents/doc-1",
            "documents:write",
            "documents:read",
        ),
    ],
)
def test_codex_route_families_require_their_existing_scopes(
    method,
    path,
    accepted_scope,
    rejected_scope,
):
    assert _allowed(method, path, [accepted_scope]) is True
    assert _allowed(method, path, [rejected_scope]) is False


def test_email_draft_document_requires_email_draft_and_document_write():
    path = "/api/codex/emails/draft-document"

    assert _allowed("POST", path, ["email:draft", "documents:write"]) is True
    assert _allowed("POST", path, ["email:send", "documents:write"]) is True
    assert _allowed("POST", path, ["email:draft"]) is False
    assert _allowed("POST", path, ["documents:write"]) is False
    assert _allowed("POST", path, ["email:read", "documents:write"]) is False


def test_bootstrap_downloads_require_at_least_one_accepted_scope():
    for path in [
        "/api/codex/capabilities",
        "/api/codex/plugin.zip",
        "/api/claude/plugin.zip",
    ]:
        for scope in ALL_API_TOKEN_SCOPES:
            assert _allowed("GET", path, [scope]) is True
        assert _allowed("GET", path, []) is False
        assert _allowed("GET", path, ["unknown:scope"]) is False


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/tokens"),
        ("POST", "/api/tokens"),
        ("GET", "/api/tokens/profiles"),
        ("PATCH", "/api/tokens/token-1"),
        ("GET", "/api/companion/pair"),
        ("POST", "/api/companion/pair"),
        ("POST", "/api/shell/exec"),
        ("POST", "/api/shell/stream"),
        ("GET", "/api/workspace/browse"),
        ("GET", "/api/tools"),
        ("POST", "/api/tools"),
        ("GET", "/api/users"),
        ("GET", "/api/sessions"),
        ("GET", "/api/history/session-1"),
        ("POST", "/api/upload"),
        ("POST", "/api/chat_stream"),
        ("GET", "/api/calendar/events"),
        ("GET", "/api/codex/todos/export"),
    ],
)
def test_privileged_and_owner_attributing_ui_routes_remain_blocked(method, path):
    decision = authorize_api_token_route(method, path, ALL_API_TOKEN_SCOPES)

    assert decision.allowed is False
    assert decision.error == API_TOKEN_FORBIDDEN_ERROR


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/codex/cookbook/tasks"),
        ("GET", "/api/codex/cookbook/servers"),
        ("GET", "/api/codex/cookbook/output/serve-1"),
        ("GET", "/api/codex/cookbook/cached"),
        ("GET", "/api/codex/cookbook/presets"),
        ("POST", "/api/codex/cookbook/serve"),
        ("POST", "/api/codex/cookbook/stop/serve-1"),
        ("POST", "/api/codex/cookbook/preset/default"),
        ("POST", "/api/codex/cookbook/adopt"),
    ],
)
def test_legacy_cookbook_scopes_are_inert_at_the_bearer_boundary(method, path):
    decision = authorize_api_token_route(
        method,
        path,
        ["cookbook:read", "cookbook:launch"],
    )

    assert decision.allowed is False
    assert decision.error == API_TOKEN_FORBIDDEN_ERROR


def test_method_matching_is_exact_and_case_insensitive():
    assert _allowed("post", "/api/v1/chat", ["chat"]) is True
    assert _allowed("GET", "/api/v1/chat", ["chat"]) is False
    assert _allowed("POST", "/api/models", ["chat"]) is False
    assert _allowed("OPTIONS", "/api/models", ["chat"]) is False


def test_single_trailing_slash_matches_but_malformed_paths_fail_closed():
    assert _allowed("GET", "/api/models/", ["chat"]) is True
    for path in [
        "/api/models//",
        "//api/models",
        "/api//models",
        "/api/./models",
        "/api/../models",
        "/api/models?refresh=true",
        "/api/models#fragment",
        "/api/models\\extra",
        "/api/models\x00",
        "api/models",
        "",
    ]:
        assert _allowed("GET", path, ["chat"]) is False


def test_path_templates_match_one_nonempty_segment_only():
    assert find_api_token_route_capability(
        "GET",
        "/api/codex/emails/abc123",
    ) is not None
    assert find_api_token_route_capability(
        "DELETE",
        "/api/codex/calendar/events/event-1",
    ) is not None
    assert find_api_token_route_capability(
        "GET",
        "/api/codex/emails/abc123/extra",
    ) is None
    assert find_api_token_route_capability("GET", "/api/codex/emails//") is None


def test_asgi_root_path_is_removed_before_matching():
    decision = authorize_api_token_request(
        "GET",
        {
            "root_path": "/odysseus",
            "path": "/odysseus/api/models",
            "raw_path": b"/odysseus/api/models",
        },
        ["chat"],
    )

    assert decision.allowed is True

    wrong_prefix = authorize_api_token_request(
        "GET",
        {
            "root_path": "/odysseus",
            "path": "/odyssey/api/models",
            "raw_path": b"/odyssey/api/models",
        },
        ["chat"],
    )
    assert wrong_prefix.allowed is False


@pytest.mark.parametrize(
    ("root_path", "path", "raw_path"),
    [
        ("/odysseus/", "/odysseus//api/models", b"/odysseus//api/models"),
        ("/", "//api/models", b"//api/models"),
    ],
)
def test_asgi_root_path_with_trailing_slash_is_removed_before_matching(
    root_path,
    path,
    raw_path,
):
    decision = authorize_api_token_request(
        "GET",
        {
            "root_path": root_path,
            "path": path,
            "raw_path": raw_path,
        },
        ["chat"],
    )

    assert decision.allowed is True


@pytest.mark.parametrize("encoded", [b"%2f", b"%2F", b"%5c", b"%5C", b"%00"])
def test_encoded_path_delimiters_fail_closed(encoded):
    decision = authorize_api_token_request(
        "GET",
        {
            "path": "/api/models",
            "raw_path": b"/api" + encoded + b"models",
        },
        ["chat"],
    )

    assert decision.allowed is False


@pytest.mark.parametrize(
    ("decoded", "encoded"),
    [("?", b"%3F"), ("#", b"%23")],
)
def test_encoded_calendar_uid_characters_follow_the_decoded_router_path(
    decoded,
    encoded,
):
    decision = authorize_api_token_request(
        "DELETE",
        {
            "path": f"/api/codex/calendar/events/team{decoded}primary",
            "raw_path": b"/api/codex/calendar/events/team" + encoded + b"primary",
        },
        ["calendar:write"],
    )

    assert decision.allowed is True


def test_encoded_static_letters_follow_the_decoded_router_path():
    decision = authorize_api_token_request(
        "GET",
        {
            "path": "/api/models",
            "raw_path": b"/api/%6dodels",
        },
        ["chat"],
    )

    assert decision.allowed is True


def test_missing_scope_and_unknown_route_share_one_public_error():
    wrong_scope = authorize_api_token_route("GET", "/api/models", ["todos:read"])
    unknown_route = authorize_api_token_route(
        "GET",
        "/api/private-owner-data",
        ["chat"],
    )

    assert wrong_scope == unknown_route
    assert wrong_scope.error == API_TOKEN_FORBIDDEN_ERROR


def test_scope_string_normalization_is_not_character_based():
    assert _allowed("GET", "/api/models", "todos:read, chat") is True
    assert _allowed("GET", "/api/models", "c,h,a,t") is False


def test_every_manifest_entry_has_known_nonempty_scopes_and_unique_methods():
    seen = set()
    for capability in API_TOKEN_ROUTE_CAPABILITIES:
        assert capability.scope_options
        for option in capability.scope_options:
            assert option
            assert option <= ALL_API_TOKEN_SCOPES
        for method in capability.methods:
            key = (method, capability.path)
            assert key not in seen
            seen.add(key)


def _router_inventory(router):
    return {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }


def test_manifest_matches_runtime_scoped_router_inventory():
    from companion.routes import setup_companion_routes
    from routes.codex_routes import setup_claude_routes, setup_codex_routes

    companion_inventory = _router_inventory(setup_companion_routes())
    companion_pairing = {
        route
        for route in companion_inventory
        if route[1] == "/api/companion/pair"
    }
    assert companion_pairing == {
        ("GET", "/api/companion/pair"),
        ("POST", "/api/companion/pair"),
    }

    expected = {
        ("POST", "/api/v1/chat"),
        ("GET", "/api/models"),
    }
    codex_inventory = _router_inventory(setup_codex_routes())
    cookbook_inventory = {
        route
        for route in codex_inventory
        if route[1].startswith("/api/codex/cookbook/")
    }
    assert cookbook_inventory == {
        ("GET", "/api/codex/cookbook/tasks"),
        ("GET", "/api/codex/cookbook/servers"),
        ("GET", "/api/codex/cookbook/output/{session_id}"),
        ("GET", "/api/codex/cookbook/cached"),
        ("GET", "/api/codex/cookbook/presets"),
        ("POST", "/api/codex/cookbook/serve"),
        ("POST", "/api/codex/cookbook/stop/{session_id}"),
        ("POST", "/api/codex/cookbook/preset/{name}"),
        ("POST", "/api/codex/cookbook/adopt"),
    }
    expected.update(codex_inventory - cookbook_inventory)
    expected.update(_router_inventory(setup_claude_routes()))
    expected.update(companion_inventory - companion_pairing)
    actual = {
        (method, capability.path)
        for capability in API_TOKEN_ROUTE_CAPABILITIES
        for method in capability.methods
    }

    assert actual == expected


def test_accepted_scope_catalog_is_explicit_and_has_no_admin_scope():
    assert ALL_API_TOKEN_SCOPES == {
        "chat",
        "todos:read",
        "todos:write",
        "documents:read",
        "documents:write",
        "email:read",
        "email:draft",
        "email:send",
        "calendar:read",
        "calendar:write",
        "memory:read",
        "memory:write",
    }


def test_token_minting_and_route_checks_share_the_scope_catalog():
    from routes.api_token_routes import ALLOWED_SCOPES
    import routes.codex_routes as codex_routes

    assert ALLOWED_SCOPES is ALL_API_TOKEN_SCOPES
    assert codex_routes.TODO_READ_SCOPES <= ALL_API_TOKEN_SCOPES
    assert codex_routes.EMAIL_READ_SCOPES <= ALL_API_TOKEN_SCOPES
    assert not hasattr(codex_routes, "COOKBOOK_READ_SCOPES")
    assert not hasattr(codex_routes, "COOKBOOK_LAUNCH_SCOPES")


def test_bundled_integrations_do_not_advertise_retired_cookbook_access():
    from pathlib import Path

    files = [
        Path("static/js/admin.js"),
        Path("static/js/settings.js"),
        Path("integrations/codex/skills/odysseus/SKILL.md"),
        Path("integrations/claude/skills/odysseus/SKILL.md"),
        Path("integrations/codex/scripts/odysseus_api.py"),
        Path("integrations/claude/skills/odysseus/scripts/odysseus_api.py"),
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert "cookbook:read" not in source, path
        assert "cookbook:launch" not in source, path

    for path in files[-2:]:
        source = path.read_text(encoding="utf-8")
        assert 'command == "cookbook"' not in source, path
        assert "/api/codex/cookbook/" not in source, path

    admin_ui = files[0].read_text(encoding="utf-8")
    settings_ui = files[1].read_text(encoding="utf-8")
    for source in (admin_ui, settings_ui):
        assert "retired_scopes" in source
        assert "are inactive and will be removed" in source
    assert "no active scopes; retired:" in settings_ui
