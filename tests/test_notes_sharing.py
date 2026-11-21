"""Per-note sharing: access rules, routes, the manage_notes tool, account
rename/deletion and note-scoped media.

`core.notes_sharing.note_access` is the one authorization helper; the REST
routes and the tool both go through it. Sharing is only changed through the
REST routes (the Notes window); the tool refuses it, so the tool cases below
grant access over REST and then drive the tool as the collaborator. Routes are driven through
httpx.ASGITransport for the reasons given in tests/test_notes_fail_closed_auth.py.
"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import core.auth as auth_mod
import core.database as cdb
import routes.note_routes as nr
from core import notes_sharing as sharing
from core.database import Note, NoteShare
from core.note_writes import note_body_revision
from tests.helpers.database import disposable_database

_PEER = ("203.0.113.7", 54321)
_REAL_AUTH_MANAGER = auth_mod.AuthManager
_UPLOAD_A = "a" * 32 + ".png"   # alice's upload
_UPLOAD_B = "b" * 32 + ".png"   # bob's upload
_UPLOAD_X = "c" * 32 + ".png"   # someone else's, never in the note
_UPLOAD_HTML = "d" * 32 + ".html"  # alice's, a page
_UPLOAD_JS = "e" * 32 + ".js"      # alice's, a script
_UPLOAD_SVG = "f" * 32 + ".svg"    # alice's, a vector image


class _Identity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            user = dict(scope.get("headers") or []).get(b"x-test-user")
            if user:
                scope.setdefault("state", {})["current_user"] = user.decode()
        await self.app(scope, receive, send)


class _Auth:
    """Stand-in auth manager: a user map, privileges and is_configured."""

    def __init__(self, users=("alice", "bob", "carol", "dave"), can_share=True, configured=True):
        self.users = {u: {} for u in users}
        self.can_share = can_share
        self.is_configured = configured
        self.admins = set()

    def get_privileges(self, user):
        return {"can_share_notes": self.can_share}

    def is_admin(self, user):
        return user in self.admins


class _Uploads:
    """Upload handler stand-in for the note-scoped media route and for upload
    reservation: each upload belongs to one account, and `reserve_upload`
    only succeeds for that account, like the real handler with
    allow_admin=False."""

    OWNERS = {_UPLOAD_A: "alice", _UPLOAD_B: "bob", _UPLOAD_X: "carol",
              _UPLOAD_HTML: "alice", _UPLOAD_JS: "alice", _UPLOAD_SVG: "alice"}
    MIMES = {_UPLOAD_HTML: "text/html", _UPLOAD_JS: "text/javascript", _UPLOAD_SVG: "image/svg+xml"}

    def __init__(self, directory):
        self.upload_dir = str(directory)
        self.reserved = []
        for name in self.OWNERS:
            (directory / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())

    def reserve_upload(self, upload_id, *, owner, allow_admin=False):
        if self.OWNERS.get(upload_id) != owner:
            return None
        self.reserved.append((upload_id, owner))
        return {"id": upload_id, "owner": owner}

    def validate_upload_id(self, upload_id):
        return upload_id in self.OWNERS

    def get_upload_info(self, upload_id):
        if upload_id not in self.OWNERS:
            return None
        return {"id": upload_id, "owner": self.OWNERS[upload_id], "mime": self.MIMES.get(upload_id, "image/png")}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(nr, "SessionLocal", factory)
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(nr, "reserve_upload_ids", lambda *a, **kw: None)
        auth = _Auth()
        # By dotted path: other suites swap core.auth in sys.modules, and the
        # tool imports AuthManager from whatever is there at call time.
        monkeypatch.setattr("core.auth.AuthManager", lambda *a, **kw: auth)
        app = FastAPI()
        app.state.auth_manager = auth
        uploads = _Uploads(uploads_dir)
        app.include_router(nr.setup_note_routes(upload_handler=uploads))

        def seed(note_id, owner="alice", shares=(), **fields):
            with factory() as db:
                db.add(Note(id=note_id, owner=owner, title=fields.pop("title", note_id), **fields))
                for user, perm in shares:
                    db.add(NoteShare(id=f"{note_id}-{user}", note_id=note_id, shared_with=user, permission=perm))
                db.commit()

        def row(note_id):
            with factory() as db:
                return db.get(Note, note_id)

        def revision(note_id):
            # The token a body write or a toggle carries, as a GET returns it.
            with factory() as db:
                note = db.get(Note, note_id)
                return note_body_revision(note) if note is not None else None

        def grants(note_id=None):
            with factory() as db:
                q = db.query(NoteShare)
                if note_id:
                    q = q.filter(NoteShare.note_id == note_id)
                return sorted((g.note_id, g.shared_with, g.permission) for g in q.all())

        async def call(method, url, user="alice", **kw):
            transport = httpx.ASGITransport(app=_Identity(app), client=_PEER)
            headers = {"x-test-user": user} if user else {}
            async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
                return await c.request(method, url, headers=headers, **kw)

        def tool(owner, **args):
            from src.tools.notes import do_manage_notes
            return asyncio.run(do_manage_notes(json.dumps(args), owner=owner))

        async def atool(owner, **args):
            from src.tools.notes import do_manage_notes
            return await do_manage_notes(json.dumps(args), owner=owner)

        yield SimpleNamespace(seed=seed, row=row, revision=revision, grants=grants, call=call, tool=tool, atool=atool,
                              factory=factory, auth=auth, app=app, uploads=uploads, uploads_dir=uploads_dir)


# ---------------------------------------------------------------------------
# The access helper
# ---------------------------------------------------------------------------

def test_note_access_matrix(env):
    env.seed("n1", owner="alice", shares=[("bob", "edit"), ("carol", "view")])
    with env.factory() as db:
        note = db.get(Note, "n1")
        assert nr._note_access("alice", note, db) == {"read", "write", "manage", "share"}
        assert nr._note_access("bob", note, db) == {"read", "write"}
        assert nr._note_access("carol", note, db) == {"read"}
        assert nr._note_access("dave", note, db) == set()


def test_note_access_lowercases_both_sides_and_unifies_no_user(env):
    env.seed("n1", owner="Alice", shares=[("bob", "edit")])
    with env.factory() as db:
        note = db.get(Note, "n1")
        assert nr._note_access("ALICE", note, db) == sharing.ACCESS_ALL
        assert nr._note_access(" Bob ", note, db) == {"read", "write"}
        # "" and None are the same single-user mode.
        assert nr._note_access("", note, db) == nr._note_access(None, note, db) == sharing.ACCESS_ALL


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

async def test_list_returns_owned_then_shared_with_me_and_me(env):
    env.seed("mine", owner="bob")
    env.seed("theirs", owner="alice", shares=[("bob", "view")])
    env.seed("hidden", owner="alice")
    env.seed("archived", owner="alice", shares=[("bob", "edit")], archived=True)
    body = (await env.call("GET", "/api/notes", user="bob")).json()
    assert body["me"] == "bob"
    assert [n["id"] for n in body["notes"]] == ["mine", "theirs"]
    shared = body["notes"][1]
    assert shared["is_owner"] is False and shared["shared_by"] == "alice"
    assert shared["my_permission"] == "view" and shared["can_edit"] is False


async def test_roster_and_owner_links_go_to_the_owner_only(env):
    env.seed("n1", owner="alice", shares=[("bob", "edit"), ("carol", "view")],
             session_id="s-1", agent_session_id="agent-1", ai_classification='{"kind":"todo"}')
    owner = (await env.call("GET", "/api/notes/n1", user="alice")).json()
    assert owner["shared_with"] == [{"username": "bob", "permission": "edit"},
                                    {"username": "carol", "permission": "view"}]
    assert owner["is_shared"] is True and owner["my_permission"] == "owner"
    assert owner["agent_session_id"] == "agent-1"
    bob = (await env.call("GET", "/api/notes/n1", user="bob")).json()
    assert bob["shared_with"] == [] and bob["is_shared"] is True
    assert bob["my_permission"] == "edit" and bob["can_edit"] is True
    for private in ("session_id", "agent_session_id", "ai_classification", "ai_content_hash"):
        assert bob[private] is None
    listed = (await env.call("GET", "/api/notes", user="carol")).json()["notes"]
    assert listed[0]["shared_with"] == [] and listed[0]["my_permission"] == "view"


async def test_stranger_gets_404_everywhere(env):
    env.seed("n1", owner="alice", content="- [ ] a")
    for method, url, kw in (
        ("GET", "/api/notes/n1", {}),
        ("PUT", "/api/notes/n1", {"json": {"content": "x"}}),
        ("DELETE", "/api/notes/n1", {}),
        ("POST", "/api/notes/n1/pin", {}),
        ("POST", "/api/notes/n1/archive", {}),
        ("POST", "/api/notes/n1/items/0/toggle", {}),
        ("POST", "/api/notes/n1/items", {"json": {"text": "b"}}),
        ("GET", "/api/notes/n1/shares", {}),
        ("POST", "/api/notes/n1/share", {"json": {"users": ["dave"]}}),
        ("DELETE", "/api/notes/n1/share/dave", {}),
    ):
        r = await env.call(method, url, user="dave", **kw)
        assert r.status_code == 404, (method, url, r.status_code)
    assert env.row("n1").content == "- [ ] a"


async def test_edit_collaborator_changes_content_title_and_tasks(env):
    env.seed("n1", owner="alice", content="- [ ] a", shares=[("bob", "edit")])
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"body_revision": env.revision("n1"), "title": "Trip", "content": "- [ ] a\n- [ ] b"})
    assert r.status_code == 200
    assert (await env.call("POST", "/api/notes/n1/items/1/toggle", user="bob",
                           json={"body_revision": env.revision("n1")})).status_code == 200
    assert (await env.call("POST", "/api/notes/n1/items", user="bob", json={"text": "c"})).status_code == 200
    row = env.row("n1")
    assert row.title == "Trip"
    assert row.content == "- [ ] a\n- [x] b\n- [ ] c"


@pytest.mark.parametrize("field,value", [
    ("agent_session_id", "agent-evil"),
    ("color", "red"),
    ("due_date", "2026-11-01T09:00"),
    ("repeat", "daily"),
    ("label", "work"),
    ("pinned", True),
    ("archived", True),
    ("sort_order", 0),
    ("note_type", "goal"),
    ("gallery_id", "g-evil"),
])
async def test_edit_collaborator_cannot_set_owner_only_fields(env, field, value):
    env.seed("n1", owner="alice", content="x", shares=[("bob", "edit")])
    before = getattr(env.row("n1"), field)
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"content": "y", field: value})
    assert r.status_code == 403, r.text
    row = env.row("n1")
    assert row.content == "x"  # nothing of the request was applied
    assert getattr(row, field) == before


@pytest.mark.parametrize("field", sharing.OWNER_ONLY_FIELDS)
async def test_edit_collaborator_cannot_send_owner_only_fields_as_null(env, field):
    """Key presence decides, so the check still holds once null clears a field on this route."""
    env.seed("n1", owner="alice", content="x", shares=[("bob", "edit")])
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"content": "y", field: None})
    assert r.status_code == 403, r.text
    assert field in r.json()["detail"]
    assert env.row("n1").content == "x"


async def test_edit_collaborator_text_save_still_goes_through(env):
    env.seed("n1", owner="alice", content="x", shares=[("bob", "edit")])
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"body_revision": env.revision("n1"), "title": "t", "content": "y"})
    assert r.status_code == 200, r.text
    assert env.row("n1").content == "y"


async def test_owner_keeps_full_control_on_the_same_route(env):
    env.seed("n1", owner="alice", content="x", shares=[("bob", "edit")])
    r = await env.call("PUT", "/api/notes/n1", user="alice", json={
        "pinned": True, "archived": True, "sort_order": 5, "color": "red", "agent_session_id": "agent-1",
    })
    assert r.status_code == 200
    assert r.json()["pinned"] is True and r.json()["agent_session_id"] == "agent-1"


async def test_view_collaborator_is_read_only(env):
    env.seed("n1", owner="alice", content="- [ ] a", shares=[("carol", "view")])
    assert (await env.call("GET", "/api/notes/n1", user="carol")).status_code == 200
    for method, url, kw in (
        ("PUT", "/api/notes/n1", {"json": {"content": "x"}}),
        ("POST", "/api/notes/n1/items/0/toggle", {}),
        ("POST", "/api/notes/n1/items", {"json": {"text": "b"}}),
        ("DELETE", "/api/notes/n1", {}),
        ("POST", "/api/notes/n1/pin", {}),
        ("POST", "/api/notes/n1/archive", {}),
        ("GET", "/api/notes/n1/shares", {}),
        ("POST", "/api/notes/n1/share", {"json": {"users": ["dave"]}}),
        ("DELETE", "/api/notes/n1/share/bob", {}),
    ):
        r = await env.call(method, url, user="carol", **kw)
        assert r.status_code == 403, (method, url, r.status_code)
    assert env.row("n1").content == "- [ ] a"


async def test_edit_collaborator_cannot_pin_archive_or_delete(env):
    """Edit covers content, title and tasks; the dedicated owner routes stay the owner's."""
    env.seed("n1", owner="alice", content="- [ ] a", shares=[("bob", "edit")])
    for method, url in (("POST", "/api/notes/n1/pin"), ("POST", "/api/notes/n1/archive"), ("DELETE", "/api/notes/n1")):
        r = await env.call(method, url, user="bob")
        assert r.status_code == 403, (method, url, r.status_code)
    row = env.row("n1")
    assert (row.pinned, row.archived, row.content) == (False, False, "- [ ] a")
    assert env.grants("n1") == [("n1", "bob", "edit")]


