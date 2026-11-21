"""A note's version history: what keeps a version, the cap, and the owner's API.

See core.note_versions. manage_notes runs as the agent would (with and
without a chat started from the note); the notes API runs over a real
database, as in test_note_agent_edit_scope.
"""
import asyncio
import datetime
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import core.database as cdb
import core.note_agent_scope as scope_mod
import core.note_versions as versions_mod
import routes.note_routes as nr
from core.database import Note, NoteShare, NoteVersion, Session as DbSession
from core.note_writes import note_body_revision
from core.note_agent_scope import bind_note_edit_scope, load_note_edit_scope, unbind_note_edit_scope
from src.agent_runtime.authority import RequestAuthority, bind_request_authority
from src.tools import notes as notes_tool
from tests.helpers.database import disposable_database

NOTE = "vers0001-note-aaaa"
SCOPED_CHAT = "chat-from-note"
OTHER_CHAT = "another-chat-from-note"
PLAIN_CHAT = "an-ordinary-chat"
UPLOAD = "0123456789abcdef0123456789abcdef.png"


@pytest.fixture
def db(monkeypatch, tmp_path):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(nr, "SessionLocal", factory)
        monkeypatch.setattr(notes_tool, "_SEEN_VERSIONS", type(notes_tool._SEEN_VERSIONS)())
        monkeypatch.setattr(scope_mod, "_SEEN_BY_SESSION", type(scope_mod._SEEN_BY_SESSION)())
        with factory() as s:
            s.add(Note(id=NOTE, owner="alice", title="Trip", content="Pack light\n- [ ] book the cabin"))
            for sid in (SCOPED_CHAT, OTHER_CHAT):
                s.add(DbSession(id=sid, name="Agent: Trip", endpoint_url="", model="m", owner="alice",
                                note_scope_id=NOTE))
            s.commit()
        yield factory


def _row(factory):
    with factory() as s:
        return s.get(Note, NOTE)


def _versions(factory):
    with factory() as s:
        return s.query(NoteVersion).filter(NoteVersion.note_id == NOTE).order_by(NoteVersion.id).all()


def _fields(version):
    return json.loads(version.fields)


def _agent(factory, args, chat=SCOPED_CHAT):
    """One manage_notes call from a chat started from the note."""
    with factory() as s:
        scope = load_note_edit_scope(s, chat, "alice")
    scope.saw(_row(factory))
    token = bind_note_edit_scope(scope)
    try:
        args = _with_toggle_revision(factory, args)
        return asyncio.run(notes_tool.do_manage_notes(json.dumps({"id": NOTE, **args}), owner="alice"))
    finally:
        unbind_note_edit_scope(token)


def _with_toggle_revision(factory, args):
    """A toggle_item carries the body revision its `view` showed (required)."""
    if args.get("action") == "toggle_item" and "body_revision" not in args:
        args = {**args, "body_revision": note_body_revision(_row(factory))}
    return args


def _plain_agent(args, chat=PLAIN_CHAT):
    """One manage_notes call from an ordinary chat: no note scope, only the turn's authority."""
    args = _with_toggle_revision(cdb.SessionLocal, args)

    async def run():
        with bind_request_authority(RequestAuthority.empty(owner="alice", session_id=chat)):
            return await notes_tool.do_manage_notes(json.dumps({"id": NOTE, **args}), owner="alice")
    return asyncio.run(run())


def _age_versions(factory, seconds):
    with factory() as s:
        for v in s.query(NoteVersion).all():
            v.created_at = v.created_at - datetime.timedelta(seconds=seconds)
        s.commit()


class _Identity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            user = dict(scope.get("headers") or []).get(b"x-test-user")
            if user:
                scope.setdefault("state", {})["current_user"] = user.decode()
        await self.app(scope, receive, send)


class _Uploads:
    """Uploads that exist: `alive`, owned by alice unless `owners` says otherwise. Anything else was deleted."""

    def __init__(self):
        self.alive = set()
        self.owners = {}

    def get_upload_info(self, upload_id):
        return {"id": upload_id, "owner": self.owners.get(upload_id, "alice")} if upload_id in self.alive else None

    def reserve_upload(self, upload_id, owner=None, allow_admin=False):
        info = self.get_upload_info(upload_id)
        return info if info is not None and info["owner"] == owner else None


@pytest.fixture
def uploads():
    return _Uploads()


