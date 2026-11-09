"""The single filesystem confinement boundary.

Nine test files in this suite each prove one call site confines correctly, and
each call site had its own ``realpath``/``commonpath`` pair to prove it about.
This file covers the one implementation they now all go through, so a property
is asserted once instead of nine times and inconsistently.

Each test names the detail the scattered copies disagreed on. The macOS tests
are the ones with history: ``/tmp`` is a symlink to ``/private/tmp`` there, and
comparing a canonicalized candidate against a root that was not canonicalized
has already produced a false failure in this suite.
"""
import os
import sys

import pytest

from src.path_confinement import (
    PathEscape,
    canonical_root,
    confine,
    is_inside,
)


@pytest.fixture
def root(tmp_path):
    """A real directory, canonicalized the way a caller's root should be.

    tmp_path is under ``/private/var/...`` on macOS via a ``/var`` symlink, so
    this fixture is itself an instance of the aliasing the module exists to
    handle — which is why it is used as-is rather than pre-resolved.
    """
    (tmp_path / "inside.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    return str(tmp_path)


# ── the basic shape ─────────────────────────────────────────────────────────
def test_path_under_the_root_resolves(root):
    assert confine(root, "inside.txt") == os.path.join(canonical_root(root), "inside.txt")


def test_relative_candidate_joins_the_root_not_the_process_cwd(root, tmp_path, monkeypatch):
    """abspath() of a relative path silently uses os.getcwd().

    A confinement helper that does that resolves against whatever directory the
    server happens to be running in, which is the wrong base before the
    comparison even starts.
    """
    elsewhere = tmp_path.parent / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert confine(root, "inside.txt") == os.path.join(canonical_root(root), "inside.txt")


def test_the_root_itself_is_inside_by_default(root):
    assert confine(root, root) == canonical_root(root)


def test_allow_root_false_excludes_the_root(root):
    """For an operation only meaningful on something *under* the root."""
    with pytest.raises(PathEscape):
        confine(root, root, allow_root=False)
    assert confine(root, "sub", allow_root=False)


def test_a_target_that_does_not_exist_yet_is_confined_not_refused(root):
    """A write target is a legitimate thing to confine.

    realpath is the non-strict kind: it resolves what exists and normalizes the
    rest, so a new file under the root passes while a new file above it does
    not.
    """
    assert confine(root, "not-created-yet.txt").startswith(canonical_root(root))
    with pytest.raises(PathEscape):
        confine(root, "../not-created-yet.txt")


# ── traversal ───────────────────────────────────────────────────────────────
def test_dotdot_escape_is_refused(root):
    with pytest.raises(PathEscape):
        confine(root, "../outside.txt")
    with pytest.raises(PathEscape):
        confine(root, "sub/../../outside.txt")


def test_absolute_candidate_outside_the_root_is_refused(root):
    with pytest.raises(PathEscape):
        confine(root, os.path.dirname(canonical_root(root)))


def test_sibling_with_a_shared_prefix_is_not_inside(tmp_path):
    """`/a/bc` begins with `/a/b` and is not inside it.

    This is why the boundary uses commonpath and not startswith. A copy written
    with startswith accepts the sibling.
    """
    (tmp_path / "b").mkdir()
    (tmp_path / "bc").mkdir()
    (tmp_path / "bc" / "f.txt").write_text("x", encoding="utf-8")
    assert not is_inside(tmp_path / "b", tmp_path / "bc" / "f.txt")
    assert is_inside(tmp_path / "bc", tmp_path / "bc" / "f.txt")


# ── symlinks ────────────────────────────────────────────────────────────────
@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX symlinks")
def test_symlink_as_the_final_component_is_followed_before_the_check(root, tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, os.path.join(root, "link.txt"))
    with pytest.raises(PathEscape):
        confine(root, "link.txt")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX symlinks")
def test_symlinked_intermediate_directory_is_followed_before_the_check(root, tmp_path):
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "f.txt").write_text("secret", encoding="utf-8")
    os.symlink(outside_dir, os.path.join(root, "hop"))
    with pytest.raises(PathEscape):
        confine(root, "hop/f.txt")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX symlinks")
