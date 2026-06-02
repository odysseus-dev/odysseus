"""Regression: add_directory must not un-exclude files in sibling directories.

``add_directory`` clears exclusions for files inside the directory being added.
It previously used a raw ``path.startswith(directory)`` test, which also matched
sibling directories sharing a name prefix — so adding ``/docs`` would silently
drop exclusions for files under ``/docs2``. The match must respect a path
boundary.
"""
import os

from src import personal_docs


def _make_manager(tmp_path):
    mgr = personal_docs.PersonalDocsManager(str(tmp_path))
    # Pre-seed the directory as already tracked so add_directory takes the
    # cheap "already indexed" branch (no indexing / refresh side effects); the
    # exclusion-clearing logic under test runs unconditionally before that.
    return mgr


def test_sibling_directory_exclusions_survive(tmp_path):
    docs = tmp_path / "docs"
    docs2 = tmp_path / "docs2"
    docs.mkdir()
    docs2.mkdir()

    sibling_excluded = os.path.abspath(str(docs2 / "secret.txt"))
    mgr = _make_manager(tmp_path)
    mgr.indexed_directories = [os.path.abspath(str(docs))]
    mgr.excluded_files = {sibling_excluded}

    mgr.add_directory(str(docs))

    # The sibling-directory exclusion must remain — /docs2 is not under /docs.
    assert sibling_excluded in mgr.excluded_files


def test_own_directory_exclusions_are_cleared(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    own_excluded = os.path.abspath(str(docs / "old.txt"))
    mgr = _make_manager(tmp_path)
    mgr.indexed_directories = [os.path.abspath(str(docs))]
    mgr.excluded_files = {own_excluded}

    mgr.add_directory(str(docs))

    # A file genuinely inside the added directory should be un-excluded.
    assert own_excluded not in mgr.excluded_files
