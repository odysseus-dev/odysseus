import io
import sys
import tempfile
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


def _drop_fake_core_database():
    parent = sys.modules.get("core")
    attr = getattr(parent, "database", None) if parent is not None else None
    mod = sys.modules.get("core.database") or attr
    if mod is None or isinstance(getattr(mod, "__file__", None), str):
        return
    sys.modules.pop("core.database", None)
    sys.modules.pop("src.database", None)
    if parent is not None and attr is mod:
        delattr(parent, "database")


_drop_fake_core_database()

import core.database as cdb
import routes.document_routes as droutes
from core.database import Document


def _endpoint(method, path):
    router = droutes.setup_document_routes(MagicMock(), None)
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


class _Request:
    state = SimpleNamespace(current_user="alice")

    def __init__(self, ids):
        self._ids = ids

    async def json(self):
        return {"ids": self._ids}


async def test_export_zip_appends_language_extension_for_dotted_titles(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    engine = create_engine(
        f"sqlite:///{tmp.name}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(droutes, "SessionLocal", session_factory)

    docs = [
        Document(
            id="doc-dotted",
            owner="alice",
            title="analysis.v1",
            language="python",
            current_content="print('one')\n",
            version_count=1,
        ),
        Document(
            id="doc-with-ext",
            owner="alice",
            title="script.py",
            language="python",
            current_content="print('two')\n",
            version_count=1,
        ),
        Document(
            id="doc-duplicate",
            owner="alice",
            title="analysis.v1",
            language="python",
            current_content="print('three')\n",
            version_count=1,
        ),
    ]
    doc_ids = [doc.id for doc in docs]
    db = session_factory()
    try:
        db.add_all(docs)
        db.commit()
    finally:
        db.close()

    export_zip = _endpoint("POST", "/api/documents/export-zip")
    response = await export_zip(_Request(doc_ids))

    with zipfile.ZipFile(io.BytesIO(response.body)) as zf:
        assert set(zf.namelist()) == {
            "analysis.v1.py",
            "analysis.v1-1.py",
            "script.py",
        }