async def test_archived_view_never_lists_notes_shared_with_me(env):
    env.seed("mine-old", owner="bob", archived=True)
    env.seed("theirs-old", owner="alice", shares=[("bob", "edit")], archived=True)
    env.seed("theirs", owner="alice", shares=[("bob", "view")])
    archived = (await env.call("GET", "/api/notes?archived=true", user="bob")).json()["notes"]
    assert [n["id"] for n in archived] == ["mine-old"]
    active = (await env.call("GET", "/api/notes?archived=false", user="bob")).json()["notes"]
    assert [n["id"] for n in active] == ["theirs"]


async def test_collaborator_edit_goes_through_the_409_check(env):
    env.seed("n1", owner="alice", content="x", shares=[("bob", "edit")])
    stale = (await env.call("GET", "/api/notes/n1", user="bob")).json()["updated_at"]
    assert (await env.call("PUT", "/api/notes/n1", user="alice", json={"body_revision": env.revision("n1"), "content": "owner"})).status_code == 200
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"content": "bob", "base_updated_at": stale})
    assert r.status_code == 409
    assert r.json()["detail"]["note"]["content"] == "owner"
    assert r.json()["detail"]["note"]["shared_with"] == []


async def test_share_upserts_skips_owner(env):
    env.seed("n1", owner="alice")
    r = await env.call("POST", "/api/notes/n1/share", json={"users": ["Bob", "alice", ""], "permission": "edit"})
    assert r.status_code == 200
    assert r.json()["shared"] == ["bob"] and r.json()["skipped"] == ["alice"]
    r = await env.call("POST", "/api/notes/n1/share", json={"users": ["bob"], "permission": "view"})
    assert r.json()["shared_with"] == [{"username": "bob", "permission": "view"}]
    assert env.grants("n1") == [("n1", "bob", "view")]
    shares = (await env.call("GET", "/api/notes/n1/shares")).json()
    assert shares == {"shared_with": [{"username": "bob", "permission": "view"}]}
    bad = await env.call("POST", "/api/notes/n1/share", json={"users": ["bob"], "permission": "admin"})
    assert bad.status_code == 400


