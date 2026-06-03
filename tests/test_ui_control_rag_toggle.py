"""The `rag` UI toggle must be accepted.

do_ui_control advertises `rag` as a valid toggle in its own docstring and in
get_toggles ("Available toggles: web, bash, rag, ..."), and the frontend
fully wires it (chatStream.js maps rag -> rag-toggle / rag-indicator-btn).
But valid_toggles omitted "rag", so `toggle rag on` returned an "Unknown
toggle" error - the advertised capability was dead.
"""
import asyncio

from src.ai_interaction import do_ui_control


def test_toggle_rag_on_is_accepted():
    r = asyncio.run(do_ui_control("toggle rag on"))
    assert r.get("ui_event") == "toggle"
    assert r.get("toggle_name") == "rag"
    assert r.get("state") is True
    assert "error" not in r


def test_toggle_rag_off_is_accepted():
    r = asyncio.run(do_ui_control("toggle rag off"))
    assert r.get("toggle_name") == "rag"
    assert r.get("state") is False
    assert "error" not in r


def test_unknown_toggle_still_rejected():
    r = asyncio.run(do_ui_control("toggle bogus on"))
    assert "error" in r


def test_existing_toggle_still_works():
    r = asyncio.run(do_ui_control("toggle web on"))
    assert r.get("toggle_name") == "web" and r.get("state") is True
