import inspect

import pytest

from src import ai_interaction


def _source(fn) -> str:
    return inspect.getsource(fn)


def test_model_resolver_applies_owner_filter():
    body = _source(ai_interaction._resolve_model)

    assert "owner: Optional[str] = None" in body
    assert "from src.auth_helpers import owner_filter" in body
    assert "owner_filter(query, ModelEndpoint, owner)" in body


def test_model_listing_and_image_fallback_are_owner_scoped():
    list_body = _source(ai_interaction.do_list_models)
    image_body = _source(ai_interaction.do_generate_image)

    assert "owner: Optional[str] = None" in list_body
    assert "owner_filter(query, ModelEndpoint, owner)" in list_body
    assert "_resolve_model(candidate, owner=owner)" in image_body
    assert "owner_filter(_img_q, ModelEndpoint, owner)" in image_body
    assert "_resolve_model(model_spec, owner=owner)" in image_body


@pytest.mark.parametrize("tool,content", [
    ("chat_with_model", "gpt-test\nhello"),
    ("pipeline", "gpt-test | summarize this"),
    ("list_models", ""),
    ("ui_control", "switch_model gpt-test"),
    ("ask_teacher", "gpt-test\nhelp me"),
])
async def test_dispatch_passes_owner_to_model_tools(monkeypatch, tool, content):
    seen = {}

    async def capture(name, content, session_id=None, owner=None):
        seen[name] = {"content": content, "session_id": session_id, "owner": owner}
        return {"ok": True}

    monkeypatch.setattr(
        ai_interaction,
        "do_chat_with_model",
        lambda content, session_id=None, owner=None: capture("chat_with_model", content, session_id, owner),
    )
    monkeypatch.setattr(
        ai_interaction,
        "do_pipeline",
        lambda content, session_id=None, owner=None: capture("pipeline", content, session_id, owner),
    )
    monkeypatch.setattr(
        ai_interaction,
        "do_list_models",
        lambda content, session_id=None, owner=None: capture("list_models", content, session_id, owner),
    )
    monkeypatch.setattr(
        ai_interaction,
        "do_ui_control",
        lambda content, session_id=None, owner=None: capture("ui_control", content, session_id, owner),
    )
    monkeypatch.setattr(
        ai_interaction,
        "do_ask_teacher",
        lambda content, session_id=None, owner=None: capture("ask_teacher", content, session_id, owner),
    )

    _desc, result = await ai_interaction.dispatch_ai_tool(tool, content, session_id="sid1", owner="alice")

    assert result == {"ok": True}
    assert seen[tool]["owner"] == "alice"
    assert seen[tool]["session_id"] == "sid1"