async def test_share_and_targets_need_the_privilege(env):
    env.seed("n1", owner="alice")
    env.auth.can_share = False
    assert (await env.call("GET", "/api/notes/share-targets")).status_code == 403
    assert (await env.call("POST", "/api/notes/n1/share", json={"users": ["bob"]})).status_code == 403
    assert env.grants() == []
    env.auth.can_share = True
    env.auth.admins = {"alice"}
    targets = (await env.call("GET", "/api/notes/share-targets")).json()
    assert targets == {"users": [{"username": u} for u in ("bob", "carol", "dave")], "directory": True}


async def test_share_targets_lists_accounts_only_to_an_admin(env):
    """A non-admin allowed to share gets no account list, only the flag that
    says to type the name."""
    env.auth.admins = {"alice"}
    assert (await env.call("GET", "/api/notes/share-targets", user="bob")).json() == {"users": [], "directory": False}
    listed = (await env.call("GET", "/api/notes/share-targets", user="ALICE")).json()
    assert listed == {"users": [{"username": u} for u in ("bob", "carol", "dave")], "directory": True}


async def test_non_admin_shares_by_exact_username_any_case(env):
    env.seed("n1", owner="bob")
    r = await env.call("POST", "/api/notes/n1/share", user="bob", json={"users": ["  CaRoL "], "permission": "view"})
    assert r.status_code == 200
    assert r.json()["shared"] == ["carol"]
    assert r.json()["shared_with"] == [{"username": "carol", "permission": "view"}]
    assert env.grants("n1") == [("n1", "carol", "view")]


@pytest.mark.parametrize("users", [["ghost"], ["carol", "ghost"], ["car"]])
async def test_share_with_an_unknown_name_is_refused_and_writes_nothing(env, users):
    """A typo is a 404 with one neutral message, whoever asks, and nothing in
    the same request is shared. A prefix of a real name is not a match."""
    env.seed("n1", owner="bob")
    env.seed("n2", owner="alice")
    env.auth.admins = {"alice"}
    for caller, nid in (("bob", "n1"), ("alice", "n2")):
        r = await env.call("POST", f"/api/notes/{nid}/share", user=caller, json={"users": users, "permission": "edit"})
        assert r.status_code == 404
        assert r.json() == {"detail": "No account with that name"}
    assert env.grants() == []


