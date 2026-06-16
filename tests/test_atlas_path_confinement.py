"""Security: every path the Atlas API/tool touches stays inside the owner's vault.

``safe_atlas_path`` is the single confinement gate. These tests pin that
traversal, absolute-path escape, and symlink escape can't reach outside the
per-owner root, and that one owner's slug never resolves into another's tree.
"""

import os
from pathlib import Path

import pytest

import routes.atlas_routes as ar


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "ATLAS_ROOT", Path(tmp_path))
    ar._notes_cache.clear()
    return tmp_path


def test_safe_path_inside_root(vault):
    p = ar.safe_atlas_path("alice", "notes/idea.md")
    root = Path(os.path.realpath(vault / "alice"))
    assert Path(os.path.realpath(p)).is_relative_to(root)


@pytest.mark.parametrize("bad", ["../escape.md", "a/../../b.md", "..\\..\\win.md", "C:\\x.md"])
def test_traversal_is_blocked(vault, bad):
    with pytest.raises(ar.AtlasPathError):
        ar.safe_atlas_path("alice", bad)


def test_absolute_path_is_contained_not_escaped(vault):
    # A leading slash is stripped, so '/etc/passwd' becomes a vault-relative
    # 'etc/passwd' INSIDE the owner root — contained, never the real /etc/passwd.
    p = ar.safe_atlas_path("alice", "/etc/passwd")
    root = Path(os.path.realpath(vault / "alice"))
    assert Path(os.path.realpath(p)).is_relative_to(root)
    assert str(p) != "/etc/passwd"


def test_empty_and_nul_rejected(vault):
    for bad in ["", "   ", "a\x00b"]:
        with pytest.raises(ar.AtlasPathError):
            ar.safe_atlas_path("alice", bad)


def test_symlink_escape_is_blocked(vault):
    root = ar.owner_root("alice")
    outside = vault / "outside_secret"
    outside.mkdir()
    (outside / "secret.md").write_text("top secret")
    link = root / "escape"
    try:
        os.symlink(outside, link)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(ar.AtlasPathError):
        ar.safe_atlas_path("alice", "escape/secret.md")


def test_owners_are_isolated(vault):
    ar.write_note("alice", "private", "alice's note")
    # Bob cannot name a path that lands in alice's tree.
    with pytest.raises(ar.AtlasPathError):
        ar.safe_atlas_path("bob", "../alice/private.md")
    assert list(ar.read_all_notes("bob")) == []
    assert list(ar.read_all_notes("alice")) == ["private.md"]


def test_write_note_adds_md_extension_and_confines(vault):
    rel = ar.write_note("alice", "Daily/today", "# Hi")
    assert rel == "Daily/today.md"
    assert (ar.owner_root("alice") / "Daily" / "today.md").is_file()


def test_write_note_size_limit(vault):
    huge = "x" * (ar.MAX_NOTE_BYTES + 1)
    with pytest.raises(ar.AtlasPathError):
        ar.write_note("alice", "big", huge)
