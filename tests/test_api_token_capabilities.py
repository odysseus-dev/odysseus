from pathlib import Path

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
        ("GET", "/api/codex/cookbook/tasks", "cookbook:read", "chat"),
        ("GET", "/api/codex/cookbook/servers", "cookbook:launch", "chat"),
        (
            "GET",
            "/api/codex/cookbook/output/serve-1",
            "cookbook:read",
            "chat",
        ),
        ("GET", "/api/codex/cookbook/cached", "cookbook:read", "chat"),
        ("GET", "/api/codex/cookbook/presets", "cookbook:read", "chat"),
        ("POST", "/api/codex/cookbook/serve", "cookbook:launch", "chat"),
        (
            "POST",
            "/api/codex/cookbook/stop/serve-1",
            "cookbook:launch",
            "cookbook:read",
        ),
        (
            "POST",
            "/api/codex/cookbook/preset/default",
            "cookbook:launch",
            "cookbook:read",
        ),
        ("POST", "/api/codex/cookbook/adopt", "cookbook:launch", "chat"),
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


def test_manifest_contains_only_the_current_audited_bearer_routes():
    expected = {
        ("POST", "/api/v1/chat"),
        ("GET", "/api/models"),
        ("GET", "/api/companion/ping"),
        ("GET", "/api/companion/info"),
        ("GET", "/api/companion/models"),
        ("GET", "/api/codex/capabilities"),
        ("GET", "/api/codex/plugin.zip"),
        ("GET", "/api/claude/plugin.zip"),
        ("GET", "/api/codex/todos"),
        ("POST", "/api/codex/todos"),
        ("GET", "/api/codex/emails"),
        ("GET", "/api/codex/emails/{uid}"),
        ("POST", "/api/codex/emails/draft-document"),
        ("POST", "/api/codex/emails/draft"),
        ("POST", "/api/codex/emails/send"),
        ("GET", "/api/codex/memory"),
        ("POST", "/api/codex/memory"),
        ("DELETE", "/api/codex/memory/{memory_id}"),
        ("GET", "/api/codex/calendar/events"),
        ("POST", "/api/codex/calendar/events"),
        ("DELETE", "/api/codex/calendar/events/{uid}"),
        ("GET", "/api/codex/documents"),
        ("GET", "/api/codex/documents/{doc_id}"),
        ("POST", "/api/codex/documents"),
        ("DELETE", "/api/codex/documents/{doc_id}"),
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
        "cookbook:read",
        "cookbook:launch",
    }


def test_token_minting_and_route_checks_share_the_scope_catalog():
    from routes.api_token_routes import ALLOWED_SCOPES
    import routes.codex_routes as codex_routes

    assert ALLOWED_SCOPES is ALL_API_TOKEN_SCOPES
    assert codex_routes.TODO_READ_SCOPES <= ALL_API_TOKEN_SCOPES
    assert codex_routes.EMAIL_READ_SCOPES <= ALL_API_TOKEN_SCOPES
    assert codex_routes.COOKBOOK_READ_SCOPES <= ALL_API_TOKEN_SCOPES


def test_capability_gate_runs_only_after_a_valid_bearer_match():
    source = Path("app.py").read_text(encoding="utf-8")
    cors_gate = source.index("if is_cors_preflight(")
    exempt_gate = source.index("if _is_auth_exempt(path):")
    internal_gate = source.index("# In-process internal-tool token bypass")
    local_gate = source.index("# Allow DIRECT localhost requests")
    bearer_gate = source.index('if auth_header.startswith("Bearer ody_"):')
    token_match = source.index("if matched_id:", bearer_gate)
    capability_gate = source.index("authorize_api_token_request(", token_match)
    token_state = source.index("request.state.api_token = True", capability_gate)
    cookie_gate = source.index("# --- Cookie-based session auth ---", token_state)

    assert (
        cors_gate
        < exempt_gate
        < internal_gate
        < local_gate
        < bearer_gate
        < token_match
        < capability_gate
        < token_state
        < cookie_gate
    )
    local_block = source[local_gate:bearer_gate]
    assert 'not auth_header.startswith("Bearer ody_")' in local_block
