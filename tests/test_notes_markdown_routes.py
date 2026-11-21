"""Notes API, tool and reminder paths over the markdown content model.

Checklists and images render from Markdown, and the legacy image pointer
keeps the independent attachment contract for old writers.
Driven through httpx.ASGITransport for the reasons given in
tests/test_notes_fail_closed_auth.py.
"""
import asyncio
import datetime
import json
from types import SimpleNamespace
from threading import Event

import httpx
import pytest
from fastapi import FastAPI

import core.database as cdb
from core.database import Note
import routes.note_routes as nr
from tests.helpers.database import disposable_database

_PEER = ("203.0.113.7", 54321)


class _Identity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            user = dict(scope.get("headers") or []).get(b"x-test-user")
            if user:
                scope.setdefault("state", {})["current_user"] = user.decode()
        await self.app(scope, receive, send)


@pytest.fixture
def notes(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(nr, "SessionLocal", factory)
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        # Upload reservation is covered elsewhere; these notes cite fake ids.
        monkeypatch.setattr(nr, "reserve_upload_references", lambda *a, **kw: None)
        app = FastAPI()
        app.state.auth_manager = SimpleNamespace(is_configured=True)
        app.include_router(nr.setup_note_routes())

        def seed(note_id, owner="alice", **fields):
            with factory() as db:
                db.add(Note(id=note_id, owner=owner, title=fields.pop("title", note_id), **fields))
                db.commit()

        def row(note_id):
            with factory() as db:
                return db.get(Note, note_id)

        async def call(method, url, user="alice", **kw):
            transport = httpx.ASGITransport(app=_Identity(app), client=_PEER)
            async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
                return await c.request(method, url, headers={"x-test-user": user}, **kw)

        yield SimpleNamespace(seed=seed, row=row, call=call, factory=factory)


async def test_create_writes_items_and_image_into_content(notes):
    r = await notes.call("POST", "/api/notes", json={
        "title": "Errands", "note_type": "todo", "content": "",
        "items": [{"text": "Post office", "done": False}, {"text": "Bank", "done": True, "indent": 1}],
        "image_url": "/api/upload/receipt",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "- [ ] Post office\n  - [x] Bank\n\n![](/api/upload/receipt)"
    assert body["items"] == [
        {"index": 0, "text": "Post office", "done": False, "indent": 0},
        {"index": 1, "text": "Bank", "done": True, "indent": 1},
    ]
    assert body["note_type"] == "note"  # tasks plus an image: mixed
    assert body["image_url"] == "/api/upload/receipt"
    stored = notes.row(body["id"])
    assert stored.items is None and stored.image_url == body["image_url"]


async def test_read_derives_items_and_type_from_content(notes):
    notes.seed("pure", content="- [ ] a\n\n- [x] b", note_type="note")
    notes.seed("mixed", content="Intro\n- [ ] a", note_type="todo")
    notes.seed("goal", content="Learn\n\n- [ ] step", note_type="goal")
    notes.seed("legacy", content="Hidden body", items='[{"text": "old", "checked": true}]', note_type="todo")
    listed = {n["id"]: n for n in (await notes.call("GET", "/api/notes")).json()["notes"]}
    assert listed["pure"]["note_type"] == "checklist"
    assert [i["text"] for i in listed["pure"]["items"]] == ["a", "b"]
    assert listed["mixed"]["note_type"] == "note"
    assert listed["goal"]["note_type"] == "goal"
    # A row the migration skipped still shows its checklist.
    assert listed["legacy"]["content"] == "Hidden body\n\n- [x] old"
    assert listed["legacy"]["items"] == [{"index": 0, "text": "old", "done": True, "indent": 0}]
    plain = (await notes.call("POST", "/api/notes", json={"title": "t", "content": "just text"})).json()
    assert plain["items"] is None and plain["note_type"] == "note"


async def test_put_items_rewrites_task_lines_in_place(notes):
    notes.seed("n", content="Shopping\n- [ ] milk\nnot a task\n- [ ] eggs\n```\n- [ ] sample\n```\n- [ ] jam")
    r = await notes.call("PUT", "/api/notes/n", json={"items": [{"text": "milk", "done": True}, {"text": "bread"}]})
    assert r.status_code == 200
    assert r.json()["content"] == "Shopping\n- [x] milk\nnot a task\n- [ ] bread\n```\n- [ ] sample\n```"
    assert notes.row("n").items is None


async def test_put_on_unmigrated_row_folds_items_first(notes):
    notes.seed("n", content="Body", items='[{"text": "a"}, {"text": "b"}]')
    r = await notes.call("PUT", "/api/notes/n", json={"items": [{"text": "a", "done": True}, {"text": "b"}]})
    assert r.json()["content"] == "Body\n\n- [x] a\n- [ ] b"
    assert notes.row("n").items is None


async def test_put_image_url_folds_and_drawings_replace_their_canvas(notes):
    notes.seed("photo", content="Receipt")
    r = await notes.call("PUT", "/api/notes/photo", json={"image_url": "/api/upload/p1"})
    assert r.json()["content"] == "Receipt\n\n![](/api/upload/p1)"
    assert r.json()["image_url"] == "/api/upload/p1"

    original = "[Canvas link](/api/upload/d1)\n\n```\n![](/api/upload/d1)\n```\n\n"
    notes.seed("draw", content=original + "![](/api/upload/d1)", image_url="/api/upload/d1", note_type="draw", gallery_id="g1")
    got = (await notes.call("GET", "/api/notes/draw")).json()
    assert got["image_url"] == "/api/upload/d1" and got["note_type"] == "draw"
    r = await notes.call("PUT", "/api/notes/draw", json={"note_type": "draw", "image_url": "/api/upload/d2", "gallery_id": "g1"})
    assert r.json()["content"] == original + "![](/api/upload/d2)"
    assert r.json()["image_url"] == "/api/upload/d2"
    assert notes.row("draw").gallery_id == "g1"



async def test_put_image_url_replaces_removes_and_never_stacks(notes):
    notes.seed("photo", content="Receipt")
    for _ in range(3):
        r = await notes.call("PUT", "/api/notes/photo", json={"image_url": "/api/upload/p1"})
        assert r.status_code == 200, r.text
    assert notes.row("photo").content == "Receipt\n\n![](/api/upload/p1)"

    # A new URL replaces the note's legacy image; inline images are left alone.
    notes.seed("mixed", content="Intro ![x](/api/upload/inline) text\n\n![](/api/upload/old)", image_url="/api/upload/old")
    r = await notes.call("PUT", "/api/notes/mixed", json={"image_url": "/api/upload/new"})
    assert r.json()["content"] == "Intro ![x](/api/upload/inline) text\n\n![](/api/upload/new)"
    # An empty string removes it, and only it.
    r = await notes.call("PUT", "/api/notes/mixed", json={"image_url": ""})
    assert r.json()["content"] == "Intro ![x](/api/upload/inline) text"
    r = await notes.call("PUT", "/api/notes/mixed", json={"image_url": ""})
    assert r.json()["content"] == "Intro ![x](/api/upload/inline) text"


@pytest.mark.parametrize("url", [
    "https://example.org/a b.png",
    "https://example.org/a(1).png",
    'https://example.org/a.png "a title"',
])
async def test_put_image_url_with_spaces_or_parentheses_does_not_double(notes, url):
    notes.seed("n", content="Body")
    for _ in range(3):
        assert (await notes.call("PUT", "/api/notes/n", json={"image_url": url})).status_code == 200
    content = notes.row("n").content
    assert content.count("![](") == 1, content
    assert "(" not in content.split("](", 1)[1].rstrip(")"), content


async def test_put_items_with_an_empty_text_removes_that_task(notes):
    # An item whose text is blank removes its task line; the others keep
    # their own lines, so what reads back is the array minus the blank one.
    notes.seed("n", content="- [ ] a\n- [x] b\n- [ ] c")
    r = await notes.call("PUT", "/api/notes/n", json={"items": [{"text": "a"}, {"text": "  "}, {"text": "c", "done": True}]})
    assert r.status_code == 200
    assert r.json()["content"] == "- [ ] a\n- [x] c"
    assert [(i["index"], i["text"], i["done"]) for i in r.json()["items"]] == [(0, "a", False), (1, "c", True)]


async def test_editing_a_goal_keeps_it_a_goal(notes):
    # A goal is a stored mode: its pure task list would otherwise read as a
    # checklist, and prose would read as a note.
    notes.seed("goal", content="- [x] Warm up\n- [ ] Run 5k", note_type="goal")
    r = await notes.call("PUT", "/api/notes/goal", json={"content": "- [x] Warm up\n- [ ] Run 5k\n- [ ] Stretch"})
    assert r.status_code == 200
    assert r.json()["note_type"] == "goal"
    r = await notes.call("PUT", "/api/notes/goal", json={"content": "Training plan\n\n- [ ] Run 5k"})
    assert r.json()["note_type"] == "goal"
    assert notes.row("goal").note_type == "goal"


async def test_unmigrated_drawing_reads_its_image_url_column(notes):
    # A drawing row the boot migration did not reach (non-SQLite DATABASE_URL,
    # or a skipped row) still reports its canvas from the legacy column.
    notes.seed("draw", content=None, note_type="draw", image_url="/api/upload/canvas", gallery_id="g1")
    got = (await notes.call("GET", "/api/notes/draw")).json()
    assert got["image_url"] == "/api/upload/canvas"
    assert got["note_type"] == "draw" and got["gallery_id"] == "g1"


async def test_unmigrated_checklist_reads_as_task_lines_without_a_migration(notes):
    # The readers do not depend on the SQLite boot migration: legacy `items`
    # JSON is folded on read, and nothing is written back by reading.
    notes.seed("old", content="Weekend", items='[{"text": "tent", "done": true}, {"text": "stove"}]', note_type="todo")
    got = (await notes.call("GET", "/api/notes/old")).json()
    assert got["content"] == "Weekend\n\n- [x] tent\n- [ ] stove"
    assert [(i["text"], i["done"]) for i in got["items"]] == [("tent", True), ("stove", False)]
    assert got["note_type"] == "note"
    stored = notes.row("old")
    assert stored.content == "Weekend" and stored.items is not None


async def test_unmigrated_photo_note_shows_its_image_and_a_save_keeps_one(notes):
    # Without the boot migration the legacy `image_url` column is folded on
    # read, as the migration would store it, so the editor shows the
    # image and a save of what it shows keeps exactly one copy.
    notes.seed("photo", content="Receipt", image_url="/api/upload/receipt")
    got = (await notes.call("GET", "/api/notes/photo")).json()
    assert got["content"] == "Receipt\n\n![](/api/upload/receipt)"
    r = await notes.call("PUT", "/api/notes/photo", json={"content": "Receipt edited by a legacy client"})
    assert r.status_code == 200, r.text
    again = (await notes.call("GET", "/api/notes/photo")).json()
    assert again["content"] == "Receipt edited by a legacy client\n\n![](/api/upload/receipt)"


async def test_migrated_photo_put_preserves_replaces_and_removes_attachment(notes, monkeypatch):
    url = "/api/upload/old"
    notes.seed("photo", content="Receipt", image_url=url)
    monkeypatch.setattr(cdb, "DATABASE_URL", str(notes.factory.kw["bind"].url))
    cdb._migrate_fold_image_url()
    assert notes.row("photo").content == f"Receipt\n\n![]({url})"

    path = "/api/notes/photo"
    updated = await notes.call("PUT", path, json={"content": "Updated prose"})
    assert updated.json()["image_url"] == url
    assert notes.row("photo").content == f"Updated prose\n\n![]({url})"

    new_url = "/api/upload/new"
    await notes.call("PUT", path, json={"image_url": new_url})
    assert notes.row("photo").content == f"Updated prose\n\n![]({new_url})"
    await notes.call("PUT", path, json={"image_url": None})
    assert notes.row("photo").content == "Updated prose"
    assert notes.row("photo").image_url is None

    # Current editor sends the complete document and its observed revision.
    before = (await notes.call("PUT", path, json={"image_url": new_url})).json()
    unrelated = "Final edit\n\n![](/api/upload/ordinary-markdown)"
    updated = await notes.call("PUT", path, json={
        "content": unrelated, "replace_content": True, "body_revision": before["body_revision"],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["image_url"] is None
    cdb._migrate_fold_image_url()
    assert notes.row("photo").content == unrelated


async def test_clear_removes_only_standalone_legacy_image_with_alt_text(notes, monkeypatch):
    url = "/api/upload/receipt"
    notes.seed(
        "photo",
        content=f"Inline ![detail]({url})\n\n![Receipt]({url})\n\n[link]({url})\n\n```\n![code]({url})\n```",
        image_url=url,
    )
    monkeypatch.setattr(cdb, "DATABASE_URL", str(notes.factory.kw["bind"].url))
    cdb._migrate_fold_image_url()
    assert notes.row("photo").content.count(f"![Receipt]({url})") == 1
    cleared = await notes.call("PUT", "/api/notes/photo", json={"image_url": None})
    assert cleared.status_code == 200, cleared.text
    expected = f"Inline ![detail]({url})\n\n[link]({url})\n\n```\n![code]({url})\n```"
    assert cleared.json()["content"] == expected and cleared.json()["image_url"] is None
    cdb._migrate_fold_image_url()
    readback = (await notes.call("GET", "/api/notes/photo")).json()
    assert readback["content"] == expected and readback["image_url"] is None


async def test_full_replacement_with_only_an_inline_copy_clears_legacy_pointer(notes):
    url = "/api/upload/receipt"
    notes.seed("photo", content=f"![Receipt]({url})", image_url=url)
    content = f"Detail ![Receipt]({url})"
    result = await notes.call("PUT", "/api/notes/photo", json={"content": content, "replace_content": True})
    assert result.status_code == 200, result.text
    assert result.json()["content"] == content and result.json()["image_url"] is None


async def test_toggle_route_flips_the_indexed_task_line(notes):
    notes.seed("n", content="```\n- [ ] sample\n```\n- [ ] real\n  * [x] nested")
    revision = (await notes.call("GET", "/api/notes/n")).json()["body_revision"]
    assert (await notes.call("POST", "/api/notes/n/items/1/toggle")).status_code == 409
    r = await notes.call("POST", "/api/notes/n/items/1/toggle", json={"body_revision": revision})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["item"]["text"] == "nested" and data["item"]["done"] is False
    assert data["note"]["content"] == "```\n- [ ] sample\n```\n- [ ] real\n  * [ ] nested"
    assert [i["done"] for i in data["items"]] == [False, False]
    assert (await notes.call("POST", "/api/notes/n/items/2/toggle", json={"body_revision": data["note"]["body_revision"]})).status_code == 400
    assert (await notes.call("POST", "/api/notes/n/items/0/toggle", user="bob")).status_code == 404
    notes.seed("plain", content="no tasks")
    plain_revision = (await notes.call("GET", "/api/notes/plain")).json()["body_revision"]
    assert (await notes.call("POST", "/api/notes/plain/items/0/toggle", json={"body_revision": plain_revision})).status_code == 400


def _pause_first_guard(monkeypatch, module=nr):
    entered, release = Event(), Event()
    sessions = []
    original = module.guard_note_body

    def guard(session, note):
        sessions.append(session)
        if len(sessions) == 1:
            entered.set()
            assert release.wait(10), "Competing request did not finish"
        return original(session, note)

    monkeypatch.setattr(module, "guard_note_body", guard)
    return entered, release, sessions


async def test_overlapping_appends_retry_the_stale_read(notes, monkeypatch):
    notes.seed("n", content="- [ ] first\n\nFooter")
    entered, release, sessions = _pause_first_guard(monkeypatch)
    path = "/api/notes/n/items"
    first = asyncio.create_task(notes.call("POST", path, json={"text": "paused"}))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        second = await notes.call("POST", path, json={"text": "committed first"})
        assert second.status_code == 200, second.text
    finally:
        release.set()
    response = await first
    assert response.status_code == 200, response.text
    assert len(sessions) == 3 and sessions[0] is sessions[2] and sessions[0] is not sessions[1]
    assert notes.row("n").content == "- [ ] first\n- [ ] committed first\n- [ ] paused\n\nFooter"


async def test_toggle_conflicts_with_reorder_during_and_before_request(notes, monkeypatch):
    notes.seed("n", content="- [ ] A\n- [ ] B")
    path = "/api/notes/n"
    initial = (await notes.call("GET", path)).json()
    body = {"body_revision": initial["body_revision"]}
    toggle_path = path + "/items/0/toggle"
    entered, release, sessions = _pause_first_guard(monkeypatch)
    toggle = asyncio.create_task(notes.call("POST", toggle_path, json=body))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        reorder = await notes.call("PUT", path, json={**body, "items": [{"text": "B"}, {"text": "A"}]})
        assert reorder.status_code == 200, reorder.text
    finally:
        release.set()
    assert (await toggle).status_code == 409
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert (await notes.call("POST", toggle_path, json=body)).status_code == 409
    assert notes.row("n").content == "- [ ] B\n- [ ] A"


async def test_agent_update_conflicts_with_rest_append(notes, monkeypatch):
    from src.tools import notes as agent_notes

    notes.seed("shared", content="- [ ] A", title="Original")
    entered, release, sessions = _pause_first_guard(monkeypatch, agent_notes)
    update = asyncio.create_task(asyncio.to_thread(_manage, {
        "action": "update", "id": "shared", "title": "Later title", "items": [{"text": "B"}],
    }))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        append = await notes.call("POST", "/api/notes/shared/items", json={"text": "Appended"})
        assert append.status_code == 200, append.text
    finally:
        release.set()
    assert (await update)["conflict"] is True
    assert len(sessions) == 1
    saved = notes.row("shared")
    assert saved.content == "- [ ] A\n- [ ] Appended"
    assert saved.title == "Original"


async def test_append_route_adds_one_task_after_the_last(notes):
    notes.seed("n", content="- [ ] a\n\nFooter")
    r = await notes.call("POST", "/api/notes/n/items", json={"text": "  b\n  c ", "indent": 1})
    assert r.status_code == 200
    assert r.json()["note"]["content"] == "- [ ] a\n  - [ ] b c\n\nFooter"
    assert r.json()["item"] == {"index": 1, "text": "b c", "done": False, "indent": 1, "line": 1}
    assert (await notes.call("POST", "/api/notes/n/items", json={"text": "   "})).status_code == 400
    assert (await notes.call("POST", "/api/notes/n/items", json={"text": "x"}, user="bob")).status_code == 404
    assert notes.row("n").content == "- [ ] a\n  - [ ] b c\n\nFooter"


async def test_append_route_closes_an_unclosed_fence_first(notes):
    notes.seed("n", content="notes\n```\ncode")
    r = await notes.call("POST", "/api/notes/n/items", json={"text": "buy milk"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["text"] == "buy milk"
    assert notes.row("n").content == "notes\n```\ncode\n```\n\n- [ ] buy milk"
    assert [i["text"] for i in (await notes.call("GET", "/api/notes/n")).json()["items"]] == ["buy milk"]


async def test_put_items_closes_an_unclosed_fence_first(notes):
    notes.seed("n", content="notes\n```\ncode")
    r = await notes.call("PUT", "/api/notes/n", json={"items": [{"text": "buy milk"}]})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "notes\n```\ncode\n```\n\n- [ ] buy milk"
    assert [i["text"] for i in r.json()["items"]] == ["buy milk"]


async def test_ping_scanner_reminder_lists_pending_task_lines(notes, monkeypatch, tmp_path):
    from src import builtin_actions, settings

    instant = datetime.datetime(2026, 10, 7, 12, tzinfo=datetime.timezone.utc)

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    sent = []

    async def capture(**kw):
        sent.append(kw)
        return {"ok": True, "browser": True}

    monkeypatch.setattr(settings, "load_settings", lambda: {"reminder_channel": "browser"})
    monkeypatch.setattr(builtin_actions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nr, "dispatch_reminder", capture)
    monkeypatch.setattr(datetime, "datetime", Clock)
    notes.seed("n", title="Groceries", due_date=instant.isoformat(),
               content="Before 6pm\n- [x] milk\n- [ ] eggs\n```\n- [ ] sample\n```")
    try:
        await builtin_actions.action_ping_notes("alice")
    except builtin_actions.TaskNoop:
        pass
    assert [c["note_body"] for c in sent] == ["Before 6pm\n```\n- [ ] sample\n```\n\nPending (1):\n- eggs"]


def test_selected_notes_context_reads_task_lines():
    from routes.chat_routes import _format_selected_notes_context

    note = SimpleNamespace(title="Trip", note_type="todo", content="Pack\n- [ ] passport\n- [x] charger", items=None)
    text = _format_selected_notes_context([note])
    assert "Type: note" in text
    assert "Content:\nPack\n- [ ] passport\n- [x] charger" in text
    assert "Items:\n- [pending] passport\n- [done] charger" in text


def test_selected_notes_context_folds_an_unmigrated_image():
    from routes.chat_routes import _format_selected_notes_context

    note = SimpleNamespace(title="Photo", note_type="note", content="Receipt", items=None, image_url="/api/upload/r1")
    assert "Content:\nReceipt\n\n![](/api/upload/r1)" in _format_selected_notes_context([note])


def test_reminder_text_folds_unmigrated_columns():
    note = SimpleNamespace(title="Trip", content="Pack", items='[{"text": "tent"}]', image_url="/api/upload/r1")
    title, body = nr._reminder_text_from_note(note)
    assert title == "Trip"
    assert "Pack" in body and "- tent" in body


def test_calendar_reminder_writer_uses_task_lines(notes):
    from routes.calendar_routes import _create_calendar_reminder_for_event

    ev = SimpleNamespace(
        summary="Dentist", location="Main St", all_day=False, is_utc=False,
        dtstart=datetime.datetime.now() + datetime.timedelta(days=2),
    )
    with notes.factory() as db:
        out = _create_calendar_reminder_for_event(db, "alice", ev, 30)
        db.commit()
    stored = notes.row(out["note_id"])
    assert stored.items is None
    assert stored.content.startswith("- [ ] Dentist @ Main St — ")


def test_calendar_tool_reminder_writer_uses_task_lines(notes):
    from src.tools.calendar import do_manage_calendar

    out = asyncio.run(do_manage_calendar(json.dumps({
        "action": "create_event", "summary": "Dentist", "location": "Main St",
        "dtstart": "2030-01-01T10:00:00", "reminder_minutes": 30,
    }), owner="alice"))
    assert out.get("exit_code") == 0, out
    stored = notes.row(out["reminder_note_id"])
    assert stored.items is None
    assert stored.content.startswith("- [ ] Dentist @ Main St — ")
    listed = asyncio.run(notes.call("GET", f"/api/notes/{stored.id}")).json()
    assert [i["text"] for i in listed["items"]] == [stored.content[len("- [ ] "):]]
    assert listed["note_type"] == "checklist"


def test_daily_brief_lists_open_tasks_from_any_note_with_task_lines(notes, monkeypatch):
    import core.auth as auth_mod
    import routes.email_helpers as email_helpers
    from src.builtin_actions import action_daily_brief

    def no_imap(*args, **kwargs):
        raise OSError("no mail in this test")

    monkeypatch.setattr(email_helpers, "_imap_connect", no_imap)
    monkeypatch.setattr(auth_mod, "AuthManager", lambda: SimpleNamespace(is_configured=True))
    # It used to read only `items` JSON on `note_type == "checklist"`.
    notes.seed("mixed", title="Trip", content="Lisbon in May\n- [ ] book flights\n- [x] passport", note_type="note")
    notes.seed("legacy", title="Errands", content="", items='[{"text": "post office"}]', note_type="todo")
    notes.seed("done", title="Done list", content="- [x] finished", note_type="checklist")
    notes.seed("pinned", title="Pinned idea", content="just prose", pinned=True)
    notes.seed("theirs", owner="bob", title="Bob", content="- [ ] not alice's")
    # The calendar's reminder notes repeat the Calendar section: never todos.
    notes.seed("cal", title="Calendar reminder: Dentist", content="- [ ] Dentist @ Main St",
               note_type="todo", label="calendar", source="calendar")
    notes.seed("cal-legacy", title="Calendar reminder: Gym", content="- [ ] Gym",
               note_type="todo", label="calendar")

    text, ok = asyncio.run(action_daily_brief("alice"))

    assert ok, text
    todos = text.split("Todos:", 1)[1]
    assert "Trip: book flights" in todos
    assert "passport" not in todos
    assert "Errands: post office" in todos
    assert "Done list" not in todos
    assert "Pinned idea" in todos
    assert "not alice's" not in todos
    assert "Dentist" not in todos and "Gym" not in todos

def _manage(args, owner="alice"):
    from src.tools.notes import do_manage_notes
    return asyncio.run(do_manage_notes(json.dumps(args), owner=owner))


def _toggle(note_id, index, **fields):
    opened = _manage({"action": "view", "id": note_id})
    return _manage({"action": "toggle_item", "id": note_id, "index": index,
                    "body_revision": opened["body_revision"], **fields})


def test_manage_notes_get_list_and_toggle_use_content(notes):
    notes.seed("abc12345-note", title="Packing", content="For Lisbon\n- [ ] passport\n- [x] charger")
    for action in ("get", "view", "read", "show", "open"):
        out = _manage({"action": action, "id": "abc12345"})
        assert out["exit_code"] == 0, out
        assert out["results"].splitlines()[1:] == [f'  Body revision: {out["body_revision"]}', "  For Lisbon", "  [ ] 0: passport", "  [x] 1: charger"]
    assert "passport" not in _manage({"action": "list"})["results"]  # list stays a locator

    out = _toggle("abc12345", 0)
    assert out["response"] == "Item 'passport' marked done"
    assert notes.row("abc12345-note").content == "For Lisbon\n- [x] passport\n- [x] charger"
    out = _toggle("abc12345", 0, done=True)
    assert out["response"] == "Item 'passport' marked done"
    assert notes.row("abc12345-note").content == "For Lisbon\n- [x] passport\n- [x] charger"
    assert _toggle("abc12345", 5)["exit_code"] == 1
    assert _manage({"action": "get", "id": "abc12345"}, owner="bob")["exit_code"] == 1


def test_manage_notes_add_and_update_write_task_lines(notes):
    out = _manage({"action": "add", "title": "Chores", "note_type": "checklist",
                   "checklist_items": [{"text": "Dishes", "done": False}, {"text": "Laundry", "done": True}]})
    assert out["exit_code"] == 0, out
    stored = notes.row(out["note_id"])
    assert stored.items is None
    assert stored.content == "- [ ] Dishes\n- [x] Laundry"
    again = _manage({"action": "add", "title": "Chores", "note_type": "checklist",
                     "checklist_items": [{"text": "Dishes", "done": False}, {"text": "Laundry", "done": True}]})
    assert again.get("duplicate") is True

    out = _manage({"action": "update", "id": stored.id, "checklist_items": [{"text": "Dishes", "done": True}]})
    assert out["exit_code"] == 0, out
    assert notes.row(stored.id).content == "- [x] Dishes"


def test_manage_notes_view_folds_an_unmigrated_image(notes):
    notes.seed("img12345-note", title="Photo", content="Receipt", image_url="/api/upload/r1")
    out = _manage({"action": "view", "id": "img12345"})
    assert out["exit_code"] == 0, out
    assert "![](/api/upload/r1)" in out["results"]


def test_manage_notes_writes_fold_legacy_columns_and_keep_unreadable_items(notes):
    notes.seed("old12345-note", title="Old", content="Body", items='[{"text": "tent"}]', image_url="/api/upload/r1")
    out = _manage({"action": "update", "id": "old12345", "title": "Old trip"})
    assert out["exit_code"] == 0, out
    row = notes.row("old12345-note")
    assert row.items is not None, "a title-only write leaves the legacy columns alone"
    out = _toggle("old12345", 0)
    assert out["exit_code"] == 0, out
    row = notes.row("old12345-note")
    assert row.content == "Body\n\n- [x] tent\n\n![](/api/upload/r1)"
    assert row.items is None and row.image_url == "/api/upload/r1"

    # Unreadable legacy items stay for the migration to retry, on both writes.
    notes.seed("bad12345-note", title="Bad", content="- [ ] a", items='{"text": "x"}')
    assert _toggle("bad12345", 0)["exit_code"] == 0
    assert notes.row("bad12345-note").items == '{"text": "x"}'
    assert _manage({"action": "update", "id": "bad12345", "content": "- [x] a\nmore"})["exit_code"] == 0
    row = notes.row("bad12345-note")
    assert row.items == '{"text": "x"}' and row.content == "- [x] a\nmore"
