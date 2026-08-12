import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.agent_run_policy import (
    AgentRunMode,
    AgentRunPolicy,
    AuthorizationOutcome,
    ExecutionProfile,
    parse_agent_run_mode,
)
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import (
    ToolRunSecurityContext,
    ToolEffect,
    capabilities_for_action,
    capabilities_for_tool,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def enabled_agent_action_gate(monkeypatch):
    import src.tool_capabilities as tool_capabilities

    monkeypatch.setattr(
        tool_capabilities,
        "AGENT_ACTION_APPROVAL_GATE_ENABLED",
        True,
    )


def test_invalid_run_mode_fails_safe_to_sandbox():
    assert parse_agent_run_mode("made-up") is AgentRunMode.SANDBOX
    assert AgentRunPolicy.for_mode(None).mode is AgentRunMode.SANDBOX


def test_ask_requires_exact_approval_for_code_but_not_public_read(
    enabled_agent_action_gate,
):
    policy = AgentRunPolicy.for_mode("ask")
    context = ToolRunSecurityContext()

    assert policy.authorize("web_search", context).outcome is AuthorizationOutcome.ALLOW_SANDBOXED
    assert policy.authorize("bash", context).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def test_sandbox_allows_code_before_external_context_then_requires_approval(
    enabled_agent_action_gate,
):
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext()

    assert policy.authorize("bash", context).outcome is AuthorizationOutcome.ALLOW_SANDBOXED
    context.external_untrusted_context_seen = True
    assert policy.authorize("bash", context).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def test_sandbox_requires_approval_for_external_side_effects(
    enabled_agent_action_gate,
):
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext()

    assert policy.authorize("send_email", context).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def test_unknown_tool_requires_approval_outside_full_access(
    enabled_agent_action_gate,
):
    context = ToolRunSecurityContext()

    assert AgentRunPolicy.for_mode("sandbox").authorize(
        "mcp__unknown__surprise", context
    ).outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert AgentRunPolicy.for_mode("full_access").authorize(
        "mcp__unknown__surprise", context
    ).outcome is AuthorizationOutcome.ALLOW_HOST


def test_full_access_selects_host_execution_profile():
    policy = AgentRunPolicy.for_mode("full_access")

    assert policy.execution_profile is ExecutionProfile.HOST_FULL_ACCESS
    assert policy.authorize(
        "bash", ToolRunSecurityContext(external_untrusted_context_seen=True)
    ).outcome is AuthorizationOutcome.ALLOW_HOST


def test_disabled_legacy_gate_keeps_run_mode_policy_active():
    context = ToolRunSecurityContext(external_untrusted_context_seen=True)

    assert AgentRunPolicy.for_mode("ask").authorize(
        "bash", context
    ).outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert AgentRunPolicy.for_mode("sandbox").authorize(
        "send_email", context
    ).outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert AgentRunPolicy.for_mode("sandbox").authorize(
        "mcp__unknown__surprise", context
    ).outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    sandbox_policy = AgentRunPolicy.for_mode("sandbox")
    for tool_name in ("bash", "manage_memory", "manage_skills"):
        assert (
            sandbox_policy.authorize(tool_name, context).outcome
            is AuthorizationOutcome.ALLOW_SANDBOXED
        )


def test_sandbox_allows_brokered_reads_without_approving_arbitrary_egress():
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext(external_untrusted_context_seen=True)

    assert policy.authorize("web_fetch", context).outcome is AuthorizationOutcome.ALLOW_SANDBOXED
    assert policy.authorize("pipeline", context).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def _pending(store, **overrides):
    values = {
        "owner": "Alice",
        "session_id": "session-1",
        "origin_run_id": "run-1",
        "tool_name": "bash",
        "content": "printf exact",
        "workspace": "/tmp/workspace",
        "security_mode": "ask",
        "security_context": ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        "capabilities": capabilities_for_tool("bash"),
    }
    values.update(overrides)
    return store.create(**values)


def test_private_read_is_classified_from_exact_action_and_requires_approval():
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext()
    read = '{"action":"list"}'
    write = '{"action":"create","name":"daily"}'

    assert capabilities_for_action("manage_tasks", read).effects == {
        ToolEffect.READ_PRIVATE
    }
    assert policy.authorize(
        "manage_tasks", context, read
    ).outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert policy.authorize(
        "manage_tasks", context, write
    ).outcome is AuthorizationOutcome.ALLOW_SANDBOXED
    assert capabilities_for_action(
        "manage_tasks", '{"action":"invented"}'
    ).effects == {
        ToolEffect.READ_PRIVATE,
        ToolEffect.WRITE_PRIVATE,
    }


def test_private_context_requires_exact_approval_before_brokered_egress():
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext(private_data_context_seen=True)

    decision = policy.authorize("web_search", context, "private-derived query")

    assert decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert "private" in (decision.reason or "").lower()


def test_workspace_context_requires_exact_approval_before_brokered_egress():
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext(workspace_untrusted_context_seen=True)

    decision = policy.authorize("web_search", context, "source-derived query")

    assert decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert "workspace" in (decision.reason or "").lower()


def test_workspace_and_odysseus_untrusted_context_gate_high_impact_actions():
    policy = AgentRunPolicy.for_mode("sandbox")

    for context in (
        ToolRunSecurityContext(workspace_untrusted_context_seen=True),
        ToolRunSecurityContext(odysseus_untrusted_context_seen=True),
    ):
        assert policy.authorize(
            "bash", context, "printf risky"
        ).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def test_approval_digest_binds_complete_provenance_snapshot():
    store = ToolApprovalStore()
    context = ToolRunSecurityContext(
        workspace_untrusted_context_seen=True,
        private_data_context_seen=True,
    )
    pending = _pending(store, security_context=context)

    assert pending.provenance == ("workspace_untrusted", "private_data")


def test_approval_is_bound_to_exact_action_and_claimed_once():
    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    assert grant is not None
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf modified",
        workspace="/tmp/workspace",
        security_mode="ask",
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
    )
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace="/tmp/workspace",
        security_mode="full_access",
    )
    assert grant.claim(
        owner="ALICE",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace="/tmp/workspace",
        security_mode="ask",
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
    )
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace="/tmp/workspace",
        security_mode="ask",
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
    )


