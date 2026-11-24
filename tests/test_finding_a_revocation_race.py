"""Regression tests for Finding A: Permission Revocation Race.

Invariants verified:
- A1. Revocation before modification commits:
  Bob's pending write loaded under edit permission must not commit if Alice revokes Bob first.
- A2. Valid modification before revocation:
  Bob's modification committed before Alice revokes remains valid.
- A3. Permission downgrade:
  Bob's pending write loaded under edit permission must not commit if Alice downgrades Bob to view.
- A4. Other mutation paths:
  Checklist toggles, appends, and agent-initiated writes respect the same authorization invariant.
"""
import json
import pytest
from core.database import Note, NoteShare
import routes.note.note_routes as nr
from src.tools.notes import do_manage_notes
from tests.helpers.note_routes_client import notes_routes_harness


@pytest.fixture
def notes(monkeypatch, tmp_path):
    with notes_routes_harness(monkeypatch, tmp_path) as harness:
        yield harness


@pytest.mark.asyncio
async def test_a1_revocation_before_modification_commits(notes, monkeypatch):
    """A1. Bob's content/title update pauses after initial auth; Alice revokes; Bob must not modify note."""
    base = notes.seed("n1", "Alice original text")

    # Hook after initial authorization in _update_note_once: Alice revokes Bob before Bob commits.
    original_fold = nr._fold_legacy_columns
    paused = False

    def hook_after_auth(note):
        nonlocal paused
        if not paused and note.id == "n1":
            paused = True
            # Alice revokes Bob in a separate transaction and commits
            from core.database import SessionLocal
            with SessionLocal() as db_alice:
                db_alice.query(NoteShare).filter(NoteShare.note_id == "n1", NoteShare.shared_with == "bob").delete()
                db_alice.commit()
        return original_fold(note)

    monkeypatch.setattr(nr, "_fold_legacy_columns", hook_after_auth)

    r = await notes.call("PUT", "/api/notes/n1", user="bob", json={
        "content": "Bob unauthorized edit",
        "base_updated_at": base,
    })
    assert paused, "Revocation hook should have executed"
    # Revoked collaborator has no access; route returns 404 (or 403) and note is untouched
    assert r.status_code in (403, 404)
    assert notes.content("n1") == "Alice original text"


@pytest.mark.asyncio
async def test_a1_revocation_before_title_modification_commits(notes, monkeypatch):
    """A1 (title variant): Title-only update where body guard does not run must also not commit."""
    base = notes.seed("n1", "Alice original text")

    # For title-only update, hook stored_stamp or inspect note after load
    original_stored_stamp = nr.stored_stamp
    paused = False

    def hook_after_auth(db, note_id):
        nonlocal paused
        stamp = original_stored_stamp(db, note_id)
        if not paused and note_id == "n1":
            paused = True
            from core.database import SessionLocal
            with SessionLocal() as db_alice:
                db_alice.query(NoteShare).filter(NoteShare.note_id == "n1", NoteShare.shared_with == "bob").delete()
                db_alice.commit()
        return stamp

    monkeypatch.setattr(nr, "stored_stamp", hook_after_auth)

    r = await notes.call("PUT", "/api/notes/n1", user="bob", json={
        "title": "Bob malicious title",
        "base_updated_at": base,
    })
    assert paused, "Revocation hook should have executed"
    assert r.status_code in (403, 404)
    with nr.SessionLocal() as db:
        row = db.get(Note, "n1")
        assert row.title == "n1", "Title must not have changed"


@pytest.mark.asyncio
async def test_a2_valid_modification_before_revocation(notes):
    """A2. Bob's modification commits, then Alice revokes. The committed modification remains valid."""
    base = notes.seed("n1", "Alice original text")

    # Bob's edit commits first
    r = await notes.call("PUT", "/api/notes/n1", user="bob", json={
        "content": "Bob valid edit",
        "base_updated_at": base,
    })
    assert r.status_code == 200
    assert notes.content("n1") == "Bob valid edit"

    # Alice revokes Bob afterward
    r_unshare = await notes.call("DELETE", "/api/notes/n1/share/bob", user="alice")
    assert r_unshare.status_code == 200

    # The modification remains intact
    assert notes.content("n1") == "Bob valid edit"

    # Subsequent writes by Bob are refused
    r_after = await notes.call("PUT", "/api/notes/n1", user="bob", json={
        "content": "Bob subsequent edit",
    })
    assert r_after.status_code == 404