async def test_unknown_name_on_someone_elses_note_is_the_missing_note_404(env):
    """The note is checked first: a stranger learns nothing about the name."""
    env.seed("n1", owner="alice")
    r = await env.call("POST", "/api/notes/n1/share", user="bob", json={"users": ["ghost"]})
    assert r.status_code == 404 and r.json() == {"detail": "Note not found"}


async def test_owner_removes_anyone_collaborator_only_leaves(env):
    env.seed("n1", owner="alice", shares=[("bob", "edit"), ("carol", "view")])
    assert (await env.call("DELETE", "/api/notes/n1/share/carol", user="bob")).status_code == 403
    r = await env.call("DELETE", "/api/notes/n1/share/BOB", user="bob")
    assert r.status_code == 200 and r.json()["left"] is True
    assert (await env.call("GET", "/api/notes/n1", user="bob")).status_code == 404
    assert (await env.call("DELETE", "/api/notes/n1/share/carol", user="alice")).status_code == 200
    assert env.grants("n1") == []


async def test_removing_people_and_leaving_do_not_need_the_share_privilege(env):
    """Only granting needs can_share_notes; taking access away never does."""
    env.seed("n1", owner="alice", shares=[("bob", "edit"), ("carol", "view")])
    env.auth.can_share = False
    assert (await env.call("DELETE", "/api/notes/n1/share/carol", user="alice")).status_code == 200
    r = await env.call("DELETE", "/api/notes/n1/share/bob", user="bob")
    assert r.status_code == 200 and r.json()["left"] is True
    assert env.grants("n1") == []


async def test_note_delete_removes_its_grants(env):
    env.seed("n1", owner="alice", shares=[("bob", "edit")])
    assert (await env.call("DELETE", "/api/notes/n1", user="alice")).status_code == 200
    assert env.grants() == []


def test_note_shares_cascade_on_note_delete(env):
    """The FK cascade alone, without the explicit route cleanup."""
    env.seed("n1", owner="alice", shares=[("bob", "edit")])
    with env.factory() as db:
        db.execute(cdb.text("DELETE FROM notes WHERE id = 'n1'"))
        db.commit()
    assert env.grants() == []


async def test_single_user_mode_lists_everything_and_refuses_to_share(env, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    env.seed("n1", owner=None, content="x")
    body = (await env.call("GET", "/api/notes", user=None)).json()
    assert body["me"] is None
    assert body["notes"][0]["is_owner"] is True and body["notes"][0]["can_edit"] is True
    assert (await env.call("PUT", "/api/notes/n1", user=None, json={"color": "red"})).status_code == 200
    r = await env.call("POST", "/api/notes/n1/share", user=None, json={"users": ["bob"]})
    assert r.status_code == 400 and "signed-in" in r.json()["detail"]
    assert (await env.call("GET", "/api/notes/share-targets", user=None)).json() == {"users": [], "directory": False}


async def test_unconfigured_auth_without_identity_fails_closed(env):
    # Auth configured, request with no identity: require_user's 401, never
    # the single-user path.
    env.seed("n1", owner="alice", shares=[("bob", "edit")])
    assert (await env.call("GET", "/api/notes", user=None)).status_code == 401
    assert (await env.call("GET", "/api/notes/n1/media/" + _UPLOAD_A, user=None)).status_code == 401


async def test_fire_reminder_stays_owner_only(env, monkeypatch):
    env.seed("n1", owner="alice", shares=[("bob", "edit")], due_date="2026-11-01T09:00")

    async def _no_dispatch(**kw):
        return {"ok": True}

    monkeypatch.setattr(nr, "dispatch_reminder", _no_dispatch)
    r = await env.call("POST", "/api/notes/fire-reminder", user="bob", json={"note_id": "n1"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Note-scoped media
# ---------------------------------------------------------------------------

def _real_reservation(env, monkeypatch):
    """The fixture stubs reservation out; these tests need the real functions."""
    from src import upload_handler as uh
    monkeypatch.setattr(nr, "reserve_upload_ids", uh.reserve_upload_ids)
    env.uploads.reserved.clear()


def _img(upload):
    return f"![](/api/upload/{upload})"


async def test_writes_reserve_only_the_uploads_they_add(env, monkeypatch):
    """Re-reserving every upload in the note as the writer refused any save by
    a collaborator once the owner's image was in it, and the owner's own save
    once a collaborator's image was."""
    _real_reservation(env, monkeypatch)
    env.seed("n1", owner="alice", content=_img(_UPLOAD_A), shares=[("bob", "edit")])
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"body_revision": env.revision("n1"), "content": f"{_img(_UPLOAD_A)}\n\ntarp"})
    assert r.status_code == 200, r.text
    assert env.uploads.reserved == [], "nothing new, nothing reserved"
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"body_revision": env.revision("n1"), "content": f"{_img(_UPLOAD_A)}\n\n{_img(_UPLOAD_B)}"})
    assert r.status_code == 200, r.text
    assert env.uploads.reserved == [(_UPLOAD_B, "bob")], "the added upload is reserved for its writer"
    r = await env.call("PUT", "/api/notes/n1", user="alice", json={"body_revision": env.revision("n1"), "content": f"{_img(_UPLOAD_A)}\n\n{_img(_UPLOAD_B)}\n\nok"})
    assert r.status_code == 200, r.text
    assert env.uploads.reserved == [(_UPLOAD_B, "bob")]
    # An upload the writer does not own, and the note does not already show, is still refused.
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"body_revision": env.revision("n1"), "content": f"{_img(_UPLOAD_A)}\n\n{_img(_UPLOAD_X)}"})
    assert r.status_code == 409
    assert env.row("n1").content == f"{_img(_UPLOAD_A)}\n\n{_img(_UPLOAD_B)}\n\nok"


