"""Gallery tag-pill filter must match a tag exactly, not as a substring.

Tags are stored comma+space separated ("cat, dog"). The filter used
GalleryImage.tags.ilike("%cat%"), so clicking the pill "cat" also returned
images tagged "category", "scatter", "wildcat", and a literal %/_ in a tag
acted as a wildcard. The fix boundary-matches against the comma-delimited
list.
"""
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryImage

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(f"sqlite:///{_TMPDB.name}", connect_args={"check_same_thread": False}, poolclass=NullPool)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _route(router, path):
    for r in router.routes:
        if r.path == path and "GET" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(path)


@pytest.fixture
def library(monkeypatch):
    import routes.gallery_routes as gr
    monkeypatch.setattr(gr, "SessionLocal", _TS)
    monkeypatch.setattr(gr, "get_current_user", lambda request: None, raising=False)
    return _route(gr.setup_gallery_routes(), "/api/gallery/library")


def _seed(*tagsets):
    db = _TS()
    try:
        db.query(GalleryImage).delete()
        for tg in tagsets:
            db.add(GalleryImage(id=str(uuid.uuid4()), filename=f"{uuid.uuid4().hex}.png",
                                tags=tg, is_active=True))
        db.commit()
    finally:
        db.close()


def test_exact_tag_match_excludes_substring_tags(library):
    import asyncio
    _seed("cat", "category", "scatter", "cat, dog", "Cat")
    # Pass every query param explicitly: called directly (not through FastAPI),
    # the Query(...) defaults are sentinel objects, not their values, and would
    # otherwise reach the SQL query as unbound Query instances.
    res = asyncio.run(library(
        request=None, search=None, tag="cat", model=None, album=None,
        favorites=False, sort="recent", seed=None, offset=0, limit=100,
    ))
    got = {tuple(sorted(it["tags"].lower().replace(" ", "").split(","))) for it in res["items"]}
    # rows whose tag set contains exactly "cat": "cat", "cat,dog", "Cat"
    assert ("cat",) in got
    assert ("cat", "dog") in got
    # category / scatter must NOT be returned
    n = len(res["items"])
    assert n == 3, f"expected 3, got {n}: {[it['tags'] for it in res['items']]}"
