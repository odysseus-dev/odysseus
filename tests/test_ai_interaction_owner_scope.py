import inspect

import pytest

from src import ai_interaction
from src.agent_tools import model_interaction_tools


def _source(fn) -> str:
    return inspect.getsource(fn)


def test_model_resolver_applies_owner_filter():
    body = _source(ai_interaction._resolve_model)

    assert "owner: Optional[str] = None" in body
    assert "from src.auth_helpers import owner_filter" in body
    assert "owner_filter(query, ModelEndpoint, owner)" in body


def test_model_listing_and_image_fallback_are_owner_scoped():
    from src.agent_tools.image_tools import GenerateImageTool
    list_body = _source(ai_interaction.do_list_models)
    image_body = _source(GenerateImageTool.execute)

    assert "owner: Optional[str] = None" in list_body
    assert "owner_filter(query, ModelEndpoint, owner)" in list_body
    # _resolve_model is offloaded to a worker thread (#4589) but stays owner-scoped.
    assert "asyncio.to_thread(_resolve_model, candidate, owner=owner)" in image_body
    assert "owner_filter(_img_q, ModelEndpoint, owner)" in image_body
    assert "asyncio.to_thread(_resolve_model, model_spec, owner=owner)" in image_body


# Tools moved to the registry (#3629) — dispatch_ai_tool now delegates to
# TOOL_HANDLERS. These tests verify owner / session_id are threaded through
# the registry wrapper correctly.
@pytest.mark.parametrize("tool,content", [
    ("chat_with_model", "gpt-test\nhello"),
    ("list_models", ""),
    ("ask_teacher", "gpt-test\nhelp me"),
    ("create_session", "My Chat\ngpt-test"),
    ("list_sessions", ""),
])
async def test_dispatch_passes_owner_to_model_tools(monkeypatch, tool, content):
    seen = {}

    async def capture(content_str, ctx):
        seen[tool] = {"content": content_str, "session_id": ctx.get("session_id"), "owner": ctx.get("owner")}
        return {"ok": True}

    # Patch the TOOL_HANDLERS entry — dispatch_ai_tool delegates to the registry.
    from src.agent_tools import TOOL_HANDLERS
    original = TOOL_HANDLERS.get(tool)
    monkeypatch.setitem(TOOL_HANDLERS, tool, capture)

    try:
        _desc, result = await ai_interaction.dispatch_ai_tool(tool, content, session_id="sid1", owner="alice")
    finally:
        if original is not None:
            TOOL_HANDLERS[tool] = original
        elif tool in TOOL_HANDLERS:
            del TOOL_HANDLERS[tool]

    assert result == {"ok": True}
    assert seen[tool]["owner"] == "alice"
    assert seen[tool]["session_id"] == "sid1"