@pytest.fixture
def api(monkeypatch, db, uploads):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(nr.setup_note_routes(upload_handler=uploads))

    def call(method, url, user="alice", **kw):
        # A body write or a toggle carries a token (a 409 without one); these
        # tests are about versions, so the client read the note just before.
        body_write = method == "PUT" and set(kw.get("json") or {}) & {"content", "items", "image_url", "note_type"}
        if (body_write or url.endswith("/toggle")) and not set(kw.get("json") or {}) & {"body_revision", "base_updated_at"}:
            with db() as s:
                note = s.get(Note, url.split("/")[3])
                if note is not None:
                    kw["json"] = {**(kw.get("json") or {}), "body_revision": note_body_revision(note)}

        async def run():
            transport = httpx.ASGITransport(app=_Identity(app), client=("203.0.113.7", 54321))
            async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
                return await c.request(method, url, headers={"x-test-user": user}, **kw)
        return asyncio.run(run())

    return call


# --- what keeps a version ---

def test_an_agent_edit_from_the_notes_chat_keeps_the_note_as_it_was(db):
    assert _agent(db, {"action": "update", "content": "Bring boots"})["exit_code"] == 0
    [v] = _versions(db)
    assert (v.source, v.session_id, v.author, v.owner) == ("agent", SCOPED_CHAT, "alice", "alice")
    assert _fields(v)["content"] == "Pack light\n- [ ] book the cabin"
    assert _fields(v)["title"] == "Trip"


def test_an_agent_edit_from_an_ordinary_chat_keeps_a_version_too(db):
    result = _plain_agent({"action": "update", "title": "Cabin trip"})
    assert result["exit_code"] == 0, result
    assert _row(db).title == "Cabin trip"
    [v] = _versions(db)
    assert (v.source, v.session_id) == ("agent", PLAIN_CHAT)
    assert _fields(v)["title"] == "Trip"


def test_every_agent_edit_keeps_its_own_version_across_chats(db):
    assert _agent(db, {"action": "update", "content": "one"})["exit_code"] == 0
    assert _agent(db, {"action": "toggle_item", "index": 0}, chat=SCOPED_CHAT)["exit_code"] == 0
    assert _agent(db, {"action": "update", "content": "two"}, chat=OTHER_CHAT)["exit_code"] == 0
    assert _plain_agent({"action": "toggle_item", "index": 0})["exit_code"] == 0
    vs = _versions(db)
    assert [(v.source, v.session_id) for v in vs] == [
        ("agent", SCOPED_CHAT), ("agent", SCOPED_CHAT), ("agent", OTHER_CHAT), ("agent", PLAIN_CHAT)]
    # The first version is still the note before any agent touched it, even
    # though another chat's edit replaced the single Undo slot.
    assert _fields(vs[0])["content"] == "Pack light\n- [ ] book the cabin"
    assert _fields(vs[1])["content"] == "one\n\n- [ ] book the cabin"
    assert _fields(vs[2])["content"] == "one\n\n- [x] book the cabin"


def test_an_agent_write_that_changes_nothing_keeps_nothing(db):
    assert _plain_agent({"action": "toggle_item", "index": 0, "done": False})["exit_code"] == 0
    assert _plain_agent({"action": "update", "title": "Trip"})["exit_code"] == 0
    assert _versions(db) == []


def test_a_refused_agent_write_keeps_nothing(db):
    with db() as s:
        s.get(Note, NOTE).content = "Pack light\n" + f"![](/api/upload/{UPLOAD})"
        s.commit()
    # The update would reserve an upload the handler no longer has.
    class Gone:
        def reserve_upload(self, *a, **kw):
            return None
    original = notes_tool.get_upload_handler
    notes_tool.get_upload_handler = lambda: Gone()
    try:
        other = "fedcba9876543210fedcba9876543210.png"
        result = _plain_agent({"action": "update", "content": f"![](/api/upload/{other})"})
    finally:
        notes_tool.get_upload_handler = original
    assert result["exit_code"] == 1
    assert _versions(db) == []