async def test_putting_back_someone_elses_removed_image_is_refused(env, monkeypatch):
    """The documented edge of the rule above: once removed and saved, another
    account's image is no longer in the note, so writing it back counts as
    new and only its owner may."""
    _real_reservation(env, monkeypatch)
    env.seed("n1", owner="alice", content=f"{_img(_UPLOAD_A)}\n\nlist", shares=[("bob", "edit")])
    assert (await env.call("PUT", "/api/notes/n1", user="bob", json={"body_revision": env.revision("n1"), "content": "list"})).status_code == 200
    r = await env.call("PUT", "/api/notes/n1", user="bob", json={"body_revision": env.revision("n1"), "content": f"{_img(_UPLOAD_A)}\n\nlist"})
    assert r.status_code == 409
    assert env.row("n1").content == "list"
    assert (await env.call("PUT", "/api/notes/n1", user="alice", json={"body_revision": env.revision("n1"), "content": f"{_img(_UPLOAD_A)}\n\nlist"})).status_code == 200


async def test_media_serves_referenced_uploads_both_ways(env):
    content = f"![](/api/upload/{_UPLOAD_A})\n\n![](/api/upload/{_UPLOAD_B} \"w=33\")"
    env.seed("n1", owner="alice", content=content, shares=[("bob", "edit")])
    for user in ("alice", "bob"):
        for upload in (_UPLOAD_A, _UPLOAD_B):
            r = await env.call("GET", f"/api/notes/n1/media/{upload}", user=user)
            assert r.status_code == 200, (user, upload)
            assert r.headers["content-type"] == "image/png"
            assert r.content.endswith(upload.encode())


async def test_media_serves_images_only(env):
    """A note shows uploads as images. A page or a script referenced from a
    note must not be served inline from this origin, where it would run in
    the reader's session; an SVG is served as an attachment."""
    content = "\n".join(_img(u) for u in (_UPLOAD_A, _UPLOAD_HTML, _UPLOAD_JS, _UPLOAD_SVG))
    env.seed("n1", owner="alice", content=content, shares=[("bob", "view")])
    for user in ("alice", "bob"):
        ok = await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_A}", user=user)
        assert ok.status_code == 200
        assert ok.headers["x-content-type-options"] == "nosniff"
        assert "content-disposition" not in ok.headers
        for upload in (_UPLOAD_HTML, _UPLOAD_JS):
            r = await env.call("GET", f"/api/notes/n1/media/{upload}", user=user)
            assert r.status_code == 404, (user, upload, r.headers.get("content-type"))
        svg = await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_SVG}", user=user)
        assert svg.status_code == 200
        assert svg.headers["content-type"].startswith("image/svg+xml")
        assert svg.headers["content-disposition"].startswith("attachment")
        assert svg.headers["x-content-type-options"] == "nosniff"


async def test_reservation_reads_the_stored_content(env, monkeypatch):
    """Reservation used to scan the request's `items` as JSON, where a quote
    after the URL is escaped and the reference is missed, while the stored
    content (what the media route serves from) keeps it. Every writer now
    reserves from the note as stored."""
    _real_reservation(env, monkeypatch)
    import src.tools.notes as tool_mod
    monkeypatch.setattr(tool_mod, "get_upload_handler", lambda: env.uploads)
    planted = f'/api/upload/{_UPLOAD_X}"'
    r = await env.call("POST", "/api/notes", user="bob", json={"title": "t", "items": [{"text": planted, "done": False}]})
    assert r.status_code == 409, r.text
    env.seed("own", owner="bob", content="list")
    r = await env.call("PUT", "/api/notes/own", user="bob", json={"body_revision": env.revision("own"), "items": [{"text": planted, "done": False}]})
    assert r.status_code == 409, r.text
    r = await env.call("POST", "/api/notes/own/items", user="bob", json={"text": planted})
    assert r.status_code == 409, r.text
    assert env.row("own").content == "list"
    added = await env.atool("bob", action="add", title="t", checklist_items=[{"text": planted, "done": False}])
    assert added.get("exit_code") == 1 and "no longer available" in added.get("error", ""), added
    updated = await env.atool("bob", action="update", note_id="own", checklist_items=[{"text": planted, "done": False}])
    assert updated.get("exit_code") == 1 and "no longer available" in updated.get("error", ""), updated
    assert env.row("own").content == "list"
    with env.factory() as db:
        assert db.query(Note).filter(Note.owner == "bob").count() == 1
    # The writer's own upload, written the same way, is reserved and saved.
    mine = f'/api/upload/{_UPLOAD_B}"'
    r = await env.call("PUT", "/api/notes/own", user="bob", json={"body_revision": env.revision("own"), "items": [{"text": mine, "done": False}]})
    assert r.status_code == 200, r.text
    assert (_UPLOAD_B, "bob") in env.uploads.reserved


async def test_media_serves_only_the_owners_and_collaborators_uploads(env):
    """A reference alone is not enough: the upload must belong to the note's
    owner or a current collaborator, whatever wrote the content."""
    content = "\n".join(_img(u) for u in (_UPLOAD_A, _UPLOAD_B, _UPLOAD_X))
    env.seed("n1", owner="alice", content=content, shares=[("bob", "edit")])
    for user in ("alice", "bob"):
        assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_A}", user=user)).status_code == 200
        assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_B}", user=user)).status_code == 200
        # carol's upload, referenced but carol is neither owner nor collaborator
        assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_X}", user=user)).status_code == 404
    # Once bob is removed, his image no longer loads through alice's note.
    assert (await env.call("DELETE", "/api/notes/n1/share/bob", user="alice")).status_code == 200
    assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_B}", user="alice")).status_code == 404
    assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_A}", user="alice")).status_code == 200


async def test_media_404s_unreferenced_upload_and_stranger(env):
    env.seed("n1", owner="alice", content=f"![](/api/upload/{_UPLOAD_A})", shares=[("bob", "view")])
    assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_X}", user="bob")).status_code == 404
    assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_X}", user="alice")).status_code == 404
    assert (await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_A}", user="dave")).status_code == 404
    assert (await env.call("GET", "/api/notes/n1/media/..%2Fapp.db", user="bob")).status_code == 404


# ---------------------------------------------------------------------------
# Account rename and deletion
# ---------------------------------------------------------------------------

