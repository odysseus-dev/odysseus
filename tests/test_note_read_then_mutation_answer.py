"""A notes read followed by a notes mutation ends on the model's answer.

A successful notes mutation normally ends a single-capability turn with the
tool's own one-line summary. When the same turn listed notes first, that
summary ("Item ... marked done") replaced the answer that was meant to show
the list, and the model never got the round to present it.
"""
import json

import src.agent_loop as al
from tests.test_agent_rounds_exhausted import _collect, _patch_common, _types

LIST_OUTPUT = "- [n1] **Groceries** #home\n- [n2] **Trip to Lyon** #travel"
TOGGLE_OUTPUT = "AI: Item 'List notes to demonstrate list action' marked done"
ANSWER = "Your notes: Groceries, Trip to Lyon. I ticked the item."


def _call(call_id, arguments):
    return "data: " + json.dumps({"type": "tool_calls", "calls": [
        {"id": call_id, "name": "manage_notes", "arguments": json.dumps(arguments)}]}) + "\n\n"


def _run(monkeypatch, rounds):
    _patch_common(monkeypatch)
    executed = []

    async def _exec(block, *a, **k):
        action = json.loads(block.content).get("action")
        executed.append(action)
        return block.tool_type, {"output": LIST_OUTPUT if action == "list" else TOGGLE_OUTPUT, "exit_code": 0}

    monkeypatch.setattr(al, "execute_tool_block", _exec, raising=False)
    streams = iter(rounds)

    async def _stream(_candidates, messages, **kwargs):
        yield next(streams, 'data: ' + json.dumps({"delta": "unexpected extra round"}) + '\n\n')
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Help me with the focus todo of the attached note."}],
        max_rounds=4, relevant_tools={"manage_notes"},
    )))
    finals = [e["content"] for e in events if e.get("type") == "final_response"]
    return executed, finals


def test_a_toggle_after_a_list_leaves_the_answer_to_the_model(monkeypatch):
    executed, finals = _run(monkeypatch, [
        _call("c1", {"action": "list"}),
        _call("c2", {"action": "toggle_item", "id": "n9", "index": 4, "done": True}),
        "data: " + json.dumps({"delta": ANSWER}) + "\n\n",
    ])
    assert executed == ["list", "toggle_item"]
    assert finals, "no final answer"
    # The model's own closing round is in the answer, and so is the list.
    assert "I ticked the item." in finals[-1]
    assert "Groceries" in finals[-1] and "Trip to Lyon" in finals[-1]


def test_a_toggle_on_its_own_still_ends_on_its_summary(monkeypatch):
    executed, finals = _run(monkeypatch, [
        _call("c1", {"action": "toggle_item", "id": "n9", "index": 4, "done": True}),
    ])
    assert executed == ["toggle_item"]
    assert finals and finals[-1].startswith("Item 'List notes to demonstrate list action' marked done")
