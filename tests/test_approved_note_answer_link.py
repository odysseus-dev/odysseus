"""An approved note create or edit answers with a link to that note.

The approval continuation replaces the model's draft with a fixed "Done."
so the approval question is not repeated. The "View note" link built just
before it was dropped with the draft, leaving the user with "Done." and no
way to reach the note from the chat.
"""

import json

import pytest

import src.agent_loop as al
from src.tool_approvals import tool_approval_store
from src.tool_capabilities import capabilities_for_action
from tests.test_agent_rounds_exhausted import _collect, _patch_common, _types

NOTE_ID = "5f079f12-0000-4000-8000-000000000001"


def _approved_run(monkeypatch, content, result):
    _patch_common(monkeypatch)
    session = f"approved-note-link-{json.loads(content)['action']}"
    pending = tool_approval_store.create(
        owner="admin",
        session_id=session,
        origin_run_id=f"{session}-run",
        tool_name="manage_notes",
        content=content,
        workspace=None,
        external_untrusted_context_seen=False,
        capabilities=capabilities_for_action("manage_notes", content),
        request_text="Create a new note with a todo list",
    )
    grant = tool_approval_store.consume(
        pending.approval_id, decision="approve_task", owner="admin", session_id=session,
    )
    assert grant is not None

    async def _fake_exec(block, *args, **kwargs):
        return (block.tool_type, dict(result))

    async def _no_model_round(_candidates, messages, **kwargs):
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _no_model_round, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Create a new note with a todo list"}],
        max_rounds=2,
        relevant_tools={"manage_notes"},
        owner="admin",
        session_id=session,
        exact_approval=grant,
    )))
    finals = [e["content"] for e in events if e.get("type") == "final_response"]
    assert finals, events
    return finals[-1]


@pytest.mark.parametrize("action, response", [
    ("add", 'Note created: "Weekend Reset" (id: 5f079f12)'),
    ("update", 'Note updated: "Weekend Reset"'),
])
def test_approved_note_write_answers_with_a_link_to_the_note(monkeypatch, action, response):
    content = json.dumps({"action": action, "title": "Weekend Reset", "note_type": "checklist"})
    final = _approved_run(monkeypatch, content, {
        "response": response, "note_id": NOTE_ID, "note_title": "Weekend Reset", "exit_code": 0,
    })

    assert final.startswith("Done.")
    assert f"[View note: Weekend Reset](#note-{NOTE_ID})" in final


def test_approved_note_delete_has_no_link(monkeypatch):
    content = json.dumps({"action": "delete", "id": NOTE_ID})
    final = _approved_run(monkeypatch, content, {
        "response": 'Deleted note: "Weekend Reset"', "exit_code": 0,
    })

    assert final == "Done."