@pytest.mark.asyncio
async def test_a3_permission_downgrade(notes, monkeypatch):
    """A3. Alice changes Bob's permission to view while Bob's write is in flight. Write must be refused."""
    base = notes.seed("n1", "Alice original text")

    original_fold = nr._fold_legacy_columns
    paused = False

    def hook_after_auth(note):
        nonlocal paused
        if not paused and note.id == "n1":
            paused = True
            # Alice downgrades Bob to view in a separate transaction and commits
            from core.database import SessionLocal
            with SessionLocal() as db_alice:
                share = db_alice.query(NoteShare).filter(NoteShare.note_id == "n1", NoteShare.shared_with == "bob").first()
                share.permission = "view"
                db_alice.commit()
        return original_fold(note)

    monkeypatch.setattr(nr, "_fold_legacy_columns", hook_after_auth)

    r = await notes.call("PUT", "/api/notes/n1", user="bob", json={
        "content": "Bob unauthorized edit after downgrade",
        "base_updated_at": base,
    })
    assert paused, "Downgrade hook should have executed"
    assert r.status_code == 403
    assert "view only" in r.json()["detail"].lower()
    assert notes.content("n1") == "Alice original text"


@pytest.mark.asyncio
async def test_a4_checklist_toggle_revocation_race(notes, monkeypatch):
    """A4. Checklist toggle paused after load fails when revoked and does not modify the checklist."""
    notes.seed("n1", "- [ ] task 1\n- [ ] task 2")
    rev = notes.revision("n1")

    original_fold = nr._fold_legacy_columns
    paused = False

    def hook_after_auth(note):
        nonlocal paused
        if not paused and note.id == "n1":
            paused = True
            from core.database import SessionLocal
            with SessionLocal() as db_alice:
                db_alice.query(NoteShare).filter(NoteShare.note_id == "n1", NoteShare.shared_with == "bob").delete()
                db_alice.commit()
        return original_fold(note)

    monkeypatch.setattr(nr, "_fold_legacy_columns", hook_after_auth)

    r = await notes.call("POST", "/api/notes/n1/items/0/toggle", user="bob", json={
        "body_revision": rev,
    })
    assert paused, "Revocation hook should have executed"
    assert r.status_code in (403, 404)
    assert notes.content("n1") == "- [ ] task 1\n- [ ] task 2"


@pytest.mark.asyncio
async def test_a4_checklist_append_downgrade_race(notes, monkeypatch):
    """A4. Checklist append paused after load fails when downgraded to view."""
    notes.seed("n1", "- [ ] task 1")

    original_fold = nr._fold_legacy_columns
    paused = False

    def hook_after_auth(note):
        nonlocal paused
        if not paused and note.id == "n1":
            paused = True
            from core.database import SessionLocal
            with SessionLocal() as db_alice:
                share = db_alice.query(NoteShare).filter(NoteShare.note_id == "n1", NoteShare.shared_with == "bob").first()
                share.permission = "view"
                db_alice.commit()
        return original_fold(note)

    monkeypatch.setattr(nr, "_fold_legacy_columns", hook_after_auth)

    r = await notes.call("POST", "/api/notes/n1/items", user="bob", json={
        "text": "unauthorized task",
    })
    assert paused, "Downgrade hook should have executed"
    assert r.status_code == 403
    assert notes.content("n1") == "- [ ] task 1"


@pytest.mark.asyncio
async def test_a4_agent_initiated_write_revocation_race(notes, monkeypatch):
    """A4. Agent-initiated write through manage_notes tool respects revocation race."""
    notes.seed("n1", "Alice initial agent note")
    rev = notes.revision("n1")

    paused = False

    import src.tools.notes as tools_notes
    original_undo = tools_notes.undo_snapshot

    def hook_after_load(note):
        nonlocal paused
        snap = original_undo(note)
        if not paused and note.id == "n1":
            paused = True
            from core.database import SessionLocal
            with SessionLocal() as db_alice:
                db_alice.query(NoteShare).filter(NoteShare.note_id == "n1", NoteShare.shared_with == "bob").delete()
                db_alice.commit()
        return snap

    monkeypatch.setattr(tools_notes, "undo_snapshot", hook_after_load)

    res = await do_manage_notes(json.dumps({
        "action": "update",
        "id": "n1",
        "content": "Bob agent unauthorized content",
        "body_revision": rev,
    }), owner="bob")

    assert paused, "Hook should have executed"
    assert res.get("exit_code") == 1
    assert "not found" in res.get("error", "").lower() or "view only" in res.get("error", "").lower()
    assert notes.content("n1") == "Alice initial agent note"


