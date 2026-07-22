"""Tests for the Odyssey life-data substrate (src/life_store.py).

The store's contract: files are the source of truth; registry.json and the
vector index are derived projections kept in sync on every write. These tests
assert that contract against a fake indexer (deterministic, no ChromaDB), plus
one integration test that exercises real retrieval when a RAG backend is up.
"""

import json

import pytest

from src.life_store import (
    ALLOWED_EXTENSIONS,
    LifeStore,
    RagLifeIndexer,
    _derive_title,
    _validate,
)


class FakeIndexer:
    """In-memory stand-in for the RAG seam. Records what was indexed under each
    source so tests can assert save/delete keep the index in sync."""

    def __init__(self):
        self.indexed = {}   # source -> (text, metadata)
        self.calls = []

    def index(self, *, text, source, metadata):
        self.indexed[source] = (text, metadata)
        self.calls.append(("index", source))
        return 1

    def remove(self, *, source):
        existed = self.indexed.pop(source, None)
        self.calls.append(("remove", source))
        return 1 if existed else 0

    def search(self, query):
        """Faithful-enough keyword search over indexed text, so 'retrievable
        via search' is checkable without ChromaDB."""
        q = query.lower()
        return [src for src, (text, _) in self.indexed.items() if q in text.lower()]


@pytest.fixture
def store(tmp_path):
    return LifeStore(root=str(tmp_path / "life"), indexer=FakeIndexer())


# -- save: file is written, registry updated, artifact indexed ---------------


def test_save_writes_file_under_domain(store):
    store.save("recipes", "tacos.md", "# Tacos\n\nCorn tortillas.")
    assert store.read("recipes", "tacos.md") == "# Tacos\n\nCorn tortillas."


def test_save_updates_registry_manifest(store):
    store.save("recipes", "tacos.md", "# Tacos\n")

    with open(store.registry_path, encoding="utf-8") as f:
        raw = json.load(f)

    assert raw["version"] == 1
    entries = raw["domains"]["recipes"]
    assert [e["name"] for e in entries] == ["tacos.md"]
    entry = entries[0]
    assert entry["path"] == "recipes/tacos.md"
    assert entry["type"] == ".md"
    assert entry["title"] == "Tacos"      # derived from the H1
    assert entry["size"] > 0


def test_save_indexes_content_with_life_metadata(store):
    store.save("recipes", "tacos.md", "# Tacos\n\nCarne asada.", owner="alice")

    text, meta = store.indexer.indexed["recipes/tacos.md"]
    assert "Carne asada" in text
    assert meta["kind"] == "life"
    assert meta["life_domain"] == "recipes"
    assert meta["life_artifact"] == "tacos.md"
    assert meta["type"] == ".md"
    assert meta["owner"] == "alice"


def test_saved_artifact_is_retrievable_via_search(store):
    """The acceptance criterion: an added file becomes findable."""
    store.save("recipes", "tacos.md", "# Tacos\n\nSmoked brisket filling.")
    assert "recipes/tacos.md" in store.indexer.search("brisket")


def test_owner_omitted_when_not_provided(store):
    store.save("routines", "night.md", "# Night\n")
    _text, meta = store.indexer.indexed["routines/night.md"]
    assert "owner" not in meta


# -- resave and delete keep projections in sync ------------------------------


def test_resave_replaces_index_content(store):
    store.save("recipes", "tacos.md", "# Tacos\n\nold filling")
    store.save("recipes", "tacos.md", "# Tacos\n\nnew filling")

    text, _ = store.indexer.indexed["recipes/tacos.md"]
    assert "new filling" in text and "old filling" not in text
    assert store.indexer.search("old filling") == []


def test_delete_removes_file_registry_entry_and_index(store):
    store.save("recipes", "tacos.md", "# Tacos\n")
    store.delete("recipes", "tacos.md")

    assert store.list_artifacts("recipes") == []
    assert "recipes/tacos.md" not in store.indexer.indexed
    with open(store.registry_path, encoding="utf-8") as f:
        assert json.load(f)["domains"].get("recipes", []) == []


def test_delete_missing_artifact_is_noop(store):
    store.delete("recipes", "ghost.md")  # must not raise
    assert store.list_artifacts("recipes") == []


# -- registry is a derived projection, never a second source of truth ---------


def test_rebuild_registry_reflects_files_on_disk(store, tmp_path):
    store.save("recipes", "tacos.md", "# Tacos\n")
    # Write a file directly, bypassing the store, then rebuild.
    (tmp_path / "life" / "recipes" / "salsa.md").write_text("# Salsa\n", encoding="utf-8")

    registry = store.rebuild_registry()
    names = {a.name for a in registry.domains["recipes"]}
    assert names == {"tacos.md", "salsa.md"}


def test_load_registry_rebuilds_when_missing(store, tmp_path):
    store.save("recipes", "tacos.md", "# Tacos\n")
    (tmp_path / "life" / "registry.json").unlink()

    registry = store.load_registry()
    assert [a.name for a in registry.domains["recipes"]] == ["tacos.md"]


def test_seed_domains_exist_after_construction(tmp_path):
    LifeStore(root=str(tmp_path / "life"), indexer=FakeIndexer())
    for domain in ("routines", "recipes", "weekly"):
        assert (tmp_path / "life" / domain).is_dir()


def test_list_artifacts_across_all_domains(store):
    store.save("recipes", "tacos.md", "# Tacos\n")
    store.save("routines", "night.md", "# Night\n")
    names = {a.name for a in store.list_artifacts()}
    assert names == {"tacos.md", "night.md"}


# -- validation at the trust boundary ----------------------------------------


@pytest.mark.parametrize(
    "domain,name",
    [
        ("recipes", "tacos.txt"),        # disallowed extension
        ("recipes", "../escape.md"),     # path traversal
        ("recipes", "sub/dir.md"),       # nested path
        ("Bad Domain", "tacos.md"),      # invalid domain slug
        ("recipes", ".hidden.md"),       # leading dot
    ],
)
def test_save_rejects_unsafe_identifiers(store, domain, name):
    with pytest.raises(ValueError):
        store.save(domain, name, "# x\n")


def test_html_title_derivation():
    assert _derive_title("plan.html", ".html", "<h1>Weekly Plan</h1>") == "Weekly Plan"
    assert _derive_title("plan.html", ".html", "<title>Fallback</title>") == "Fallback"
    assert _derive_title("plan.html", ".html", "<p>no heading</p>") == "plan"


def test_validate_returns_extension():
    assert _validate("recipes", "tacos.md") == ".md"
    assert _validate("weekly", "2026-W30.html") == ".html"
    assert ".md" in ALLOWED_EXTENSIONS


# -- integration: real retrieval through the RAG when a backend is available --


def test_real_rag_indexes_and_retrieves(tmp_path):
    from src.rag_singleton import get_rag_manager

    rag = get_rag_manager()
    if rag is None or not getattr(rag, "healthy", False):
        pytest.skip("ChromaDB/RAG backend not reachable — integration path not exercised")

    store = LifeStore(root=str(tmp_path / "life"), indexer=RagLifeIndexer())
    store.save("recipes", "tacos.md", "# Tacos\n\nThe filling is smoked pineapple carnitas.")

    hits = rag.search("smoked pineapple carnitas", k=5)
    sources = {h.get("metadata", {}).get("source") for h in hits}
    assert "recipes/tacos.md" in sources
