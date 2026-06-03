"""_owner_filter must not blank out the gallery in single-user mode.

When AUTH_ENABLED=false, get_current_user returns None. The gallery main
list and stats treat None as "show all images" (`if user is not None`), but
_owner_filter returned q.filter(False) (zero rows) for None. So the tag and
model filter chips were always empty and clear-user-tags / clear-ai-tags /
dedupe-tags silently no-oped. _owner_filter must match the main list: no
filter when user is None, owner-scoped otherwise.
"""
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryImage
from routes.gallery_helpers import _owner_filter

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(f"sqlite:///{_TMPDB.name}", connect_args={"check_same_thread": False}, poolclass=NullPool)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _seed(*owners):
    db = _TS()
    try:
        db.query(GalleryImage).delete()
        for o in owners:
            db.add(GalleryImage(id=str(uuid.uuid4()), filename=f"{uuid.uuid4().hex}.png", owner=o))
        db.commit()
    finally:
        db.close()


def test_none_user_returns_all_rows():
    _seed(None, None, "alice")
    db = _TS()
    try:
        n = _owner_filter(db.query(GalleryImage), None).count()
        assert n == 3  # old code returned 0
    finally:
        db.close()


def test_named_user_is_still_scoped():
    _seed("alice", "alice", "bob", None)
    db = _TS()
    try:
        assert _owner_filter(db.query(GalleryImage), "alice").count() == 2
        assert _owner_filter(db.query(GalleryImage), "bob").count() == 1
    finally:
        db.close()