def test_approval_rejects_changed_current_provenance():
    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    assert grant is not None
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace="/tmp/workspace",
        security_mode="ask",
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True,
            private_data_context_seen=True,
        ),
    )


def test_approval_wrong_owner_cannot_consume_grant():
    store = ToolApprovalStore()
    pending = _pending(store)

    assert store.consume(
        pending.approval_id,
        decision="approve",
        owner="mallory",
        session_id="session-1",
    ) is None
    assert store.peek(pending.approval_id) is pending


def test_deny_destructively_consumes_pending_action():
    store = ToolApprovalStore()
    pending = _pending(store)

    assert store.consume(
        pending.approval_id,
        decision="deny",
        owner="alice",
        session_id="session-1",
    ) is None
    assert store.peek(pending.approval_id) is None


def test_expired_approval_cannot_be_consumed(monkeypatch):
    store = ToolApprovalStore(ttl_seconds=1)
    pending = _pending(store)
    monkeypatch.setattr(time, "time", lambda: pending.expires_at + 1)

    assert store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    ) is None


def test_public_approval_payload_shows_the_complete_exact_action():
    store = ToolApprovalStore()
    pending = _pending(store, content="printf safe\nSECRET_SECOND_LINE")

    payload = pending.public_payload()
    encoded = str(payload)
    assert payload["kind"] == "tool_approval"
    assert payload["action"]["content"] == "printf safe\nSECRET_SECOND_LINE"
    assert "SECRET_SECOND_LINE" in encoded


@pytest.mark.asyncio
async def test_dispatcher_claims_exact_approval_immediately_before_execution(
    monkeypatch,
    tmp_path,
):
    import src.tool_execution as tool_execution

    ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
    store = ToolApprovalStore()
    pending = _pending(store, workspace=str(tmp_path))
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )
    calls = []

    async def fake_implementation(block, **kwargs):
        calls.append((block.tool_type, block.content, kwargs["execution_profile"]))
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        fake_implementation,
    )
    desc, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf exact"),
        session_id="session-1",
        owner="alice",
        workspace=str(tmp_path),
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        run_policy=AgentRunPolicy.for_mode("ask"),
        exact_approval=grant,
    )

    assert desc == "bash"
    assert result["exit_code"] == 0
    assert calls == [
        ("bash", "printf exact", ExecutionProfile.WORKSPACE_SANDBOX)
    ]


