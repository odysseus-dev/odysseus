"""Regression guard for #5559 — the KEYWORD index (load_personal_index, which
PersonalDocsManager.refresh_index builds from) must skip hidden dirs, hidden
files, and junk dirs at ANY depth, the same as the vector index. Both walkers
share one pruning helper (src/index_walk) so they cannot drift again.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from src.personal_docs import load_personal_index


def _write(path, content="real content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _indexed(root):
    return {rec["name"] for rec in load_personal_index(str(root))}


def test_keyword_index_skips_hidden_and_junk_dirs(tmp_path):
    _write(tmp_path / "note.md")
    _write(tmp_path / "sub" / "deeper.md")
    _write(tmp_path / ".obsidian" / "workspace.json")
    _write(tmp_path / ".git" / "hooks.md")
    _write(tmp_path / "node_modules" / "lib" / "readme.md")
    _write(tmp_path / "__pycache__" / "cached.txt")
    _write(tmp_path / "venv" / "lib" / "site.txt")
    assert _indexed(tmp_path) == {"note.md", os.path.join("sub", "deeper.md")}


def test_keyword_index_skips_hidden_files(tmp_path):
    _write(tmp_path / "visible.md")
    _write(tmp_path / ".hidden.md")
    _write(tmp_path / "sub" / ".secret.txt")
    assert _indexed(tmp_path) == {"visible.md"}


def test_keyword_index_prunes_junk_at_depth(tmp_path):
    """Pruning must apply at every level, not just the first (the vector test's
    fixtures only nested one level under the root)."""
    _write(tmp_path / "a" / "b" / "keep.md")
    _write(tmp_path / "a" / "b" / "node_modules" / "dep.md")
    _write(tmp_path / "a" / ".obsidian" / "deep.json")
    assert _indexed(tmp_path) == {os.path.join("a", "b", "keep.md")}


def test_keyword_index_junk_match_is_case_insensitive(tmp_path):
    """A case-variant junk dir must still be pruned (macOS default FS is
    case-insensitive, so `Node_Modules` and `node_modules` are the same dir)."""
    _write(tmp_path / "keep.md")
    _write(tmp_path / "Node_Modules" / "dep.md")
    assert _indexed(tmp_path) == {"keep.md"}


def test_keyword_index_explicit_hidden_root_still_indexed(tmp_path):
    """Children-only pruning: pointing indexing at a hidden dir gets its
    contents, minus nested hidden/junk."""
    root = tmp_path / ".notes"
    _write(root / "idea.md")
    _write(root / ".obsidian" / "plugin.json")
    assert {rec["name"] for rec in load_personal_index(str(root))} == {"idea.md"}