def test_a_manual_save_keeps_a_version_at_most_once_per_gap(db, api):
    assert api("PUT", f"/api/notes/{NOTE}", json={"title": "A"}).status_code == 200
    assert api("PUT", f"/api/notes/{NOTE}", json={"title": "B"}).status_code == 200
    assert api("POST", f"/api/notes/{NOTE}/items", json={"text": "tickets"}).status_code == 200
    assert api("POST", f"/api/notes/{NOTE}/items/0/toggle").status_code == 200
    vs = _versions(db)
    assert [(v.source, v.author, _fields(v)["title"]) for v in vs] == [("user", "alice", "Trip")]
    _age_versions(db, versions_mod.NOTE_VERSION_USER_GAP_SECONDS + 1)
    assert api("PUT", f"/api/notes/{NOTE}", json={"title": "C"}).status_code == 200
    vs = _versions(db)
    assert [v.source for v in vs] == ["user", "user"]
    assert _fields(vs[1])["title"] == "B"
    assert "tickets" in _fields(vs[1])["content"]


def test_a_save_right_after_an_agent_edit_keeps_what_the_agent_wrote(db, api):
    assert api("PUT", f"/api/notes/{NOTE}", json={"title": "Mine"}).status_code == 200
    assert _agent(db, {"action": "update", "content": "agent text"})["exit_code"] == 0
    # Inside the gap, but the version before it is the agent's: keep its text.
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "my own text"}).status_code == 200
    vs = _versions(db)
    assert [v.source for v in vs] == ["user", "agent", "user"]
    assert _fields(vs[2])["content"].startswith("agent text")


def test_a_save_by_someone_else_inside_the_gap_keeps_the_text_it_replaces(db, api):
    with db() as s:
        s.add(NoteShare(id="g1", note_id=NOTE, shared_with="bob", permission="edit"))
        s.commit()
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "alice wrote this"}).status_code == 200
    # A minute later, well inside the gap, bob replaces everything.
    assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": "bob replaced it"}).status_code == 200
    assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": "bob again"}).status_code == 200
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "alice back"}).status_code == 200
    vs = _versions(db)
    assert [v.author for v in vs] == ["alice", "bob", "alice"]
    assert [_fields(v)["content"].split("\n")[0] for v in vs] == ["Pack light", "alice wrote this", "bob again"]


def test_a_save_that_changes_nothing_versioned_keeps_nothing(db, api):
    assert api("PUT", f"/api/notes/{NOTE}", json={"title": "Trip", "sort_order": 4}).status_code == 200
    assert _versions(db) == []


def test_the_cap_drops_the_oldest(db, monkeypatch):
    monkeypatch.setattr(versions_mod, "NOTE_VERSIONS_MAX", 3)
    for i in range(5):
        assert _plain_agent({"action": "update", "title": f"T{i}"})["exit_code"] == 0
    assert [_fields(v)["title"] for v in _versions(db)] == ["T1", "T2", "T3"]


def _share_with_bob(factory):
    with factory() as s:
        s.add(NoteShare(id="g-bob", note_id=NOTE, shared_with="bob", permission="edit"))
        s.commit()


def test_the_size_budget_drops_the_oldest_and_keeps_the_newest(db, monkeypatch):
    big = "x" * 1000
    for i in range(6):
        assert _plain_agent({"action": "update", "content": f"{i} {big}"})["exit_code"] == 0
    sizes = [len(v.fields) for v in _versions(db)]
    # Room for three of the 1 KB versions, not four.
    monkeypatch.setattr(versions_mod, "NOTE_VERSIONS_MAX_CHARS", 3 * max(sizes) + 10)
    assert _plain_agent({"action": "update", "content": f"6 {big}"})["exit_code"] == 0
    vs = _versions(db)
    assert sum(len(v.fields) for v in vs) <= versions_mod.NOTE_VERSIONS_MAX_CHARS
    assert [_fields(v)["content"].split(" ")[0] for v in vs] == ["3", "4", "5"]
    # A version bigger than the whole budget is still kept: the newest always is.
    monkeypatch.setattr(versions_mod, "NOTE_VERSIONS_MAX_CHARS", 10)
    assert _plain_agent({"action": "update", "content": "7"})["exit_code"] == 0
    [v] = _versions(db)
    assert _fields(v)["content"].startswith("6 ")


