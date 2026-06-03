"""Regression: _resolve_allowed_personal_dir must resolve symlinks (realpath)
when confining a path to PERSONAL_DIR.

It used os.path.abspath, which normalises ``..`` but does NOT resolve symlinks,
so a symlink placed inside PERSONAL_DIR pointing outside it passes the
os.path.commonpath confinement check and lets index_personal_documents read
files outside the root. os.path.realpath resolves the symlink before the check.

_resolve_allowed_personal_dir is a closure inside setup_personal_routes, so the
source-level test pins the fix and the behavioural test proves the underlying
confinement principle.
"""
import ast
import os
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "routes" / "personal_routes.py"


def _function_source(src_text, name):
    tree = ast.parse(src_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src_text, node)
    raise AssertionError(f"{name} not found in {SRC}")


def test_confinement_uses_realpath_not_abspath():
    body = _function_source(SRC.read_text(), "_resolve_allowed_personal_dir")
    assert "os.path.realpath" in body, (
        "_resolve_allowed_personal_dir must use os.path.realpath so a symlink "
        "inside PERSONAL_DIR cannot escape the confinement check"
    )
    assert "os.path.abspath" not in body, (
        "os.path.abspath does not resolve symlinks; the confinement check must "
        "not rely on it"
    )


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
