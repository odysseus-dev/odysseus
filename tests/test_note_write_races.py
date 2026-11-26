"""Concurrent writes to one note never lose each other's changes.

Every write route reads the note, changes it and commits. Two requests that
read the same version must not both commit blindly: a write with a base gets
a 409. A toggle carries a token too (`body_revision` here): one whose body
changed meanwhile is a 409 that leaves the other write in place, and one
that only lost to a metadata write is re-applied. An append is re-applied
to the newer row. The races
are injected deterministically (a competing commit right after the route
loaded the note), plus one run where the toggle requests really overlap.
"""
import asyncio

import pytest

from tests.helpers.note_routes_client import notes_routes_harness


@pytest.fixture
def notes(monkeypatch, tmp_path):
    with notes_routes_harness(monkeypatch, tmp_path) as harness:
        yield harness


async def test_put_with_a_base_is_refused_when_another_write_lands_after_its_check(notes):
    base = notes.seed("n1", "v0")
    fired = notes.write_between("n1", lambda _c: "bob's text")
    r = await notes.call("PUT", "/api/notes/n1", json={"content": "alice's text", "base_updated_at": base})
    assert fired
    assert r.status_code == 409
    assert r.json()["detail"]["note"]["content"] == "bob's text"
    assert notes.content("n1") == "bob's text"


async def test_a_toggle_over_a_changed_body_is_a_409_that_loses_nothing(notes):
    notes.seed("n1", "- [ ] a\n- [ ] b")
    revision = notes.revision("n1")
    notes.write_between("n1", lambda c: c.replace("- [ ] b", "- [x] b"))
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle", json={"body_revision": revision})
    assert r.status_code == 409
    assert r.json()["detail"]["note"]["content"] == "- [ ] a\n- [x] b"
    assert notes.content("n1") == "- [ ] a\n- [x] b"
    # Read again (the 409 carries the note) and tick: both ticks count.
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle",
                         json={"body_revision": r.json()["detail"]["note"]["body_revision"]})
    assert r.status_code == 200
    assert notes.content("n1") == "- [x] a\n- [x] b"


async def test_a_toggle_that_lost_only_to_a_metadata_write_is_reapplied(notes):
    notes.seed("n1", "- [ ] a\n- [ ] b")
    revision = notes.revision("n1")
    # Same body, newer stamp: the compare-and-swap loses, the body revision still holds.
    fired = notes.write_between("n1", lambda c: c)
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle", json={"body_revision": revision})
    assert fired
    assert r.status_code == 200
    assert notes.content("n1") == "- [x] a\n- [ ] b"


async def test_concurrent_appends_both_land(notes):
    notes.seed("n1", "- [ ] a")
    notes.write_between("n1", lambda c: c + "\n- [ ] from bob")
    r = await notes.call("POST", "/api/notes/n1/items", json={"text": "from alice"})
    assert r.status_code == 200
    texts = [t["text"] for t in r.json()["note"]["items"]]
    assert "from alice" in texts and "from bob" in texts


async def test_toggles_racing_for_real_all_count(notes):
    # Each client reads the token, ticks, and on a 409 reads again and
    # retries, as the Notes UI does: every tick lands, none is lost.
    async def tick(note_id, index, user="alice"):
        statuses = []
        for _ in range(10):
            revision = (await notes.call("GET", f"/api/notes/{note_id}", user=user)).json()["body_revision"]
            r = await notes.call("POST", f"/api/notes/{note_id}/items/{index}/toggle", user=user,
                                 json={"body_revision": revision})
            statuses.append(r.status_code)
            if r.status_code != 409:
                break
        return statuses

    for trial in range(8):
        note_id = f"n{trial}"
        notes.seed(note_id, "- [ ] a\n- [ ] b\n- [ ] c")
        results = await asyncio.gather(tick(note_id, 0), tick(note_id, 1, user="bob"), tick(note_id, 2))
        assert [statuses[-1] for statuses in results] == [200, 200, 200], results
        assert all(set(statuses[:-1]) <= {409} for statuses in results), results
        assert notes.content(note_id) == "- [x] a\n- [x] b\n- [x] c", f"trial {trial}"


async def test_card_item_write_with_a_stale_base_is_a_409(notes):
    base = notes.seed("n1", "- [ ] a\n- [ ] b")
    await notes.call("POST", "/api/notes/n1/items/0/toggle", user="bob", json={"body_revision": notes.revision("n1")})
    r = await notes.call("PUT", "/api/notes/n1", json={
        "items": [{"text": "a", "done": False}, {"text": "b edited", "done": False}],
        "base_updated_at": base,
    })
    assert r.status_code == 409
    assert notes.content("n1") == "- [x] a\n- [ ] b"