def test_a5_postgresql_commit_ordering_revocation():
    """A5 / Finding E: PostgreSQL READ COMMITTED revocation commit ordering.

    Under READ COMMITTED, if Bob executes UPDATE notes before Alice's revocation commits,
    Bob's UPDATE rowcount is 1. The pre-commit check check_collaborator_permission
    must execute right before commit, detect Alice's committed revocation (or downgrade),
    roll back Bob's transaction, and prevent Bob's modification from persisting.
    """
    import os
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base, Note, NoteShare
    from core.note_writes import commit_if_unchanged, stored_stamp, NotePermissionRevoked

    pg_url = os.environ.get("ODYSSEUS_TEST_PG_URL")
    if not pg_url:
        pytest.skip("ODYSSEUS_TEST_PG_URL environment variable not set")
    try:
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            pass
    except Exception as exc:
        pytest.skip(f"PostgreSQL server not reachable at {pg_url}: {exc}")

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    note_id = f"test-pg-race-{uuid.uuid4().hex[:8]}"

    # Setup: Alice creates note, shares with Bob (permission=edit)
    with Session() as db:
        alice_note = Note(id=note_id, owner="alice", title="Alice PG Note", content="Original Alice Content")
        db.add(alice_note)
        share = NoteShare(id=f"share-{note_id}", note_id=note_id, shared_with="bob", permission="edit")
        db.add(share)
        db.commit()

    try:
        # Step 1: Bob starts transaction and loads note
        db_bob = Session()
        bob_note = db_bob.query(Note).filter(Note.id == note_id).first()
        stamp = stored_stamp(db_bob, note_id)
        bob_note.content = "Bob Malicious PG Content"

        # Step 2: Alice in a concurrent transaction revokes Bob's share and commits
        with Session() as db_alice:
            db_alice.query(NoteShare).filter(NoteShare.note_id == note_id, NoteShare.shared_with == "bob").delete()
            db_alice.commit()

        # Step 3: Bob tries to commit his change.
        # commit_if_unchanged must detect the revocation right before commit and raise NotePermissionRevoked
        with pytest.raises(NotePermissionRevoked) as exc:
            commit_if_unchanged(db_bob, bob_note, stamp, user="bob")
        assert exc.value.status_code == 404
        db_bob.close()

        # Step 4: Verify Alice's note was NOT modified
        with Session() as db_verify:
            verified_note = db_verify.query(Note).filter(Note.id == note_id).first()
            assert verified_note.content == "Original Alice Content", "Bob's content must not have persisted"

    finally:
        with Session() as cleanup_db:
            cleanup_db.query(NoteShare).filter(NoteShare.note_id == note_id).delete()
            cleanup_db.query(Note).filter(Note.id == note_id).delete()
            cleanup_db.commit()