def test_rename_repoints_grants_and_merges_collisions(env):
    env.seed("n1", owner="alice", shares=[("bob", "edit")])
    env.seed("n2", owner="alice", shares=[("bob", "view"), ("bobby", "edit")])
    env.seed("n3", owner="bobby", shares=[("bob", "view")])
    with env.factory() as db:
        sharing.rename_shared_user(db, "bob", "bobby")
        db.commit()
    assert env.grants() == [("n1", "bobby", "edit"), ("n2", "bobby", "edit")]


async def test_rename_route_moves_grants_in_the_same_transaction(env, monkeypatch):
    import routes.auth_routes as ar
    env.seed("n1", owner="alice", shares=[("bob", "edit")])

    class _RenameAuth(_Auth):
        def is_admin(self, user):
            return user == "alice"

        def get_username_for_token(self, token):
            return "alice"

        def rename_user(self, old, new, by):
            self.users[new] = self.users.pop(old, {})
            return True

    am = _RenameAuth()
    app = FastAPI()
    app.state.auth_manager = am
    app.include_router(ar.setup_auth_routes(am))
    transport = httpx.ASGITransport(app=_Identity(app), client=_PEER)
    async with httpx.AsyncClient(transport=transport, base_url="http://auth.test") as c:
        r = await c.put("/api/auth/users/bob/rename", json={"username": "bobby"}, headers={"x-test-user": "alice"})
    assert r.status_code == 200, r.text
    assert env.grants() == [("n1", "bobby", "edit")]


def _legacy_rows(env):
    with env.factory() as db:
        if not cdb.inspect(db.connection()).has_table("note_legacy_fields"):
            return None
        return sorted(r[0] for r in db.execute(cdb.text("SELECT note_id FROM note_legacy_fields")))


def _seed_legacy(env, *note_ids):
    with env.factory() as db:
        db.execute(cdb.text(cdb.NOTE_LEGACY_FIELDS_DDL))
        for note_id in note_ids:
            db.execute(cdb.text(
                "INSERT INTO note_legacy_fields (note_id, content, migrated_at) VALUES (:id, 'old', 'then')"
            ), {"id": note_id})
        db.commit()


def _seed_accounts(env):
    """bob owns two notes, one shared with carol; alice shares one with bob;
    carol owns one; a legacy note has no owner. bob's notes and the legacy
    one have migration backup rows."""
    env.seed("bob-1", owner="bob", shares=[("carol", "edit")], content="- [ ] x")
    env.seed("bob-2", owner="Bob ")  # spelled loosely, still bob's (as is_owner() sees it)
    env.seed("alice-1", owner="alice", shares=[("bob", "edit"), ("carol", "view")])
    env.seed("carol-1", owner="carol")
    env.seed("legacy-1", owner=None, content="single-user era")
    _seed_legacy(env, "bob-1", "bob-2", "legacy-1", "alice-1")


def _notes(env):
    with env.factory() as db:
        return sorted(n.id for n in db.query(Note).all())


async def _delete_account(env, tmp_path, username):
    import routes.auth_routes as ar
    mgr = _REAL_AUTH_MANAGER(str(tmp_path / "auth.json"))
    assert mgr.create_user("admin", "correct-horse-battery", True)
    assert mgr.create_user("bob", "correct-horse-battery", False)
    mgr.get_username_for_token = lambda token: "admin"  # the admin's session cookie
    app = FastAPI()
    app.state.auth_manager = mgr
    app.include_router(ar.setup_auth_routes(mgr))
    transport = httpx.ASGITransport(app=_Identity(app), client=_PEER)
    async with httpx.AsyncClient(transport=transport, base_url="http://auth.test") as c:
        r = await c.request("DELETE", "/api/auth/users", json={"username": username}, headers={"x-test-user": "admin"})
    return r, mgr


async def test_account_deletion_deletes_owned_notes_shares_and_backups(env, tmp_path):
    _seed_accounts(env)
    r, mgr = await _delete_account(env, tmp_path, "Bob")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert "bob" not in mgr.users
    # bob's notes are gone; alice's, carol's and the owner-less legacy note stay.
    assert _notes(env) == ["alice-1", "carol-1", "legacy-1"]
    # Grants on bob's notes and grants naming bob are both gone.
    assert env.grants() == [("alice-1", "carol", "view")]
    # The rollback SQL cannot resurrect bob's notes; other backups stay.
    assert _legacy_rows(env) == ["alice-1", "legacy-1"]
    assert env.row("legacy-1").content == "single-user era"


async def test_collaborator_gets_404_once_the_owner_is_deleted(env, tmp_path):
    _seed_accounts(env)
    assert (await env.call("GET", "/api/notes/bob-1", user="carol")).status_code == 200
    r, _mgr = await _delete_account(env, tmp_path, "bob")
    assert r.status_code == 200
    assert (await env.call("GET", "/api/notes/bob-1", user="carol")).status_code == 404
    put = await env.call("PUT", "/api/notes/bob-1", user="carol", json={"body_revision": env.revision("bob-1"), "content": "late edit"})
    assert put.status_code == 404
    assert (await env.call("POST", "/api/notes/bob-1/items/0/toggle", user="carol")).status_code == 404
    listed = (await env.call("GET", "/api/notes", user="carol")).json()["notes"]
    assert sorted(n["id"] for n in listed) == ["alice-1", "carol-1"]


async def test_account_deletion_keeps_the_account_when_its_notes_cannot_be_removed(env, tmp_path, monkeypatch):
    _seed_accounts(env)

    def _boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(sharing, "purge_notes_owned_by", _boom)
    r, mgr = await _delete_account(env, tmp_path, "bob")
    assert r.status_code == 500, r.text
    assert "could not be removed, so the account was kept" in r.json()["detail"]
    assert "bob" in mgr.users, "the account stays"
    assert "bob-1" in _notes(env)


