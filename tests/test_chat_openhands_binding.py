"""Chat send must bind OpenHands conversation ids, never the Odysseus session id."""

from pathlib import Path


def test_agent_mode_skips_odysseus_model_picker():
    """OpenHands owns the LLM for agent turns; 9router still owns Chat mode."""
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1]
    gated = stream.split("if not user_requested_agent:", 1)[1]
    assert "No model selected for this chat" in gated[:800]
    assert "Selected model endpoint is not configured" in gated[:800]


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
