"""Note route branches no other test reaches: the tag filter over notes shared
with me, writes that keep losing a race, an Undo snapshot carrying a field it
does not restore, a toggle sent with the current version, sharing without an
account store, and a media request for something that is not an upload id.

Same harness as tests/test_notes_sharing.py (real routes over a disposable
database, the identity from a test header).
"""
import json

import routes.note_routes as nr
from core.database import Note
from core.note_agent_scope import content_digest, snapshot
from core.note_writes import NOTE_WRITE_ATTEMPTS, NoteChanged
from tests.test_notes_sharing import env  # noqa: F401  (fixture)


async def test_the_tag_filter_keeps_notes_shared_with_me_that_carry_the_tag(env):
    env.seed("mine", owner="alice", label="work", content="a")
    env.seed("theirs-work", owner="carol", label="work", content="b", shares=[("alice", "view")])
    env.seed("theirs-home", owner="carol", label="home", content="c", shares=[("alice", "view")])
    r = await env.call("GET", "/api/notes?label=work")
    assert r.status_code == 200
    assert [n["id"] for n in r.json()["notes"]] == ["mine", "theirs-work"], "mine first, then the shared one"


async def test_a_write_without_a_base_retries_a_lost_race_then_answers_409(env, monkeypatch):
    env.seed("n1", owner="alice", content="x")
    attempts = []

    def lose(db, note, stamp):
        attempts.append(note.id)
        db.rollback()
        raise NoteChanged(note.id)

    monkeypatch.setattr(nr, "commit_if_unchanged", lose)
    # A body write with no token at all is refused before any attempt.
    r = await env.call("PUT", "/api/notes/n1", json={"content": "mine"})
    assert r.status_code == 409, r.text
    assert attempts == []
    # No base, only the body revision: the write is re-applied to the newer
    # row (re-checking the revision), up to the limit.
    r = await env.call("PUT", "/api/notes/n1", json={"content": "mine", "body_revision": env.revision("n1")})
    assert r.status_code == 409, r.text
    assert len(attempts) == NOTE_WRITE_ATTEMPTS
    assert r.json()["detail"]["note"]["content"] == "x"
    # With a base the client asked for that version: one try, then the 409.
    attempts.clear()
    base = (await env.call("GET", "/api/notes/n1")).json()["updated_at"]
    r = await env.call("PUT", "/api/notes/n1", json={"content": "mine", "base_updated_at": base})
    assert r.status_code == 409
    assert len(attempts) == 1


async def test_an_undo_restores_only_the_fields_undo_owns_and_a_lost_race_is_a_409(env, monkeypatch):
    env.seed("n1", owner="alice", title="Agent title", content="agent text")
    with env.factory() as db:
        note = db.get(Note, "n1")
        note.agent_undo = json.dumps({
            # An older or hand-edited slot: `owner` is not a field Undo restores.
            "before": {"title": "Mine", "content": "my text", "owner": "mallory"},
            "after": content_digest(snapshot(note)),
            "session_id": "chat-1",
            "at": "2026-11-13T01:00:00",
        })
        db.commit()
    lost = {"n": 0}
    real_commit = nr.commit_if_unchanged

    def lose_once(db, note, stamp):
        if lost["n"] == 0:
            lost["n"] += 1
            db.rollback()
            raise NoteChanged(note.id)
        return real_commit(db, note, stamp)

    monkeypatch.setattr(nr, "commit_if_unchanged", lose_once)
    r = await env.call("POST", "/api/notes/n1/agent-undo")
    assert r.status_code == 409, r.text
    assert env.row("n1").content == "agent text", "a lost race restores nothing"
    r = await env.call("POST", "/api/notes/n1/agent-undo")
    assert r.status_code == 200, r.text
    row = env.row("n1")
    assert (row.title, row.content, row.owner) == ("Mine", "my text", "alice")


async def test_a_toggle_sent_with_the_current_version_goes_through(env):
    env.seed("n1", owner="alice", content="- [ ] pack")
    current = (await env.call("GET", "/api/notes/n1")).json()["updated_at"]
    r = await env.call("POST", "/api/notes/n1/items/0/toggle", json={"base_updated_at": current})
    assert r.status_code == 200, r.text
    assert env.row("n1").content == "- [x] pack"


async def test_sharing_without_an_account_store_is_unavailable_not_an_empty_share(env):
    env.seed("n1", owner="alice", content="x")
    env.auth.users = None  # no account list to check the names against
    r = await env.call("POST", "/api/notes/n1/share", json={"users": ["bob"], "permission": "view"})
    assert r.status_code == 503
    assert env.grants("n1") == []


async def test_media_for_something_that_is_not_an_upload_id_is_404(env):
    env.seed("n1", owner="alice", content="x")
    r = await env.call("GET", "/api/notes/n1/media/..%2F..%2Fapp.db")
    assert r.status_code == 404
    r = await env.call("GET", "/api/notes/n1/media/not-an-upload")
    assert r.status_code == 404


async def test_note_media_never_follows_an_upload_link_out_of_the_upload_folder(env, tmp_path):
    # alice's upload id now names a link to a file outside the upload folder:
    # the note shows it, the owner matches, and still nothing outside is served.
    from tests.test_notes_sharing import _UPLOAD_A
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\nnot an upload")
    target = env.uploads_dir / _UPLOAD_A
    target.unlink()
    target.symlink_to(secret)
    env.seed("n1", owner="alice", content=f"![](/api/upload/{_UPLOAD_A})")
    r = await env.call("GET", f"/api/notes/n1/media/{_UPLOAD_A}")
    assert r.status_code == 403
    assert b"not an upload" not in r.content


def test_an_upload_is_found_in_a_subfolder_and_never_through_a_link(tmp_path):
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from routes.upload_routes import resolve_upload_file_path

    root = tmp_path / "uploads"
    (root / "2026" / "11").mkdir(parents=True)
    (root / "2026" / "11" / "a.png").write_bytes(b"png")
    (root / "dir.png").mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")
    (root / "2026" / "11" / "link.png").symlink_to(outside)
    handler = SimpleNamespace(upload_dir=str(root))
    assert resolve_upload_file_path(handler, "a.png") == str(root / "2026" / "11" / "a.png")
    for file_id, status in (("link.png", 403), ("dir.png", 404), ("missing.png", 404)):
        with pytest.raises(HTTPException) as caught:
            resolve_upload_file_path(handler, file_id)
        assert caught.value.status_code == status, file_id