async def test_a_purge_failing_mid_transaction_keeps_account_notes_and_grants(env, tmp_path):
    # A delete that fails half way through the purge (here a trigger on one of
    # bob's notes) rolls the whole purge back and the account is kept, so a
    # new account can never be created under the name while its notes remain.
    _seed_accounts(env)
    before = (_notes(env), env.grants(), _legacy_rows(env))
    with env.factory() as db:
        db.execute(cdb.text(
            "CREATE TRIGGER jam BEFORE DELETE ON notes WHEN OLD.id = 'bob-2' "
            "BEGIN SELECT RAISE(ABORT, 'jammed'); END"
        ))
        db.commit()
    r, mgr = await _delete_account(env, tmp_path, "bob")
    assert r.status_code == 500, r.text
    assert "bob" in mgr.users
    assert (_notes(env), env.grants(), _legacy_rows(env)) == before
    assert mgr.create_user("bob", "correct-horse-battery", False) is False, "the name is still taken"

    with env.factory() as db:
        db.execute(cdb.text("DROP TRIGGER jam"))
        db.commit()
    app_mgr = mgr
    import routes.auth_routes as ar
    app = FastAPI()
    app.state.auth_manager = app_mgr
    app.include_router(ar.setup_auth_routes(app_mgr))
    transport = httpx.ASGITransport(app=_Identity(app), client=_PEER)
    async with httpx.AsyncClient(transport=transport, base_url="http://auth.test") as c:
        again = await c.request("DELETE", "/api/auth/users", json={"username": "bob"}, headers={"x-test-user": "admin"})
    assert again.status_code == 200 and again.json() == {"ok": True}
    assert "bob" not in mgr.users
    assert _notes(env) == ["alice-1", "carol-1", "legacy-1"]


async def test_a_refused_account_deletion_removes_no_notes(env, tmp_path):
    _seed_accounts(env)
    before = _notes(env)
    (tmp_path / "self").mkdir()
    (tmp_path / "nobody").mkdir()
    r, _mgr = await _delete_account(env, tmp_path / "self", "admin")  # an admin cannot delete themselves
    assert r.status_code == 400
    r, _mgr = await _delete_account(env, tmp_path / "nobody", "nobody")
    assert r.status_code == 400
    assert _notes(env) == before


def test_purge_is_idempotent_and_never_touches_ownerless_notes(env):
    _seed_accounts(env)
    first = sharing.purge_notes_owned_by("bob")
    assert first == {"notes": 2, "shares": 1, "legacy_rows": 2, "grants": 1}
    assert sharing.purge_notes_owned_by("bob") == {"notes": 0, "shares": 0, "legacy_rows": 0, "grants": 0}
    for nobody in ("", None, "   "):
        assert sharing.purge_notes_owned_by(nobody) == {"notes": 0, "shares": 0, "legacy_rows": 0, "grants": 0}
    assert _notes(env) == ["alice-1", "carol-1", "legacy-1"]


def test_purge_inside_a_caller_transaction_rolls_back_with_it(env):
    _seed_accounts(env)
    with env.factory() as db:
        assert sharing.purge_notes_owned_by("bob", db)["notes"] == 2
        db.rollback()
    assert "bob-1" in _notes(env) and "bob-2" in _notes(env)
    assert _legacy_rows(env) == ["alice-1", "bob-1", "bob-2", "legacy-1"]


def test_purge_leaves_upload_files_to_the_upload_cleanup(env):
    """Deleting the notes does not delete their images; the admin upload
    cleanup removes them once unreferenced and past its age cutoff."""
    env.seed("bob-img", owner="bob", content=f"![](/api/upload/{_UPLOAD_B})")
    before = (env.uploads_dir / _UPLOAD_B).read_bytes()
    assert sharing.purge_notes_owned_by("bob")["notes"] == 1
    assert _notes(env) == []
    assert (env.uploads_dir / _UPLOAD_B).read_bytes() == before


def test_purge_without_a_legacy_backup_table(env):
    env.seed("bob-1", owner="bob")
    assert _legacy_rows(env) is None
    assert sharing.purge_notes_owned_by("bob")["notes"] == 1
    assert _notes(env) == []


# ---------------------------------------------------------------------------
# manage_notes
# ---------------------------------------------------------------------------

def _new_id(result):
    return result["note_id"]


async def _share(env, nid, users, permission="view", user="alice"):
    r = await env.call("POST", f"/api/notes/{nid}/share", user=user, json={"users": users, "permission": permission})
    assert r.status_code == 200, r.text
    return r.json()


async def test_tool_works_on_a_note_shared_over_rest(env):
    nid = _new_id(await env.atool("alice", action="add", title="Trip", content="- [ ] Pack"))
    assert (await env.atool("bob", action="list")).get("response") == "No notes found."
    assert "not found" in (await env.atool("bob", action="update", id=nid, title="hax"))["error"]

    assert (await _share(env, nid, ["bob"], "edit"))["shared"] == ["bob"]
    listed = (await env.atool("bob", action="list"))["results"]
    assert "shared by alice, you can edit" in listed
    revision = (await env.atool("bob", action="view", id=nid))["body_revision"]
    assert "marked done" in (await env.atool("bob", action="toggle_item", id=nid, index=0,
                                             body_revision=revision))["response"]
    updated = await env.atool("bob", action="update", id=nid, content="- [x] Pack\n- [ ] Tickets")
    assert "updated" in updated["response"]
    assert "owner" in (await env.atool("bob", action="delete", id=nid))["error"]
    # Read-side visibility stays: the agent can say who has the note.
    assert "shared with bob: edit" in (await env.atool("alice", action="list"))["results"]
    assert "shared with bob: edit" in (await env.atool("alice", action="get", id=nid))["results"]

    assert (await env.call("DELETE", f"/api/notes/{nid}/share/bob")).status_code == 200
    assert (await env.atool("bob", action="list")).get("response") == "No notes found."


async def test_tool_view_permission_and_owner_only_fields(env):
    nid = _new_id(await env.atool("alice", action="add", title="Trip", content="- [ ] Pack"))
    await _share(env, nid, ["bob"])  # view is the default
    assert "Trip" in (await env.atool("bob", action="get", id=nid))["results"]
    assert "view only" in (await env.atool("bob", action="update", id=nid, title="hax"))["error"]
    assert "view only" in (await env.atool("bob", action="toggle_item", id=nid, index=0))["error"]
    await _share(env, nid, ["bob"], "edit")
    before = env.row(nid)
    stored = (before.label, before.note_type)
    for field, value in (("pinned", True), ("archived", True), ("color", "red"), ("due_date", "tomorrow 9am"),
                         ("label", "work"), ("note_type", "goal")):
        err = (await env.atool("bob", action="update", id=nid, content="y", **{field: value})).get("error", "")
        assert "owner" in err and field in err, field
    row = env.row(nid)
    assert row.content == "- [ ] Pack"
    assert (row.label, row.note_type) == stored, "a refused update writes nothing"
    assert stored[1] != "goal"