def test_a5_postgresql_guarded_update_before_revocation():
    """A5 / Finding E: PostgreSQL two-transaction interleaving where Bob executes his guarded UPDATE
    BEFORE Alice attempts revocation, calling production commit_if_unchanged(...).

    Interleaving sequence with threading.Event:
    1. Bob prepares his update on bob_note and calls commit_if_unchanged(db_bob, bob_note, stamp, user="bob") in a background thread.
    2. Inside commit_if_unchanged, Bob's guarded UPDATE statement executes against PostgreSQL (rowcount == 1).
    3. Before check_collaborator_permission queries the share, Bob's thread pauses and signals event_bob_updated.
    4. Alice's thread (main thread) executes DELETE FROM note_shares and COMMITS in Transaction 2.
    5. Alice signals event_alice_revoked.
    6. Bob's thread resumes, check_collaborator_permission detects the revocation, rolls back Bob's transaction, and raises NotePermissionRevoked.
    7. Bob's thread catches NotePermissionRevoked.
    8. We verify in a clean session that Alice's note was NOT modified.
    """
    import os
    import threading
    import unittest.mock as mock
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base, Note, NoteShare
    import core.note_writes as cnw
    from core.note_writes import commit_if_unchanged, stored_stamp, NotePermissionRevoked

    pg_url = os.environ.get("ODYSSEUS_TEST_PG_URL")
    if not pg_url:
        pytest.skip("ODYSSEUS_TEST_PG_URL environment variable not set")

    try:
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            pass
    except Exception as exc:
        pytest.skip(f"PostgreSQL server not reachable at {pg_url}: {exc}")

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    note_id = f"test-pg-race2-{uuid.uuid4().hex[:8]}"

    # Setup: Alice creates note, shares with Bob (permission=edit)
    with Session() as db:
        alice_note = Note(id=note_id, owner="alice", title="Alice PG Note 2", content="Original Alice Content 2")
        db.add(alice_note)
        share = NoteShare(id=f"share-{note_id}", note_id=note_id, shared_with="bob", permission="edit")
        db.add(share)
        db.commit()

    try:
        event_bob_updated = threading.Event()
        event_alice_revoked = threading.Event()
        bob_exc = None

        orig_check = cnw.check_collaborator_permission

        def hook_check_collaborator_permission(db, nid, user):
            if nid == note_id and user == "bob":
                event_bob_updated.set()
                assert event_alice_revoked.wait(timeout=5.0), "Timed out waiting for Alice to commit revocation"
            return orig_check(db, nid, user)

        db_bob = Session()
        bob_note = db_bob.query(Note).filter(Note.id == note_id).first()
        stamp = stored_stamp(db_bob, note_id)
        bob_note.content = "Bob Early Executed Content"

        def run_bob():
            nonlocal bob_exc
            try:
                with mock.patch.object(cnw, "check_collaborator_permission", side_effect=hook_check_collaborator_permission):
                    commit_if_unchanged(db_bob, bob_note, stamp, user="bob")
            except Exception as e:
                bob_exc = e
            finally:
                db_bob.close()

        t_bob = threading.Thread(target=run_bob)
        t_bob.start()

        assert event_bob_updated.wait(timeout=5.0), "Timed out waiting for Bob's UPDATE to execute"

        with Session() as db_alice:
            deleted_count = db_alice.query(NoteShare).filter(NoteShare.note_id == note_id, NoteShare.shared_with == "bob").delete()
            assert deleted_count == 1
            db_alice.commit()

        event_alice_revoked.set()
        t_bob.join(timeout=5.0)
        assert not t_bob.is_alive(), "Bob thread should have completed"

        assert isinstance(bob_exc, NotePermissionRevoked), f"Expected NotePermissionRevoked, got {bob_exc}"
        assert bob_exc.status_code == 404

        with Session() as db_verify:
            verified_note = db_verify.query(Note).filter(Note.id == note_id).first()
            assert verified_note.content == "Original Alice Content 2", "Bob's content must not have persisted despite prior UPDATE execution"

    finally:
        with Session() as cleanup_db:
            cleanup_db.query(NoteShare).filter(NoteShare.note_id == note_id).delete()
            cleanup_db.query(Note).filter(Note.id == note_id).delete()
            cleanup_db.commit()