def test_fifty_one_collaborator_saves_keep_the_owners_last_version(db, api, monkeypatch):
    # Every save keeps a version (no typing gap), at the real cap.
    monkeypatch.setattr(versions_mod, "NOTE_VERSION_USER_GAP_SECONDS", -1)
    _share_with_bob(db)
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "alice's last text"}).status_code == 200
    for i in range(versions_mod.NOTE_VERSIONS_MAX + 1):
        assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": f"bob {i}"}).status_code == 200
    vs = _versions(db)
    contents = [_fields(v)["content"].split("\n")[0] for v in vs]
    # What alice left (kept before bob's first save), and alice's own save.
    assert "alice's last text" in contents
    assert [v.author for v in vs].count("alice") == 1
    # The newest is bob's last-but-one text; the cap only gives way for the kept ones.
    assert contents[-1] == f"bob {versions_mod.NOTE_VERSIONS_MAX - 1}"
    assert len(vs) <= versions_mod.NOTE_VERSIONS_MAX + 1
    # The owner can still go back to it.
    kept = next(v for v in vs if _fields(v)["content"].startswith("alice's last text"))
    assert api("POST", f"/api/notes/{NOTE}/versions/{kept.id}/restore").status_code == 200
    assert _row(db).content.startswith("alice's last text")


def test_the_note_as_created_is_the_owners_until_they_edit_it(db, api, monkeypatch):
    monkeypatch.setattr(versions_mod, "NOTE_VERSIONS_MAX", 3)
    monkeypatch.setattr(versions_mod, "NOTE_VERSION_USER_GAP_SECONDS", -1)
    _share_with_bob(db)
    for i in range(5):
        assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": f"bob {i}"}).status_code == 200
    contents = [_fields(v)["content"].split("\n")[0] for v in _versions(db)]
    assert contents == ["Pack light", "bob 2", "bob 3"]


def test_each_author_keeps_their_last_version_under_the_budget(db, api, monkeypatch):
    monkeypatch.setattr(versions_mod, "NOTE_VERSION_USER_GAP_SECONDS", -1)
    _share_with_bob(db)
    with db() as s:
        s.add(NoteShare(id="g-carol", note_id=NOTE, shared_with="carol", permission="edit"))
        s.commit()
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "alice text"}).status_code == 200
    assert api("PUT", f"/api/notes/{NOTE}", user="carol", json={"content": "carol text"}).status_code == 200
    for i in range(4):
        assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": f"bob {i}"}).status_code == 200
    monkeypatch.setattr(versions_mod, "NOTE_VERSIONS_MAX_CHARS", 1)
    assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": "bob 4"}).status_code == 200
    vs = _versions(db)
    contents = [_fields(v)["content"].split("\n")[0] for v in vs]
    # Over budget, only what each person left (and the save that left it) stays, plus the newest.
    assert contents == ["Pack light", "alice text", "carol text", "bob 3"]
    assert [v.author for v in vs] == ["alice", "carol", "bob", "bob"]


def test_undo_and_restore_still_work_once_the_history_is_pruned(db, api, monkeypatch):
    monkeypatch.setattr(versions_mod, "NOTE_VERSIONS_MAX", 3)
    for i in range(5):
        assert _agent(db, {"action": "update", "content": f"agent {i}"})["exit_code"] == 0
    assert len(_versions(db)) == 3
    # The chat's Undo still takes the note back, and keeps what it replaced.
    assert api("POST", f"/api/notes/{NOTE}/agent-undo").status_code == 200
    assert _row(db).content == "Pack light\n- [ ] book the cabin"
    vs = _versions(db)
    assert len(vs) == 3 and vs[-1].source == "restore" and _fields(vs[-1])["content"].startswith("agent 4")
    oldest = _versions(db)[0]
    assert api("POST", f"/api/notes/{NOTE}/versions/{oldest.id}/restore").status_code == 200
    assert _row(db).content == _fields(oldest)["content"]


# --- the owner's API ---

def test_the_list_is_newest_first_without_the_full_text(db, api):
    assert _agent(db, {"action": "update", "content": "one"})["exit_code"] == 0
    assert _plain_agent({"action": "update", "title": "Two"})["exit_code"] == 0
    r = api("GET", f"/api/notes/{NOTE}/versions")
    assert r.status_code == 200, r.text
    listed = r.json()["versions"]
    assert [(v["source"], v["session_id"], v["title"]) for v in listed] == [
        ("agent", PLAIN_CHAT, "Trip"), ("agent", SCOPED_CHAT, "Trip")]
    assert listed[1]["preview"] == "Pack light - [ ] book the cabin"
    assert "content" not in listed[0] and listed[0]["is_current"] is False
    full = api("GET", f"/api/notes/{NOTE}/versions/{listed[1]['id']}").json()
    assert full["content"] == "Pack light\n- [ ] book the cabin" and full["title"] == "Trip"
    assert api("GET", f"/api/notes/{NOTE}/versions/99999").status_code == 404


