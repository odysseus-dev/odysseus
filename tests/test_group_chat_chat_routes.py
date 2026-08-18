import types

import pytest

import core.database as cdb
from core.models import ChatMessage
from routes import chat_routes
from src.request_models import ChatRequest


class _ToolPolicy:
    block_all_tool_calls = False

    def blocks(self, _tool_name):
        return False


class _ChatHandler:
    async def handle_memory_command(self, _session, _message):
        return None


class _Session:
    def __init__(self, session_id, owner="alice"):
        self.id = session_id
        self.owner = owner
        self.name = session_id
        self.endpoint_url = "http://model.test/v1/chat/completions"
        self.model = "test-model"
        self.headers = {}
        self.history = []

    def add_message(self, message):
        self.history.append(message)


class _SessionManager:
    def __init__(self, sessions):
        self.sessions = sessions
        self.save_count = 0

    def get_session(self, session_id):
        return self.sessions[session_id]

    def save_sessions(self):
        self.save_count += 1


class _FormRequest:
    headers = {}

    def __init__(self, form_data):
        self.form_data = form_data
        self.requested_keys = []

    async def form(self):
        request = self

        class _TrackingForm(dict):
            def get(self, key, default=None):
                request.requested_keys.append(key)
                return super().get(key, default)

        return _TrackingForm(self.form_data)


def _route_endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


def _setup_route_dependencies(monkeypatch):
    monkeypatch.setattr(chat_routes, "_set_user_time_from_request", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda *_args: "alice")
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "build_effective_tool_policy", lambda **_kwargs: _ToolPolicy())
    monkeypatch.setattr(chat_routes, "_resolve_request_workspace", lambda *_args: (None, False))
    monkeypatch.setattr(chat_routes, "_classify_tool_intent", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "_is_contextual_web_followup", lambda *_args: False)
    monkeypatch.setattr(chat_routes, "_is_contextual_browser_followup", lambda *_args: False)
    monkeypatch.setattr(chat_routes, "_resolve_workspace_from_message_path", lambda *_args: (None, None))
    monkeypatch.setattr(chat_routes, "_reconcile_selected_route_from_request", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda *_args: "chat")
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "web_search_enabled_for_turn", lambda *_args: False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_form", "expected_incognito"),
    [
        ({}, False),
        ({"incognito": "true"}, True),
        ({"group_internal": "true"}, False),
    ],
    ids=["ordinary", "incognito", "group-internal"],
)
async def test_chat_stream_modes_reach_context_build_without_unbound_flags(
    monkeypatch,
    extra_form,
    expected_incognito,
):
    _setup_route_dependencies(monkeypatch)
    session = _Session("child")
    manager = _SessionManager({"child": session})
    captured = {}

    class _ContextReached(Exception):
        pass

    async def capture_context(*_args, **kwargs):
        captured.update(kwargs)
        raise _ContextReached

    monkeypatch.setattr(chat_routes, "build_chat_context", capture_context)
    router = chat_routes.setup_chat_routes(
        manager,
        _ChatHandler(),
        object(),
        object(),
        object(),
        object(),
    )
    endpoint = _route_endpoint(router, "/api/chat_stream")
    request = _FormRequest({"message": "hello", "session": "child", **extra_form})

    with pytest.raises(_ContextReached):
        await endpoint(request)

    assert captured["incognito"] is expected_incognito
    assert "group_internal" in request.requested_keys


@pytest.mark.asyncio
@pytest.mark.parametrize("group_internal", [False, True], ids=["direct-whisper", "group-internal"])
async def test_nonstream_chat_mirrors_direct_child_whispers_once(
    monkeypatch,
    group_internal,
):
    _setup_route_dependencies(monkeypatch)
    parent = _Session("parent")
    child = _Session("child")
    manager = _SessionManager({"parent": parent, "child": child})

    async def build_context(*_args, **_kwargs):
        child.add_message(ChatMessage("user", "hello"))
        return types.SimpleNamespace(
            user="alice",
            messages=[{"role": "user", "content": "hello"}],
            preprocessed=types.SimpleNamespace(user_content="hello"),
            preset=types.SimpleNamespace(
                temperature=0.2,
                max_tokens=100,
                character_name=None,
            ),
            uprefs={},
        )

    async def llm_call(candidates, *_args, **_kwargs):
        selected = candidates[0]
        return "hello back", selected, selected[1]

    monkeypatch.setattr(chat_routes, "build_chat_context", build_context)
    monkeypatch.setattr(chat_routes, "llm_call_async_with_route_fallback", llm_call)
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cdb, "update_session_last_accessed", lambda *_args: None)
    monkeypatch.setattr(
        chat_routes,
        "_group_child_whisper_context",
        lambda *_args: {
            "parent_session_id": "parent",
            "participant_session_id": "child",
            "participant_name": "Athena",
            "participant_model": "test-model",
        },
    )

    router = chat_routes.setup_chat_routes(
        manager,
        _ChatHandler(),
        object(),
        object(),
        object(),
        object(),
    )
    endpoint = _route_endpoint(router, "/api/chat")
    response = await endpoint(
        types.SimpleNamespace(),
        ChatRequest(message="hello", session="child", group_internal=group_internal),
    )

    assert response["response"] == "hello back"
    assert response["requested_model"] == "test-model"
    assert response["model"] == "test-model"
    if group_internal:
        assert parent.history == []
    else:
        assert [(message.role, message.content) for message in parent.history] == [
            ("user", "hello"),
            ("assistant", "hello back"),
        ]
        assert parent.history[0].metadata["whisper_to"] == "Athena"
        assert parent.history[1].metadata["whisper_from"] == "Athena"
