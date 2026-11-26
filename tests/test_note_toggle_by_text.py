"""A tick names the task it was made on.

A toggle carries a token (`body_revision` or `base_updated_at`); without
one, or with a stale one, it is a 409, since an index read from another
version can name another task. With the task's text in the request as well,
the server flips the task the person actually clicked when the index has
drifted, or answers 409 with the current note when that task is gone or no
longer unique.
"""
import pytest

from tests.helpers.note_routes_client import notes_routes_harness


@pytest.fixture
def notes(monkeypatch, tmp_path):
    with notes_routes_harness(monkeypatch, tmp_path) as harness:
        yield harness


async def test_toggle_follows_its_task_when_a_line_was_inserted_above(notes):
    base = notes.seed("n1", "- [ ] a\n- [ ] b")
    r = await notes.call("PUT", "/api/notes/n1", user="bob",
                         json={"content": "- [ ] new\n- [ ] a\n- [ ] b", "base_updated_at": base})
    assert r.status_code == 200
    # A token from before the insert is stale.
    r = await notes.call("POST", "/api/notes/n1/items/1/toggle", json={"text": "b", "base_updated_at": base})
    assert r.status_code == 409
    assert notes.content("n1") == "- [ ] new\n- [ ] a\n- [ ] b"
    # With a current token, an index that drifted still flips the task named by its text.
    r = await notes.call("POST", "/api/notes/n1/items/1/toggle",
                         json={"text": "b", "body_revision": notes.revision("n1")})
    assert r.status_code == 200
    assert r.json()["item"]["text"] == "b"
    assert notes.content("n1") == "- [ ] new\n- [ ] a\n- [x] b"


async def test_toggle_of_a_task_that_is_gone_or_ambiguous_is_a_409(notes):
    notes.seed("n1", "- [ ] a\n- [ ] b")
    r = await notes.call("POST", "/api/notes/n1/items/1/toggle",
                         json={"text": "renamed", "body_revision": notes.revision("n1")})
    assert r.status_code == 409
    assert r.json()["detail"]["note"]["content"] == "- [ ] a\n- [ ] b"
    notes.seed("n2", "- [ ] x\n- [ ] dup\n- [ ] dup")
    r = await notes.call("POST", "/api/notes/n2/items/0/toggle",
                         json={"text": "dup", "body_revision": notes.revision("n2")})
    assert r.status_code == 409
    assert notes.content("n2") == "- [ ] x\n- [ ] dup\n- [ ] dup"


async def test_toggle_without_a_token_is_a_409(notes):
    notes.seed("n1", "- [ ] a\n- [ ] b")
    for kw in ({}, {"json": {}}, {"json": {"text": "b"}}):
        r = await notes.call("POST", "/api/notes/n1/items/1/toggle", **kw)
        assert r.status_code == 409
        assert r.json()["detail"]["note"]["content"] == "- [ ] a\n- [ ] b"
    assert notes.content("n1") == "- [ ] a\n- [ ] b"
    r = await notes.call("POST", "/api/notes/n1/items/1/toggle", json={"body_revision": notes.revision("n1")})
    assert r.status_code == 200
    assert notes.content("n1") == "- [ ] a\n- [x] b"


async def test_toggle_with_a_stale_base_is_a_409(notes):
    base = notes.seed("n1", "- [ ] a")
    assert (await notes.call("PUT", "/api/notes/n1", json={"title": "renamed"})).status_code == 200
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle", json={"text": "a", "base_updated_at": base})
    assert r.status_code == 409
    assert notes.content("n1") == "- [ ] a"


async def test_toggle_with_done_sets_the_state_instead_of_flipping(notes):
    # Another device ticked "milk"; this view asks for it done. (The view
    # has a current token: a stale one would be a 409.)
    notes.seed("n1", "- [ ] milk\n- [ ] eggs")
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle", json={"text": "milk", "body_revision": notes.revision("n1")})
    assert r.status_code == 200
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle", json={"text": "milk", "done": True, "body_revision": notes.revision("n1")})
    assert r.status_code == 200
    assert r.json()["item"]["done"] is True
    assert notes.content("n1") == "- [x] milk\n- [ ] eggs"
    # And the other way round: an untick of a task already open stays open.
    r = await notes.call("POST", "/api/notes/n1/items/1/toggle", json={"text": "eggs", "done": False, "body_revision": notes.revision("n1")})
    assert r.status_code == 200
    assert notes.content("n1") == "- [x] milk\n- [ ] eggs"
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle", json={"text": "milk", "done": False, "body_revision": notes.revision("n1")})
    assert notes.content("n1") == "- [ ] milk\n- [ ] eggs"
