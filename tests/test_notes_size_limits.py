"""A note's title, text and every other string field have a size limit, and
the list's view cache a memory budget.

Without limits a 3 MB body and a 50,000-character title were stored (200),
and the list's derived-view cache held every distinct content it had seen
up to 4096 rows, however long each one was. A write past a limit is refused
with 413 and a message naming the field and the limit; a note already past
it (stored before the limit) can still be edited down.

The tags, colour, reminder and the other short fields had none: a 3 MB
`label` was stored (200) and sent on every list read, collaborators' too.
"""
import routes.note_routes as nr
from core import note_writes
from src.constants import (
    NOTE_CONTENT_MAX_CHARS,
    NOTE_FIELD_MAX_CHARS,
    NOTE_LABEL_MAX_CHARS,
    NOTE_TITLE_MAX_CHARS,
    NOTE_VIEW_CACHE_MAX_CHARS,
    NOTE_VIEW_CACHE_SIZE,
)
from tests.test_notes_sharing import _PEER, _Identity, env  # noqa: F401  (fixture)


def _refused(response, field):
    assert response.status_code == 413, response.text
    detail = response.json()["detail"]
    assert detail["field"] == field
    limit = getattr(note_writes, {
        "title": "NOTE_TITLE_MAX_CHARS",
        "content": "NOTE_CONTENT_MAX_CHARS",
        "label": "NOTE_LABEL_MAX_CHARS",
    }.get(field, "NOTE_FIELD_MAX_CHARS"))
    assert detail["limit"] == limit
    assert f"{detail['limit']:,}" in detail["message"]
    return detail


async def test_a_body_or_title_past_the_limit_is_refused_on_create(env):
    body = "x" * 3_000_000
    detail = _refused(await env.call("POST", "/api/notes", json={"title": "big", "content": body}), "content")
    assert detail["length"] == 3_000_000
    _refused(await env.call("POST", "/api/notes", json={"title": "t" * 50_000, "content": "ok"}), "title")
    assert (await env.call("GET", "/api/notes")).json()["notes"] == []
    at_limit = await env.call("POST", "/api/notes", json={"title": "t" * NOTE_TITLE_MAX_CHARS,
                                                          "content": "x" * NOTE_CONTENT_MAX_CHARS})
    assert at_limit.status_code == 200


