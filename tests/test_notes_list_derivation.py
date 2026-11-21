"""The list derives each note's content, items and type once per stored row.

`_note_to_dict` folds legacy columns into content and parses the task lines
for every note on every list read. That derivation is a pure function of the
stored columns, so an unchanged row is not derived again on the next read.
"""
import json

import httpx

import routes.note_routes as nr
from tests.test_notes_sharing import _PEER, _Identity, env  # noqa: F401  (fixture)


async def _list(env, user="alice"):
    transport = httpx.ASGITransport(app=_Identity(env.app), client=_PEER)
    async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
        return await c.get("/api/notes", headers={"x-test-user": user})


def test_cached_derivation_matches_a_fresh_one_and_follows_content(env):
    content = "Intro\n- [ ] a\n  - [x] b\n![](/api/upload/" + "d" * 32 + ".png)"
    env.seed("n1", content=content, note_type="checklist")
    # `image_url` is the stored legacy attachment record, not derived from content.
    env.seed("d1", content="sketch\n![](/api/upload/" + "e" * 32 + ".png)", note_type="draw",
             image_url="/api/upload/" + "e" * 32 + ".png")
    env.seed("legacy", content="old", items=json.dumps([{"text": "milk", "done": True}]))
    for note_id in ("n1", "d1", "legacy"):
        note = env.row(note_id)
        payload = nr._note_to_dict(note, "alice", [])
        nr._derived_view.cache_clear()
        fresh = nr._note_to_dict(note, "alice", [])
        assert payload == fresh
    assert nr._note_to_dict(env.row("n1"), "alice", [])["items"] == [
        {"index": 0, "text": "a", "done": False, "indent": 0},
        {"index": 1, "text": "b", "done": True, "indent": 1},
    ]
    assert nr._note_to_dict(env.row("d1"), "alice", [])["image_url"] == "/api/upload/" + "e" * 32 + ".png"
    # A changed row derives again: the cache is keyed by the stored columns.
    note = env.row("n1")
    note.content = "- [x] a"
    assert nr._note_to_dict(note, "alice", [])["items"] == [{"index": 0, "text": "a", "done": True, "indent": 0}]
    # Each payload owns its items list: mutating one never leaks into the next.
    first = nr._note_to_dict(note, "alice", [])
    first["items"][0]["text"] = "changed"
    assert nr._note_to_dict(note, "alice", [])["items"][0]["text"] == "a"


async def test_a_second_read_does_not_parse_unchanged_notes_again(env, monkeypatch):
    env.seed("n1", content="- [ ] a\n- [ ] b")
    env.seed("n2", content="plain")
    env.seed("n3", content="- [x] c")
    clear = getattr(getattr(nr, "_derived_view", None), "cache_clear", None)
    if clear:
        clear()
    calls = []
    real = nr.parse_task_lines
    monkeypatch.setattr(nr, "parse_task_lines", lambda content: calls.append(content) or real(content))
    assert (await _list(env)).status_code == 200
    first = len(calls)
    assert first >= 3
    assert (await _list(env)).status_code == 200
    assert len(calls) == first, "unchanged rows are not parsed again"
    revision = (await env.call("GET", "/api/notes/n1")).json()["body_revision"]
    calls.clear()
    assert (await env.call("PUT", "/api/notes/n1", json={"content": "- [x] a", "body_revision": revision})).status_code == 200
    listed = await _list(env)
    assert [n["items"] for n in listed.json()["notes"] if n["id"] == "n1"] == [[{"index": 0, "text": "a", "done": True, "indent": 0}]]
    assert set(calls) <= {"- [x] a"}, "only the edited row is derived again"