@pytest.mark.asyncio
async def test_dispatcher_rejects_modified_action_without_execution(monkeypatch):
    import src.tool_execution as tool_execution

    ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("modified approved action reached implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf changed"),
        session_id="session-1",
        owner="alice",
        workspace="/tmp/workspace",
        security_context=ToolRunSecurityContext(),
        run_policy=AgentRunPolicy.for_mode("ask"),
        exact_approval=grant,
    )

    assert result["blocked"] is True
    assert result["policy"] == "exact_tool_approval"


@pytest.mark.asyncio
async def test_dispatcher_rejects_full_access_for_non_admin(monkeypatch):
    import src.tool_execution as tool_execution

    ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
    monkeypatch.setattr(
        tool_execution,
        "owner_is_admin_or_single_user",
        lambda owner: False,
    )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("non-admin host action reached implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf host"),
        session_id="session-1",
        owner="ordinary-user",
        workspace="/tmp/workspace",
        security_context=ToolRunSecurityContext(),
        run_policy=AgentRunPolicy.for_mode("full_access"),
    )

    assert result["blocked"] is True
    assert "admin" in result["error"].lower()


def test_frontend_exposes_sandbox_and_full_access_without_ask_mode():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")

    assert '<option value="sandbox" selected>Sandbox</option>' in index
    assert '<option value="full_access">Full access</option>' in index
    assert '<option value="ask">' not in index
    assert "fd.append('security_mode', securityMode)" in chat


class _ChatStreamRequest:
    def __init__(self, *, user: str, security_mode: str):
        self.user = user
        self.headers = {"content-type": "application/json"}
        self.cookies = {}
        self.app = SimpleNamespace(
            state=SimpleNamespace(auth_manager=None),
        )
        self._body = {
            "message": "exercise the route policy",
            "session": "session-1",
            "mode": "agent",
            "security_mode": security_mode,
        }

    async def json(self):
        return self._body

    async def form(self):
        return {}


def _chat_stream_endpoint(router):
    for route in router.routes:
        if route.path == "/api/chat_stream" and "POST" in route.methods:
            return route.endpoint
    raise AssertionError("POST /api/chat_stream route not registered")


