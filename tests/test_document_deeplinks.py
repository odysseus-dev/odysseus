import sys
import types
from types import SimpleNamespace


class _Column:
    def __eq__(self, other):
        return self

    def desc(self):
        return self


class _DocumentModel:
    id = _Column()
    is_active = _Column()
    owner = _Column()
    updated_at = _Column()


if "core.database" not in sys.modules:
    core_mod = types.ModuleType("core")
    core_mod.__path__ = []
    db_mod = types.ModuleType("core.database")
    db_mod.Document = _DocumentModel
    db_mod.DocumentVersion = object
    db_mod.Session = object
    sys.modules["core"] = core_mod
    sys.modules["core.database"] = db_mod

from routes.document_helpers import _document_ref_slug, _resolve_document_ref


class _FakeQuery:
    def __init__(self, first_doc=None, docs=None):
        self._first_doc = first_doc
        self._docs = docs or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def first(self):
        return self._first_doc

    def all(self):
        return self._docs


class _FakeDb:
    def __init__(self, first_doc=None, docs=None):
        self._first_doc = first_doc
        self._docs = docs or []
        self._calls = 0

    def query(self, *args, **kwargs):
        self._calls += 1
        if self._calls == 1:
            return _FakeQuery(first_doc=self._first_doc)
        return _FakeQuery(docs=self._docs)


def test_document_ref_slug_matches_agent_title_links():
    assert _document_ref_slug("Gradran Homes Social Media Advertising Strategy") == (
        "gradran-homes-social-media-advertising-strategy"
    )
    assert _document_ref_slug("Gradran_Homes Strategy.pdf") == "gradran-homes-strategy"


def test_resolve_document_ref_prefers_exact_id():
    doc = SimpleNamespace(id="doc-123", title="Different Title")

    assert _resolve_document_ref(_FakeDb(first_doc=doc), "doc-123", "alice") is doc


def test_resolve_document_ref_accepts_title_slug():
    doc = SimpleNamespace(id="uuid-1", title="Gradran Homes Social Media Advertising Strategy")

    assert _resolve_document_ref(
        _FakeDb(docs=[doc]),
        "gradran-homes-social-media-advertising-strategy",
        "alice",
    ) is doc
