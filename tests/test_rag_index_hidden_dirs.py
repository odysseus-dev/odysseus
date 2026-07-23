"""Regression guard for #5559 — directory indexing must skip hidden directories,
hidden files, and well-known junk directories.

VectorRAG.index_personal_documents walked the whole tree with no pruning, so
pointing RAG at a real-world folder (an Obsidian vault, a git repo) swept in
`.obsidian/` plugin JavaScript, `.git/` internals, `node_modules/`, etc. The
junk multiplied indexing time and polluted retrieval.

These tests are hermetic — no chromadb; VectorRAG is created via __new__ (skip
Chroma connect) with add_document stubbed to record which files get indexed.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import src.rag_vector as rag_vector


def _make_rag(recorded_sources):
    rag = rag_vector.VectorRAG.__new__(rag_vector.VectorRAG)  # skip Chroma connect

    def _record(text, metadata):
        recorded_sources.add(metadata["source"])
        return True

    rag.add_document = _record
    return rag


def _write(path, content="some real content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_index_skips_hidden_and_junk_directories(tmp_path):
    _write(tmp_path / "note.md")
    _write(tmp_path / "sub" / "deeper.md")
    _write(tmp_path / ".obsidian" / "plugins" / "plugin.js")
    _write(tmp_path / ".git" / "hooks.js")
    _write(tmp_path / "node_modules" / "lib" / "index.js")
    _write(tmp_path / "__pycache__" / "cached.py")
    _write(tmp_path / "venv" / "lib" / "site.py")

    recorded = set()
    rag = _make_rag(recorded)
    result = rag.index_personal_documents(str(tmp_path))

    assert result["success"] is True
    indexed = {os.path.relpath(p, str(tmp_path)) for p in recorded}
    assert indexed == {"note.md", os.path.join("sub", "deeper.md")}


def test_index_skips_hidden_files(tmp_path):
    _write(tmp_path / "visible.md")
    _write(tmp_path / ".hidden.md")
    _write(tmp_path / "sub" / ".secret.txt")

    recorded = set()
    rag = _make_rag(recorded)
    rag.index_personal_documents(str(tmp_path))

    indexed = {os.path.relpath(p, str(tmp_path)) for p in recorded}
    assert indexed == {"visible.md"}


def test_explicitly_passed_hidden_root_is_still_indexed(tmp_path):
    """Pruning applies to children only — a user who deliberately points RAG at
    a hidden directory gets its contents, minus nested hidden/junk dirs."""
    root = tmp_path / ".notes"
    _write(root / "idea.md")
    _write(root / ".obsidian" / "plugin.js")

    recorded = set()
    rag = _make_rag(recorded)
    rag.index_personal_documents(str(root))

    indexed = {os.path.relpath(p, str(root)) for p in recorded}
    assert indexed == {"idea.md"}
