"""A note's colour is a palette name, nothing, or a picture upload.

The colour lands in a class name and a style on every card that shows the
note, a collaborator's included, so the API, the agent tool and the CLI all
refuse anything else, and the palette matches the one the Notes UI offers.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

from core.notes_markdown import NOTE_COLORS, normalize_note_color
from src import tool_implementations
from tests.test_notes_markdown_routes import notes  # noqa: F401  (fixture)

_REPO = Path(__file__).resolve().parent.parent
_UPLOAD = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("value, stored", [
    (None, None),
    ("", ""),
    ("  ", ""),
    ("red", "red"),
    ("Purple", "purple"),
    (f"bg:/api/upload/{_UPLOAD}", f"bg:/api/upload/{_UPLOAD}"),
    (f"bg:/api/upload/{_UPLOAD}.png", f"bg:/api/upload/{_UPLOAD}.png"),
    (f"bg:http://notes.test:7000/api/upload/{_UPLOAD}", f"bg:/api/upload/{_UPLOAD}"),
    (f"bg:https://example.org/api/upload/{_UPLOAD}.jpg", f"bg:/api/upload/{_UPLOAD}.jpg"),
])
def test_accepted_colours_and_how_they_are_stored(value, stored):
    assert normalize_note_color(value) == stored


@pytest.mark.parametrize("value", [
    'red" onmouseover="alert(1)',
    "red onmouseover=alert(1)",
    "custom",
    "#ff0000",
    "pink",
    "bg:",
    "bg:javascript:alert(1)",
    "bg:https://tracker.example/p.png",
    f"bg:/api/upload/{_UPLOAD}');background:url(https://tracker.example/x",
    f"bg:/api/upload/{_UPLOAD}\"",
    f"bg:/api/notes/n/media/{_UPLOAD}",
    "bg:/api/upload/../secret",
])
def test_anything_else_is_refused(value):
    with pytest.raises(ValueError):
        normalize_note_color(value)


def test_palette_matches_the_notes_ui():
    source = (_REPO / "frontend/src/features/notes/ui/colors.js").read_text()
    offered = set(re.findall(r"\{ name: '[a-z]+',\s*value: '([a-z]*)' \}", source)) - {"", "custom"}
    styled = set(re.findall(r"^\s+([a-z]+):\s+'#[0-9a-f]{6}',", source, re.M))
    assert offered == set(NOTE_COLORS)
    assert styled == set(NOTE_COLORS)


async def test_rest_refuses_a_bad_colour_and_stores_a_picture_as_a_path(notes):  # noqa: F811
    bad = await notes.call("POST", "/api/notes", json={"title": "t", "color": 'red" x="1'})
    assert bad.status_code == 400
    created = await notes.call("POST", "/api/notes", json={"title": "t", "color": f"bg:http://notes.test/api/upload/{_UPLOAD}"})
    assert created.status_code == 200
    note_id = created.json()["id"]
    assert notes.row(note_id).color == f"bg:/api/upload/{_UPLOAD}"
    r = await notes.call("PUT", f"/api/notes/{note_id}", json={"color": "bg:https://tracker.example/p.png"})
    assert r.status_code == 400
    assert notes.row(note_id).color == f"bg:/api/upload/{_UPLOAD}"
    r = await notes.call("PUT", f"/api/notes/{note_id}", json={"color": "Blue"})
    assert r.status_code == 200 and notes.row(note_id).color == "blue"
    r = await notes.call("PUT", f"/api/notes/{note_id}", json={"color": ""})
    assert r.status_code == 200 and not notes.row(note_id).color


def _tool(args, owner="alice"):
    return asyncio.run(tool_implementations.do_manage_notes(json.dumps(args), owner=owner))


def test_the_agent_tool_refuses_a_bad_colour_and_saves_nothing(notes):  # noqa: F811
    added = _tool({"action": "add", "title": "Coloured", "content": "x", "color": "red' onmouseover='x"})
    assert added.get("exit_code") == 1 and "color must be" in added["error"]
    notes.seed("n1", title="Plain", content="body")
    updated = _tool({"action": "update", "id": "n1", "color": "url(https://tracker.example)"})
    assert updated.get("exit_code") == 1
    assert notes.row("n1").color is None
    ok = _tool({"action": "update", "id": "n1", "color": "GREEN"})
    assert ok.get("exit_code", 0) == 0 and notes.row("n1").color == "green"