def test_a5_postgresql_downgrade_before_commit():
    """A5 / Finding E: PostgreSQL two-transaction interleaving where Alice downgrades Bob's permission
    from 'edit' to 'view' before Bob's transaction commits, calling production commit_if_unchanged(...).
    """
    import os
    import threading
    import unittest.mock as mock
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base, Note, NoteShare
    import core.note_writes as cnw
    from core.note_writes import commit_if_unchanged, stored_stamp, NotePermissionRevoked

    pg_url = os.environ.get("ODYSSEUS_TEST_PG_URL")
    if not pg_url:
        pytest.skip("ODYSSEUS_TEST_PG_URL environment variable not set")

    try:
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            pass
    except Exception as exc:
        pytest.skip(f"PostgreSQL server not reachable at {pg_url}: {exc}")

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    note_id = f"test-pg-race-dw-{uuid.uuid4().hex[:8]}"

    # Setup: Alice creates note, shares with Bob (permission=edit)
    with Session() as db:
        alice_note = Note(id=note_id, owner="alice", title="Alice PG Downgrade Note", content="Original Alice Content")
        db.add(alice_note)
        share = NoteShare(id=f"share-{note_id}", note_id=note_id, shared_with="bob", permission="edit")
        db.add(share)
        db.commit()

    try:
        event_bob_updated = threading.Event()
        event_alice_downgraded = threading.Event()
        bob_exc = None

        orig_check = cnw.check_collaborator_permission

        def hook_check_collaborator_permission(db, nid, user):
            if nid == note_id and user == "bob":
                event_bob_updated.set()
                assert event_alice_downgraded.wait(timeout=5.0), "Timed out waiting for Alice to downgrade"
            return orig_check(db, nid, user)

        db_bob = Session()
        bob_note = db_bob.query(Note).filter(Note.id == note_id).first()
        stamp = stored_stamp(db_bob, note_id)
        bob_note.content = "Bob Content After Downgrade"

        def run_bob():
            nonlocal bob_exc
            try:
                with mock.patch.object(cnw, "check_collaborator_permission", side_effect=hook_check_collaborator_permission):
                    commit_if_unchanged(db_bob, bob_note, stamp, user="bob")
            except Exception as e:
                bob_exc = e
            finally:
                db_bob.close()

        t_bob = threading.Thread(target=run_bob)
        t_bob.start()

        assert event_bob_updated.wait(timeout=5.0), "Timed out waiting for Bob's UPDATE to execute"

        with Session() as db_alice:
            share = db_alice.query(NoteShare).filter(NoteShare.note_id == note_id, NoteShare.shared_with == "bob").first()
            assert share is not None
            share.permission = "view"
            db_alice.commit()

        event_alice_downgraded.set()
        t_bob.join(timeout=5.0)
        assert not t_bob.is_alive(), "Bob thread should have completed"

        assert isinstance(bob_exc, NotePermissionRevoked), f"Expected NotePermissionRevoked, got {bob_exc}"
        assert bob_exc.status_code == 403
        assert "view only" in bob_exc.message.lower()

        with Session() as db_verify:
            verified_note = db_verify.query(Note).filter(Note.id == note_id).first()
            assert verified_note.content == "Original Alice Content", "Bob's content must not have persisted after downgrade"

    finally:
        with Session() as cleanup_db:
            cleanup_db.query(NoteShare).filter(NoteShare.note_id == note_id).delete()
            cleanup_db.query(Note).filter(Note.id == note_id).delete()
            cleanup_db.commit()


def test_a5_sqlite_pre_commit_collaborator_check(tmp_path):
    """A5 / Finding F (SQLite): Pre-commit collaborator check enforces revocation and rollback."""
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base, Note, NoteShare
    from core.note_writes import commit_if_unchanged, stored_stamp, NotePermissionRevoked

    db_path = tmp_path / "sqlite_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    note_id = f"test-sqlite-{uuid.uuid4().hex[:8]}"

    # Setup: Alice note shared with Bob (edit)
    with Session() as db:
        db.add(Note(id=note_id, owner="alice", title="Alice SQLite Note", content="Original Content"))
        db.add(NoteShare(id=f"share-{note_id}", note_id=note_id, shared_with="bob", permission="edit"))
        db.commit()

    # Bob prepares update
    with Session() as db_bob:
        bob_note = db_bob.query(Note).filter(Note.id == note_id).first()
        stamp = stored_stamp(db_bob, note_id)
        bob_note.content = "Bob Attempted Content"

        # Alice revokes Bob before commit
        with Session() as db_alice:
            db_alice.query(NoteShare).filter(NoteShare.note_id == note_id, NoteShare.shared_with == "bob").delete()
            db_alice.commit()

        # Bob's commit must be refused
        with pytest.raises(NotePermissionRevoked) as exc:
            commit_if_unchanged(db_bob, bob_note, stamp, user="bob")
        assert exc.value.status_code == 404

    # Verify content preserved
    with Session() as db:
        note = db.query(Note).filter(Note.id == note_id).first()
        assert note.content == "Original Content"
