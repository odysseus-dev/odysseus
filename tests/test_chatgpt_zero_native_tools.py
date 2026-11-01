"""ChatGPT Subscription is model inference only — Odysseus is the only agent.

These regressions pin the zero-native-tool invariant relied on by the Odysseus
dogfood benchmark: the ChatGPT/Codex Responses request never declares
provider-native tools, and the stream handler never executes provider-side
tool calls. Odysseus' own text tool protocol travels as plain instructions/
input and is parsed and executed by Odysseus.
"""

import asyncio
import json

import pytest

from src import llm_core
from src import chatgpt_subscription

_URL = "https://chatgpt.com/backend-api/codex"
_TOOLS = [
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}},
    {"type": "web_search_preview"},
    {"type": "computer_use_preview", "display_width": 1024, "display_height": 768, "environment": "browser"},
    {"type": "file_search", "vector_store_ids": ["vs_1"]},
    {"type": "local_shell"},
    {"type": "mcp", "server_label": "fs", "server_url": "http://localhost/mcp"},
]
_MESSAGES = [
    {"role": "system", "content": "You are Odysseus. Use <tool>read_file</tool> protocol when needed."},
    {"role": "user", "content": "List the repo"},
    {"role": "assistant", "content": "<tool>read_file</tool>"},
    {"role": "tool", "content": "README.md"},
]
_ALLOWED_KEYS = {"model", "instructions", "input", "stream", "store", "temperature"}


def test_responses_payload_has_no_native_tool_surfaces():
    payload = llm_core._build_chatgpt_responses_payload("gpt-5.5", _MESSAGES, 0.7, 4096, stream=True)
    assert set(payload) <= _ALLOWED_KEYS
    assert payload["model"] == "gpt-5.5"
    assert payload["stream"] is True
    assert payload["store"] is False
    assert "tools" not in payload
    assert "tool_choice" not in payload
    for key in llm_core.CHATGPT_FORBIDDEN_PAYLOAD_KEYS:
        assert key not in payload
    # Odysseus protocol text is preserved as plain instructions/input.
    assert "<tool>read_file</tool>" in payload["instructions"]
    roles = [item["role"] for item in payload["input"]]
    assert roles == ["user", "assistant", "user"]  # tool results become user input text
    assert "system" not in roles


def test_upper_level_tools_argument_is_discarded_by_chatgpt_builder():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.5", _MESSAGES, 0.7, 4096, stream=True, tools=_TOOLS, tool_choice="required", parallel_tool_calls=True,
    )
    assert set(payload) <= _ALLOWED_KEYS
    serialized = json.dumps(payload)
    assert "run_shell" not in serialized
    assert "web_search" not in serialized
    assert "computer_use" not in serialized
    assert "file_search" not in serialized
    assert "local_shell" not in serialized
    assert '"mcp"' not in serialized


def test_strip_helper_removes_any_native_tool_key_added_later():
    payload = {"model": "m", "input": [], "tools": _TOOLS, "tool_choice": "auto", "web_search": {}, "shell": {}, "computer": {}, "future_agent_surface": {}}
    stripped = llm_core._strip_chatgpt_native_tool_surfaces(payload)
    assert set(stripped) == {"model", "input"}


def test_responses_payload_includes_reasoning_effort_without_tools():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-6-astra", _MESSAGES, 0.7, 4096, stream=True, reasoning_effort="high", tools=_TOOLS
    )
    assert set(payload) <= (_ALLOWED_KEYS | {"reasoning"})
    assert payload["reasoning"] == {"effort": "high"}
    assert "tools" not in payload
    assert "tool_choice" not in payload
    for key in llm_core.CHATGPT_FORBIDDEN_PAYLOAD_KEYS:
        assert key not in payload


def test_responses_payload_default_or_none_omits_reasoning():
    for effort in [None, "", "default", "Default"]:
        payload = llm_core._build_chatgpt_responses_payload(
            "gpt-6-astra", _MESSAGES, 0.7, 4096, stream=True, reasoning_effort=effort
        )
        assert set(payload) <= _ALLOWED_KEYS
        assert "reasoning" not in payload


class _Resp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _Ctx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _Resp(self._lines)

    async def __aexit__(self, *args):
        return False


