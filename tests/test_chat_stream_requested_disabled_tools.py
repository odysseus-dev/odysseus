"""chat_stream honours a caller's `disabled_tools` for the turn.

Notes starts agent runs with `disabled_tools=["manage_notes"]` so a run
started from a note cannot edit notes. The route used to ignore the field, so
the run kept manage_notes. The names only ever narrow the turn's tools.
"""
import json

import pytest

import routes.chat_routes as chat_routes
from routes.chat_routes import _requested_disabled_tools
from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint

# Names both the notes and the tasks family under dev's request routing, so
# withholding manage_notes has a family to drop and one to keep.
NOTES_PROMPT = (
    "Context (from note \"Groceries\").\n\n"
    "Add oat milk to my groceries note, then schedule a task to buy it tomorrow"
)


def test_requested_disabled_tools_parsing():
    assert _requested_disabled_tools('["manage_notes", " bash "]') == {"manage_notes", "bash"}
    assert _requested_disabled_tools(["manage_notes"]) == {"manage_notes"}
    for raw in (None, "", "manage_notes", "{}", '{"a": 1}', "[1, null]", 7, "not json"):
        assert _requested_disabled_tools(raw) == set()
    assert len(_requested_disabled_tools(json.dumps([f"t{i}" for i in range(500)]))) == 64


async def _agent_disabled_tools(monkeypatch, *, form=None):
    """The disabled set the route builds its tool policy from, and the one it hands the agent loop."""
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured)
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *args, **kwargs: (NOTES_PROMPT, "session-1"))
    real_policy = chat_routes.build_effective_tool_policy

    def capture_policy(*args, **kwargs):
        # The pre-context policy passes no disabled set; the turn's policy does.
        if "disabled_tools" in kwargs:
            captured.setdefault("policy_inputs", []).append(set(kwargs["disabled_tools"] or ()))
        return real_policy(*args, **kwargs)

    async def capture_agent_stream(endpoint_url, model, messages, **kwargs):
        captured["agent"] = set(kwargs.get("disabled_tools") or ())
        contract = kwargs.get("turn_contract")
        captured["contract"] = contract.audit() if contract is not None else None
        yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "build_effective_tool_policy", capture_policy)
    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent_stream)
    request = _RouteRequest("agent")
    request._form.update({"message": NOTES_PROMPT, "compare_mode": "false", **(form or {})})
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass
    _agent_disabled_tools.contract = captured["contract"]
    return captured["policy_inputs"][0], captured["agent"]


@pytest.mark.asyncio
async def test_form_disabled_tools_reach_the_policy_and_the_agent_loop(monkeypatch):
    policy, _agent = await _agent_disabled_tools(monkeypatch)
    assert "manage_notes" not in policy, "nothing withholds manage_notes from this turn by default"

    narrowed, agent = await _agent_disabled_tools(monkeypatch, form={"disabled_tools": json.dumps(["manage_notes"])})
    assert narrowed == policy | {"manage_notes"}, "the request adds its names and nothing else"
    assert "manage_notes" in agent


@pytest.mark.asyncio
async def test_json_body_disabled_tools_are_honoured_too(monkeypatch):
    policy, _agent = await _agent_disabled_tools(monkeypatch, form={"disabled_tools": ["manage_notes"]})
    assert "manage_notes" in policy


@pytest.mark.asyncio
async def test_malformed_disabled_tools_change_nothing(monkeypatch):
    baseline, _ = await _agent_disabled_tools(monkeypatch)
    assert (await _agent_disabled_tools(monkeypatch, form={"disabled_tools": "manage_notes"}))[0] == baseline


@pytest.mark.asyncio
async def test_a_withheld_family_is_not_required(monkeypatch):
    """The notes prompt routes to the notes family. With manage_notes withheld that family is
    dropped from the turn, so the run is answered instead of refused as unavailable."""
    await _agent_disabled_tools(monkeypatch)
    baseline = _agent_disabled_tools.contract
    assert baseline is not None and {"notes", "tasks"} <= set(baseline["capabilities"])

    await _agent_disabled_tools(monkeypatch, form={"disabled_tools": json.dumps(["manage_notes"])})
    contract = _agent_disabled_tools.contract
    assert "notes" not in contract["capabilities"] and "tasks" in contract["capabilities"]
    assert "manage_notes" not in contract["required"] and "manage_notes" not in contract["unavailable"]
