"""Edges of core.notes_sharing the route and tool tests do not reach.

A rename that merges an edit grant into a view grant, and the helpers'
answers when there is no user (auth disabled, a blank name) or no account
store. Runs against a disposable database.
"""
from types import SimpleNamespace

import pytest

from core import notes_sharing as sharing
from core.database import Note, NoteShare
from tests.helpers.database import disposable_database


@pytest.fixture
def factory(tmp_path):
    with disposable_database(tmp_path) as make:
        with make() as db:
            db.add(Note(id="n1", owner="alice", title="Trip", content="x"))
            db.add(Note(id="n2", owner="carol", title="Other", content="y"))
            db.add(NoteShare(id="n1-bob", note_id="n1", shared_with="bob", permission="edit"))
            db.add(NoteShare(id="n1-bobby", note_id="n1", shared_with="bobby", permission="view"))
            db.commit()
        yield make


def _grants(make):
    with make() as db:
        return sorted((s.note_id, s.shared_with, s.permission) for s in db.query(NoteShare).all())


def test_a_rename_onto_a_view_grant_keeps_the_edit_one(factory):
    # bob (edit) is renamed to bobby, who already had view on the same note:
    # one grant is left, and it is the stronger.
    with factory() as db:
        assert sharing.rename_shared_user(db, "bob", "bobby") == 1
        db.commit()
    assert _grants(factory) == [("n1", "bobby", "edit")]


@pytest.mark.parametrize("old,new", [("", "bobby"), ("bob", ""), ("Bob", "bob"), (None, None)])
def test_a_rename_with_a_blank_or_unchanged_name_moves_nothing(factory, old, new):
    with factory() as db:
        assert sharing.rename_shared_user(db, old, new) == 0
        db.commit()
    assert _grants(factory) == [("n1", "bob", "edit"), ("n1", "bobby", "view")]


def test_removing_grants_for_a_blank_name_removes_nothing(factory):
    with factory() as db:
        assert sharing.remove_share(db, "n1", "  ") == 0
        assert sharing.delete_shares_for_user(db, None) == 0
        db.commit()
    assert len(_grants(factory)) == 2


def test_without_a_user_every_note_is_readable_and_no_ids_means_no_grants(factory):
    # Auth disabled: the single-user install reads every note, as before sharing.
    with factory() as db:
        assert sorted(n.id for n in sharing.readable_notes_query(db, None).all()) == ["n1", "n2"]
        assert sorted(n.id for n in sharing.readable_notes_query(db, "bob").all()) == ["n1"]
        assert sharing.shares_by_note(db, []) == {}
        assert sharing.shares_by_note(db, [None, ""]) == {}


def test_known_usernames_needs_an_account_store():
    assert sharing.known_usernames(None) is None
    assert sharing.known_usernames(SimpleNamespace(users=None)) is None
    assert sharing.known_usernames(SimpleNamespace(users={"Alice": {}, " ": {}, "bob": {}})) == {"alice", "bob"}