class _CapturingClient:
    def __init__(self, lines):
        self._lines = lines
        self.requests = []

    def stream(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        return _Ctx(self._lines)


def _stream_with_tools(monkeypatch, lines):
    client = _CapturingClient(lines)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)

    async def run():
        return [
            chunk
            async for chunk in llm_core._stream_llm_inner(
                _URL + "/responses",
                "gpt-5.5",
                _MESSAGES,
                headers={"Authorization": "Bearer test"},
                tools=_TOOLS,
            )
        ]

    return client, asyncio.run(run())


def test_stream_transport_never_serializes_tools_even_when_passed(monkeypatch):
    lines = [
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "<tool>read_file</tool>"}),
        "data: " + json.dumps({"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}}),
    ]
    client, chunks = _stream_with_tools(monkeypatch, lines)
    assert len(client.requests) == 1
    sent = client.requests[0]
    assert sent["url"] == _URL + "/responses"
    assert set(sent["json"]) <= _ALLOWED_KEYS
    assert "tools" not in sent["json"] and "tool_choice" not in sent["json"]
    assert "run_shell" not in json.dumps(sent["json"])
    # The Odysseus protocol tag is streamed back verbatim for Odysseus to parse.
    deltas = [json.loads(c[6:])["delta"] for c in chunks if c.startswith("data: ") and '"delta"' in c]
    assert deltas == ["<tool>read_file</tool>"]


def test_stream_handler_ignores_provider_side_tool_call_events(monkeypatch):
    """A provider-emitted function_call is never executed nor surfaced as a tool_call."""
    lines = [
        "data: " + json.dumps({"type": "response.output_item.added", "item": {"type": "function_call", "name": "run_shell", "call_id": "c1"}}),
        "data: " + json.dumps({"type": "response.function_call_arguments.delta", "delta": '{"cmd": "rm -rf /"}'}),
        "data: " + json.dumps({"type": "response.function_call_arguments.done", "arguments": '{"cmd": "rm -rf /"}'}),
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "done"}),
        "data: " + json.dumps({"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}}),
    ]
    client, chunks = _stream_with_tools(monkeypatch, lines)
    joined = "".join(chunks)
    assert "tool_calls" not in joined
    assert "rm -rf" not in joined
    assert "run_shell" not in joined
    deltas = [json.loads(c[6:])["delta"] for c in chunks if c.startswith("data: ") and '"delta"' in c]
    assert deltas == ["done"]
    assert chunks[-1] == "data: [DONE]\n\n"


def test_stream_error_cannot_echo_the_request_bearer(monkeypatch):
    _client, chunks = _stream_with_tools(monkeypatch, [
        'data: ' + json.dumps({"type": "error", "message": "rejected Bearer test", "status": 401}),
    ])
    assert "Bearer test" not in "".join(chunks)
    assert "[redacted]" in "".join(chunks)


def test_model_probe_never_posts_provider_tools(monkeypatch):
    import routes.model_routes as models
    monkeypatch.setattr(models.httpx, "post", lambda *args, **kwargs: pytest.fail("ChatGPT is discovery-only"))
    assert models._probe_single_model(_URL, "secret", "gpt-5.5", with_tools=True)["skipped"] is True


def test_provisioned_endpoint_supports_tools_false(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base, ModelEndpoint
    import routes.chatgpt_subscription_routes as csr

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(csr, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(chatgpt_subscription, "fetch_available_models", lambda token: ["gpt-5.5"])
    a = csr._provision_endpoint({"access_token": "A", "refresh_token": "RA"}, "alice", label="codex00")
    b = csr._provision_endpoint({"access_token": "B", "refresh_token": "RB"}, "alice", label="codex01")
    # Reconnect must not flip the flag either.
    csr._provision_endpoint({"access_token": "A2", "refresh_token": "RA2"}, "alice", reconnect_auth_id=a["provider_auth_id"])
    db = TestSessionLocal()
    try:
        for ep_id in (a["id"], b["id"]):
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            assert ep.supports_tools is False
    finally:
        db.close()


def test_responses_input_never_carries_tool_call_structures():
    items = chatgpt_subscription.build_responses_input([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "x", "function": {"name": "run_shell", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "output"},
    ])
    for item in items:
        assert set(item) == {"role", "content"}
        assert "tool_calls" not in json.dumps(item)
