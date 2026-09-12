"""Chat send must bind OpenHands conversation ids, never the Odysseus session id."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_interactive_openhands_turns_skip_odysseus_model_picker():
    """OpenHands owns Chat/Agent LLM. Compare is bounded jobs, not the Odysseus picker."""
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1]
    picker = stream.split("if compare_mode:", 1)[0]
    assert "No model selected for this chat" not in picker
    assert "Selected model endpoint is not configured" not in picker
    compare = stream.split('elif compare_mode and chat_mode == "chat":', 1)[1].split("else:", 1)[0]
    assert "submit_model_job" in compare
    assert "stream_llm_with_fallback" not in compare


def test_non_compare_chat_uses_governed_agent_not_stream_llm():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    assert "async for chunk in stream_governed_agent(" in stream
    # Non-compare Chat must not keep the old tool-less stream_llm branch.
    assert "# ── Chat mode: call stream_llm directly, NO tools, NO document access ──" not in stream


def test_chat_route_passes_binding_kwargs():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert "openhands_conversation_id" in source
    assert "turn_id" in source
    assert "agent_profile_id" in source
    assert "bound_agent_profile_id" in source
    assert 'data.get("type") == "pending_confirmation"' in source
    sse_parser = source.split('elif data.get("type") == "execution":', 1)[1]
    confirm_block = sse_parser.split('elif data.get("type") == "pending_confirmation":', 1)[1]
    assert "yield chunk" in confirm_block.split("elif ", 1)[0]


def test_chat_stream_passes_user_requested_agent_into_governed_stream():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1]
    call = stream.split("async for chunk in stream_governed_agent(", 1)[1].split("):", 1)[0]
    assert "user_requested_agent=user_requested_agent" in call
    assert "workspace_grants=" in call


def test_leftover_post_api_chat_uses_governed_agent_not_llm_fallback():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    endpoint = source.split("async def chat_endpoint", 1)[1].split("async def chat_stream", 1)[0]
    assert "stream_governed_agent(" in endpoint
    assert "llm_call_async_with_route_fallback" not in endpoint
    assert "llm_call_async(" not in endpoint
    assert "stream_llm(" not in endpoint
    assert "stream_llm_with_fallback" not in endpoint
    assert "No model selected for this chat" not in endpoint
    assert "Selected model endpoint is not configured" not in endpoint
    assert "Selected model endpoint was removed" not in endpoint


def test_agent_loop_file_remains_for_task_18_hold():
    assert (_ROOT / "src" / "agent_loop.py").is_file()


def test_odysseus_registers_no_inbound_chat_completions_route():
    forbidden = ('"/v1/chat/completions"', "'/v1/chat/completions'",
                 '"/api/v1/chat/completions"', "'/api/v1/chat/completions'")
    for path in (_ROOT / "routes").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path} registers inbound completions path {needle}"


def test_webhook_sync_chat_is_not_a_completions_gateway():
    source = Path("routes/webhook/webhook_routes.py").read_text(encoding="utf-8")
    sync = source.split("async def sync_chat", 1)[1]
    assert "llm_call_async(" not in sync
    assert "stream_governed_agent(" in sync
    assert "body.api_key" not in sync or "Provider credentials" in sync
    assert "build_chat_url" not in sync
    assert "build_headers" not in sync


def test_can_use_agent_false_clears_user_requested_agent():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    block = source.split('if not _privs.get("can_use_agent", True):', 1)[1]
    branch = block.split("\n", 4)[0:4]
    assert any("user_requested_agent = False" in line for line in branch)
