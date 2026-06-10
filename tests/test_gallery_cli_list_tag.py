"""Regression: `odysseus-gallery list --tag` must match the web route.

cmd_list filtered `GalleryImage.tags.ilike("%<whole arg>%")` — it matched the
entire --tag string (commas included) against the manual `tags` column only.
The web route (gallery_library) splits a comma-separated tag string and
AND-stacks each tag, matching EITHER `tags` or the AI-generated `ai_tags`
column. So the CLI ignored ai_tags and never narrowed on stacked tags.
"""
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_db(monkeypatch):
    # Use monkeypatch.setitem so sys.modules is restored at teardown. A raw
    # assignment left this by-path copy of core.database (and a mutated core
    # __path__) in place and broke every later-collected test that imports the
    # real module.
    if "core" not in sys.modules:
        core_pkg = types.ModuleType("core")
        core_pkg.__path__ = [str(ROOT / "core")]
        monkeypatch.setitem(sys.modules, "core", core_pkg)
    path = ROOT / "core" / "database.py"
    spec = importlib.util.spec_from_file_location("core.database", path)
    db = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "core.database", db)
    spec.loader.exec_module(db)
    return db


def _load_cli():
    path = ROOT / "scripts" / "odysseus-gallery"
    loader = importlib.machinery.SourceFileLoader("odysseus_gallery_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_list_tag_splits_commas_and_searches_ai_tags(monkeypatch):
    db = _load_db(monkeypatch)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    db.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db.SessionLocal = Session  # CLI imports this name at load time

    s = Session()
    s.add_all([
        db.GalleryImage(id="a", filename="a.png", tags="sunset,beach", ai_tags="", is_active=True),
        db.GalleryImage(id="b", filename="b.png", tags="sunset", ai_tags="", is_active=True),
        db.GalleryImage(id="c", filename="c.png", tags="", ai_tags="sunset,beach", is_active=True),
    ])
    s.commit()
    s.close()

    cli = _load_cli()
    captured = {}
    cli.emit = lambda payload, args: captured.setdefault("rows", payload)

    args = types.SimpleNamespace(tag="sunset,beach", favorites=False,
                                 album=None, limit=50, json=False)
    cli.cmd_list(args)

    ids = sorted(r["id"] for r in captured["rows"])
    # 'a' (both in tags) and 'c' (both in ai_tags) match every stacked tag;
    # 'b' lacks 'beach'. The old code returned only ['a'].
    assert ids == ["a", "c"], ids
