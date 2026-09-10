"""Chat send must bind OpenHands conversation ids, never the Odysseus session id."""

from pathlib import Path


def test_interactive_openhands_turns_skip_odysseus_model_picker():
    """OpenHands owns the LLM for Chat and Agent; compare still uses Odysseus models."""
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1]
    gated = stream.split("if compare_mode:", 1)[1]
    assert "No model selected for this chat" in gated[:1200]
    assert "Selected model endpoint is not configured" in gated[:1200]
    picker = stream.split("if compare_mode:", 1)[0]
    assert "No model selected for this chat" not in picker


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
    call = source.split("async for chunk in stream_governed_agent(", 1)[1].split("):", 1)[0]
    assert "user_requested_agent=user_requested_agent" in call
    assert "workspace_grants=" in call


def test_can_use_agent_false_clears_user_requested_agent():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    block = source.split('if not _privs.get("can_use_agent", True):', 1)[1]
    branch = block.split("\n", 4)[0:4]
    assert any("user_requested_agent = False" in line for line in branch)