@pytest.mark.parametrize("field", ["pinned", "archived", "color", "label", "due_date", "note_type"])
async def test_tool_collaborator_sending_null_owner_only_field_is_refused(env, field):
    """A null over a stored owner-only value is still a write of that field."""
    env.seed("n1", owner="alice", content="x", shares=[("bob", "edit")],
             color="yellow", label="work", due_date="2026-12-01T09:00:00+00:00")
    stored = getattr(env.row("n1"), field)
    err = (await env.atool("bob", action="update", id="n1", content="y", **{field: None})).get("error", "")
    assert "owner" in err and field in err
    row = env.row("n1")
    assert row.content == "x" and getattr(row, field) == stored


async def test_tool_collaborator_cannot_hide_the_owners_note_with_nulls(env):
    """Archived and pinned sent as null must not leave the note in no list."""
    env.seed("n1", owner="alice", content="x", shares=[("bob", "edit")])
    result = await env.atool("bob", action="update", id="n1", content="x", archived=None, pinned=None)
    assert "owner" in result.get("error", "")
    row = env.row("n1")
    assert (row.archived, row.pinned) == (False, False)
    listed = (await env.call("GET", "/api/notes", user="alice")).json()["notes"]
    assert "n1" in [n["id"] for n in listed]


@pytest.mark.parametrize("value", [None, "false", "true", 0, 1, "yes", []])
@pytest.mark.parametrize("field", ["pinned", "archived"])
async def test_tool_update_takes_only_true_or_false_for_pin_and_archive(env, field, value):
    env.seed("n1", owner="alice", content="x")
    err = (await env.atool("alice", action="update", id="n1", content="y", **{field: value})).get("error", "")
    assert field in err and "true or false" in err
    row = env.row("n1")
    assert row.content == "x" and getattr(row, field) is False
    assert "error" not in await env.atool("alice", action="update", id="n1", **{field: True})
    assert getattr(env.row("n1"), field) is True


@pytest.mark.parametrize("action", ["share", "unshare", "leave", "Share", " LEAVE "])
def test_tool_refuses_to_change_sharing(env, action):
    """Sharing is a human action in the Notes window; the tool never changes it."""
    env.seed("n1", owner="alice", shares=[("bob", "edit")])
    as_owner = env.tool("alice", action=action, id="n1", users=["carol"], permission="edit")
    as_collaborator = env.tool("bob", action=action, id="n1")
    for result in (as_owner, as_collaborator):
        assert result == {"error": sharing.SHARING_UI_ONLY_ERROR, "exit_code": 1}
    assert "Notes window" in sharing.SHARING_UI_ONLY_ERROR
    assert env.grants() == [("n1", "bob", "edit")]


def test_manage_notes_schema_offers_no_sharing_actions():
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    schema = next(s["function"] for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "manage_notes")
    props = schema["parameters"]["properties"]
    assert not set(props["action"]["enum"]) & {"share", "unshare", "leave"}
    assert "users" not in props and "permission" not in props


async def test_rest_leave_is_the_collaborators_way_out(env):
    """Was the tool's `leave`; a collaborator leaves through the REST route."""
    env.seed("n1", owner="alice", shares=[("bob", "view"), ("carol", "view")])
    r = await env.call("DELETE", "/api/notes/n1/share/carol", user="bob")
    assert r.status_code == 403
    r = await env.call("DELETE", "/api/notes/n1/share/bob", user="bob")
    assert r.status_code == 200 and r.json()["left"] is True
    assert env.grants("n1") == [("n1", "carol", "view")]


async def test_rest_share_writes_nothing_without_readable_accounts(env):
    """Was the tool's fail-closed case: no auth store means nothing is shared."""
    env.seed("n1", owner="alice")
    env.app.state.auth_manager = None
    r = await env.call("POST", "/api/notes/n1/share", json={"users": ["bob"]})
    assert r.status_code == 503
    assert env.grants() == []


async def test_rest_share_with_unknown_account_writes_nothing(env):
    env.seed("n1", owner="alice")
    r = await env.call("POST", "/api/notes/n1/share", json={"users": ["ghost"], "permission": "view"})
    assert r.status_code == 404
    assert env.grants() == []


def test_tool_prefix_lookup_cannot_reach_a_strangers_note(env):
    env.seed("abcd1234-stranger", owner="alice", title="secret")
    env.seed("ffff0000-mine", owner="bob", title="mine")
    missing = env.tool("bob", action="get", id="zzzz")
    stranger = env.tool("bob", action="get", id="abcd")
    assert stranger == {"error": "Note 'abcd' not found", "exit_code": 1}
    assert missing == {"error": "Note 'zzzz' not found", "exit_code": 1}
    # LIKE wildcards are literal, not "any note".
    assert env.tool("bob", action="get", id="%")["error"] == "Note '%' not found"
    assert env.tool("bob", action="get", id="_")["error"] == "Note '_' not found"
    assert "mine" in env.tool("bob", action="get", id="ffff")["results"]


def test_tool_prefix_prefers_own_note_over_a_shared_one(env):
    env.seed("abcd-shared", owner="alice", title="theirs", shares=[("bob", "view")])
    env.seed("abcd-own", owner="bob", title="mine")
    assert "mine" in env.tool("bob", action="get", id="abcd")["results"]


def test_tool_delete_removes_grants(env):
    env.seed("n1", owner="alice", shares=[("bob", "view")])
    assert "Deleted" in env.tool("alice", action="delete", id="n1")["response"]
    assert env.grants() == []


def test_can_share_notes_is_a_default_privilege():
    assert auth_mod.DEFAULT_PRIVILEGES["can_share_notes"] is True
    assert auth_mod.ADMIN_PRIVILEGES["can_share_notes"] is True
