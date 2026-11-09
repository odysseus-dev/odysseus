from src.agent_loop import (
    _classify_agent_request,
    _looks_like_explicit_notes_only_turn,
    _looks_like_implicit_notes_turn,
)


def test_implicit_saved_item_routes_to_notes_domain():
    prompt = "What did I write down under Heat island caveats? Open the saved item and tell me what it says."
    assert _looks_like_implicit_notes_turn(prompt)
    assert "notes_calendar_tasks" in _classify_agent_request([{"role": "user", "content": prompt}], prompt)["domains"]
    assert _looks_like_explicit_notes_only_turn(prompt)


def test_mixed_implicit_note_request_is_not_notes_only():
    prompt = "Open what I saved as Model benchmark notes, then read Launch Checklist from documents."
    assert "notes_calendar_tasks" in _classify_agent_request([{"role": "user", "content": prompt}], prompt)["domains"]
    assert not _looks_like_explicit_notes_only_turn(prompt)


def test_generic_saved_file_does_not_route_to_notes():
    prompt = "Open the saved file from my workspace."
    assert not _looks_like_implicit_notes_turn(prompt)
