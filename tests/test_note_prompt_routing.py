"""The notices every note-started prompt carries leave the run's routing alone.

A note run's prompt is the user's own turn, so its words feed the lexical
intent classifiers. A notice that said "show" would add the UI family to
every note run, and "search" the web family. A notice that said "list"
made the stalled-notes fallback replace the model's own view or update with
a list of every note, round after round. Each request below is routed with
and without the notices, and must come out the same.
"""
import re
from pathlib import Path

import pytest

from src.action_intents import classify_tool_intent
from src.agent_loop import _classify_agent_request, _parse_simple_notes_tool_request
from src.turn_contract import requested_capabilities

RULES = Path(__file__).resolve().parents[1] / "frontend/src/features/notes/domain/noteRules.js"


def _constant(name):
    match = re.search(rf"export const {name} = '([^']*)';", RULES.read_text(encoding="utf-8"))
    assert match, name
    return match.group(1)


ATTACHED = "The note is attached as the selected note."


def _route(text):
    intent = classify_tool_intent(text)
    request = _classify_agent_request([{"role": "user", "content": text}], text)
    return ((intent.needs_tools, intent.category), sorted(request.get("domains") or ()),
            sorted(requested_capabilities(text, [])), _parse_simple_notes_tool_request(text))


@pytest.mark.parametrize("request_text", [
    f"Help me with the focus todo of the attached note. {ATTACHED}",
    f"Help me get this note done. {ATTACHED}",
    f"Summarize this note.\n\n{ATTACHED}",
    f"Rewrite this note more clearly.\n\n{ATTACHED}",
    f"Add this to my calendar.\n\n{ATTACHED}",
    f"Search the web about the subject of this note and tell me what you find.\n\n{ATTACHED}",
])
def test_the_show_results_notice_changes_no_routing(request_text):
    edit = _constant("EDIT_SCOPE_NOTICE")
    results = _constant("SHOW_RESULTS_NOTICE")
    assert _route(f"{request_text}\n\n{results}\n\n{edit}") == _route(f"{request_text}\n\n{edit}")