@pytest.fixture
def chat_stream_security_route(monkeypatch):
    import routes.chat_routes as chat_routes

    session = SimpleNamespace(
        id="session-1",
        owner="admin",
        model="test-model",
        endpoint_url="http://model.invalid/v1",
        headers={},
        name="test-session",
        history=[],
        security_mode="sandbox",
    )
    stored_modes = []
    stream_kwargs = {}
    started_streams = {}

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class _Db:
        def query(self, *_args, **_kwargs):
            return _Query()

        def close(self):
            return None

    class _ToolPolicy:
        block_all_tool_calls = False

        def blocks(self, *_args, **_kwargs):
            return False

        def all_disabled_names(self):
            return set()

    session_manager = SimpleNamespace(
        get_session=lambda session_id: session if session_id == session.id else None,
        save_sessions=lambda: None,
    )
    context = SimpleNamespace(
        user="admin",
        messages=[{"role": "user", "content": "exercise the route policy"}],
        web_sources=[],
        rag_sources=[],
        used_memories=[],
        uprefs={},
        uploaded_files=[],
        preprocessed=SimpleNamespace(attachment_meta=[]),
        auto_opened_docs=[],
        preset=SimpleNamespace(character_name="", temperature=0, max_tokens=1),
        context_length=0,
        was_compacted=False,
        context_trimmed=False,
        preface=[],
    )

    monkeypatch.setattr(chat_routes, "_set_user_time_from_request", lambda _request: None)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda request: request.user)
    monkeypatch.setattr(
        chat_routes,
        "owner_is_admin_or_single_user",
        lambda owner: owner == "admin",
    )
    monkeypatch.setattr(
        chat_routes,
        "get_session_security_mode",
        lambda _session_id: session.security_mode,
    )

    def _set_session_security_mode(_session_id, mode):
        stored_modes.append(mode)
        session.security_mode = mode
        return True

    monkeypatch.setattr(chat_routes, "set_session_security_mode", _set_session_security_mode)
    monkeypatch.setattr(chat_routes, "set_session_mode", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda _session_id: "chat")
    monkeypatch.setattr(chat_routes.tool_approval_store, "retire_for_session", lambda **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_reconcile_selected_route_from_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_is_contextual_web_followup", lambda *_args: False)
    monkeypatch.setattr(chat_routes, "_is_contextual_browser_followup", lambda *_args: False)
    monkeypatch.setattr(chat_routes, "_resolve_workspace_from_message_path", lambda *_args: ("", ""))
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_classify_tool_intent", lambda _message: None)
    monkeypatch.setattr(chat_routes, "web_search_enabled_for_turn", lambda *_args: False)
    monkeypatch.setattr(chat_routes, "build_effective_tool_policy", lambda **_kwargs: _ToolPolicy())
    monkeypatch.setattr(
        chat_routes,
        "resolve_foreground_model_policy",
        lambda **_kwargs: SimpleNamespace(
            enabled=False,
            eligible_statuses=(),
            fallback_on_empty=False,
        ),
    )
    monkeypatch.setattr(chat_routes, "_allowed_models_for_request", lambda _request: [])
    async def _build_chat_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(chat_routes, "build_chat_context", _build_chat_context)
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(chat_routes, "_owner_session_filter", lambda query, _owner: query)
    monkeypatch.setattr(chat_routes, "build_foreground_model_candidates", lambda *_args, **_kwargs: ["candidate"])
    monkeypatch.setattr(
        chat_routes,
        "build_foreground_route_descriptors",
        lambda *_args, **_kwargs: [{"endpoint_id": "test-endpoint", "endpoint_label": "Test"}],
    )
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda _key, default=None: default,
    )

    async def _fake_stream_agent_loop(*_args, **kwargs):
        stream_kwargs.update(kwargs)
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", _fake_stream_agent_loop)

    def _start(_session_id, stream):
        started_streams["stream"] = stream
        return SimpleNamespace(run_id="route-test-run")

    async def _empty_subscription():
        if False:
            yield ""

    monkeypatch.setattr(chat_routes.agent_runs, "start", _start)
    monkeypatch.setattr(
        chat_routes.agent_runs,
        "subscribe",
        lambda *_args: _empty_subscription(),
    )

    router = chat_routes.setup_chat_routes(
        session_manager,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    return SimpleNamespace(
        endpoint=_chat_stream_endpoint(router),
        request=lambda user, mode: _ChatStreamRequest(
            user=user,
            security_mode=mode,
        ),
        stored_modes=stored_modes,
        stream_kwargs=stream_kwargs,
        started_streams=started_streams,
    )


@pytest.mark.asyncio
async def test_chat_stream_rejects_invalid_security_mode_before_persisting(
    chat_stream_security_route,
):
    with pytest.raises(HTTPException) as exc_info:
        await chat_stream_security_route.endpoint(
            chat_stream_security_route.request("admin", "not-a-mode")
        )

    assert exc_info.value.status_code == 400
    assert chat_stream_security_route.stored_modes == []


@pytest.mark.asyncio
async def test_chat_stream_rejects_non_admin_full_access_before_persisting(
    chat_stream_security_route,
):
    with pytest.raises(HTTPException) as exc_info:
        await chat_stream_security_route.endpoint(
            chat_stream_security_route.request("alice", "full_access")
        )

    assert exc_info.value.status_code == 403
    assert "admin" in str(exc_info.value.detail).lower()
    assert chat_stream_security_route.stored_modes == []


@pytest.mark.asyncio
async def test_chat_stream_persists_and_uses_allowed_full_access_mode(
    chat_stream_security_route,
):
    response = await chat_stream_security_route.endpoint(
        chat_stream_security_route.request("admin", "full_access")
    )
    assert response is not None
    assert chat_stream_security_route.stored_modes == ["full_access"]

    async for _chunk in chat_stream_security_route.started_streams["stream"]:
        pass

    assert chat_stream_security_route.stream_kwargs["security_mode"] == "full_access"