def test_restore_goes_back_and_can_itself_be_undone(db, api):
    assert _agent(db, {"action": "update", "content": "one"})["exit_code"] == 0
    assert _agent(db, {"action": "update", "content": "two"}, chat=OTHER_CHAT)["exit_code"] == 0
    assert _plain_agent({"action": "update", "title": "Three"})["exit_code"] == 0
    first = _versions(db)[0]
    note = api("GET", f"/api/notes/{NOTE}").json()
    r = api("POST", f"/api/notes/{NOTE}/versions/{first.id}/restore",
            json={"base_updated_at": note["updated_at"]})
    assert r.status_code == 200, r.text
    row = _row(db)
    assert (row.title, row.content) == ("Trip", "Pack light\n- [ ] book the cabin")
    restore = _versions(db)[-1]
    assert restore.source == "restore" and _fields(restore)["title"] == "Three"
    # Restoring the restore's version undoes it.
    assert api("POST", f"/api/notes/{NOTE}/versions/{restore.id}/restore").status_code == 200
    assert _row(db).title == "Three"


def test_restore_refuses_a_note_that_changed_meanwhile(db, api):
    assert _plain_agent({"action": "update", "title": "Agent"})["exit_code"] == 0
    [v] = _versions(db)
    stale = api("GET", f"/api/notes/{NOTE}").json()["updated_at"]
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "typed meanwhile"}).status_code == 200
    r = api("POST", f"/api/notes/{NOTE}/versions/{v.id}/restore", json={"base_updated_at": stale})
    assert r.status_code == 409
    assert r.json()["detail"]["note"]["content"].startswith("typed meanwhile")
    assert _row(db).content.startswith("typed meanwhile")
    assert [x.source for x in _versions(db)] == ["agent", "user"]


def test_restore_loses_a_race_with_a_409_and_keeps_nothing(db, api, monkeypatch):
    assert _plain_agent({"action": "update", "title": "Agent"})["exit_code"] == 0
    [v] = _versions(db)
    # A write lands between the restore's read and its commit.
    monkeypatch.setattr(nr, "stored_stamp", lambda _db, _id: "1999-01-01 00:00:00.000000")
    r = api("POST", f"/api/notes/{NOTE}/versions/{v.id}/restore")
    assert r.status_code == 409
    assert _row(db).title == "Agent"
    assert len(_versions(db)) == 1


def test_restore_refuses_an_upload_deleted_since(db, api, uploads):
    with db() as s:
        s.get(Note, NOTE).content = f"![](/api/upload/{UPLOAD})"
        s.commit()
    uploads.alive.add(UPLOAD)
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "no image any more"}).status_code == 200
    [v] = _versions(db)
    uploads.alive.clear()
    r = api("POST", f"/api/notes/{NOTE}/versions/{v.id}/restore")
    assert r.status_code == 409 and UPLOAD in r.json()["detail"]
    assert _row(db).content == "no image any more"
    uploads.alive.add(UPLOAD)
    assert api("POST", f"/api/notes/{NOTE}/versions/{v.id}/restore").status_code == 200


def test_restore_brings_back_a_collaborators_image(db, api, uploads):
    with db() as s:
        s.add(NoteShare(id="g1", note_id=NOTE, shared_with="bob", permission="edit"))
        s.commit()
    uploads.alive.add(UPLOAD)
    uploads.owners[UPLOAD] = "bob"
    image = f"bob's photo\n\n![](/api/upload/{UPLOAD})"
    assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": image}).status_code == 200
    _age_versions(db, versions_mod.NOTE_VERSION_USER_GAP_SECONDS + 1)
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "alice took it out"}).status_code == 200
    v = _versions(db)[-1]
    kept = _fields(v)["content"]
    assert kept.startswith(image)
    r = api("POST", f"/api/notes/{NOTE}/versions/{v.id}/restore")
    assert r.status_code == 200, r.text
    assert _row(db).content == kept
    # Still bob's upload, and once it is gone the restore says so again.
    assert uploads.owners[UPLOAD] == "bob"
    assert api("PUT", f"/api/notes/{NOTE}", json={"content": "alice took it out"}).status_code == 200
    uploads.alive.clear()
    r = api("POST", f"/api/notes/{NOTE}/versions/{v.id}/restore")
    assert r.status_code == 409 and UPLOAD in r.json()["detail"]


