"""Regression test for odysseus-docs `search` multi-word queries.

The CLI used a single `%foo bar%` LIKE, which only matched documents
where the words appeared as an exact adjacent phrase. The web route
(routes/document_routes.py) splits the query on whitespace and requires
EACH term to match (title OR content). This test asserts the CLI mirrors
that per-term AND behaviour so multi-word searches don't silently return
nothing.
"""

import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]


def _load_db_module(monkeypatch):
    # Load core/database.py directly by path to avoid the heavy core
    # package __init__ (pyotp/cryptography aren't installed in CI envs).
    # Use monkeypatch.setitem so sys.modules is restored at teardown — a raw
    # assignment left this by-path copy of core.database in place and broke
    # every later-collected test that imports the real module.
    if "core" not in sys.modules:
        core_pkg = types.ModuleType("core")
        core_pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "core", core_pkg)
    spec = importlib.util.spec_from_file_location(
        "core.database", str(ROOT / "core" / "database.py")
    )
    cdb = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    spec.loader.exec_module(cdb)
    return cdb


def _load_cli(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts" / "_lib"))
    path = ROOT / "scripts" / "odysseus-docs"
    loader = importlib.machinery.SourceFileLoader("odysseus_docs_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _Args:
    def __init__(self, query, limit=50):
        self.query = query
        self.limit = limit
        self.pretty = False


def _run_search(cli, query):
    captured = []
    cli.emit = lambda data, args: captured.append(data)
    cli.cmd_search(_Args(query))
    return [row["id"] for row in captured[0]]


def test_multiword_search_matches_terms_in_any_position(monkeypatch):
    cdb = _load_db_module(monkeypatch)
    engine = create_engine("sqlite:///:memory:")
    cdb.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    cdb.SessionLocal = Session

    cli = _load_cli(monkeypatch)

    db = Session()
    # "machine" lives in the content, "learning" in the title — never
    # adjacent, so a single `%machine learning%` LIKE matches nothing.
    db.add(cdb.Document(
        id="d1", title="learning notes",
        current_content="this covers the machine fundamentals",
        is_active=True,
    ))
    db.add(cdb.Document(
        id="d2", title="cooking recipes",
        current_content="nothing relevant here",
        is_active=True,
    ))
    db.commit()
    db.close()

    # Multi-word query must find d1 (FAILS on the old single-LIKE code).
    assert _run_search(cli, "machine learning") == ["d1"]
    # Per-term AND: a query where one term has no match returns nothing.
    assert _run_search(cli, "machine spaghetti") == []
    # Single-word search still works.
    assert _run_search(cli, "machine") == ["d1"]
