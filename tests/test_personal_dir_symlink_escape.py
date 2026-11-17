"""Regression: _resolve_allowed_personal_dir must resolve symlinks (realpath)
when confining a path to PERSONAL_DIR.

It used os.path.abspath, which normalises ``..`` but does NOT resolve symlinks,
so a symlink placed inside PERSONAL_DIR pointing outside it passes the
os.path.commonpath confinement check and lets index_personal_documents read
files outside the root. os.path.realpath resolves the symlink before the check.

_resolve_allowed_personal_dir is a closure inside setup_personal_routes, so it
cannot be imported and called directly. The resolution now happens in
src.path_confinement, so the behavioural test runs against that boundary and
the source-level test is reduced to the one thing still worth pinning here:
this closure must not grow its own abspath-based check again.
"""
import ast
import os
from pathlib import Path

import pytest

from src.path_confinement import PathEscape, confine

SRC = Path(__file__).resolve().parent.parent / "routes" / "personal_routes.py"


def _function_source(src_text, name):
    tree = ast.parse(src_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src_text, node)
    raise AssertionError(f"{name} not found in {SRC}")


def test_confinement_does_not_rely_on_abspath():
    """The resolver must not reach a confinement verdict through abspath.

    Originally this asserted the presence of the literal ``os.path.realpath``.
    The resolution now happens inside ``src.path_confinement.confine``, which
    is the point — one boundary instead of a copy per call site — so the
    literal is gone while the behaviour is unchanged. What is still worth
    pinning at the source level is the negative: this closure must not grow its
    own abspath-based check again.
    """
    body = _function_source(SRC.read_text(), "_resolve_allowed_personal_dir")
    assert "os.path.abspath" not in body, (
        "os.path.abspath does not resolve symlinks; the confinement check must "
        "not rely on it"
    )
    assert "confine(" in body, (
        "the resolver must go through the shared confinement boundary rather "
        "than reimplementing one"
    )


def test_shared_boundary_refuses_a_symlink_out_of_the_base(tmp_path):
    """The behaviour the source assertion used to stand in for.

    A symlink inside the base pointing outside it is refused, and the file it
    points at is not reachable through it. This is asserted against the
    boundary the resolver now calls, so it covers every call site that shares
    it rather than this one closure.
    """
    base = tmp_path / "personal"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    os.symlink(outside, base / "escape")

    with pytest.raises(PathEscape):
        confine(base, "escape")
    with pytest.raises(PathEscape):
        confine(base, "escape/secret.txt")
    # A real directory inside the base is still reachable.
    (base / "real").mkdir()
    assert confine(base, "real") == os.path.join(os.path.realpath(base), "real")


def test_realpath_catches_symlink_escape(tmp_path):
    # The principle the fix relies on: abspath keeps the symlink path inside the
    # base (confinement fooled); realpath resolves it outside (confinement holds).
    base = tmp_path / "personal"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = base / "escape"
    os.symlink(outside, link)

    base_abs = os.path.realpath(base)  # base itself may live under a symlinked tmp
    # abspath: the symlink still looks inside base -> escape not detected
    assert os.path.commonpath([os.path.abspath(base / "escape"), os.path.abspath(base)]) == os.path.abspath(base)
    # realpath: the symlink resolves to `outside` -> escape detected
    assert os.path.commonpath([os.path.realpath(link), base_abs]) != base_abs
