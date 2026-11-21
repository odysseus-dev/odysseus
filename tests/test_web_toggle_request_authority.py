"""Web tools the turn offers are also granted by its request authority.

With the Web search toggle on, the agent was offered web_search for a plain
question, but the request authority only granted the web family when the
message itself matched English search wording. Every call then failed with
"The exact operation is outside server request authority." (#6631).
"""
import json

import pytest

from routes import chat_routes
from src.agent_runtime.authority import ExactOperation
from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint

_SEARCH = ExactOperation.normalize("web_search", json.dumps({"query": "champions league final"}))


async def _authority_for(monkeypatch, message, form):
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", {})
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *args, **kwargs: (message, "session-1"))
    seen = {}

    async def capture_agent(*args, **kwargs):
        seen["authority"] = kwargs.get("request_authority")
        yield 'data: {"delta":"ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form.update({"message": message, **form})
    response = await endpoint(request)
    [chunk async for chunk in response.body_iterator]
    return seen["authority"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "who won the Champions League final 2026?",
    "πόσο κοστίζει το iPhone 17 στην Ελλάδα;",
])
async def test_web_toggle_grants_the_web_search_it_offers(monkeypatch, message):
    authority = await _authority_for(monkeypatch, message, {"allow_web_search": "true"})
    assert authority.permits(_SEARCH)


@pytest.mark.asyncio
async def test_web_toggle_off_still_grants_no_web_search(monkeypatch):
    authority = await _authority_for(
        monkeypatch, "who won the Champions League final 2026?", {"allow_web_search": "false"},
    )
    assert not authority.permits(_SEARCH)


@pytest.mark.asyncio
async def test_web_toggle_grants_no_shell_or_personal_tools(monkeypatch):
    authority = await _authority_for(
        monkeypatch, "who won the Champions League final 2026?", {"allow_web_search": "true"},
    )
    granted = {grant.tool for grant in authority.grants}
    assert not granted & {"bash", "python", "write_file", "manage_notes", "manage_calendar"}
