"""Chat send must bind OpenHands conversation ids, never the Odysseus session id."""

from pathlib import Path


def test_chat_route_passes_binding_kwargs():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert "openhands_conversation_id" in source
    assert "turn_id" in source
    assert "agent_profile_id" in source
    assert "bound_agent_profile_id" in source