def test_the_agent_undo_keeps_what_it_replaces(db, api):
    assert _agent(db, {"action": "update", "content": "agent"})["exit_code"] == 0
    assert api("POST", f"/api/notes/{NOTE}/agent-undo").status_code == 200
    vs = _versions(db)
    assert [v.source for v in vs] == ["agent", "restore"]
    assert _fields(vs[1])["content"].startswith("agent")


def test_history_is_the_owners_and_keeps_collaborators_edits(db, api):
    with db() as s:
        s.add(NoteShare(id="g1", note_id=NOTE, shared_with="bob", permission="edit"))
        s.commit()
    assert api("PUT", f"/api/notes/{NOTE}", user="bob", json={"content": "bob was here"}).status_code == 200
    [v] = _versions(db)
    assert (v.author, v.owner) == ("bob", "alice")
    assert api("GET", f"/api/notes/{NOTE}/versions", user="bob").status_code == 403
    assert api("GET", f"/api/notes/{NOTE}/versions/{v.id}", user="bob").status_code == 403
    assert api("POST", f"/api/notes/{NOTE}/versions/{v.id}/restore", user="bob").status_code == 403
    assert api("GET", f"/api/notes/{NOTE}/versions", user="eve").status_code == 404
    listed = api("GET", f"/api/notes/{NOTE}/versions").json()["versions"]
    assert [x["author"] for x in listed] == ["bob"]


def test_a_version_id_of_another_note_is_not_found(db, api):
    with db() as s:
        s.add(Note(id="else0002-note-bbbb", owner="alice", title="Else", content="x"))
        s.commit()
    assert _plain_agent({"action": "update", "title": "Agent"})["exit_code"] == 0
    [v] = _versions(db)
    assert api("GET", f"/api/notes/else0002-note-bbbb/versions/{v.id}").status_code == 404
    assert api("POST", f"/api/notes/else0002-note-bbbb/versions/{v.id}/restore").status_code == 404


# --- versions go with the note ---

def test_deleting_the_note_deletes_its_versions(db, api):
    assert _plain_agent({"action": "update", "title": "Agent"})["exit_code"] == 0
    assert len(_versions(db)) == 1
    assert api("DELETE", f"/api/notes/{NOTE}").status_code == 200
    assert _versions(db) == []


def test_the_agent_deleting_the_note_deletes_its_versions(db):
    assert _plain_agent({"action": "update", "title": "Agent"})["exit_code"] == 0
    assert _plain_agent({"action": "delete"})["exit_code"] == 0
    assert _versions(db) == []


def test_purging_an_account_deletes_its_notes_versions(db):
    from core.notes_sharing import purge_notes_owned_by
    assert _plain_agent({"action": "update", "title": "Agent"})["exit_code"] == 0
    with db() as s:
        purge_notes_owned_by("alice", db=s)
        s.commit()
    assert _versions(db) == []


def test_the_pin_and_archive_buttons_keep_no_version(db, api):
    # The card's pin and archive buttons (their own routes) are not edits; a
    # PUT that changes `pinned` is, since pin is one of the fields Undo restores.
    for _ in range(2):
        assert api("POST", f"/api/notes/{NOTE}/pin").status_code == 200
        assert api("POST", f"/api/notes/{NOTE}/archive").status_code == 200
    assert _versions(db) == []


def test_a_calendar_event_deleting_its_reminder_deletes_the_reminders_versions(db):
    from routes.calendar_routes import _delete_calendar_reminders_for_event
    with db() as s:
        s.add(Note(id="cal-reminder", owner="alice", title="Calendar reminder: Dentist", content="",
                   label="calendar", source="calendar", due_date="2026-11-20T08:45"))
        s.commit()
        note = s.get(Note, "cal-reminder")
        before = versions_mod.snapshot(note)
        note.content = "Bring the forms"
        assert versions_mod.record_version(s, note, before, versions_mod.SOURCE_USER, author="alice") is not None
        s.commit()
    with db() as s:
        # The event goes, and its reminder note with every version of it.
        assert _delete_calendar_reminders_for_event(s, "alice", SimpleNamespace(summary="Dentist")) == 1
        s.commit()
        assert s.get(Note, "cal-reminder") is None
        assert s.query(NoteVersion).filter(NoteVersion.note_id == "cal-reminder").count() == 0