def test_symlink_pointing_back_inside_the_root_is_allowed(root):
    os.symlink(os.path.join(root, "inside.txt"), os.path.join(root, "loop.txt"))
    assert confine(root, "loop.txt") == os.path.join(canonical_root(root), "inside.txt")


# ── the aliasing class that has already cost real time ──────────────────────
@pytest.mark.skipif(
    not os.path.islink("/tmp"), reason="needs a platform where /tmp is a symlink",
)
def test_root_reached_through_a_symlink_still_contains_its_own_files(tmp_path):
    """macOS: /tmp is a symlink to /private/tmp.

    A root given as `/tmp/x` and a candidate that canonicalizes to
    `/private/tmp/x/f` describe the same file. Canonicalizing one side and not
    the other reads as an escape and refuses a legitimate access — the false
    false failure this suite has already recorded. Canonicalizing *neither*
    side would agree, which is why the rule is both or nothing.
    """
    unresolved_root = os.path.join("/tmp", os.path.basename(str(tmp_path)))
    os.makedirs(unresolved_root, exist_ok=True)
    try:
        target = os.path.join(unresolved_root, "f.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("x")
        assert os.path.realpath(unresolved_root) != unresolved_root, (
            "fixture assumption: /tmp should not canonicalize to itself here"
        )
        # Either spelling of the root, either spelling of the candidate.
        assert is_inside(unresolved_root, target)
        assert is_inside(unresolved_root, os.path.realpath(target))
        assert is_inside(os.path.realpath(unresolved_root), target)
    finally:
        try:
            os.remove(os.path.join(unresolved_root, "f.txt"))
            os.rmdir(unresolved_root)
        except OSError:
            pass


def test_canonical_root_is_idempotent(root):
    once = canonical_root(root)
    assert canonical_root(once) == once


# ── malformed input is a reason, not an accidental "outside" ────────────────
@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_candidate_is_a_value_error_not_an_escape(root, bad):
    with pytest.raises(ValueError) as caught:
        confine(root, bad)
    assert not isinstance(caught.value, PathEscape)
    assert "required" in str(caught.value)


def test_nul_is_refused_with_a_reason(root):
    with pytest.raises(ValueError, match="NUL"):
        confine(root, "a\x00b")


def test_newline_is_refused_with_a_reason(root):
    with pytest.raises(ValueError, match="newline"):
        confine(root, "a\nb")


def test_is_inside_is_false_for_malformed_input_rather_than_raising(root):
    """The predicate form never raises; that is why callers wrapped the old
    copies in `except Exception` and reached "outside" by accident."""
    for bad in ("", None, "a\x00b", "a\nb", 17, object()):
        assert is_inside(root, bad) is False
    assert is_inside(None, "x") is False


def test_path_escape_is_a_value_error(root):
    """The call sites this replaces raised ValueError and their callers catch
    it as such, so the subclass relationship is part of the contract."""
    assert issubclass(PathEscape, ValueError)
    with pytest.raises(ValueError):
        confine(root, "../elsewhere")


def test_path_escape_names_the_root_and_the_candidate(root):
    with pytest.raises(PathEscape) as caught:
        confine(root, "../elsewhere")
    message = str(caught.value)
    assert "../elsewhere" in message
    assert canonical_root(root) in message


# ── the deny list is somebody else's job ────────────────────────────────────
def test_confinement_does_not_decide_whether_a_path_is_sensitive(root):
    """`.ssh` inside the root is inside the root.

    Confinement answers "inside"; the sensitive-file deny list answers
    "allowed", and it stays with src.tool_execution, which owns that policy.
    Folding the two together here is how a boundary acquires a second job and
    then disagrees with itself.
    """
    secret = os.path.join(root, ".ssh")
    os.makedirs(secret, exist_ok=True)
    assert is_inside(root, os.path.join(secret, "id_rsa"))
