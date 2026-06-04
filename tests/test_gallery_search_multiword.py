"""Gallery library search must match multi-word queries and escape wildcards.

The gallery search used a single "%{search}%" LIKE over prompt/tags/ai_tags,
so a multi-word query ("red car") only matched the exact adjacent phrase and
missed an image whose prompt is "a car that is red" — the same bug the
document library already fixed with per-token AND. The query was also
interpolated unescaped, so a literal "%" or "_" acted as a wildcard.
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


def _seed(*prompts):
    db = _TS()
    try:
        db.query(GalleryImage).delete()
        for pr in prompts:
            db.add(GalleryImage(id=str(uuid.uuid4()), filename=f"{uuid.uuid4().hex}.png",
                                prompt=pr, is_active=True))
        db.commit()
    finally:
        db.close()


async def _call(library, **kw):
    # Fill every Query-defaulted param explicitly. Called directly (not through
    # FastAPI) the unset params would otherwise stay as Query sentinel objects,
    # and `if tag:` on a sentinel is truthy so tag.split(",") crashes.
    params = dict(search=None, tag=None, model=None, album=None,
                  favorites=False, sort="recent", seed=None, offset=0, limit=24)
    params.update(kw)
    return await library(request=None, **params)


def test_multiword_search_matches_out_of_order(library):
    import asyncio
    _seed("a car that is red", "a blue boat")
    res = asyncio.run(_call(library, search="red car"))
    prompts = {it["prompt"] for it in res["items"]}
    assert "a car that is red" in prompts  # old phrase-only LIKE missed this
    assert "a blue boat" not in prompts


def test_literal_percent_is_escaped(library):
    import asyncio
    _seed("discount 100% off", "plain text")
    res = asyncio.run(_call(library, search="100%"))
    prompts = {it["prompt"] for it in res["items"]}
    assert prompts == {"discount 100% off"}  # not every row