async def test_an_update_or_an_added_task_that_grows_past_the_limit_changes_nothing(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_CONTENT_MAX_CHARS", 40)
    monkeypatch.setattr(note_writes, "NOTE_TITLE_MAX_CHARS", 10)
    env.seed("n1", title="Trip", content="- [ ] passport")
    before = env.row("n1").updated_at
    _refused(await env.call("PUT", "/api/notes/n1", json={"body_revision": env.revision("n1"), "content": "y" * 41}), "content")
    _refused(await env.call("PUT", "/api/notes/n1", json={"title": "z" * 11}), "title")
    _refused(await env.call("PUT", "/api/notes/n1", json={"body_revision": env.revision("n1"), "items": [{"text": "w" * 41}]}), "content")
    _refused(await env.call("POST", "/api/notes/n1/items", json={"text": "v" * 30}), "content")
    row = env.row("n1")
    assert (row.title, row.content, row.updated_at) == ("Trip", "- [ ] passport", before)
    ok = await env.call("PUT", "/api/notes/n1", json={"body_revision": env.revision("n1"), "content": "y" * 40, "items": [], "title": "z" * 10})
    assert ok.status_code == 200


async def test_a_note_stored_past_the_limit_can_still_be_ticked_and_edited_down(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_CONTENT_MAX_CHARS", 20)
    long_text = "- [ ] a task that is far past the limit"
    env.seed("old", content=long_text)
    assert (await env.call("POST", "/api/notes/old/items/0/toggle", json={"body_revision": env.revision("old"), "done": True})).status_code == 200
    shorter = "- [x] a task past the limit"
    assert (await env.call("PUT", "/api/notes/old", json={"body_revision": env.revision("old"), "content": shorter})).status_code == 200
    assert env.row("old").content == shorter
    _refused(await env.call("PUT", "/api/notes/old", json={"body_revision": env.revision("old"), "content": shorter + "!"}), "content")


def test_the_agent_tool_refuses_a_note_past_the_limit(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_CONTENT_MAX_CHARS", 20)
    added = env.tool("alice", action="add", title="Big", content="q" * 21)
    assert added["exit_code"] == 1 and "too long" in added["error"]
    assert env.tool("alice", action="list").get("response", "").count("Big") == 0
    env.seed("n1", content="short")
    updated = env.tool("alice", action="update", note_id="n1", content="q" * 21)
    assert updated["exit_code"] == 1 and "Nothing was saved" in updated["error"]
    assert env.row("n1").content == "short"


# Every string field a create or an update can set, with a value past its limit.
_SHORT_FIELDS = ("color", "due_date", "repeat", "note_type", "gallery_id")


async def test_a_3mb_tag_or_any_other_long_field_is_refused_on_create(env):
    detail = _refused(await env.call("POST", "/api/notes", json={"title": "t", "label": "x" * 3_000_000}), "label")
    assert detail["length"] == 3_000_000 and "tags" in detail["message"]
    for field in _SHORT_FIELDS + ("source", "session_id"):
        value = "bg:/api/upload/" + "a" * NOTE_FIELD_MAX_CHARS if field == "color" else "z" * (NOTE_FIELD_MAX_CHARS + 1)
        if field == "color":
            # Colour is checked against its palette before its length.
            assert (await env.call("POST", "/api/notes", json={"color": value})).status_code == 400
            continue
        _refused(await env.call("POST", "/api/notes", json={"title": "t", field: value}), field)
    assert (await env.call("GET", "/api/notes")).json()["notes"] == []
    at_limit = await env.call("POST", "/api/notes", json={
        "title": "t", "label": "l" * NOTE_LABEL_MAX_CHARS, "due_date": "d" * NOTE_FIELD_MAX_CHARS,
        "session_id": "s" * NOTE_FIELD_MAX_CHARS,
    })
    assert at_limit.status_code == 200, at_limit.text


async def test_an_update_that_grows_a_short_field_past_its_limit_changes_nothing(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_LABEL_MAX_CHARS", 12, raising=False)
    monkeypatch.setattr(note_writes, "NOTE_FIELD_MAX_CHARS", 8, raising=False)
    env.seed("n1", title="Trip", content="pack", label="travel", due_date="2026", repeat="none")
    before = env.row("n1").updated_at
    _refused(await env.call("PUT", "/api/notes/n1", json={"label": "travel " * 2}), "label")
    for field in ("due_date", "repeat", "note_type", "gallery_id", "agent_session_id"):
        # `note_type` is a body write, so it carries the token.
        _refused(await env.call("PUT", "/api/notes/n1", json={field: "q" * 9, "body_revision": env.revision("n1")}), field)
    # A long colour is refused too, once the palette lets it through.
    monkeypatch.setattr(note_writes, "NOTE_FIELD_MAX_CHARS", 20, raising=False)
    _refused(await env.call("PUT", "/api/notes/n1", json={"color": "bg:/api/upload/" + "a" * 32}), "color")
    row = env.row("n1")
    assert (row.label, row.due_date, row.repeat, row.color, row.updated_at) == (
        "travel", "2026", "none", None, before)
    ok = await env.call("PUT", "/api/notes/n1", json={"label": "l" * 12, "due_date": "d" * 20})
    assert ok.status_code == 200, ok.text
    assert ok.json()["label"] == "l" * 12


async def test_a_note_with_a_long_tag_stored_before_the_limit_can_still_be_edited(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_LABEL_MAX_CHARS", 5, raising=False)
    env.seed("old", content="text", label="far too long")
    assert (await env.call("PUT", "/api/notes/old", json={"body_revision": env.revision("old"), "content": "new text"})).status_code == 200
    assert (await env.call("PUT", "/api/notes/old", json={"label": "too long"})).status_code == 200
    _refused(await env.call("PUT", "/api/notes/old", json={"label": "too long!"}), "label")
    assert env.row("old").label == "too long"


def test_the_agent_tool_refuses_a_long_tag_or_reminder(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_LABEL_MAX_CHARS", 10, raising=False)
    monkeypatch.setattr(note_writes, "NOTE_FIELD_MAX_CHARS", 10, raising=False)
    added = env.tool("alice", action="add", title="Tagged", content="x", label="t" * 11)
    assert added["exit_code"] == 1 and "tags is too long" in added["error"]
    assert env.tool("alice", action="list").get("response", "").count("Tagged") == 0
    env.seed("n1", content="short", label="ok")
    for field in ("label", "due_date", "note_type"):
        updated = env.tool("alice", action="update", note_id="n1", **{field: "q" * 11})
        assert updated["exit_code"] == 1 and "Nothing was saved" in updated["error"], (field, updated)
    row = env.row("n1")
    assert (row.label, row.due_date, row.note_type) == ("ok", None, "note")


async def test_the_codex_bridge_refuses_a_long_tag(env, monkeypatch):
    import httpx
    from fastapi import FastAPI
    import routes.codex_routes as cr

    class _Token:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope.setdefault("state", {}).update(
                    current_user="api", api_token=True, api_token_scopes=["todos:write"], api_token_owner="alice")
            await self.app(scope, receive, send)

    app = FastAPI()
    app.include_router(cr.setup_codex_routes())
    transport = httpx.ASGITransport(app=_Token(app), client=("203.0.113.7", 54321))
    async with httpx.AsyncClient(transport=transport, base_url="http://codex.test") as c:
        r = await c.post("/api/codex/todos", json={"action": "add", "title": "Big tag",
                                                   "label": "x" * (NOTE_LABEL_MAX_CHARS + 1)})
    assert r.json()["exit_code"] == 1 and "tags is too long" in r.json()["error"]
    assert (await env.call("GET", "/api/notes")).json()["notes"] == []


def test_the_view_cache_stays_within_its_character_budget():
    nr._derived_view.cache_clear()
    try:
        block = 1_000_000
        for i in range(200):
            content = f"{i}\n" + "x" * block
            assert nr._derived_view(content, None, None, "note")[0] == content
        info = nr._derived_view.cache_info()
        assert info["chars"] <= NOTE_VIEW_CACHE_MAX_CHARS
        assert 0 < info["entries"] < 200
        # The most recently read row is still cached; the first one is gone.
        assert (f"199\n" + "x" * block, None, None, "note") in nr._derived_view._entries
        assert (f"0\n" + "x" * block, None, None, "note") not in nr._derived_view._entries
        # A row too large for the whole budget is derived but never kept.
        huge = "h" * (NOTE_VIEW_CACHE_MAX_CHARS + 1)
        assert nr._derived_view(huge, None, None, "note")[0] == huge
        assert (huge, None, None, "note") not in nr._derived_view._entries
        # The row cap still applies to small rows, least recently read first.
        nr._derived_view.cache_clear()
        for i in range(NOTE_VIEW_CACHE_SIZE + 5):
            nr._derived_view(f"- [ ] {i}", None, None, "note")
        nr._derived_view("- [ ] 5", None, None, "note")
        for i in range(3):
            nr._derived_view(f"new {i}", None, None, "note")
        assert nr._derived_view.cache_info()["entries"] == NOTE_VIEW_CACHE_SIZE
        assert ("- [ ] 5", None, None, "note") in nr._derived_view._entries
        assert ("- [ ] 6", None, None, "note") not in nr._derived_view._entries
    finally:
        nr._derived_view.cache_clear()
