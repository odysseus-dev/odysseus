import asyncio
import json
import pytest
import src.agent_loop as al
from src.tool_policy import ToolPolicy


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]
    return asyncio.run(_run())


def _offered_tool_names(tools_arg):
    if not tools_arg:
        return set()
    names = set()
    for item in tools_arg:
        fn = item.get("function") if isinstance(item, dict) else None
        name = (fn or {}).get("name") or (item.get("name") if isinstance(item, dict) else None)
        if name:
            names.add(name)
    return names


def _audit_selected_tools(chunks):
    for chunk in chunks:
        if chunk.startswith("data: ") and '"selected_tools"' in chunk:
            payload = json.loads(chunk[6:])
            if "selected_tools" in payload:
                return set(payload["selected_tools"] or [])
    return set()


@pytest.mark.parametrize("messages", [
    [{"role": "user", "content": "Hello OdyAI"}],
    [{"role": "user", "content": "Reply HELLO"}],
    [{"role": "user", "content": "What is 17 * 24?"}],
    [
        {"role": "user", "content": "Hi there"},
        {"role": "assistant", "content": "Hello! How can I help?"},
        {"role": "user", "content": "Reply HELLO"},
    ],
])
@pytest.mark.parametrize("model_name", ["gemma4:31b", "gemma4:26b", "llama3.3:70b"])
def test_auth002_chat_conversational_turns_omit_tools_for_any_model(monkeypatch, messages, model_name):
    """AUTH-T01, AUTH-T02, AUTH-T03, AUTH-T11: Conversational Chat turns pass zero tools regardless of model name."""
    captured = []

    async def fake_stream(candidates, req_messages, tools=None, **kwargs):
        captured.append(_offered_tool_names(tools))
        yield 'data: {"delta": "HELLO"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    chunks = _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            model_name,
            messages,
            max_rounds=2,
            owner="admin",
            effective_chat_mode="chat",
        )
    )
    assert captured, "Provider must be invoked"
    assert captured[0] == set(), f"Expected no tools in Chat mode, got {captured[0]}"
    assert _audit_selected_tools(chunks) == set()
    assert any("HELLO" in c for c in chunks)


def test_auth002_chat_explicit_search_permitted_vs_denied(monkeypatch):
    """AUTH-T04 & AUTH-T05: Explicit search works in Chat mode when permitted and stays blocked when denied."""
    captured = []

    async def fake_stream(candidates, req_messages, tools=None, **kwargs):
        captured.append(_offered_tool_names(tools))
        yield 'data: {"delta": "Done"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)

    # AUTH-T04: Permitted explicit search
    chunks_ok = _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gemma4:31b",
            [{"role": "user", "content": "Search the web for RTX 5090 release price"}],
            max_rounds=1,
            owner="admin",
            effective_chat_mode="chat",
        )
    )
    assert "web_search" in captured[-1]
    assert "web_search" in _audit_selected_tools(chunks_ok)

    # AUTH-T05: Disabled search preserves denial
    chunks_denied = _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gemma4:31b",
            [{"role": "user", "content": "Search the web for RTX 5090 release price"}],
            max_rounds=1,
            owner="admin",
            effective_chat_mode="chat",
            disabled_tools={"web_search", "web_fetch"},
            tool_policy=ToolPolicy(disabled_tools=frozenset({"web_search", "web_fetch"})),
        )
    )
    assert "web_search" not in captured[-1]
    assert "web_fetch" not in captured[-1]
    assert "web_search" not in _audit_selected_tools(chunks_denied)


def test_auth002_agent_and_research_modes_preserve_tools(monkeypatch):
    """AUTH-T07 & AUTH-T08: Agent and Research modes retain their tool surfaces without regression."""
    captured = []

    async def fake_stream(candidates, req_messages, tools=None, **kwargs):
        captured.append(_offered_tool_names(tools))
        yield 'data: {"delta": "OK"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)

    for mode in ("agent", "research"):
        _collect(
            al.stream_agent_loop(
                "https://api.openai.com/v1",
                "gemma4:31b",
                [{"role": "user", "content": "Search the web for RTX 5090 specs"}],
                max_rounds=1,
                owner="admin",
                effective_chat_mode=mode,
            )
        )
        assert "web_search" in captured[-1], f"Expected web_search available in {mode} mode"


def test_auth002_default_none_mode_backward_compatible(monkeypatch):
    """Backward compatibility: callers omitting effective_chat_mode (schedulers/monitors) retain default routing."""
    captured = []

    async def fake_stream(candidates, req_messages, tools=None, **kwargs):
        captured.append(_offered_tool_names(tools))
        yield 'data: {"delta": "OK"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gemma4:31b",
            [{"role": "user", "content": "Reply HELLO"}],
            max_rounds=1,
            owner="admin",
        )
    )
    assert len(captured[-1]) > 0


def test_chat_mode_warm_web_followup_aligns_authority_and_declared_tools():
    """Warm web follow-ups in Chat mode must not declare tools denied by RequestAuthority."""
    from src.agent_runtime.authority import create_request_authority
    from src.turn_contract import requested_capabilities

    history = [
        {"role": "user", "content": "Search the web for the latest RTX 5090 street price"},
        {
            "role": "assistant",
            "content": "Street prices range from $6,199 to $11,896.",
            "metadata": {
                "tool_events": [
                    {"tool": "web_search", "status": "done", "exit_code": 0}
                ]
            },
        },
    ]
    followup = "Which retailer had the lowest one?"
    caps = requested_capabilities(followup, history=history)
    assert "search_browser" in caps

    from src.tool_policy import build_effective_tool_policy
    policy = build_effective_tool_policy(
        last_user_message="Search the web for the latest RTX 5090 street price",
    )
    auth = create_request_authority(
        followup,
        owner="admin",
        session_id="s1",
        history=history,
        policy=policy,
        capabilities=caps,
    )
    granted = {g.tool for g in auth.grants}
    assert "web_search" in granted
